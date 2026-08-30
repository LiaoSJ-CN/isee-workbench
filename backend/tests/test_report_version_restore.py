"""Restore overwrites live Report + items + parameters with snapshot state."""

import uuid

import pytest
from sqlalchemy import text
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


def test_restore_after_rowid_recycling_does_not_unique_violate(
    db, editor, report
) -> None:
    """Regression: bulk DELETE in restore_version must synchronize the
    session identity map before re-INSERTing, otherwise stale objects
    collide with new INSERTs on recycled SQLite rowids.

    Forces the scenario end-to-end:
      1. Load ``report.parameters[0]`` so the session's identity map
         holds an ORM object for the existing row (id=X).
      2. Raw ``DELETE`` that row (bypasses the ORM entirely; the stale
         object stays in the identity map).
      3. Raw ``INSERT`` a new row with the *same* id X + same
         ``(report_id, name='region')`` — SQLite ``INTEGER PRIMARY
         KEY`` without ``AUTOINCREMENT`` reuses the freed rowid.
      4. Call ``restore_version``: it does ``Query.delete()`` then
         ``db.add(ReportParameter(...))``. Without
         ``synchronize_session='fetch'`` on the DELETE, the stale
         identity-map entry (id=X) collides with the INSERT and the
         UNIQUE constraint on ``(report_id, name)`` fires.
    """
    # Step 1: snapshot v1 + load the parameter into the session.
    v1 = create_snapshot(db, user=editor, report_id=report.id)
    stale_param_id = report.parameters[0].id
    # Touch an attribute to make sure it's a fully-loaded persistent
    # object, not a lazy proxy.
    assert report.parameters[0].name == "region"
    db.commit()

    # Step 2: raw DELETE — bypasses ORM, leaves stale object in identity map.
    db.execute(
        text("DELETE FROM report_parameters WHERE id = :pid"),
        {"pid": stale_param_id},
    )
    db.commit()

    # Step 3: raw INSERT reusing the freed rowid + same (report_id, name).
    db.execute(
        text(
            "INSERT INTO report_parameters "
            "(id, report_id, name, label, type, required, order_index) "
            "VALUES (:id, :rid, 'region', 'Stale', 'string', 0, 0)"
        ),
        {"id": stale_param_id, "rid": report.id},
    )
    db.commit()

    # Step 4: restore_version must NOT raise UNIQUE violation.
    # The identity-map fix is in app/services/report_version.py
    # (``synchronize_session='fetch'`` + ``db.expire(report, ['parameters'])``).
    restore_version(db, user=editor, report_id=report.id, version_id=v1.id)

    db.refresh(report)
    assert report.parameters[0].label == "Region"
