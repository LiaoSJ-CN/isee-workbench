"""normalize legacy notification_config payloads (TODO-8)

Revision ID: c0a2b1d4e5f6
Revises: 222001adeb57
Create Date: 2026-08-16 12:00:00.000000

批 6b (commit ``9fa171c``) replaced the free-form
``notification_config: dict | None`` field with a Pydantic discriminated
union (WebhookConfig / EmailConfig / DingTalkConfig). The SQLAlchemy
column stayed as ``JSON`` so the *type* of the data didn't change, but
the *shape* did. Production deployments that persisted rows under the
old schema need those payloads normalized or every read of
``/reports/{id}`` 422s.

See :func:`app.services.notification_migration.normalize_legacy_notification_config`
for the per-row decision logic. This revision walks every non-null
``reports.notification_config`` row, normalizes the payload, and writes
it back. Rows whose shape doesn't match any known variant are left
alone — silent data loss is worse than a visible 422 the operator can
fix.

``downgrade()`` is a no-op: the pre-6b ``dict | None`` column accepted
any shape, so the normalized shape still passes validation going back.
A real reverse (rewriting ``url`` → ``webhook_url``) would need a side
table; no demand today.
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.services.notification_migration import normalize_legacy_notification_config


# revision identifiers, used by Alembic.
revision: str = "c0a2b1d4e5f6"
down_revision: Union[str, Sequence[str], None] = "222001adeb57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Normalize every non-null notification_config row in place.

    SQLite's JSON1 extension exposes ``json_extract`` but we're
    cross-dialect (SQLite + Postgres-family in OpenGauss/DWS), so the
    simplest portable approach is fetch → Python transform → write back.
    Per-row round-trips are fine here: the table is small (one row per
    report) and this runs once at deploy time.
    """
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, notification_config FROM reports "
            "WHERE notification_config IS NOT NULL"
        )
    ).fetchall()

    rewritten = 0
    skipped = 0
    for row_id, raw in rows:
        # SQLAlchemy hands back a dict for JSON columns on most dialects
        # but may hand back a JSON string on some — normalise both.
        if isinstance(raw, str):
            try:
                cfg = json.loads(raw)
            except (TypeError, ValueError):
                skipped += 1
                continue
        else:
            cfg = raw

        new = normalize_legacy_notification_config(cfg)
        if new is None:
            skipped += 1
            continue

        conn.execute(
            text(
                "UPDATE reports SET notification_config = :cfg WHERE id = :id"
            ),
            {"cfg": json.dumps(new), "id": row_id},
        )
        rewritten += 1

    # Echo a summary so the operator can see the migration actually ran
    # (and how many rows it touched). Alembic surfaces this in stdout.
    if rewritten or skipped:
        print(
            f"[c0a2b1d4e5f6] notification_config: "
            f"rewritten={rewritten} skipped={skipped}"
        )


def downgrade() -> None:
    """No-op — see module docstring.

    The pre-6b ``dict | None`` schema accepts the normalized shape, so
    reverting the schema (which is the only thing alembic downgrade
    handles) is non-destructive at the data layer. If we ever need a
    real reverse, dump the original payloads into a side table during
    ``upgrade()``.
    """