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
from urllib.parse import parse_qsl, urlsplit

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


def _expected_dingtalk_signature(timestamp_ms: str, secret: str) -> str:
    """Reference implementation of DingTalk's 加签 algorithm.

    Note how it inverts Feishu's: the *secret* is the HMAC key and the
    ``f"{ts}\\n{secret}"`` string is the message, where Feishu makes the
    joined string the key and signs an empty message.
    """
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
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

    monkeypatch.setattr(scheduler_module, "create_webhook_client", factory)
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
    the JSON body (Feishu's protocol — *not* an HTTP header).

    批 G changed the message type from ``text`` to ``interactive``; the
    signing contract is orthogonal to it and must survive unchanged.
    """
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

    assert payload["msg_type"] == "interactive"
    rendered = str(payload["card"])
    assert payload["card"]["header"]["title"]["content"] == "报表「ok」已生成"
    assert "/tmp/r.xlsx" not in rendered
    # basename only — the absolute path is stripped (SEC-8).
    assert "r.xlsx" in rendered

    # Signature keys present, at the top level next to msg_type/card.
    assert "timestamp" in payload
    assert "sign" in payload
    # Header count = 0 for Feishu (signing is in-body, per protocol).
    assert "headers" not in kwargs or "X-Webhook-Signature" not in kwargs.get("headers", {})

    expected_sign = _expected_feishu_signature(payload["timestamp"], "shared-secret")
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

    assert payload["msg_type"] == "interactive"
    assert "timestamp" not in payload
    assert "sign" not in payload
    # basename preservation.
    assert "q.pdf" in str(payload["card"])


def test_feishu_card_button_follows_public_base_url(monkeypatch: Any) -> None:
    """批 G: the card's action button exists iff ``public_base_url`` is
    configured. Wired through the sender (not just the builder) so a
    future refactor can't drop the settings lookup."""
    from app.services import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module.settings, "public_base_url", "https://isee.example.com")
    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_feishu(
        webhook_url="https://8.8.8.8/feishu-hook",
        secret=None,
        report=Report(id=42, name="ok"),
        file_paths=["/tmp/r.xlsx"],
    )

    _, kwargs = factory.client.calls[0]
    actions = [e for e in kwargs["json"]["card"]["elements"] if e["tag"] == "action"]
    assert len(actions) == 1
    assert actions[0]["actions"][0]["url"] == "https://isee.example.com/reports/42"

    # And with the setting cleared, no action block at all.
    monkeypatch.setattr(scheduler_module.settings, "public_base_url", "")
    factory_no_link = _patch_webhook_client(monkeypatch)
    scheduler_module._send_feishu(
        webhook_url="https://8.8.8.8/feishu-hook",
        secret=None,
        report=Report(id=42, name="ok"),
        file_paths=["/tmp/r.xlsx"],
    )
    _, kwargs_no_link = factory_no_link.client.calls[0]
    assert not [e for e in kwargs_no_link["json"]["card"]["elements"] if e["tag"] == "action"]


def test_feishu_blocked_by_ssrf_guard(monkeypatch: Any, caplog: Any) -> None:
    """Loopback IP is rejected by the SSRF guard before any outbound
    HTTP — same defence that protects the other variants."""
    import logging

    from app.services import scheduler as scheduler_module

    def _explode(*args: Any, **kw: Any) -> None:
        raise AssertionError("create_webhook_client must NOT be called")

    monkeypatch.setattr(scheduler_module, "create_webhook_client", _explode)

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        scheduler_module._send_feishu(
            webhook_url="http://127.0.0.1:9000/x",
            secret=None,
            report=Report(id=1, name="x"),
            file_paths=[],
        )

    assert any("SSRF guard" in rec.message for rec in caplog.records)


# -------------------- _send_wechatwork --------------------


def test_wechatwork_posts_markdown_when_no_public_base_url(monkeypatch: Any) -> None:
    """Without ``public_base_url`` there is no ``card_action`` target,
    so the sender must keep the markdown envelope rather than emit a
    ``template_card`` WeCom would reject (批 G)."""
    from app.services import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module.settings, "public_base_url", "")
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
    assert "template_card" not in payload
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


def test_wechatwork_posts_template_card_when_linkable(monkeypatch: Any) -> None:
    """With ``public_base_url`` set the sender upgrades to a
    ``template_card`` carrying a ``card_action`` deep link (批 G)."""
    from app.services import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module.settings, "public_base_url", "https://isee.example.com")
    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_wechatwork(
        webhook_url="https://8.8.8.8/cgi-bin/webhook/send?key=xyz",
        report=Report(id=42, name="ok"),
        file_paths=["/var/reports/q.pdf"],
    )

    _, kwargs = factory.client.calls[0]
    payload = kwargs["json"]

    assert payload["msgtype"] == "template_card"
    card = payload["template_card"]
    assert card["card_type"] == "text_notice"
    assert card["main_title"]["title"] == "报表「ok」已生成"
    assert card["card_action"]["url"] == "https://isee.example.com/reports/42"
    # basenames only, here too.
    rendered = str(card)
    assert "q.pdf" in rendered
    assert "/var/reports/" not in rendered
    # Still no signing — the key= query param remains the authenticator.
    assert "sign" not in payload
    assert "timestamp" not in payload


def test_wechatwork_blocked_by_ssrf_guard(monkeypatch: Any) -> None:
    """Loopback IP rejected — same SSRF contract as the other senders."""
    from app.services import scheduler as scheduler_module

    def _explode(*args: Any, **kw: Any) -> None:
        raise AssertionError("create_webhook_client must NOT be called")

    monkeypatch.setattr(scheduler_module, "create_webhook_client", _explode)

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


# -------------------- _send_webhook per-config secret plumbing --------------------
# Regression guard: pre-fix, ``_send_notification`` routed
# WebhookConfig / DingTalkConfig through ``_send_webhook`` without
# forwarding the per-config ``secret`` field — so operators who set a
# signing key on a specific report had it silently dropped in favour
# of the global ``settings.webhook_secret``. The two tests below pin
# the new behaviour: per-config ``secret`` (when truthy) takes
# precedence; ``None`` / falsy values fall back to the global setting.


def test_webhook_uses_per_config_secret_when_provided(monkeypatch: Any) -> None:
    """``WebhookConfig(secret=...)`` must end up as the signing key
    on the outbound request, not the global ``settings.webhook_secret``."""
    from app.schemas.notification import WebhookConfig
    from app.services import scheduler as scheduler_module

    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            # Capture both the body (needed to verify the signature)
            # and the headers.
            captured["headers"] = kwargs.get("headers") or {}
            captured["body"] = kwargs.get("json") or {}
            return _FakeResponse()

        def close(self) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def fake_create_client(url: str, **kw: Any) -> _FakeClient:
        return _FakeClient()

    # Force a known global secret so we can assert the request did
    # NOT use it.
    monkeypatch.setattr(scheduler_module.settings, "webhook_secret", "GLOBAL")
    monkeypatch.setattr(scheduler_module, "create_webhook_client", fake_create_client)

    scheduler_module._send_notification(
        notification_config=WebhookConfig(
            type="webhook",
            url="https://8.8.8.8/hook",
            secret="PER_REPORT",
        ),
        report=Report(id=1, name="x"),
        file_paths=["/tmp/r.xlsx"],
    )

    headers = captured.get("headers", {})
    body = captured.get("body", {})
    assert "X-Webhook-Signature" in headers, (
        "per-config secret should have produced a signature header"
    )
    # Re-compute the signature using the *actual* body the sender
    # put on the wire — the helper signs ``timestamp.body``, so any
    # divergence from the live payload would invalidate the test
    # even if the fix were correct.
    from app.services.scheduler import _sign_payload

    timestamp = headers["X-Webhook-Timestamp"]
    expected = _sign_payload(body, "PER_REPORT", timestamp)
    not_expected = _sign_payload(body, "GLOBAL", timestamp)
    assert headers["X-Webhook-Signature"] == expected
    assert headers["X-Webhook-Signature"] != not_expected


def test_webhook_falls_back_to_global_secret_when_per_config_is_none(
    monkeypatch: Any,
) -> None:
    """When ``WebhookConfig.secret`` is ``None``, ``_send_webhook``
    must fall back to ``settings.webhook_secret`` so existing
    deployments that sign at the app level keep working unchanged."""
    from app.schemas.notification import WebhookConfig
    from app.services import scheduler as scheduler_module

    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            captured["headers"] = kwargs.get("headers") or {}
            captured["body"] = kwargs.get("json") or {}
            return _FakeResponse()

        def close(self) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def fake_create_client(url: str, **kw: Any) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr(scheduler_module.settings, "webhook_secret", "GLOBAL")
    monkeypatch.setattr(scheduler_module, "create_webhook_client", fake_create_client)

    scheduler_module._send_notification(
        notification_config=WebhookConfig(
            type="webhook",
            url="https://8.8.8.8/hook",
            secret=None,
        ),
        report=Report(id=1, name="x"),
        file_paths=["/tmp/r.xlsx"],
    )

    headers = captured.get("headers", {})
    body = captured.get("body", {})
    assert "X-Webhook-Signature" in headers
    from app.services.scheduler import _sign_payload

    timestamp = headers["X-Webhook-Timestamp"]
    assert headers["X-Webhook-Signature"] == _sign_payload(body, "GLOBAL", timestamp)


def test_dingtalk_signs_in_query_string_not_headers(monkeypatch: Any) -> None:
    """批 G rewrite. This test previously asserted that DingTalk went
    through :func:`_send_webhook` and produced an
    ``X-Webhook-Signature`` header — pinning a shape a real DingTalk
    robot rejects with ``errcode 40035 (缺少参数 msgtype)``. Its own
    docstring called itself a "guard against a future refactor that
    splits DingTalk delivery away from ``_send_webhook``"; this batch
    is that refactor, so the guard is re-pointed at the real contract:

    * a ``msgtype`` envelope in the body,
    * ``timestamp`` + ``sign`` in the query string,
    * the bot's own ``access_token`` query param preserved,
    * no HMAC headers.
    """
    from app.schemas.notification import DingTalkConfig
    from app.services import scheduler as scheduler_module

    captured: dict[str, Any] = {}

    class _CapturingClient:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["body"] = kwargs.get("json") or {}
            return _FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(scheduler_module.settings, "webhook_secret", "GLOBAL")
    monkeypatch.setattr(
        scheduler_module, "create_webhook_client", lambda url, **kw: _CapturingClient()
    )

    scheduler_module._send_notification(
        notification_config=DingTalkConfig(
            type="dingtalk",
            webhook_url="https://8.8.8.8/robot/send?access_token=abc123",
            secret="DINGTALK_KEY",
        ),
        report=Report(id=1, name="x"),
        file_paths=["/tmp/r.xlsx"],
    )

    # --- Body is a DingTalk envelope, not the generic webhook JSON ---
    body = captured["body"]
    assert "msgtype" in body
    assert "report_name" not in body, "generic webhook payload leaked into DingTalk"

    # --- Signature rides in the query string ---
    query = dict(parse_qsl(urlsplit(captured["url"]).query))
    assert query["access_token"] == "abc123", "bot's own query param must survive signing"
    assert "timestamp" in query
    assert "sign" in query
    assert query["sign"] == _expected_dingtalk_signature(query["timestamp"], "DINGTALK_KEY")

    # --- ...and not in headers or the body ---
    assert "X-Webhook-Signature" not in captured["headers"]
    assert "sign" not in body
    assert "timestamp" not in body


def test_dingtalk_signature_differs_from_feishu_for_same_inputs() -> None:
    """The two algorithms look alike — same ``f"{ts}\\n{secret}"``
    string, same HMAC-SHA256, same base64 — but swap which half is the
    key. Swapping them yields a well-formed signature the provider
    rejects, which is invisible without this assertion."""
    from app.services.scheduler import _dingtalk_signature, _feishu_signature

    timestamp = "1700000000"
    secret = "same-secret"

    assert _dingtalk_signature(timestamp, secret) != _feishu_signature(timestamp, secret)
    assert _dingtalk_signature(timestamp, secret) == _expected_dingtalk_signature(timestamp, secret)


def test_dingtalk_without_secret_posts_unsigned_url(monkeypatch: Any) -> None:
    """DingTalk bots can be configured with keyword/IP allow-listing
    instead of 加签 — no secret means the URL is left alone."""
    from app.services import scheduler as scheduler_module

    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_dingtalk(
        webhook_url="https://8.8.8.8/robot/send?access_token=abc123",
        secret=None,
        report=Report(id=1, name="x"),
        file_paths=[],
    )

    url, kwargs = factory.client.calls[0]
    assert url == "https://8.8.8.8/robot/send?access_token=abc123"
    assert "msgtype" in kwargs["json"]


def test_dingtalk_blocked_by_ssrf_guard(monkeypatch: Any, caplog: Any) -> None:
    """Loopback IP rejected before any outbound HTTP — same contract as
    the Feishu and WeChat Work senders."""
    import logging

    from app.services import scheduler as scheduler_module

    def _explode(*args: Any, **kw: Any) -> None:
        raise AssertionError("create_webhook_client must NOT be called")

    monkeypatch.setattr(scheduler_module, "create_webhook_client", _explode)

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        scheduler_module._send_dingtalk(
            webhook_url="http://127.0.0.1:9000/robot/send",
            secret="s",
            report=Report(id=1, name="x"),
            file_paths=[],
        )

    assert any("SSRF guard" in rec.message for rec in caplog.records)


def test_dispatch_routes_dingtalk_to_dingtalk_sender(monkeypatch: Any) -> None:
    """``DingTalkConfig`` must reach :func:`_send_dingtalk`. Routing it
    back to :func:`_send_webhook` is exactly the bug 批 G fixed."""
    from app.schemas.notification import DingTalkConfig
    from app.services import scheduler as scheduler_module

    seen_in: list[str] = []

    monkeypatch.setattr(
        scheduler_module, "_send_dingtalk", lambda *a, **k: seen_in.append("dingtalk")
    )
    monkeypatch.setattr(
        scheduler_module, "_send_webhook", lambda *a, **k: seen_in.append("generic_webhook")
    )
    monkeypatch.setattr(scheduler_module, "_send_feishu", lambda *a, **k: seen_in.append("feishu"))

    scheduler_module._send_notification(
        notification_config=DingTalkConfig(
            type="dingtalk",
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=abc",
            secret="s",
        ),
        report=Report(id=1, name="x"),
        file_paths=[],
    )

    assert seen_in == ["dingtalk"]


def test_generic_webhook_payload_contract_is_unchanged(monkeypatch: Any) -> None:
    """Regression guard for 批 G: the IM channels moved to rich cards,
    but ``WebhookConfig`` is consumed by *programs*. Its flat JSON
    contract must not drift along with the card work."""
    from app.schemas.notification import WebhookConfig
    from app.services import scheduler as scheduler_module

    factory = _patch_webhook_client(monkeypatch)

    scheduler_module._send_notification(
        notification_config=WebhookConfig(type="webhook", url="https://8.8.8.8/hook", secret=None),
        report=Report(id=42, name="ok"),
        file_paths=["/var/reports/q.pdf"],
    )

    _, kwargs = factory.client.calls[0]
    body = kwargs["json"]

    assert set(body) == {"report_name", "report_id", "generated_at", "files"}
    assert body["report_name"] == "ok"
    assert body["report_id"] == 42
    assert body["files"] == ["q.pdf"]
    # Definitely not a card.
    assert "msgtype" not in body
    assert "msg_type" not in body
