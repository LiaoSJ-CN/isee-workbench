"""Tests for the request-id middleware (批 6a)."""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.request_id import (
    NO_REQUEST_ID,
    RequestIDMiddleware,
    get_request_id,
    install_request_id_log_factory,
)

REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")  # uuid4().hex


def _make_app() -> FastAPI:
    """Build a tiny FastAPI app exercising the middleware surfaces."""
    test_app = FastAPI()
    test_app.add_middleware(RequestIDMiddleware)

    @test_app.get("/echo")
    def echo():
        return {"request_id": get_request_id()}

    @test_app.get("/state")
    async def state(request: Request):
        return {"state_id": getattr(request.state, "request_id", None)}

    return test_app


@pytest.fixture
def isolated_client():
    """A TestClient with the request-id factory saved/restored."""
    saved_factory = logging.getLogRecordFactory()
    yield TestClient(_make_app())
    logging.setLogRecordFactory(saved_factory)


# ---------------------------------------------------------------------------
# Header echo
# ---------------------------------------------------------------------------


def test_inbound_request_id_is_echoed_back(isolated_client):
    """A supplied ``X-Request-ID`` flows unchanged through to the response."""
    incoming = "deadbeef-cafe-1234-5678-abcdef012345"
    response = isolated_client.get("/echo", headers={"X-Request-ID": incoming})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == incoming
    assert response.json()["request_id"] == incoming


def test_missing_request_id_is_generated(isolated_client):
    """An absent header gets replaced with a uuid4 hex."""
    response = isolated_client.get("/echo")
    assert response.status_code == 200
    generated = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.match(generated), f"expected uuid4 hex, got {generated!r}"
    assert response.json()["request_id"] == generated


def test_empty_request_id_header_is_replaced(isolated_client):
    """A whitespace-only ``X-Request-ID`` is treated as missing."""
    response = isolated_client.get("/echo", headers={"X-Request-ID": "   "})
    assert response.status_code == 200
    generated = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.match(generated)


def test_duplicate_request_id_headers_first_wins(isolated_client):
    """With multiple X-Request-ID headers, the first value is preserved."""
    primary = "first-keep-me"
    secondary = "second-discard-me"
    response = isolated_client.get(
        "/echo",
        headers=[("X-Request-ID", primary), ("X-Request-ID", secondary)],
    )
    assert response.headers["X-Request-ID"] == primary


def test_request_id_is_set_on_request_state(isolated_client):
    """``request.state.request_id`` matches the response header."""
    incoming = "feed-face-feed-face"
    response = isolated_client.get("/state", headers={"X-Request-ID": incoming})
    assert response.status_code == 200
    assert response.json()["state_id"] == incoming
    assert response.headers["X-Request-ID"] == incoming


def test_request_id_is_reset_after_request(isolated_client):
    """The contextvar falls back to ``-`` once the request handler returns."""
    isolated_client.get("/echo", headers={"X-Request-ID": "abc-123"})
    assert get_request_id() == NO_REQUEST_ID


def test_consecutive_requests_get_distinct_generated_ids(isolated_client):
    """Two consecutive requests with no header see distinct generated ids."""
    first = isolated_client.get("/echo")
    second = isolated_client.get("/echo")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_invalid_uuid_in_header_is_still_passed_through(isolated_client):
    """Non-uuid values are honored — the middleware doesn't validate format."""
    incoming = "weird-but-not-uuid"
    response = isolated_client.get("/echo", headers={"X-Request-ID": incoming})
    assert response.headers["X-Request-ID"] == incoming


# ---------------------------------------------------------------------------
# Logging integration
# ---------------------------------------------------------------------------


def test_install_request_id_log_factory_is_idempotent():
    """Calling the factory installer twice doesn't double-wrap."""
    install_request_id_log_factory()
    factory_after_first = logging.getLogRecordFactory()
    install_request_id_log_factory()
    factory_after_second = logging.getLogRecordFactory()
    assert factory_after_first is factory_after_second


def test_log_records_carry_default_dash_outside_request():
    """Without an active request, the factory reports ``-``."""
    install_request_id_log_factory()
    factory = logging.getLogRecordFactory()
    record = factory(
        name="test",
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    assert record.request_id == NO_REQUEST_ID


# ---------------------------------------------------------------------------
# Direct middleware unit tests
# ---------------------------------------------------------------------------


def test_extract_request_id_picks_first_non_empty():
    headers = [
        (b"x-other-header", b"junk"),
        (b"x-request-id", b"abc"),
        (b"x-request-id", b"def"),
    ]
    assert RequestIDMiddleware._extract_request_id(headers) == "abc"


def test_extract_request_id_ignores_empty_values():
    headers = [
        (b"x-request-id", b"   "),
        (b"x-request-id", b"real-id"),
    ]
    assert RequestIDMiddleware._extract_request_id(headers) == "real-id"


def test_extract_request_id_returns_empty_when_absent():
    headers = [(b"x-other", b"value")]
    assert RequestIDMiddleware._extract_request_id(headers) == ""


def test_generated_request_id_is_uuid4_hex_shape():
    """Spot-check the generated fallback matches ``uuid4().hex`` shape."""
    generated = uuid.uuid4().hex
    assert REQUEST_ID_PATTERN.match(generated)
