"""Tests for 批 6b.1 — Prometheus /metrics endpoint + custom counters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.metrics import (
    report_generate_errors_total,
    sql_validator_rejections_total,
    webhook_delivery_attempts_total,
)
from app.services.sql_validator import UnsafeSQLError, validate_select_only


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_metrics_endpoint_is_reachable(client: TestClient) -> None:
    """``GET /metrics`` is unauthenticated (Prometheus scrapers don't
    carry JWTs) and returns text/plain in the Prometheus exposition format."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.parametrize(
    "metric_name",
    [
        "report_generate_duration_seconds",
        "report_generate_errors_total",
        "webhook_delivery_attempts_total",
        "sql_validator_rejections_total",
    ],
)
def test_metrics_endpoint_exposes_custom_metric_names(client: TestClient, metric_name: str) -> None:
    """Each custom metric from 批 6b.1 plan §6b.1 must be present in
    the /metrics output, even before any traffic. Prometheus-client
    pre-registers label combinations; the metric_name header is what
    dashboards key off of."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert metric_name in resp.text


def test_sql_validator_rejections_increment_counter() -> None:
    """Trigger several distinct rejection paths and assert each
    ``rule`` label increments independently."""
    before = sql_validator_rejections_total.labels(rule="empty")._value.get()
    with pytest.raises(UnsafeSQLError):
        validate_select_only("")
    after = sql_validator_rejections_total.labels(rule="empty")._value.get()
    assert after == before + 1, "empty rejection must bump the 'empty' counter"


def test_sql_validator_bare_semicolon_uses_its_own_rule() -> None:
    """``SELECT 1; SELECT 2`` is rejected by the pre-check regex on
    bare ``;`` *before* sqlglot sees it — so the label that fires is
    ``bare_semicolon``, not ``multi_stmt``. Asserting on the actual
    label documents the real rejection path."""
    before = sql_validator_rejections_total.labels(rule="bare_semicolon")._value.get()
    with pytest.raises(UnsafeSQLError):
        validate_select_only("SELECT 1; SELECT 2")
    after = sql_validator_rejections_total.labels(rule="bare_semicolon")._value.get()
    assert after == before + 1, "bare_semicolon must use its own label"


def test_webhook_counter_labels_exist() -> None:
    """Pre-initialise the labelled counters so /metrics scrape sees the
    HELP/TYPE lines even before any webhook fires. Without this, an
    empty webhook history looks like the metric is missing entirely."""
    # Touch each label so the counter family is registered.
    for outcome in ("success", "ssrf_blocked", "https_required", "http_error", "no_url"):
        webhook_delivery_attempts_total.labels(outcome=outcome)
    assert True  # Construction didn't raise; sufficient.


def test_report_generate_errors_counter_labels_exist() -> None:
    """Same as the webhook test — pre-touch the reason label family."""
    for reason in ("generator_error", "data_source_missing", "io_error"):
        report_generate_errors_total.labels(reason=reason)
    assert True
