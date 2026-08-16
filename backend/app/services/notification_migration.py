"""Normalize legacy ``notification_config`` payloads to the 批 6b union.

批 6b (:commit:`9fa171c`) replaced the free-form
``notification_config: dict | None`` on :class:`~app.models.report.Report`
with a Pydantic discriminated union (``WebhookConfig`` / ``EmailConfig`` /
``DingTalkConfig``). The SQLAlchemy column itself didn't change — still
``JSON`` — but the *shape* of valid values did. Pre-union callers could
write ``{webhook_url: "..."}`` (no ``type``, old field name) and the row
would persist; the new validator rejects it because:

* :class:`WebhookConfig` requires ``{type: "webhook", url: ...}``, not
  ``webhook_url``.
* The union discriminator ``type`` is missing, so Pydantic can't pick
  which variant to validate against.
* :class:`DingTalkConfig` happens to also use ``webhook_url``, but
  without ``type`` we can't tell a DingTalk row from a generic webhook.

The data migration in
``alembic/versions/c0a2b1d4e5f6_normalize_legacy_notification_config.py``
walks every non-null ``notification_config`` row and calls
:func:`normalize_legacy_notification_config` to rewrite it in place.
Returns ``None`` when no rewrite is needed.

Dev DB has no legacy rows (the union was added in the same batch that
introduced the NotificationConfig validator), but production
deployments that started on the old shape need this to round-trip
their data.

Caveats:

* Rows that don't match any known shape are LEFT UNTOUCHED — silent
  data loss is worse than a 422 the operator can see and fix.
* ``downgrade()`` is a no-op. The pre-6b ``dict | None`` field accepted
  any shape, so the normalized shape also passes — going back is
  non-destructive. A real reverse would need a side table recording
  originals during ``upgrade()``; we haven't seen demand.
"""

from __future__ import annotations

from typing import Any

# Public names — used by the alembic migration and the unit tests.
LEGACY_WEBHOOK_FIELD = "webhook_url"
NEW_WEBHOOK_FIELD = "url"
WEBHOOK_TYPE = "webhook"


def _build_webhook_payload(url: Any, source_cfg: dict[str, Any]) -> dict[str, Any]:
    """Compose a ``WebhookConfig``-shaped dict from ``url`` + optional ``secret``."""
    payload: dict[str, Any] = {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: url}
    if "secret" in source_cfg:
        payload["secret"] = source_cfg["secret"]
    return payload


def normalize_legacy_notification_config(
    cfg: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the normalized shape, or ``None`` if no rewrite needed.

    ``None`` is also returned for empty / missing inputs — the Pydantic
    ``_empty_dict_to_none`` validator on ``ReportResponse`` handles those
    on the read path.
    """
    # `not cfg` covers None and `{}` (both falsy).
    if not cfg:
        return None

    cfg_type = cfg.get("type")
    has_webhook_url = LEGACY_WEBHOOK_FIELD in cfg
    has_url = NEW_WEBHOOK_FIELD in cfg

    # 1. type=webhook + webhook_url (legacy rename).
    if cfg_type == WEBHOOK_TYPE:
        if not has_webhook_url:
            return None  # already correct shape
        if has_url:
            # Both fields present — data inconsistency. Leave untouched
            # so the operator sees the 422 and picks which to keep.
            return None
        rewritten = dict(cfg)
        rewritten[NEW_WEBHOOK_FIELD] = rewritten.pop(LEGACY_WEBHOOK_FIELD)
        return rewritten

    # 2. type=dingtalk — webhook_url is the correct field name, no-op.
    if cfg_type == "dingtalk":
        return None

    # 3. type=email — no URL field, no-op.
    if cfg_type == "email":
        return None

    # 4. No type discriminator — guess webhook from URL field name.
    if cfg_type is None:
        if has_webhook_url and not has_url:
            return _build_webhook_payload(cfg[LEGACY_WEBHOOK_FIELD], cfg)
        if has_url and not has_webhook_url:
            return _build_webhook_payload(cfg[NEW_WEBHOOK_FIELD], cfg)
        # Unknown shape (e.g. bare email fields) — leave alone.
        return None

    # 5. Future type we don't know about — leave alone.
    return None


__all__ = [
    "LEGACY_WEBHOOK_FIELD",
    "NEW_WEBHOOK_FIELD",
    "WEBHOOK_TYPE",
    "normalize_legacy_notification_config",
]
