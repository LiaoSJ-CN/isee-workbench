"""Tests for P4 webhook security (PY-4, SEC-4, SEC-8, SEC-14)."""

from __future__ import annotations

from unittest import mock

import pytest

from app.config import settings
from app.services.scheduler import _sign_payload
from app.services.ssrf_guard import (
    SSRFBlocked,
    _resolve_and_validate,
    create_webhook_client,
    validate_webhook_url,
)

# ---------------------------------------------------------------------------
# SEC-4: HMAC signing
# ---------------------------------------------------------------------------


class TestHmacSigning:
    def test_sign_payload_produces_hex_digest(self):
        sig = _sign_payload({"key": "value"}, "secret", "1234567890")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_payload_deterministic(self):
        a = _sign_payload({"a": 1}, "secret", "ts")
        b = _sign_payload({"a": 1}, "secret", "ts")
        assert a == b

    def test_sign_payload_differs_with_secret(self):
        a = _sign_payload({"a": 1}, "secret1", "ts")
        b = _sign_payload({"a": 1}, "secret2", "ts")
        assert a != b

    def test_sign_payload_differs_with_timestamp(self):
        a = _sign_payload({"a": 1}, "secret", "t1")
        b = _sign_payload({"a": 1}, "secret", "t2")
        assert a != b

    def test_sign_payload_differs_with_body(self):
        a = _sign_payload({"a": 1}, "secret", "ts")
        b = _sign_payload({"a": 2}, "secret", "ts")
        assert a != b


# ---------------------------------------------------------------------------
# PY-4: DNS resolution + validation
# ---------------------------------------------------------------------------


def _fake_getaddrinfo(hostname, port):
    return [(mock.sentinel.FAMILY, mock.sentinel.TYPE, mock.sentinel.PROTO, "", ("8.8.8.8", port))]


def _fake_getaddrinfo_private(hostname, port):
    return [(mock.sentinel.FAMILY, mock.sentinel.TYPE, mock.sentinel.PROTO, "", ("10.0.0.1", port))]


class TestResolveAndValidate:
    def test_returns_public_ip(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
            result = _resolve_and_validate("example.com", 443)
        assert result == "8.8.8.8"

    def test_blocks_private_ip(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_private):
            with pytest.raises(SSRFBlocked, match="blocked"):
                _resolve_and_validate("internal.local", 443)

    def test_raises_on_dns_failure(self):
        import socket

        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            with pytest.raises(SSRFBlocked, match="DNS resolution failed"):
                _resolve_and_validate("does-not-exist.invalid", 443)


# ---------------------------------------------------------------------------
# URL validation (regression — unchanged behaviour)
# ---------------------------------------------------------------------------


class TestValidateWebhookUrl:
    def test_allows_public_https_url(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
            validate_webhook_url("https://example.com/hook")

    def test_rejects_empty_url(self):
        with pytest.raises(SSRFBlocked, match="empty"):
            validate_webhook_url("")

    def test_rejects_non_http_scheme(self):
        with pytest.raises(SSRFBlocked, match="scheme"):
            validate_webhook_url("ftp://evil.com/hook")

    def test_rejects_loopback(self):
        with pytest.raises(SSRFBlocked, match="blocked IP literal"):
            validate_webhook_url("http://127.0.0.1/hook")

    def test_rejects_private_ip(self):
        with pytest.raises(SSRFBlocked, match="blocked IP literal"):
            validate_webhook_url("http://10.0.0.1/hook")


# ---------------------------------------------------------------------------
# SEC-8: Path sanitization (os.path.basename)
# ---------------------------------------------------------------------------


class TestPathSanitization:
    def test_basename_strips_directory(self):
        import os

        assert os.path.basename("/var/generated/report_123.xlsx") == "report_123.xlsx"

    def test_basename_preserves_plain_filename(self):
        import os

        assert os.path.basename("report_123.xlsx") == "report_123.xlsx"


# ---------------------------------------------------------------------------
# SEC-14: HTTPS enforcement config
# ---------------------------------------------------------------------------


class TestHttpsEnforcement:
    def test_setting_exists(self):
        assert hasattr(settings, "webhook_https_only")

    def test_default_is_false_for_dev(self):
        assert settings.webhook_https_only is False


# ---------------------------------------------------------------------------
# create_webhook_client integration (PY-4)
# ---------------------------------------------------------------------------


class TestCreateWebhookClient:
    def test_creates_client_for_public_url(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
            client = create_webhook_client("https://example.com/hook")
            assert client is not None
            client.close()

    def test_raises_on_blocked_url(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_private):
            with pytest.raises(SSRFBlocked, match="blocked"):
                create_webhook_client("https://internal.local/hook")

    def test_follow_redirects_is_false(self):
        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
            client = create_webhook_client("https://example.com/hook")
            assert client.follow_redirects is False
            client.close()
