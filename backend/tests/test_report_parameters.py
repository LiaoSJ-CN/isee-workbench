"""Tests for batch 4a — typed parameter declarations + runtime validation.

CRUD tests cover the 4 endpoints at ``/reports/{report_id}/parameters[/...]``.
Runtime validation tests mock ``generate_report`` at the router boundary
to assert exactly which ``parameters`` dict the SQL pipeline receives
after spec validation — no need to drive a real SQLite query path.
"""

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.report_parameter import ReportParameter


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _make_report(prefix: str = "pytest_r") -> int:
    """Create a fresh DataSource + Report; return the report id.

    Caller is responsible for cleanup via ``client.delete(f"/reports/{id}")``
    which cascades parameters and lets us delete the DataSource last.
    """
    ds_name = _unique_name(f"{prefix}_ds")
    rep_name = _unique_name(prefix)
    db: Session = SessionLocal()
    try:
        src = DataSource(
            name=ds_name,
            db_type="sqlite",
            host="placeholder",
            port=1,
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
        )
        db.add(rep)
        db.commit()
        db.refresh(rep)
        return int(rep.id)
    finally:
        db.close()


@pytest.fixture
def temp_report_with_params() -> Iterator[tuple[int, list[int]]]:
    """Create a Report with 3 ReportParameter rows (string/number/enum),
    yield (report_id, [param_ids]). Teardown: delete report first
    (cascade clears parameters) then delete the DataSource.
    """
    rep_id = _make_report("pytest_params")
    db: Session = SessionLocal()
    try:
        params = [
            ReportParameter(
                report_id=rep_id,
                name="region",
                label="Region",
                type="string",
                required=True,
                order_index=0,
            ),
            ReportParameter(
                report_id=rep_id,
                name="limit",
                label="Row cap",
                type="number",
                required=False,
                default=100,
                order_index=1,
            ),
            ReportParameter(
                report_id=rep_id,
                name="status",
                label="Status",
                type="enum",
                options=["active", "archived"],
                required=False,
                default="active",
                order_index=2,
            ),
        ]
        for p in params:
            db.add(p)
        db.commit()
        for p in params:
            db.refresh(p)
        param_ids = [p.id for p in params]
        yield rep_id, param_ids
    finally:
        # Delete via SQLAlchemy (cascade clears parameters) then ds.
        rep_row = db.query(Report).filter(Report.id == rep_id).first()
        if rep_row is not None:
            db.delete(rep_row)
            db.commit()
        db.close()


@pytest.fixture
def stub_generate_report(monkeypatch):
    """Replace ``app.routers.report.generate_report`` with a stub that
    captures the validated ``parameters`` and returns a benign success
    dict so the router doesn't try to actually run SQL.

    Returns the stub so tests can read ``stub.calls``.
    """
    from app.routers import report as report_router

    class _Stub:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, *, report, output_format, parameters, db, **_kwargs):
            self.calls.append(
                {
                    "report_id": report.id,
                    "output_format": output_format,
                    "parameters": parameters,
                }
            )
            # Router builds ReportGenerateResponse with explicit `success=True`
            # and `report_id`, `report_name`, `output_format`, `item_errors`,
            # so the stub only contributes the payload-shaped fields.
            return {"preview_html": "<p>stub</p>"}

    stub = _Stub()
    monkeypatch.setattr(report_router, "generate_report", stub)
    return stub


# ============ CRUD tests ============


def test_create_param_string_happy(client: TestClient, auth_headers: dict) -> None:
    rep_id = _make_report("pytest_create_str")
    try:
        r = client.post(
            f"/reports/{rep_id}/parameters",
            headers=auth_headers,
            json={"type": "string", "name": "region", "label": "Region"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "region"
        assert body["type"] == "string"
        assert body["required"] is True
        assert body["order_index"] == 1  # first param → auto-assigned
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)


def test_create_param_enum_requires_options(client: TestClient, auth_headers: dict) -> None:
    rep_id = _make_report("pytest_enum_no_opts")
    try:
        r = client.post(
            f"/reports/{rep_id}/parameters",
            headers=auth_headers,
            json={"type": "enum", "name": "color", "label": "Color"},
        )
        assert r.status_code == 422, r.text
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)


def test_create_param_unknown_type_is_422(client: TestClient, auth_headers: dict) -> None:
    rep_id = _make_report("pytest_unknown_type")
    try:
        r = client.post(
            f"/reports/{rep_id}/parameters",
            headers=auth_headers,
            json={"type": "float", "name": "x", "label": "X"},
        )
        assert r.status_code == 422, r.text
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)


def test_create_param_duplicate_name_returns_409(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, _ = temp_report_with_params
    # 'region' is already declared by the fixture.
    r = client.post(
        f"/reports/{rep_id}/parameters",
        headers=auth_headers,
        json={"type": "string", "name": "region", "label": "Other"},
    )
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["detail"]


def test_create_param_for_missing_report_404(client: TestClient, auth_headers: dict) -> None:
    r = client.post(
        "/reports/9999999/parameters",
        headers=auth_headers,
        json={"type": "string", "name": "x", "label": "X"},
    )
    assert r.status_code == 404


def test_list_params_orders_by_order_index(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.get(f"/reports/{rep_id}/parameters", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["name"] for p in body] == ["region", "limit", "status"]


def test_list_params_empty_report_200(client: TestClient, auth_headers: dict) -> None:
    rep_id = _make_report("pytest_empty_params")
    try:
        r = client.get(f"/reports/{rep_id}/parameters", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)


def test_update_param_changes_label_and_default(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, param_ids = temp_report_with_params
    r = client.put(
        f"/reports/{rep_id}/parameters/{param_ids[1]}",
        headers=auth_headers,
        json={"label": "Max rows", "default": 50},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Max rows"
    assert r.json()["default"] == 50


def test_update_param_to_duplicate_name_409(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, param_ids = temp_report_with_params
    # Try renaming 'limit' to 'region' — collides with the fixture's region.
    r = client.put(
        f"/reports/{rep_id}/parameters/{param_ids[1]}",
        headers=auth_headers,
        json={"name": "region"},
    )
    assert r.status_code == 409, r.text


def test_update_param_wrong_report_404(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    # The param_id belongs to a different report — using a wrong report_id.
    _, param_ids = temp_report_with_params
    r = client.put(
        f"/reports/9999999/parameters/{param_ids[0]}",
        headers=auth_headers,
        json={"label": "Whatever"},
    )
    assert r.status_code == 404


def test_delete_param_204_then_get_404(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, param_ids = temp_report_with_params
    r = client.delete(
        f"/reports/{rep_id}/parameters/{param_ids[2]}", headers=auth_headers
    )
    assert r.status_code == 204
    r = client.get(f"/reports/{rep_id}/parameters", headers=auth_headers)
    assert r.status_code == 200
    remaining = {p["id"] for p in r.json()}
    assert param_ids[2] not in remaining


def test_param_endpoints_require_auth(client: TestClient) -> None:
    # No auth headers → 401 across all four endpoints.
    assert client.get("/reports/1/parameters").status_code == 401
    assert client.post(
        "/reports/1/parameters",
        json={"type": "string", "name": "x", "label": "X"},
    ).status_code == 401
    assert client.put(
        "/reports/1/parameters/1",
        json={"label": "X"},
    ).status_code == 401
    assert client.delete("/reports/1/parameters/1").status_code == 401


def test_order_index_auto_assigned_on_omit(
    client: TestClient, auth_headers: dict, temp_report_with_params
) -> None:
    rep_id, _ = temp_report_with_params
    # Fixture has 3 params with order_index 0,1,2 — max=2, so next auto-assign is 3.
    r = client.post(
        f"/reports/{rep_id}/parameters",
        headers=auth_headers,
        json={"type": "bool", "name": "verbose", "label": "Verbose"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["order_index"] == 3


# ============ Runtime validation tests ============


def _generate_url() -> str:
    return "/reports/generate"


def test_generate_missing_required_400(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    # 'region' is required; we send no parameters at all.
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={"report_id": rep_id, "parameters": {}},
    )
    assert r.status_code == 400, r.text
    assert "region" in r.json()["detail"]
    assert stub_generate_report.calls == []  # never reached the pipeline


def test_generate_default_fills_missing_optional(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={"report_id": rep_id, "parameters": {"region": "north"}},
    )
    assert r.status_code == 200, r.text
    # 'limit' (default=100) and 'status' (default="active") should be filled.
    params = stub_generate_report.calls[0]["parameters"]
    assert params["region"] == "north"
    assert params["limit"] == 100
    assert params["status"] == "active"


def test_generate_unknown_key_400(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={"report_id": rep_id, "parameters": {"region": "x", "bogus": "y"}},
    )
    assert r.status_code == 400, r.text
    assert "bogus" in r.json()["detail"]


def test_generate_wrong_type_400(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={"report_id": rep_id, "parameters": {"region": "x", "limit": "abc"}},
    )
    assert r.status_code == 400, r.text
    assert "limit" in r.json()["detail"]


def test_generate_enum_out_of_range_400(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={"report_id": rep_id, "parameters": {"region": "x", "status": "bogus"}},
    )
    assert r.status_code == 400, r.text
    assert "status" in r.json()["detail"]


def test_generate_no_spec_passes_empty(
    client: TestClient, auth_headers: dict, stub_generate_report
) -> None:
    rep_id = _make_report("pytest_no_spec")
    try:
        r = client.post(
            _generate_url(),
            headers=auth_headers,
            json={"report_id": rep_id, "parameters": {}},
        )
        assert r.status_code == 200, r.text
        assert stub_generate_report.calls[0]["parameters"] == {}
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)


def test_generate_numeric_string_coerced(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={
            "report_id": rep_id,
            "parameters": {"region": "x", "limit": "3.14"},
        },
    )
    assert r.status_code == 200, r.text
    params = stub_generate_report.calls[0]["parameters"]
    assert params["limit"] == 3.14
    assert isinstance(params["limit"], float)


def test_generate_bool_rejected_for_number(
    client: TestClient, auth_headers: dict, temp_report_with_params, stub_generate_report
) -> None:
    rep_id, _ = temp_report_with_params
    r = client.post(
        _generate_url(),
        headers=auth_headers,
        json={
            "report_id": rep_id,
            "parameters": {"region": "x", "limit": True},
        },
    )
    assert r.status_code == 400, r.text
    assert "limit" in r.json()["detail"]
    assert "bool" in r.json()["detail"]


def test_generate_date_iso8601_accepted(
    client: TestClient, auth_headers: dict, stub_generate_report
) -> None:
    rep_id = _make_report("pytest_date")
    db: Session = SessionLocal()
    try:
        # Add a date parameter on the freshly-created report.
        p = ReportParameter(
            report_id=rep_id,
            name="start_date",
            label="Start date",
            type="date",
            required=True,
        )
        db.add(p)
        db.commit()
    finally:
        db.close()
    try:
        r = client.post(
            _generate_url(),
            headers=auth_headers,
            json={
                "report_id": rep_id,
                "parameters": {"start_date": "2026-01-15"},
            },
        )
        assert r.status_code == 200, r.text
        assert stub_generate_report.calls[0]["parameters"]["start_date"] == "2026-01-15"

        # Now an invalid date string should 400.
        r = client.post(
            _generate_url(),
            headers=auth_headers,
            json={
                "report_id": rep_id,
                "parameters": {"start_date": "not-a-date"},
            },
        )
        assert r.status_code == 400, r.text
    finally:
        client.delete(f"/reports/{rep_id}", headers=auth_headers)
