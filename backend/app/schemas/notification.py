"""Notification configuration discriminated union (批 6b.4).

Replaces the pre-union ``notification_config: dict | None`` on the
:class:`Report` schema. Five variants today, all sharing the
``type`` discriminator:

* :class:`WebhookConfig` — generic HMAC-signed JSON POST to any URL.
* :class:`EmailConfig` — SMTP delivery (only the data shape is
  defined here; the actual sender is out of scope for this batch).
* :class:`DingTalkConfig` — DingTalk robot webhook. Kept as a
  separate variant (rather than folded into ``WebhookConfig``)
  because the field name is ``webhook_url`` — same as the legacy
  dict shape — so we don't break callers that built payloads by
  hand before the union landed.
* :class:`FeishuConfig` — Feishu (Lark) bot webhook (批 8.4). Signs
  *inside* the JSON body (``timestamp`` + ``sign`` keys), not as
  headers — Feishu's protocol differs from the generic webhook
  HMAC contract, so we don't share :class:`WebhookConfig` here.
* :class:`WeChatWorkConfig` — WeChat Work (企业微信) bot webhook
  (批 8.4). Posts a plain JSON envelope; older bot versions don't
  sign. Kept as its own variant for parity with the operator
  mental model — "I configured a WeChat Work bot" should be a
  single first-class option.

The :data:`NotificationConfig` annotated union is the type consumed by
``Report.notification_config``; callers can pass a dict that matches
one of the variants and Pydantic discriminates on the ``type`` key
into the right concrete model.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


class WebhookConfig(BaseModel):
    """Generic HMAC-signed JSON POST to a user-supplied URL.

    Delivery + SSRF guard logic lives in
    :mod:`app.services.scheduler` (``_send_notification``); this schema
    is purely the typed payload shape.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["webhook"] = "webhook"
    url: HttpUrl
    secret: str | None = None


class EmailConfig(BaseModel):
    """Email delivery configuration.

    The SMTP transport isn't implemented yet — the scheduler logs an
    info message and moves on. Defining the schema now lets callers
    configure email notifications end-to-end so the wiring is ready
    when the SMTP sender lands.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["email"] = "email"
    to: list[EmailStr] = Field(..., min_length=1)
    subject: str = Field(..., min_length=1, max_length=255)


class DingTalkConfig(BaseModel):
    """DingTalk robot webhook.

    Field name is ``webhook_url`` (not ``url``) to match the legacy
    dict shape so pre-union callers that build payloads by hand
    don't have to rename. The scheduler signs and posts the same way
    as :class:`WebhookConfig`.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["dingtalk"] = "dingtalk"
    webhook_url: HttpUrl
    secret: str | None = None


class FeishuConfig(BaseModel):
    """Feishu (Lark) bot webhook.

    Field name is ``webhook_url`` (matches the legacy shape and
    parallels :class:`DingTalkConfig` so reporters that already
    built DingTalk-shaped payloads don't have to rename the URL
    key when switching provider). Signing, if ``secret`` is set,
    is added as ``timestamp`` + ``sign`` keys *inside* the JSON
    body — Feishu's protocol differs from the generic webhook
    HMAC contract, so :func:`_sign_payload` is not reused here.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["feishu"] = "feishu"
    webhook_url: HttpUrl
    secret: str | None = None


class WeChatWorkConfig(BaseModel):
    """WeChat Work (企业微信) bot webhook.

    Older bot versions don't support a signing secret — the sender
    POSTs a plain JSON envelope and relies on the URL's ``key=``
    query parameter (set by the operator at bot-creation time) for
    authentication. Field naming mirrors :class:`DingTalkConfig` /
    :class:`FeishuConfig` for consistency.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["wechatwork"] = "wechatwork"
    webhook_url: HttpUrl


NotificationConfig = Annotated[
    Union[
        WebhookConfig,
        EmailConfig,
        DingTalkConfig,
        FeishuConfig,
        WeChatWorkConfig,
    ],
    Field(discriminator="type"),
]


__all__ = [
    "WebhookConfig",
    "EmailConfig",
    "DingTalkConfig",
    "FeishuConfig",
    "WeChatWorkConfig",
    "NotificationConfig",
]
