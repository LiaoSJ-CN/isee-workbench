"""IM rich-card payload builders (批 G).

Splits *what a notification looks like* away from *how it gets
delivered*. The senders in :mod:`app.services.scheduler` keep the SSRF
gate, the IP-pinned transport, and the Prometheus counter; everything
here is a pure function over a :class:`CardContext`, so the card shapes
can be tested without mocking a single HTTP client.

Three IM providers, three envelope dialects — none of them share a
schema, so each gets its own builder:

* **Feishu** — ``msg_type: "interactive"`` with a ``card`` object
  (header + elements). The subject name rides in a ``plain_text``
  field, *not* ``lark_md``: a report literally named ``**Q3**`` would
  otherwise render as bold "Q3" and lose its asterisks. Using
  ``plain_text`` sidesteps escaping entirely.
* **WeChat Work** — ``msgtype: "template_card"`` when we have a link to
  put in ``card_action``, otherwise a markdown envelope. WeCom's
  ``text_notice`` card treats ``card_action`` as required, and a card
  rejected by the API fails silently (the sender only logs), so we
  don't emit one we can't populate.
* **DingTalk** — ``msgtype: "actionCard"`` when linkable, else
  ``markdown``. Note the envelope is the *body*; DingTalk's signature
  goes in the query string and is applied by the sender, not here.

``link_url`` is ``None`` whenever ``settings.public_base_url`` is unset.
Every builder degrades to a button-less card in that case rather than
inventing a URL.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

SubjectKind = Literal["report", "dashboard"]

# Wording + route segment per subject kind. Dashboard subscriptions
# borrow the report senders (see ``_dashboard_sender_shim``), and before
# 批 G they announced a dashboard as 「报表」 and had no deep link at
# all. Keeping label and path in one table means the two can't drift.
_KIND_LABEL: dict[str, str] = {"report": "报表", "dashboard": "看板"}
_KIND_PATH: dict[str, str] = {"report": "reports", "dashboard": "dashboards"}

# Characters that would leak formatting if a subject or file name landed
# unescaped in a markdown envelope. Only the ones WeCom's and DingTalk's
# markdown subsets actually interpret — this is a rendering fix, not a
# security boundary (neither dialect executes anything).
_MARKDOWN_SPECIALS = re.compile(r"([*_`~\[\]])")

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


def escape_markdown(text: str) -> str:
    """Backslash-escape the markdown specials in *text*."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


@dataclass(frozen=True)
class CardContext:
    """Everything the builders are allowed to know about a delivery.

    Deliberately narrow. Dashboard subscriptions pass a
    ``SimpleNamespace`` shim rather than a real ``Report`` row, so a
    context field that only exists on ``Report`` would crash that path
    at send time. :func:`build_card_context` reads exactly ``.id``,
    ``.name`` and ``.kind`` off the subject — nothing else.
    """

    subject_kind: SubjectKind
    subject_id: int
    subject_name: str
    generated_at: datetime
    file_names: list[str]
    """Basenames only — the receiver has no business knowing the
    server's filesystem layout (SEC-8)."""
    link_url: str | None
    """Deep link to the subject, or ``None`` when ``public_base_url``
    is unset."""

    @property
    def subject_label(self) -> str:
        """``报表`` or ``看板``."""
        return _KIND_LABEL[self.subject_kind]

    @property
    def title(self) -> str:
        """Card headline, safe for ``plain_text`` fields."""
        return f"{self.subject_label}「{self.subject_name}」已生成"

    @property
    def generated_at_text(self) -> str:
        return self.generated_at.strftime(_TIMESTAMP_FORMAT)

    @property
    def link_button_text(self) -> str:
        return f"查看{self.subject_label}"


def _resolve_kind(subject: Any) -> SubjectKind:
    """Duck-type the subject's kind, defaulting to ``"report"``.

    Anything unrecognised (including a subject with no ``kind`` at all,
    which is every ``Report`` row) is a report — the dashboard shim is
    the only caller that opts in.
    """
    return "dashboard" if getattr(subject, "kind", None) == "dashboard" else "report"


def build_card_context(
    subject: Any,
    file_paths: list[str],
    *,
    base_url: str,
    now: datetime | None = None,
) -> CardContext:
    """Assemble a :class:`CardContext` from what a sender has in scope.

    *subject* is duck-typed on purpose: report deliveries pass a
    ``Report`` row, dashboard deliveries pass a ``SimpleNamespace``.
    ``kind`` defaults to ``"report"`` so neither the report senders nor
    their existing tests need to grow a new argument — only the
    dashboard shim opts in.

    *base_url* that isn't an absolute http(s) URL is treated as unset.
    It's operator-supplied config, so the failure mode we care about is
    a typo producing a dead button, not an attack.
    """
    kind = _resolve_kind(subject)

    base = base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = ""

    subject_id = int(getattr(subject, "id", 0) or 0)

    return CardContext(
        subject_kind=kind,
        subject_id=subject_id,
        subject_name=str(getattr(subject, "name", "") or ""),
        generated_at=now or datetime.now(timezone.utc),
        file_names=[os.path.basename(p) for p in file_paths],
        link_url=f"{base}/{_KIND_PATH[kind]}/{subject_id}" if base else None,
    )


def _markdown_file_lines(ctx: CardContext) -> str:
    if not ctx.file_names:
        return "_（无文件）_"
    return "\n".join(f"- `{escape_markdown(name)}`" for name in ctx.file_names)


def build_feishu_card(ctx: CardContext) -> dict[str, Any]:
    """Feishu ``interactive`` card body.

    The caller adds ``timestamp`` / ``sign`` at the top level alongside
    ``msg_type`` and ``card`` when a secret is configured — that's
    Feishu's in-body signing contract and it is unchanged by 批 G.
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": f"**生成时间**\n{ctx.generated_at_text}"},
                },
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": f"**文件数**\n{len(ctx.file_names)}"},
                },
            ],
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**文件**\n{_markdown_file_lines(ctx)}"},
        },
    ]

    if ctx.link_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ctx.link_button_text},
                        "type": "primary",
                        "url": ctx.link_url,
                    }
                ],
            }
        )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                # plain_text, not lark_md — see module docstring.
                "title": {"tag": "plain_text", "content": ctx.title},
            },
            "elements": elements,
        },
    }


def build_wechatwork_payload(ctx: CardContext) -> dict[str, Any]:
    """WeCom ``template_card`` when linkable, markdown otherwise.

    ``text_notice`` requires ``card_action``; without a
    ``public_base_url`` we have nothing to point it at, and a rejected
    card would only surface as an ``http_error`` counter tick. The
    markdown fallback is the pre-批 G envelope with the same
    information, so operators who never set ``public_base_url`` see an
    unchanged-in-kind message rather than a broken one.
    """
    if not ctx.link_url:
        content = (
            f"**{escape_markdown(ctx.title)}**\n"
            f"> 生成时间: {ctx.generated_at_text}\n"
            f"> 文件数: {len(ctx.file_names)}\n\n"
            f"{_markdown_file_lines(ctx)}"
        )
        return {"msgtype": "markdown", "markdown": {"content": content}}

    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "main_title": {
                "title": ctx.title,
                "desc": f"生成时间 {ctx.generated_at_text}",
            },
            "horizontal_content_list": [
                {"keyname": "文件数", "value": str(len(ctx.file_names))},
                {"keyname": "文件", "value": "、".join(ctx.file_names) or "（无文件）"},
            ],
            "card_action": {"type": 1, "url": ctx.link_url},
        },
    }


def build_dingtalk_payload(ctx: CardContext) -> dict[str, Any]:
    """DingTalk ``actionCard`` when linkable, ``markdown`` otherwise.

    Body only. The ``timestamp`` / ``sign`` pair goes in the query
    string (DingTalk's 加签 mode) and is the sender's job — unlike
    Feishu, which signs in the body.
    """
    text = (
        f"### {escape_markdown(ctx.title)}\n\n"
        f"- 生成时间: {ctx.generated_at_text}\n"
        f"- 文件数: {len(ctx.file_names)}\n\n"
        f"{_markdown_file_lines(ctx)}"
    )

    if not ctx.link_url:
        return {"msgtype": "markdown", "markdown": {"title": ctx.title, "text": text}}

    return {
        "msgtype": "actionCard",
        "actionCard": {
            "title": ctx.title,
            "text": text,
            "btnOrientation": "0",
            "singleTitle": ctx.link_button_text,
            "singleURL": ctx.link_url,
        },
    }


__all__ = [
    "CardContext",
    "SubjectKind",
    "build_card_context",
    "build_dingtalk_payload",
    "build_feishu_card",
    "build_wechatwork_payload",
    "escape_markdown",
]
