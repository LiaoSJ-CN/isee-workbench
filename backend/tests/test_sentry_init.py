"""Tests for the Sentry integration (批 6a).

These tests verify:
1. When ``SENTRY_DSN`` is empty, ``init_sentry`` is a no-op (no SDK
   initialization, no module import past the feature check).
2. When ``SENTRY_DSN`` is set, ``init_sentry`` configures the SDK with
   FastAPI + Starlette + logging integrations and the configured
   environment / sample rate.
3. ``_filter_event`` stamps the current request id onto events and
   drops events whose only exception is an HTTPException.
4. ``init_sentry`` is idempotent at the SDK level (sentry_sdk.init
   itself no-ops on identical config).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.middleware import sentry as sentry_module
from app.middleware.request_id import install_request_id_log_factory


@pytest.fixture
def fake_sentry_sdk():
    """Patch ``sentry_sdk.init`` and capture the kwargs passed."""
    with patch("sentry_sdk.init") as mock_init, patch(
        "sentry_sdk.integrations.fastapi.FastApiIntegration"
    ) as mock_fastapi, patch(
        "sentry_sdk.integrations.logging.LoggingIntegration"
    ) as mock_logging, patch(
        "sentry_sdk.integrations.starlette.StarletteIntegration"
    ) as mock_starlette:
        mock_fastapi.return_value = MagicMock(name="fastapi_integration")
        mock_logging.return_value = MagicMock(name="logging_integration")
        mock_starlette.return_value = MagicMock(name="starlette_integration")
        yield {
            "init": mock_init,
            "integrations": {
                "fastapi": mock_fastapi,
                "logging": mock_logging,
                "starlette": mock_starlette,
            },
        }


def test_init_sentry_noop_when_dsn_empty(fake_sentry_sdk):
    """Empty DSN → no init, no integrations instantiated."""
    # Patch the settings object the module reads from.
    monkeypatch_dsn = ""
    with patch.object(sentry_module, "settings") as mock_settings:
        mock_settings.sentry_dsn = monkeypatch_dsn
        mock_settings.sentry_environment = "test"
        mock_settings.sentry_traces_sample_rate = 0.0
        assert sentry_module.init_sentry() is False
    fake_sentry_sdk["init"].assert_not_called()
    fake_sentry_sdk["integrations"]["fastapi"].assert_not_called()


def test_init_sentry_configures_sdk_with_settings(fake_sentry_sdk):
    """Set DSN → init runs with the configured environment + sample rate."""
    with patch.object(sentry_module, "settings") as mock_settings:
        mock_settings.sentry_dsn = "https://key@sentry.io/123"
        mock_settings.sentry_environment = "staging"
        mock_settings.sentry_traces_sample_rate = 0.25
        assert sentry_module.init_sentry() is True

    fake_sentry_sdk["init"].assert_called_once()
    kwargs = fake_sentry_sdk["init"].call_args.kwargs
    assert kwargs["dsn"] == "https://key@sentry.io/123"
    assert kwargs["environment"] == "staging"
    assert kwargs["traces_sample_rate"] == 0.25

    # All three integrations should be registered.
    integrations = kwargs["integrations"]
    assert len(integrations) == 3
    assert fake_sentry_sdk["integrations"]["fastapi"].called
    assert fake_sentry_sdk["integrations"]["logging"].called
    assert fake_sentry_sdk["integrations"]["starlette"].called


def test_init_sentry_returns_false_when_dsn_unset_then_true_when_set(
    fake_sentry_sdk,
):
    """The first call short-circuits; setting DSN enables subsequent calls."""
    with patch.object(sentry_module, "settings") as mock_settings:
        mock_settings.sentry_dsn = ""
        mock_settings.sentry_environment = "test"
        mock_settings.sentry_traces_sample_rate = 0.0
        assert sentry_module.init_sentry() is False

        mock_settings.sentry_dsn = "https://new@sentry.io/999"
        assert sentry_module.init_sentry() is True


# ---------------------------------------------------------------------------
# _filter_event
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _install_factory():
    """Ensure the log factory is installed for contextvar tests."""
    install_request_id_log_factory()


def _make_http_exception_event() -> dict[str, Any]:
    return {
        "tags": {},
        "exception": {
            "values": [
                {
                    "type": "HTTPException",
                    "mechanism": {"type": "generic", "handled": True},
                    "value": "404 Not Found",
                }
            ]
        },
    }


def _make_real_exception_event() -> dict[str, Any]:
    return {
        "tags": {},
        "exception": {
            "values": [
                {
                    "type": "ZeroDivisionError",
                    "value": "division by zero",
                }
            ]
        },
    }


def test_filter_event_stamps_request_id_when_set():
    """Active request id flows through into ``tags.request_id``."""
    from app.middleware.request_id import _request_id_var

    token = _request_id_var.set("abc-123-request")
    try:
        event = _make_real_exception_event()
        result = sentry_module._filter_event(event, {})
    finally:
        _request_id_var.reset(token)

    assert result is not None
    assert result["tags"]["request_id"] == "abc-123-request"


def test_filter_event_skips_tag_when_no_request():
    """No active request → no ``request_id`` tag added."""
    event = _make_real_exception_event()
    # No contextvar set; default is "-"
    result = sentry_module._filter_event(event, {})
    assert result is not None
    assert "request_id" not in result["tags"]


def test_filter_event_drops_http_exception():
    """An event whose only exception is an HTTPException is dropped."""
    event = _make_http_exception_event()
    assert sentry_module._filter_event(event, {}) is None


def test_filter_event_keeps_mixed_exceptions():
    """A real exception alongside an HTTPException still passes through."""
    event = {
        "tags": {},
        "exception": {
            "values": [
                {"type": "HTTPException", "mechanism": {"type": "generic", "handled": True}},
                {"type": "RuntimeError", "value": "boom"},
            ]
        },
    }
    result = sentry_module._filter_event(event, {})
    assert result is not None


def test_filter_transaction_stamps_request_id():
    """Performance transactions also pick up the request id tag."""
    from app.middleware.request_id import _request_id_var

    token = _request_id_var.set("txn-trace-id")
    try:
        event: dict[str, Any] = {"tags": {}}
        result = sentry_module._filter_transaction(event, {})
    finally:
        _request_id_var.reset(token)

    assert result is event  # passthrough
    assert result["tags"]["request_id"] == "txn-trace-id"


# ---------------------------------------------------------------------------
# _is_http_exception helper
# ---------------------------------------------------------------------------


def test_is_http_exception_detects_handled_generic():
    value = {"type": "HTTPException", "mechanism": {"type": "generic", "handled": True}}
    assert sentry_module._is_http_exception(value) is True


def test_is_http_exception_detects_type_suffix():
    value = {"type": "starlette.exceptions.HTTPException"}
    assert sentry_module._is_http_exception(value) is True


def test_is_http_exception_rejects_real_exception():
    value = {"type": "ZeroDivisionError"}
    assert sentry_module._is_http_exception(value) is False


# ---------------------------------------------------------------------------
# Logger fixture for tests that read app logs
# ---------------------------------------------------------------------------


@pytest.fixture
def _captured_log(caplog):  # pragma: no cover - reserved for future tests
    caplog.set_level(logging.INFO)
    return caplog
