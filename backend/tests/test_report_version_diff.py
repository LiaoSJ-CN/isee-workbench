"""Tests for the diff algorithm + serialize_full."""

import uuid

import pytest

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.models.report_parameter import ReportParameter
from app.models.user import ROLE_ADMIN, User
from app.services.report_version import create_snapshot
from app.services.report_version_diff import (
    compute_diff,
    serialize_full,
)


@pytest.fixture
def db():
    """Yield a SQLAlchemy session bound to the dev metadata DB.

    Mirrors the fixture in ``test_report_version_acl.py`` /
    ``test_report_version_crud.py``: tests create and discard their own
    User / Report rows; cleanup is the test's responsibility (the dev
    ``app.db`` is intentionally not truncated so we can debug from the
    UI afterwards).
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def editor(db):
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
def report_with_items(db, editor):
    ds = DataSource(name=_unique("ds"), db_type="sqlite", database=":memory:")
    db.add(ds)
    db.commit()
    db.refresh(ds)
    r = Report(name=_unique("r"), data_source_id=ds.id, owner_user_id=editor.id)
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


def test_diff_no_changes(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    result = compute_diff(base_version=v, live_report=report_with_items)
    assert result["report_changes"] == []
    assert result["items_added"] == []
    assert result["items_removed"] == []
    assert result["items_modified"] == []


def test_diff_report_field_change(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    new_name = _unique("mutated")
    report_with_items.name = new_name
    db.commit()
    db.refresh(report_with_items)
    result = compute_diff(base_version=v, live_report=report_with_items)
    field_names = [c["field"] for c in result["report_changes"]]
    assert "name" in field_names
    # Sanity: the actual mutation shows up in the change record.
    change = next(c for c in result["report_changes"] if c["field"] == "name")
    assert change["new_value"] == new_name


def test_diff_items_added_removed_modified(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    db.query(ReportItem).filter(ReportItem.report_id == report_with_items.id).delete()
    db.add(ReportItem(report_id=report_with_items.id, name="a", item_type="table", order_index=0))
    db.add(ReportItem(report_id=report_with_items.id, name="b", item_type="chart", order_index=1))
    db.commit()
    db.refresh(report_with_items)
    result = compute_diff(base_version=v, live_report=report_with_items)
    assert [i.name for i in result["items_added"]] == ["a", "b"]
    assert [i.name for i in result["items_removed"]] == ["sales"]
    assert result["items_modified"] == []


def test_diff_items_matched_by_name(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    report_with_items.items[0].table_name = "mutated"
    db.commit()
    db.refresh(report_with_items)
    result = compute_diff(base_version=v, live_report=report_with_items)
    assert len(result["items_modified"]) == 1
    assert result["items_modified"][0]["name"] == "sales"
    fields = [c["field"] for c in result["items_modified"][0]["changes"]]
    assert "table_name" in fields


def test_diff_items_renamed_treated_as_remove_plus_add(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    report_with_items.items[0].name = "renamed"
    db.commit()
    db.refresh(report_with_items)
    result = compute_diff(base_version=v, live_report=report_with_items)
    assert [i.name for i in result["items_removed"]] == ["sales"]
    assert [i.name for i in result["items_added"]] == ["renamed"]


def test_diff_parameters_modified(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    report_with_items.parameters[0].label = "Mutated"
    db.commit()
    db.refresh(report_with_items)
    result = compute_diff(base_version=v, live_report=report_with_items)
    assert len(result["parameters_modified"]) == 1
    assert result["parameters_modified"][0]["name"] == "region"


def test_serialize_full_includes_items_and_params(db, editor, report_with_items):
    v = create_snapshot(db, user=editor, report_id=report_with_items.id)
    payload = serialize_full(v)
    assert payload["version"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["name"] == "sales"
    assert len(payload["parameters"]) == 1
    assert payload["parameters"][0]["name"] == "region"
