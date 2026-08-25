"""Create / list / get / delete tests for report versions."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.models.report_parameter import ReportParameter
from app.models.user import ROLE_ADMIN, User
from app.services.report_version import create_snapshot, get_version, list_versions


@pytest.fixture
def db():
    """Yield a SQLAlchemy session bound to the dev metadata DB.

    Mirrors the fixture in ``test_report_version_acl.py``: tests create
    and discard their own User / Report rows via the local helpers;
    cleanup is the test's responsibility.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def editor(db: Session):
    u = User(
        username=_unique("editor"),
        role=ROLE_ADMIN,
        disabled=False,
        password_hash="x",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def report(db: Session, editor: User):
    ds = DataSource(name=_unique("ds"), db_type="sqlite", database=":memory:")
    db.add(ds)
    db.commit()
    db.refresh(ds)
    r = Report(
        name=_unique("r"),
        data_source_id=ds.id,
        owner_user_id=editor.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    db.add(
        ReportItem(
            report_id=r.id,
            name="sales",
            item_type="table",
            order_index=0,
            table_name="orders",
            fields=["id", "total"],
        )
    )
    db.add(
        ReportParameter(
            report_id=r.id,
            name="region",
            label="Region",
            type="string",
            required=False,
            order_index=0,
        )
    )
    db.commit()
    db.refresh(r)
    return r


def test_create_snapshot_happy(db, editor, report):
    v = create_snapshot(db, user=editor, report_id=report.id, label="v1 launch")
    assert v.version_number == 1
    assert v.label == "v1 launch"
    assert v.created_by == editor.id
    assert len(v.items) == 1
    assert v.items[0].name == "sales"
    assert len(v.parameters) == 1
    assert v.parameters[0].name == "region"


def test_create_snapshot_increments(db, editor, report):
    v1 = create_snapshot(db, user=editor, report_id=report.id)
    v2 = create_snapshot(db, user=editor, report_id=report.id, label="hotfix")
    assert v1.version_number == 1
    assert v2.version_number == 2


def test_create_snapshot_no_label_allowed(db, editor, report):
    v = create_snapshot(db, user=editor, report_id=report.id)
    assert v.label is None


def test_create_snapshot_missing_report_raises(db, editor):
    with pytest.raises(ValueError):
        create_snapshot(db, user=editor, report_id=99999)


def test_list_versions_ordered_desc(db, editor, report):
    v1 = create_snapshot(db, user=editor, report_id=report.id)  # noqa: F841
    v2 = create_snapshot(db, user=editor, report_id=report.id)  # noqa: F841
    versions = list_versions(db, report_id=report.id)
    assert [v.version_number for v in versions] == [2, 1]


def test_get_version_returns_full_snapshot(db, editor, report):
    v = create_snapshot(db, user=editor, report_id=report.id)
    fetched = get_version(db, version_id=v.id)
    assert fetched.id == v.id
    assert fetched.items[0].table_name == "orders"
    assert fetched.parameters[0].name == "region"
