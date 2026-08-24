"""Tests for the report-item reorder endpoint (PATCH /reports/{id}/items/order).

Replaces the prior client-side pattern of N parallel PUTs with a single
atomic backend call. These tests lock the all-or-nothing contract:
partial ownership mismatches must reject the whole request.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem


def _unique_name(prefix: str) -> str:
    """Test-local unique name so parallel runs / reruns don't collide."""
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_report_with_items() -> Iterator[tuple[int, list[int]]]:
    """Create a Report with 3 ReportItems (order_index 0,1,2). Yields (report_id, [item_ids])."""
    db: Session = SessionLocal()
    rep_name = _unique_name("pytest_reorder_report")
    ds_name = _unique_name("pytest_reorder_ds")
    src = DataSource(
        name=ds_name,
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(src)
    db.commit()
    db.refresh(src)

    rep = Report(
        name=rep_name,
        data_source_id=src.id,
        is_active=True,
        is_scheduled=False,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)

    item_ids: list[int] = []
    for idx in range(3):
        item = ReportItem(
            report_id=rep.id,
            name=f"item_{idx}",
            item_type="table",
            order_index=idx,
        )
        db.add(item)
        db.flush()
        item_ids.append(item.id)
    db.commit()

    try:
        yield rep.id, item_ids
    finally:
        db.delete(rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


def test_reorder_requires_auth(client: TestClient, temp_report_with_items) -> None:
    rid, _ = temp_report_with_items
    r = client.patch(
        f"/reports/{rid}/items/order",
        json={"items": [{"item_id": 1, "order_index": 0}]},
    )
    assert r.status_code == 401


def test_reorder_happy_path(client: TestClient, auth_headers: dict, temp_report_with_items) -> None:
    """Reverse 3 items: [A,B,C] -> [C,B,A] should land as order_index 0,1,2 in that order."""
    rid, item_ids = temp_report_with_items
    # Original: item_ids[0]=0, item_ids[1]=1, item_ids[2]=2
    # New: item_ids[2] gets 0, item_ids[1] stays 1, item_ids[0] gets 2
    payload = {
        "items": [
            {"item_id": item_ids[2], "order_index": 0},
            {"item_id": item_ids[1], "order_index": 1},
            {"item_id": item_ids[0], "order_index": 2},
        ]
    }
    r = client.patch(f"/reports/{rid}/items/order", json=payload, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 3}

    # Verify DB state — read back via GET /reports/{id}
    get_r = client.get(f"/reports/{rid}", headers=auth_headers)
    assert get_r.status_code == 200
    items = get_r.json()["items"]
    by_name = {i["name"]: i["order_index"] for i in items}
    assert by_name["item_0"] == 2
    assert by_name["item_1"] == 1
    assert by_name["item_2"] == 0


def test_reorder_rejects_empty_list(
    client: TestClient, auth_headers: dict, temp_report_with_items
) -> None:
    rid, _ = temp_report_with_items
    r = client.patch(
        f"/reports/{rid}/items/order",
        json={"items": []},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_reorder_rejects_cross_report_item(
    client: TestClient, auth_headers: dict, temp_report_with_items
) -> None:
    """An item belonging to a different report must reject the whole request (atomicity)."""
    rid, item_ids = temp_report_with_items

    # Build a second report with one item
    db: Session = SessionLocal()
    src = DataSource(
        name=_unique_name("pytest_other_ds"),
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    other_rep = Report(
        name=_unique_name("pytest_other_report"),
        data_source_id=src.id,
        is_active=True,
    )
    db.add(other_rep)
    db.commit()
    db.refresh(other_rep)
    other_item = ReportItem(
        report_id=other_rep.id,
        name="other_item",
        item_type="table",
        order_index=0,
    )
    db.add(other_item)
    db.commit()
    other_item_id = other_item.id

    try:
        # Mix: one of ours + one from another report
        payload = {
            "items": [
                {"item_id": item_ids[0], "order_index": 0},
                {"item_id": other_item_id, "order_index": 1},
            ]
        }
        r = client.patch(f"/reports/{rid}/items/order", json=payload, headers=auth_headers)
        assert r.status_code == 422
        assert "belong" in r.json()["detail"].lower()
    finally:
        db.delete(other_rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


def test_reorder_rejects_missing_item_id(
    client: TestClient, auth_headers: dict, temp_report_with_items
) -> None:
    """An item_id that doesn't exist anywhere must reject the whole request."""
    rid, item_ids = temp_report_with_items
    # 999_999_999 should not exist
    payload = {
        "items": [
            {"item_id": item_ids[0], "order_index": 0},
            {"item_id": 999_999_999, "order_index": 1},
        ]
    }
    r = client.patch(f"/reports/{rid}/items/order", json=payload, headers=auth_headers)
    assert r.status_code == 422
