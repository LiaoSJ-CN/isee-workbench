"""Restore overwrites live Report + items + parameters with snapshot state."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.models.report_parameter import ReportParameter
from app.models.user import ROLE_ADMIN, User
from app.services.report_version import create_snapshot, restore_version


@pytest.fixture
def db():
    """Yield a SQLAlchemy session bound to the dev metadata DB.

    Local copy of the conftest-style fixture; this file must not import
    from ``test_report_version_crud.py``.
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


def test_restore_overwrites_report_fields(db, editor, report):
    v1 = create_snapshot(db, user=editor, report_id=report.id, label="baseline")
    original_name = report.name
    original_desc = report.description
    report.name = "MUTATED"
    report.description = "oops"
    db.commit()
    restored = restore_version(db, user=editor, report_id=report.id, version_id=v1.id)
    db.refresh(restored)
    assert restored.name == original_name
    assert restored.description == original_desc


def test_restore_overwrites_items(db, editor, report):
    v1 = create_snapshot(db, user=editor, report_id=report.id)
    report.items[0].table_name = "mutated_table"
    report.items[0].custom_sql = "SELECT bad"
    db.commit()
    restore_version(db, user=editor, report_id=report.id, version_id=v1.id)
    db.refresh(report)
    assert len(report.items) == 1
    assert report.items[0].table_name == "orders"
    assert report.items[0].custom_sql is None


def test_restore_replaces_items_entirely(db, editor, report):
    """A version with 2 items replaces live state which had 1 item."""
    v1 = create_snapshot(db, user=editor, report_id=report.id)
    db.add(
        ReportItem(
            report_id=report.id,
            name="returns",
            item_type="table",
            order_index=1,
            table_name="returns",
        )
    )
    db.commit()
    v2 = create_snapshot(db, user=editor, report_id=report.id, label="v2")  # noqa: F841
    restore_version(db, user=editor, report_id=report.id, version_id=v1.id)
    db.refresh(report)
    names = sorted([i.name for i in report.items])
    assert names == ["sales"]


def test_restore_overwrites_parameters(db, editor, report):
    v1 = create_snapshot(db, user=editor, report_id=report.id)
    report.parameters[0].label = "Mutated"
    db.commit()
    restore_version(db, user=editor, report_id=report.id, version_id=v1.id)
    db.refresh(report)
    assert report.parameters[0].label == "Region"


def test_restore_wrong_version_404(db, editor, report):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        restore_version(db, user=editor, report_id=report.id, version_id=99999)
    assert exc.value.status_code == 404
