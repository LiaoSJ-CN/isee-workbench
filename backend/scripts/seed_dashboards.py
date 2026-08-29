"""Seed meta DB (app.db) with a demo dashboard (批 14.6 — 看板示例).

Mirrors :mod:`scripts.seed_reports` shape — same JSON-as-Python-literal
style, same ``META_DB`` sqlite path, same admin-id lookup order.

Inserts one demo dashboard ("运营分析看板") that aggregates the three
seed reports:

  * 1 banner text item (12 cols, h=1)
  * 1 report-item linking to ``财务经营月报``
  * 3 chart items pulling from the same ERP warehouse as the demo
    reports (月度利润趋势 / 应收余额按区域 / 付款方式分布)

The dashboard is created ``visibility=public``, ``owner_user_id=<admin>``
so it shows up on every operator's first visit — the same convention
:func:`scripts.seed_reports.seed` uses for the demo reports.

Idempotency:

* Without ``--keep-existing`` → wipes all dashboard rows whose ``name``
  matches a seed name (cascades to ``dashboard_items`` via FK).
* With ``--keep-existing`` → only wipes rows that look like a prior seed
  run (``description LIKE 'demo:%'``). The bootstrap-demo recovery
  path uses this so non-demo operator dashboards survive a re-run.

The :class:`Dashboard` model doesn't have an ``is_demo`` flag (only
``Report`` does — 批 10 demo-badge), so we lean on a description prefix
marker instead. Cheap, robust, no schema change required.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

META_DB = Path(__file__).resolve().parent.parent / "app.db"
DEMO_DESCRIPTION_PREFIX = "demo:"  # marker — see module docstring


def _dump(obj: Any) -> str | None:
    """JSON-serialise obj, ``None`` stays ``None`` (→ SQL NULL)."""
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


# ---------------------------------------------------------------------------
# Dashboard definitions
# ---------------------------------------------------------------------------

# One demo dashboard for now. The shape mirrors the seed-report JSON so
# the bootstrap orchestrator can iterate uniformly if more are added later.
DASHBOARDS: list[dict[str, Any]] = [
    {
        "name": "运营分析看板",
        # ``description`` doubles as the demo marker — see idem­potency note
        # in the module docstring.
        "description": (
            "demo:看板示例 — 聚合财务经营月报、应收 / 应付分析三个 demo "
            "报表的关键指标。修改 / 删除不会被恢复。"
        ),
        "visibility": "public",
        # Items live on a 12-col grid; row order is (y, x) so
        # ``order_index`` is a tiebreaker for items sharing a cell.
        "items": [
            {
                "item_type": "text",
                "title": "看板说明",
                "x": 0,
                "y": 0,
                "w": 12,
                "h": 1,
                "text_content": (
                    "看板示例 — 顶部 1 个报表 + 3 个图表 + 1 个静态横幅。"
                    "试试拖动 / 调整大小 / 双击编辑。"
                ),
                # References by *name* (resolved at seed time) — keeps the
                # JSON stable across re-seeds even when the underlying
                # report id changes.
                "report_ref": None,
            },
            {
                "item_type": "report",
                "title": "财务经营月报（嵌入）",
                "x": 0,
                "y": 1,
                "w": 6,
                "h": 4,
                "report_ref": "财务经营月报",
                "display_config": {"title": "财务经营月报"},
            },
            {
                "item_type": "chart",
                "title": "月度利润趋势",
                "x": 6,
                "y": 1,
                "w": 6,
                "h": 4,
                "table_name": "ads_fin_pl_monthly",
                "fields": ["year_month", "revenue", "operating_profit", "net_profit"],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "year_month", "direction": "ASC"}],
                "limit": 100,
                "display_config": {
                    "title": "月度利润趋势",
                    "chart_type": "line",
                    "show_legend": True,
                    "legend_position": "top",
                },
                "custom_sql": (
                    "SELECT year_month, ROUND(revenue / 10000, 2) AS revenue, "
                    "ROUND(operating_profit / 10000, 2) AS operating_profit, "
                    "ROUND(net_profit / 10000, 2) AS net_profit "
                    "FROM ads_fin_pl_monthly ORDER BY year_month ASC LIMIT 100"
                ),
            },
            {
                "item_type": "chart",
                "title": "应收余额按区域",
                "x": 0,
                "y": 5,
                "w": 6,
                "h": 4,
                "table_name": "dwd_fin_ar_balance",
                "fields": [],
                "where_conditions": [],
                "group_by": [],
                "order_by": [],
                "limit": 100,
                "display_config": {
                    "title": "应收余额按区域",
                    "chart_type": "pie",
                    "show_legend": True,
                },
                "custom_sql": (
                    "SELECT c.region AS region, ROUND(SUM(a.balance), 2) AS total_balance "
                    "FROM dwd_fin_ar_balance a "
                    "JOIN dim_customer c ON a.customer_id = c.customer_id "
                    "GROUP BY c.region ORDER BY total_balance DESC"
                ),
            },
            {
                "item_type": "chart",
                "title": "付款方式分布",
                "x": 6,
                "y": 5,
                "w": 6,
                "h": 4,
                "table_name": "dwd_fin_payment",
                "fields": [],
                "where_conditions": [
                    {"field": "payment_type", "operator": "=", "value": "payment"}
                ],
                "group_by": ["payment_method"],
                "order_by": [{"field": "total", "direction": "DESC"}],
                "limit": 100,
                "display_config": {
                    "title": "付款方式分布（金额）",
                    "chart_type": "doughnut",
                },
                "custom_sql": (
                    "SELECT payment_method, COUNT(*) AS cnt, "
                    "ROUND(SUM(amount), 2) AS total "
                    "FROM dwd_fin_payment WHERE payment_type = 'payment' "
                    "GROUP BY payment_method ORDER BY total DESC"
                ),
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _admin_id(cur: sqlite3.Cursor) -> int | None:
    row = cur.execute(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def _resolve_data_source_id(cur: sqlite3.Cursor) -> int | None:
    """Mirror :func:`scripts.seed_reports._resolve_data_source_id` —
    prefer ``sqlite_demo`` by name, fall back to the first row.

    Returns ``None`` when there is no data source at all — the operator
    must run ``seed_reports`` (or ``bootstrap_demo``) first.
    """
    row = cur.execute(
        "SELECT id FROM data_sources WHERE name = 'sqlite_demo' "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        return int(row[0])
    row = cur.execute(
        "SELECT id FROM data_sources ORDER BY id LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def _resolve_report_id(cur: sqlite3.Cursor, name: str) -> int | None:
    """Look up a report by *name* so the dashboard JSON doesn't have to
    bake in a numeric id (which would change on every fresh DB)."""
    row = cur.execute(
        "SELECT id FROM reports WHERE name = ? ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    return int(row[0]) if row else None


def _wipe_existing(cur: sqlite3.Cursor, keep_existing: bool, names: list[str]) -> None:
    """Remove prior seed dashboards so the next insert doesn't trip the
    UNIQUE(name) constraint. Two modes:

    * ``keep_existing=False`` → wipe every row matching a seed name
      (default — matches :func:`scripts.seed_reports.seed`).
    * ``keep_existing=True`` → only wipe rows that look like a prior
      seed run (the ``demo:`` description marker). Operator-authored
      dashboards are preserved. Used by ``bootstrap_demo``.
    """
    placeholders = ",".join("?" * len(names))
    if keep_existing:
        cur.execute(
            f"DELETE FROM dashboards "
            f"WHERE description LIKE '{DEMO_DESCRIPTION_PREFIX}%' "
            f"  AND name IN ({placeholders})",
            names,
        )
    else:
        cur.execute(
            f"DELETE FROM dashboards WHERE name IN ({placeholders})",
            names,
        )


def seed(keep_existing: bool = False) -> dict[str, bool]:
    """Insert / re-insert the demo dashboards. Idempotent.

    Returns ``{dashboard_name: present_after}`` so callers can assert
    state without re-querying.
    """
    conn = sqlite3.connect(META_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    admin_id = _admin_id(cur)
    if admin_id is None:
        print(
            "no admin user — create one via /auth/login + initial bootstrap "
            "before seeding dashboards",
            file=sys.stderr,
        )
        conn.close()
        return {d["name"]: False for d in DASHBOARDS}

    ds_id = _resolve_data_source_id(cur)
    if ds_id is None:
        print(
            "no DataSource found — run scripts/seed_reports.py "
            "(or scripts/bootstrap_demo.py) first",
            file=sys.stderr,
        )
        conn.close()
        return {d["name"]: False for d in DASHBOARDS}

    seed_names = [d["name"] for d in DASHBOARDS]
    _wipe_existing(cur, keep_existing, seed_names)

    for d in DASHBOARDS:
        cur.execute(
            """INSERT INTO dashboards
               (name, description, visibility, owner_user_id,
                org_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (d["name"], d["description"], d["visibility"], admin_id),
        )
        dashboard_id = cur.lastrowid

        for idx, item in enumerate(d["items"]):
            # ``report_ref`` is a name → id lookup key (kept out of the
            # INSERT so we never bake in a numeric id that drifts across
            # fresh DBs).
            report_ref = item.get("report_ref")
            report_id = (
                _resolve_report_id(cur, report_ref)
                if (item["item_type"] == "report" and report_ref)
                else None
            )

            cur.execute(
                """INSERT INTO dashboard_items
                   (dashboard_id, x, y, w, h, order_index,
                    item_type, title,
                    report_id, data_source_id,
                    table_name, fields, where_conditions,
                    group_by, order_by, "limit",
                    display_config, custom_sql,
                    text_content, parameters,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?,
                           ?, ?,
                           ?, ?,
                           ?, ?, ?,
                           ?, ?, ?,
                           ?, ?,
                           ?, ?,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    dashboard_id,
                    item["x"],
                    item["y"],
                    item["w"],
                    item["h"],
                    idx,
                    item["item_type"],
                    item["title"],
                    report_id,
                    ds_id if item["item_type"] == "chart" else None,
                    item.get("table_name"),
                    _dump(item.get("fields") or []),
                    _dump(item.get("where_conditions") or []),
                    _dump(item.get("group_by") or []),
                    _dump(item.get("order_by") or []),
                    item.get("limit"),
                    _dump(item.get("display_config") or {}),
                    item.get("custom_sql"),
                    item.get("text_content"),
                    _dump(item.get("parameters") or {}),
                ),
            )

        print(
            f"  + dashboard '{d['name']}' (id={dashboard_id}, "
            f"{len(d['items'])} items)"
        )

    conn.commit()

    present: dict[str, bool] = {}
    for d in DASHBOARDS:
        row = cur.execute(
            "SELECT 1 FROM dashboards WHERE name = ?", (d["name"],)
        ).fetchone()
        present[d["name"]] = row is not None
    conn.close()
    return present


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="only wipe prior seed rows (description LIKE 'demo:%'); "
             "preserve operator-authored dashboards",
    )
    args = parser.parse_args()
    result = seed(keep_existing=args.keep_existing)
    print(f"\nseeded {META_DB}: {result}")


if __name__ == "__main__":
    main()