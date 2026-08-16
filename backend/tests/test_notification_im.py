"""Tests for the Feishu and WeChat Work notification variants (批 8.4).

Covers:

* :func:`app.services.scheduler._feishu_signature` — exact byte-level
  shape of Feishu's contract (HMAC-SHA256 with key=
  ``f"{timestamp}\\n{secret}"``, empty msg, base64-encoded digest).
* :func:`app.services.scheduler._send_feishu` —
  - body envelope (``msg_type: 'text'``, ``content.text``)
  - timestamp + sign keys added iff a secret is configured
  - basename-only file paths (defence-in-depth: SEC-8 still applies)
* :func:`app.services.scheduler._send_wechatwork` —
  - body envelope (``msgtype: 'markdown'``, ``markdown.content``)
  - no signing (older bot protocol authenticates via the ``key=``
    query parameter; the sender doesn't touch it)
* :func:`app.services.scheduler._send_notification` dispatch — both
  new variants route to their own sender (regression guard: a future
  refactor that folds them back into ``_send_webhook`` would break
  Feishu's in-body signature).

Mocking strategy mirrors ``test_scheduler.py``: patch
``create_webhook_client`` to a fake that records (url, kwargs) calls
and returns a 200. We don't hit the network — the SSRF guard is
intentionally bypassed by using a public IP literal as the URL so the
guard's DNS resolution step can no-op (the IP literal is on the public
allow-list).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from app.models.report import Report
from app.schemas.notification import (
    FeishuConfig,
    WeChatWorkConfig,
)


def _expected_feishu_signature(timestamp: str, secret: str) -> str:
    """Reference implementation mirroring Feishu's documented algorithm.

    Kept here (rather than imported from the module) so the test
    can flag any drift in the production helper without having to
    remember which "correct" behaviour to expect.
    """
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        # No-op — the production helper calls ``raise_for_status`` after
        # the post, mirroring httpx. A failure here would raise an
        # exception, which the production sender treats as
        # ``http_error`` in the metrics; the tests below want to
        # exercise the happy path so we deliberately no-op.
        return None


class _FakeClient:
    """Captures each ``post`` call so the test can assert on it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return _FakeResponse()

    def close(self) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _CapturingClientFactory:
    """One-stop helper: tracks ``create_client`` invocations and
    returns a single shared :class:`_FakeClient` whose ``calls`` are
    accessible to the test after the production helper has posted.

    Using one factory simplifies test assertions: every test in this
    module passes the same factory and can read ``factory.create_calls``
    / ``factory.client.calls`` to see what the production code did.
    """

    def __init__(self) -> None:
        self.create_calls: list[tuple[str, dict[str, Any]]] = []
        self.client = _FakeClient()

    def __call__(self, webhook_url: str, **kw: Any) -> _FakeClient:
        self.create_calls.append((webhook_url, kw))
        return self.client


def _patch_webhook_client(
    monkeypatch: Any,
) -> _CapturingClientFactory:
    """Install a ``create_webhook_client`` stand-in and return the factory."""

    factory = _CapturingClientFactory()

    from app.services import scheduler as scheduler_module

    monkeypatch.setattr(
        scheduler_module, "create_webhook_client", factory
    )
    return factory


# -------------------- _feishu_signature --------------------


def test_feishu_signature_matches_documented_algorithm() -> None:
    """Reference-vs-implementation: the helper must produce Feishu's
    documented shape byte-for-byte."""
    from app.services.scheduler import _feishu_signature

    sig = _feishu_signature("1700000000", "test-secret")
    expected = _expected_feishu_signature("1700000000", "test-secret")
    assert sig == expected
    # And it's base64 — 32 raw bytes → 44-char base64 string.
    assert len(base64.b64decode(sig)) == 32


# -------------------- _send_feishu --------------------


def test_feishu_with_secret_signs_body(monkeypatch: Any) -> None:
    """Feishu with a secret adds ``timestamp`` + ``sign`` keys inside
    the JSON body (Feishu's protocol — *not* an HTTP header)."""
    from app.services import scheduler as scheduler_module

    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_feishu(
        webhook_url="https://8.8.8.8/feishu-hook",
        secret="shared-secret",
        report=Report(id=42, name="ok"),
        file_paths=["/tmp/r.xlsx"],
    )

    assert len(factory.client.calls) == 1
    url, kwargs = factory.client.calls[0]
    payload = kwargs["json"]

    assert payload["msg_type"] == "text"
    assert "报表「ok」已生成" in payload["content"]["text"]
    assert "/tmp/r.xlsx" not in payload["content"]["text"]
    # basename only — the absolute path is stripped (SEC-8).
    assert "r.xlsx" in payload["content"]["text"]

    # Signature keys present.
    assert "timestamp" in payload
    assert "sign" in payload
    # Header count = 0 for Feishu (signing is in-body, per protocol).
    assert "headers" not in kwargs or "X-Webhook-Signature" not in kwargs.get(
        "headers", {}
    )

    expected_sign = _expected_feishu_signature(
        payload["timestamp"], "shared-secret"
    )
    assert payload["sign"] == expected_sign


def test_feishu_without_secret_omits_signature(monkeypatch: Any) -> None:
    """No ``secret`` → no ``timestamp`` / ``sign`` keys. Operators can
    configure signing later by editing the row."""
    from app.services import scheduler as scheduler_module

    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_feishu(
        webhook_url="https://8.8.8.8/feishu-hook",
        secret=None,
        report=Report(id=7, name="noauth"),
        file_paths=["/var/reports/q.pdf"],
    )

    assert len(factory.client.calls) == 1
    _, kwargs = factory.client.calls[0]
    payload = kwargs["json"]

    assert payload["msg_type"] == "text"
    assert "timestamp" not in payload
    assert "sign" not in payload
    # basename preservation.
    assert "q.pdf" in payload["content"]["text"]


def test_feishu_blocked_by_ssrf_guard(monkeypatch: Any, caplog: Any) -> None:
    """Loopback IP is rejected by the SSRF guard before any outbound
    HTTP — same defence that protects the other variants."""
    import logging

    from app.services import scheduler as scheduler_module

    def _explode(*args: Any, **kw: Any) -> None:
        raise AssertionError("create_webhook_client must NOT be called")

    monkeypatch.setattr(
        scheduler_module, "create_webhook_client", _explode
    )

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        scheduler_module._send_feishu(
            webhook_url="http://127.0.0.1:9000/x",
            secret=None,
            report=Report(id=1, name="x"),
            file_paths=[],
        )

    assert any("SSRF guard" in rec.message for rec in caplog.records)


# -------------------- _send_wechatwork --------------------


def test_wechatwork_posts_markdown_envelope(monkeypatch: Any) -> None:
    """WeChat Work sender wraps the report in a ``msgtype: markdown`` payload
    with no signing keys — the URL's ``key=`` query param is the
    authenticator."""
    from app.services import scheduler as scheduler_module

    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_wechatwork(
        webhook_url="https://8.8.8.8/cgi-bin/webhook/send?key=xyz",
        report=Report(id=42, name="ok"),
        file_paths=["/var/reports/q.pdf", "/tmp/r.xlsx"],
    )

    assert len(factory.client.calls) == 1
    _, kwargs = factory.client.calls[0]
    payload = kwargs["json"]

    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert "报表「ok」已生成" in content
    # basenames only.
    assert "q.pdf" in content
    assert "r.xlsx" in content
    assert "/var/reports/" not in content
    assert "/tmp/" not in content
    # No signing keys — WeChat Work authenticates via the URL's key= param.
    assert "sign" not in payload
    assert "timestamp" not in payload
    # Public URL — no Content-Signature header either.
    headers = kwargs.get("headers") or {}
    assert "X-Webhook-Signature" not in headers


def test_wechatwork_blocked_by_ssrf_guard(monkeypatch: Any) -> None:
    """Loopback IP rejected — same SSRF contract as the other senders."""
    from app.services import scheduler as scheduler_module

    def _explode(*args: Any, **kw: Any) -> None:
        raise AssertionError("create_webhook_client must NOT be called")

    monkeypatch.setattr(
        scheduler_module, "create_webhook_client", _explode
    )

    scheduler_module._send_wechatwork(
        webhook_url="http://10.0.0.5/cgi-bin/webhook/send?key=x",
        report=Report(id=1, name="x"),
        file_paths=[],
    )


# -------------------- _send_notification dispatch --------------------


def test_dispatch_routes_feishu_to_feishu_sender(monkeypatch: Any) -> None:
    """``_send_notification`` must route ``FeishuConfig`` to
    :func:`_send_feishu`, **not** :func:`_send_webhook` —
    otherwise the Feishu in-body signature contract would be lost."""
    from app.services import scheduler as scheduler_module

    seen_in: list[str] = []

    def fake_feishu(*args: Any, **kw: Any) -> None:
        seen_in.append("feishu")

    def fake_wechat(*args: Any, **kw: Any) -> None:
        seen_in.append("wechatwork")

    def fake_webhook(*args: Any, **kw: Any) -> None:
        seen_in.append("generic_webhook")

    monkeypatch.setattr(scheduler_module, "_send_feishu", fake_feishu)
    monkeypatch.setattr(scheduler_module, "_send_wechatwork", fake_wechat)
    monkeypatch.setattr(scheduler_module, "_send_webhook", fake_webhook)

    scheduler_module._send_notification(
        notification_config=FeishuConfig(
            type="feishu",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            secret="s",
        ),
        report=Report(id=1, name="x"),
        file_paths=["/tmp/r.xlsx"],
    )

    assert seen_in == ["feishu"]


def test_dispatch_routes_wechatwork_to_wechatwork_sender(monkeypatch: Any) -> None:
    """Mirror of the Feishu dispatch test for WeChat Work."""
    from app.services import scheduler as scheduler_module

    seen_in: list[str] = []

    def fake_feishu(*args: Any, **kw: Any) -> None:
        seen_in.append("feishu")

    def fake_wechat(*args: Any, **kw: Any) -> None:
        seen_in.append("wechatwork")

    def fake_webhook(*args: Any, **kw: Any) -> None:
        seen_in.append("generic_webhook")

    monkeypatch.setattr(scheduler_module, "_send_feishu", fake_feishu)
    monkeypatch.setattr(scheduler_module, "_send_wechatwork", fake_wechat)
    monkeypatch.setattr(scheduler_module, "_send_webhook", fake_webhook)

    scheduler_module._send_notification(
        notification_config=WeChatWorkConfig(
            type="wechatwork",
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        ),
        report=Report(id=1, name="x"),
        file_paths=[],
    )

    assert seen_in == ["wechatwork"]
