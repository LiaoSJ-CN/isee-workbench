"""Tests for the IM rich-card payload builders (批 G).

Pure functions in, dicts out — no HTTP client, no monkeypatching, no
network. The delivery-side concerns (SSRF gate, signing, metrics) stay
in ``test_notification_im.py``; this file only pins the *shape* of what
each provider receives.

Every builder is exercised twice: with a ``link_url`` (operator set
``PUBLIC_BASE_URL``) and without. The no-link path is the one that runs
in a default deployment, so it gets equal weight.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.notification_cards import (
    build_card_context,
    build_dingtalk_payload,
    build_feishu_card,
    build_wechatwork_payload,
    escape_markdown,
)

FIXED_NOW = datetime(2026, 9, 5, 9, 30, 0, tzinfo=timezone.utc)
EXPECTED_TIME_TEXT = "2026-09-05 09:30:00 UTC"

BASE_URL = "https://isee.example.com"


def _report(name: str = "季度销售", report_id: int = 42) -> SimpleNamespace:
    """Stand-in for a ``Report`` row — the builders only read id/name/kind."""
    return SimpleNamespace(id=report_id, name=name)


def _dashboard(name: str = "运营看板", dashboard_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(id=dashboard_id, name=name, kind="dashboard")


def _ctx(subject: SimpleNamespace, files: list[str] | None = None, base_url: str = BASE_URL):
    return build_card_context(
        subject,
        files if files is not None else ["/var/reports/q3.xlsx", "/tmp/summary.pdf"],
        base_url=base_url,
        now=FIXED_NOW,
    )


# -------------------- build_card_context --------------------


def test_context_defaults_to_report_kind() -> None:
    """A ``Report`` row has no ``kind`` attribute — the builders must
    not require one, or every existing caller would break."""
    ctx = _ctx(_report())

    assert ctx.subject_kind == "report"
    assert ctx.subject_label == "报表"
    assert ctx.title == "报表「季度销售」已生成"
    assert ctx.link_url == f"{BASE_URL}/reports/42"
    assert ctx.link_button_text == "查看报表"


def test_context_honours_dashboard_kind() -> None:
    """The dashboard shim opts in via ``kind`` — wording and route
    segment must both follow. Pre-批 G a dashboard was announced as
    「报表」."""
    ctx = _ctx(_dashboard())

    assert ctx.subject_kind == "dashboard"
    assert ctx.title == "看板「运营看板」已生成"
    assert ctx.link_url == f"{BASE_URL}/dashboards/7"
    assert ctx.link_button_text == "查看看板"


def test_context_strips_directories_from_file_paths() -> None:
    """SEC-8: basenames only, never the server's filesystem layout."""
    ctx = _ctx(_report())

    assert ctx.file_names == ["q3.xlsx", "summary.pdf"]


def test_context_without_base_url_has_no_link() -> None:
    """The default deployment (``PUBLIC_BASE_URL`` unset) gets a
    link-less context rather than a guessed URL."""
    ctx = _ctx(_report(), base_url="")

    assert ctx.link_url is None


def test_context_ignores_non_http_base_url() -> None:
    """A typo'd base URL is treated as unset — better no button than a
    dead one."""
    assert _ctx(_report(), base_url="isee.example.com").link_url is None
    assert _ctx(_report(), base_url="   ").link_url is None


def test_context_strips_trailing_slash_from_base_url() -> None:
    ctx = _ctx(_report(), base_url="https://isee.example.com/")

    assert ctx.link_url == "https://isee.example.com/reports/42"


def test_context_formats_timestamp_for_humans() -> None:
    """Cards are read by people — not ``2026-09-05T09:30:00+00:00``."""
    assert _ctx(_report()).generated_at_text == EXPECTED_TIME_TEXT


# -------------------- escape_markdown --------------------


def test_escape_markdown_neutralises_formatting_characters() -> None:
    assert escape_markdown("**Q3**") == r"\*\*Q3\*\*"
    assert escape_markdown("a_b_c") == r"a\_b\_c"
    assert escape_markdown("`code`") == r"\`code\`"
    assert escape_markdown("[link]") == r"\[link\]"
    assert escape_markdown("~strike~") == r"\~strike\~"


def test_escape_markdown_leaves_plain_text_alone() -> None:
    assert escape_markdown("季度销售 2026") == "季度销售 2026"


# -------------------- build_feishu_card --------------------


def test_feishu_card_envelope_and_header() -> None:
    payload = build_feishu_card(_ctx(_report()))

    assert payload["msg_type"] == "interactive"
    header = payload["card"]["header"]
    assert header["title"]["content"] == "报表「季度销售」已生成"
    # plain_text, not lark_md: a report named "**Q3**" must not render
    # as bold "Q3".
    assert header["title"]["tag"] == "plain_text"


def test_feishu_card_name_is_not_escaped_in_plain_text_header() -> None:
    """Since the header is ``plain_text``, the raw name goes through
    verbatim — escaping there would show literal backslashes."""
    payload = build_feishu_card(_ctx(_report(name="**Q3**")))

    assert payload["card"]["header"]["title"]["content"] == "报表「**Q3**」已生成"


def test_feishu_card_lists_files_and_count() -> None:
    payload = build_feishu_card(_ctx(_report()))
    rendered = str(payload["card"]["elements"])

    assert EXPECTED_TIME_TEXT in rendered
    assert "q3.xlsx" in rendered
    assert "summary.pdf" in rendered
    assert "/var/reports/" not in rendered


def test_feishu_card_has_button_only_when_linkable() -> None:
    with_link = build_feishu_card(_ctx(_report()))
    actions = [e for e in with_link["card"]["elements"] if e["tag"] == "action"]
    assert len(actions) == 1
    button = actions[0]["actions"][0]
    assert button["url"] == f"{BASE_URL}/reports/42"
    assert button["text"]["content"] == "查看报表"

    without_link = build_feishu_card(_ctx(_report(), base_url=""))
    assert not [e for e in without_link["card"]["elements"] if e["tag"] == "action"]


def test_feishu_card_handles_empty_file_list() -> None:
    payload = build_feishu_card(_ctx(_report(), files=[]))

    assert "（无文件）" in str(payload["card"]["elements"])


# -------------------- build_wechatwork_payload --------------------


def test_wechatwork_uses_template_card_when_linkable() -> None:
    payload = build_wechatwork_payload(_ctx(_report()))

    assert payload["msgtype"] == "template_card"
    card = payload["template_card"]
    assert card["card_type"] == "text_notice"
    assert card["main_title"]["title"] == "报表「季度销售」已生成"
    # card_action is what forces the two-path design — WeCom treats it
    # as required for text_notice.
    assert card["card_action"] == {"type": 1, "url": f"{BASE_URL}/reports/42"}
    assert "q3.xlsx" in str(card["horizontal_content_list"])


def test_wechatwork_falls_back_to_markdown_without_link() -> None:
    """No ``public_base_url`` → no ``card_action`` target → we must not
    emit a template_card the API would reject."""
    payload = build_wechatwork_payload(_ctx(_report(), base_url=""))

    assert payload["msgtype"] == "markdown"
    assert "template_card" not in payload
    content = payload["markdown"]["content"]
    assert "报表「季度销售」已生成" in content
    assert EXPECTED_TIME_TEXT in content
    assert "q3.xlsx" in content
    assert "/var/reports/" not in content


def test_wechatwork_markdown_escapes_subject_name() -> None:
    payload = build_wechatwork_payload(_ctx(_report(name="**Q3**"), base_url=""))

    assert r"\*\*Q3\*\*" in payload["markdown"]["content"]


# -------------------- build_dingtalk_payload --------------------


def test_dingtalk_uses_action_card_when_linkable() -> None:
    payload = build_dingtalk_payload(_ctx(_report()))

    assert payload["msgtype"] == "actionCard"
    card = payload["actionCard"]
    assert card["title"] == "报表「季度销售」已生成"
    assert card["singleTitle"] == "查看报表"
    assert card["singleURL"] == f"{BASE_URL}/reports/42"
    assert "q3.xlsx" in card["text"]


def test_dingtalk_falls_back_to_markdown_without_link() -> None:
    payload = build_dingtalk_payload(_ctx(_report(), base_url=""))

    assert payload["msgtype"] == "markdown"
    assert "actionCard" not in payload
    assert payload["markdown"]["title"] == "报表「季度销售」已生成"
    assert EXPECTED_TIME_TEXT in payload["markdown"]["text"]


def test_dingtalk_body_carries_no_signature() -> None:
    """DingTalk signs in the query string, not the body — a ``sign``
    key here would mean the sender and builder disagree about whose
    job it is."""
    payload = build_dingtalk_payload(_ctx(_report()))

    assert "sign" not in payload
    assert "timestamp" not in payload


def test_dingtalk_dashboard_wording_and_link() -> None:
    payload = build_dingtalk_payload(_ctx(_dashboard()))

    assert payload["actionCard"]["title"] == "看板「运营看板」已生成"
    assert payload["actionCard"]["singleURL"] == f"{BASE_URL}/dashboards/7"
    assert payload["actionCard"]["singleTitle"] == "查看看板"


# -------------------- dashboard shim integration --------------------


def test_dashboard_shim_drives_dashboard_wording() -> None:
    """End-to-end on the duck-typing contract: the real shim from
    ``dashboard_subscription`` must produce a context the builders read
    as a dashboard.

    Before 批 G the shim carried only ``id`` / ``name``, so dashboard
    subscriptions announced themselves as 「报表」. This test fails if
    the ``kind`` field is dropped from the shim again.
    """
    from app.models.dashboard import Dashboard
    from app.services.dashboard_subscription import _dashboard_sender_shim

    shim = _dashboard_sender_shim(Dashboard(id=7, name="运营看板"))
    ctx = _ctx(shim)

    assert ctx.subject_kind == "dashboard"
    assert ctx.title == "看板「运营看板」已生成"
    assert ctx.link_url == f"{BASE_URL}/dashboards/7"


def test_report_row_needs_no_kind_attribute() -> None:
    """The mirror case: a real ``Report`` row has no ``kind`` column,
    and must still resolve to a report without any caller changes."""
    from app.models.report import Report

    ctx = _ctx(Report(id=42, name="季度销售"))

    assert ctx.subject_kind == "report"
    assert ctx.link_url == f"{BASE_URL}/reports/42"
