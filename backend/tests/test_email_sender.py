"""Tests for the SMTP ``_send_email`` notification sender (批 8.3).

Covers:

* :func:`app.services.scheduler._send_notification` dispatch —
  ``EmailConfig`` routes to :func:`_send_email`, **not** the
  webhook / Feishu / WeChatWork senders.
* :func:`app.services.scheduler._send_email` —
  - short-circuit when ``SMTP_HOST`` is empty
  - TLS mode dispatch (``smtp_use_ssl`` → ``SMTP_SSL``,
    ``smtp_use_starttls`` → ``starttls()`` upgrade)
  - login behaviour (``smtp_user`` set → ``login()``, unset → skip)
  - From-address fallback (``smtp_from_address`` empty → derive from
    ``smtp_user@smtp_host`` / ``noreply@smtp_host``)
  - attachment basename-only (SEC-8 defence-in-depth: even though
    SMTP doesn't leak the filesystem to the receiver, the convention
    matches the webhook senders and keeps the test surface tight)
  - error metric counters (``smtp_unconfigured`` / ``smtp_auth`` /
    ``email_error``)

Mocking strategy: stub the ``smtplib.SMTP`` / ``smtplib.SMTP_SSL``
constructors on the scheduler module so we never hit a real
network. Each fake records the call arguments and returns a context
manager so the production ``with smtp_client as client:`` block
exercises its real flow.
"""

from __future__ import annotations

import os
import smtplib
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.models.report import Report

# -------------------- fakes --------------------


class _FakeSMTPBase:
    """Records method calls on a fake SMTP client.

    Used as both the ``SMTP`` and ``SMTP_SSL`` class — the production
    helper treats them symmetrically once the connection is up
    (login / send_message / starttls all live on both). Returning a
    context-manager-compatible instance lets the ``with`` block in
    production flow without special-casing.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.sent_message: Any = None

    def starttls(self) -> None:
        self.calls.append(("starttls", (), {}))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, password), {}))

    def send_message(self, msg: Any) -> None:
        self.calls.append(("send_message", (msg,), {}))
        self.sent_message = msg

    def quit(self) -> None:
        # ``quit`` is called by the ``__exit__`` of the SMTP context
        # manager. Fake it so the helper doesn't blow up on close.
        self.calls.append(("quit", (), {}))

    def __enter__(self) -> "_FakeSMTPBase":
        return self

    def __exit__(self, *args: Any) -> None:
        self.quit()


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ssl_constructor: Any | None = None,
    plain_constructor: Any | None = None,
) -> tuple[Any, Any]:
    """Replace ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` (the global
    cached module) with the supplied constructors (or a default
    :class:`_FakeSMTPBase` factory).

    Returns the (ssl_ctor, plain_ctor) pair so tests can introspect
    which constructor was invoked.
    """
    ssl_ctor = ssl_constructor or _FakeSMTPBase
    plain_ctor = plain_constructor or _FakeSMTPBase
    # Patch the module-level names — every importer (including
    # ``app.services.scheduler``) reads ``smtplib.SMTP`` from the
    # cached stdlib module, so a single monkeypatch here covers
    # all callers without needing to reach into private namespaces.
    monkeypatch.setattr(smtplib, "SMTP_SSL", ssl_ctor)
    monkeypatch.setattr(smtplib, "SMTP", plain_ctor)
    return ssl_ctor, plain_ctor


def _set_smtp_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host: str = "smtp.example.com",
    port: int = 587,
    user: str = "",
    password: str = "",
    from_address: str = "",
    from_name: str = "",
    use_starttls: bool = True,
    use_ssl: bool = False,
) -> None:
    """Pin the SMTP settings on the live :class:`Settings` instance.

    Tests run against the actual ``settings`` object so we mutate
    attributes (not the env) and monkeypatch restores them on
    teardown.
    """
    monkeypatch.setattr(settings, "smtp_host", host)
    monkeypatch.setattr(settings, "smtp_port", port)
    monkeypatch.setattr(settings, "smtp_user", user)
    monkeypatch.setattr(settings, "smtp_password", password)
    monkeypatch.setattr(settings, "smtp_from_address", from_address)
    monkeypatch.setattr(settings, "smtp_from_name", from_name)
    monkeypatch.setattr(settings, "smtp_use_starttls", use_starttls)
    monkeypatch.setattr(settings, "smtp_use_ssl", use_ssl)


# -------------------- dispatch --------------------


def test_dispatch_routes_email_to_email_sender(monkeypatch: Any) -> None:
    """``_send_notification`` must route ``EmailConfig`` to
    :func:`_send_email`, **not** the webhook senders."""
    from app.schemas.notification import EmailConfig
    from app.services import scheduler as scheduler_module

    seen_in: list[str] = []

    def fake_email(*args: Any, **kw: Any) -> None:
        seen_in.append("email")

    def fake_webhook(*args: Any, **kw: Any) -> None:
        seen_in.append("generic_webhook")

    def fake_feishu(*args: Any, **kw: Any) -> None:
        seen_in.append("feishu")

    def fake_wechat(*args: Any, **kw: Any) -> None:
        seen_in.append("wechatwork")

    monkeypatch.setattr(scheduler_module, "_send_email", fake_email)
    monkeypatch.setattr(scheduler_module, "_send_webhook", fake_webhook)
    monkeypatch.setattr(scheduler_module, "_send_feishu", fake_feishu)
    monkeypatch.setattr(scheduler_module, "_send_wechatwork", fake_wechat)

    scheduler_module._send_notification(
        notification_config=EmailConfig(
            type="email",
            to=["ops@example.com"],
            subject="hello",
        ),
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    assert seen_in == ["email"]


# -------------------- _send_email --------------------


def test_send_email_unconfigured_logs_and_returns(monkeypatch: Any) -> None:
    """Empty ``smtp_host`` short-circuits with ``smtp_unconfigured``
    metric and no SMTP connection."""
    from app.middleware.metrics import webhook_delivery_attempts_total
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(monkeypatch, host="", port=587)
    _install_fakes(monkeypatch)  # ssl_ctor / plain_ctor unused

    counter = webhook_delivery_attempts_total.labels(
        outcome="smtp_unconfigured"
    )
    before = counter._value.get() if hasattr(counter, "_value") else 0
    scheduler_module._send_email(
        to=["ops@example.com"],
        subject="x",
        report=Report(id=1, name="r"),
        file_paths=[],
    )
    # The metric exists (Prometheus Client may use ``_value.get()``
    # or direct attribute access depending on version). Check it
    # was incremented by reading the private counter via the
    # internal ``_metrics`` dict.
    after = counter._value.get() if hasattr(counter, "_value") else 0
    # The :func:`_send_email` code calls ``.inc()`` exactly once on
    # the ``smtp_unconfigured`` label when ``smtp_host`` is empty —
    # before/after deltas of 1 confirm the call.
    assert after - before == 1


def test_send_email_uses_smtp_ssl_when_ssl_mode(monkeypatch: Any) -> None:
    """``smtp_use_ssl=True`` → ``SMTP_SSL(...)`` is constructed;
    ``SMTP(...)`` is **not**."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch, host="smtp.example.com", port=465, use_ssl=True
    )
    plain_calls: list[Any] = []

    def plain_factory(*a: Any, **kw: Any) -> _FakeSMTPBase:
        plain_calls.append((a, kw))
        return _FakeSMTPBase()

    ssl_calls: list[Any] = []

    def ssl_factory(*a: Any, **kw: Any) -> _FakeSMTPBase:
        ssl_calls.append((a, kw))
        return _FakeSMTPBase()

    _install_fakes(
        monkeypatch,
        ssl_constructor=MagicMock(side_effect=ssl_factory),
        plain_constructor=MagicMock(side_effect=plain_factory),
    )

    scheduler_module._send_email(
        to=["ops@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    assert len(ssl_calls) == 1
    assert len(plain_calls) == 0
    # Host + port passed positionally; ``timeout=30`` set so a hung
    # server doesn't pin the scheduler thread.
    args, kwargs = ssl_calls[0]
    assert args[0] == "smtp.example.com"
    assert args[1] == 465
    assert kwargs.get("timeout") == 30


def test_send_email_calls_starttls_on_plain_connection(monkeypatch: Any) -> None:
    """``smtp_use_ssl=False, smtp_use_starttls=True`` → ``SMTP(...)``
    is constructed, ``starttls()`` is called, login/send happen."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        use_ssl=False,
        use_starttls=True,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["ops@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    method_names = [c[0] for c in plain_instance.calls]
    assert "starttls" in method_names
    assert "login" not in method_names  # no user configured
    assert "send_message" in method_names
    assert "quit" in method_names  # context-manager exit


def test_send_email_skips_starttls_when_disabled(monkeypatch: Any) -> None:
    """Local dev relays (mailhog / mailpit) often run plaintext on
    1025 — ``smtp_use_starttls=False`` must NOT call ``starttls()``."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="localhost",
        port=1025,
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["ops@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    method_names = [c[0] for c in plain_instance.calls]
    assert "starttls" not in method_names
    assert "send_message" in method_names


def test_send_email_logs_in_with_credentials(monkeypatch: Any) -> None:
    """When ``smtp_user`` is set, ``login(user, password)`` is called
    before ``send_message``."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        user="ops@example.com",
        password="hunter2",
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    login_calls = [c for c in plain_instance.calls if c[0] == "login"]
    assert len(login_calls) == 1
    assert login_calls[0][1] == ("ops@example.com", "hunter2")


def test_send_email_skips_login_when_no_user(monkeypatch: Any) -> None:
    """Anonymous SMTP (mailhog default) → ``login()`` is not called."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="localhost",
        port=1025,
        user="",
        password="",
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    method_names = [c[0] for c in plain_instance.calls]
    assert "login" not in method_names
    assert "send_message" in method_names


def test_send_email_from_falls_back_to_user_at_host(monkeypatch: Any) -> None:
    """When ``smtp_from_address`` is empty AND ``smtp_user`` is set,
    the From header derives ``smtp_user@smtp_host``."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        user="ops@example.com",
        password="hunter2",
        from_address="",
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    sent = plain_instance.sent_message
    assert sent is not None
    from_header = sent["From"]
    # ``smtp_user="ops@example.com"`` already contains ``@`` — we must
    # use it verbatim (the helper's fallback glues ``user@host`` only
    # when the user is a bare username). Glued output
    # (``ops@example.com@smtp.example.com``) would be malformed.
    assert "ops@example.com" in from_header
    assert "ops@example.com@smtp.example.com" not in from_header


def test_send_email_from_falls_back_to_noreply_when_no_user(
    monkeypatch: Any,
) -> None:
    """When ``smtp_from_address`` is empty AND ``smtp_user`` is empty,
    the From header derives ``noreply@smtp_host``."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        user="",
        password="",
        from_address="",
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    sent = plain_instance.sent_message
    assert "noreply@smtp.example.com" in sent["From"]


def test_send_email_from_uses_explicit_address(monkeypatch: Any) -> None:
    """When ``smtp_from_address`` is set, the From header uses it
    verbatim (operator override beats the fallback)."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        user="ops@example.com",
        password="hunter2",
        from_address="alerts@example.com",
        from_name="Alerts Bot",
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )

    sent = plain_instance.sent_message
    assert "alerts@example.com" in sent["From"]
    assert "Alerts Bot" in sent["From"]


def test_send_email_attachments_use_basename(monkeypatch: Any) -> None:
    """``file_path`` of ``/abs/path/foo.xlsx`` produces an attachment
    named ``foo.xlsx`` — receiver never sees the server's filesystem."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="localhost",
        port=1025,
        use_ssl=False,
        use_starttls=False,
    )

    # Write a real file so ``open(path, 'rb')`` inside the production
    # helper succeeds. ``tempfile.NamedTemporaryFile`` gives us a
    # deterministic path; we rename the basename for the test.
    fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="secret_path_")
    os.close(fd)
    target_basename = "report_q3.xlsx"
    target_path = os.path.join(
        os.path.dirname(path), target_basename
    )
    os.rename(path, target_path)
    with open(target_path, "wb") as fh:
        fh.write(b"fake-xlsx-bytes")

    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    try:
        scheduler_module._send_email(
            to=["dest@example.com"],
            subject="hello",
            report=Report(id=1, name="r"),
            file_paths=[target_path],
        )
    finally:
        if os.path.exists(target_path):
            os.unlink(target_path)

    sent = plain_instance.sent_message
    attachments = list(sent.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == target_basename


def test_send_email_auth_error_increments_smtp_auth(monkeypatch: Any) -> None:
    """``SMTPAuthenticationError`` (535 — wrong creds) bumps the
    ``smtp_auth`` metric and does NOT bubble."""
    from app.middleware.metrics import webhook_delivery_attempts_total
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.example.com",
        port=587,
        user="ops@example.com",
        password="wrong",
        use_ssl=False,
        use_starttls=False,
    )

    def boom_login(user: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"Auth failed")

    plain_instance = _FakeSMTPBase()
    plain_instance.login = boom_login  # type: ignore[method-assign]
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    counter = webhook_delivery_attempts_total.labels(outcome="smtp_auth")
    before = counter._value.get() if hasattr(counter, "_value") else 0
    # Must NOT raise — the production helper swallows SMTP errors so a
    # single bad tick doesn't kill the scheduler thread.
    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )
    after = counter._value.get() if hasattr(counter, "_value") else 0
    assert after - before == 1


def test_send_email_smtp_error_increments_email_error(monkeypatch: Any) -> None:
    """Generic ``SMTPException`` (host unreachable, timeout, etc.)
    bumps the ``email_error`` metric and does NOT bubble."""
    from app.middleware.metrics import webhook_delivery_attempts_total
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="smtp.invalid",
        port=587,
        use_ssl=False,
        use_starttls=False,
    )

    def boom_send_message(msg: Any) -> None:
        raise smtplib.SMTPConnectError(421, b"Cannot connect")

    plain_instance = _FakeSMTPBase()
    plain_instance.send_message = boom_send_message  # type: ignore[method-assign]
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    counter = webhook_delivery_attempts_total.labels(outcome="email_error")
    before = counter._value.get() if hasattr(counter, "_value") else 0
    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[],
    )
    after = counter._value.get() if hasattr(counter, "_value") else 0
    assert after - before == 1


def test_send_email_missing_attachment_skipped_not_aborted(
    monkeypatch: Any,
) -> None:
    """When an attachment path doesn't exist (transient cleanup race),
    the helper logs a warning and still sends the email without it
    — losing the email is worse than losing one file."""
    from app.services import scheduler as scheduler_module

    _set_smtp_settings(
        monkeypatch,
        host="localhost",
        port=1025,
        use_ssl=False,
        use_starttls=False,
    )
    plain_instance = _FakeSMTPBase()
    plain_ctor = MagicMock(return_value=plain_instance)
    _install_fakes(monkeypatch, plain_constructor=plain_ctor)

    # Path that almost certainly doesn't exist.
    bogus_path = "/nonexistent/report.xlsx"

    scheduler_module._send_email(
        to=["dest@example.com"],
        subject="hello",
        report=Report(id=1, name="r"),
        file_paths=[bogus_path],
    )

    sent = plain_instance.sent_message
    # Email still went out (send_message was called).
    method_names = [c[0] for c in plain_instance.calls]
    assert "send_message" in method_names
    # No attachments — the missing path was skipped.
    assert len(list(sent.iter_attachments())) == 0
