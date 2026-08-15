"""Tests for 批 6b.4 — NotificationConfig discriminated union."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.notification import (
    DingTalkConfig,
    EmailConfig,
    NotificationConfig,
    WebhookConfig,
)

# ---- Pydantic discrimination ------------------------------------------------


_NOTIFICATION_ADAPTER = TypeAdapter(NotificationConfig)


def _accept(payload: dict[str, Any]) -> Any:
    """Validate a payload against the NotificationConfig union."""
    return _NOTIFICATION_ADAPTER.validate_python(payload)


def test_webhook_config_accepts_minimal_payload() -> None:
    cfg = _accept({"type": "webhook", "url": "https://example.com/hook", "secret": "s"})
    assert isinstance(cfg, WebhookConfig)
    assert str(cfg.url) == "https://example.com/hook"
    assert cfg.type == "webhook"


def test_webhook_config_secret_optional() -> None:
    """``secret`` defaults to None — unauthenticated webhooks are
    a legitimate use case (internal services, dev-only)."""
    cfg = _accept({"type": "webhook", "url": "https://example.com/hook"})
    assert isinstance(cfg, WebhookConfig)
    assert cfg.secret is None


def test_email_config_validates_addresses() -> None:
    cfg = _accept(
        {
            "type": "email",
            "to": ["alice@example.com", "bob@example.com"],
            "subject": "report ready",
        }
    )
    assert isinstance(cfg, EmailConfig)
    assert len(cfg.to) == 2


def test_email_config_rejects_invalid_address() -> None:
    """Pydantic's EmailStr rejects malformed addresses — operators
    don't have to hand-validate."""
    with pytest.raises(ValidationError):
        _accept(
            {"type": "email", "to": ["not-an-email"], "subject": "x"}
        )


def test_email_config_requires_non_empty_to_list() -> None:
    """``min_length=1`` keeps the dispatcher honest — empty ``to``
    would silently no-op in any real SMTP send."""
    with pytest.raises(ValidationError):
        _accept({"type": "email", "to": [], "subject": "x"})


def test_dingtalk_config_uses_webhook_url_field() -> None:
    """``DingTalkConfig.webhook_url`` is intentionally distinct from
    ``WebhookConfig.url`` so pre-union payloads that already built
    with ``webhook_url`` keep working."""
    cfg = _accept(
        {
            "type": "dingtalk",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=x",
            "secret": "SEC...",
        }
    )
    assert isinstance(cfg, DingTalkConfig)
    assert "oapi.dingtalk.com" in str(cfg.webhook_url)


def test_unknown_type_is_rejected() -> None:
    """The ``type`` discriminator only accepts the three documented
    values — typos raise 422 at the API boundary."""
    with pytest.raises(ValidationError):
        _accept({"type": "sms", "to": "+15551234567"})


def test_extra_fields_are_rejected() -> None:
    """``extra='forbid'`` on every variant means unknown keys 422 —
    no silent typo'd config."""
    with pytest.raises(ValidationError):
        _accept(
            {
                "type": "webhook",
                "url": "https://example.com/h",
                "secret": "x",
                "extra_field": "nope",
            }
        )


def test_invalid_url_is_rejected() -> None:
    """HttpUrl parses the value — ``not-a-url`` 422s."""
    with pytest.raises(ValidationError):
        _accept({"type": "webhook", "url": "not-a-url"})


def test_discrimination_routes_to_correct_class() -> None:
    """A single payload that matches each variant must produce the
    right concrete type — Pydantic's ``discriminator='type'``
    key does the routing, not a manual isinstance."""
    cases = [
        ({"type": "webhook", "url": "https://e.co/h"}, WebhookConfig),
        (
            {"type": "email", "to": ["a@b.co"], "subject": "x"},
            EmailConfig,
        ),
        (
            {"type": "dingtalk", "webhook_url": "https://oapi.dingtalk.com/r"},
            DingTalkConfig,
        ),
    ]
    for payload, expected_cls in cases:
        cfg = _accept(payload)
        assert type(cfg) is expected_cls, (
            f"payload {payload!r} discriminated to {type(cfg).__name__}, "
            f"expected {expected_cls.__name__}"
        )
