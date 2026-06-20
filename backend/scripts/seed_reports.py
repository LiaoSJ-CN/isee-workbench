"""Seed meta DB (app.db) with finance-DW-themed reports + items.

Deletes any existing reports (cascades to report_items) and inserts 3 fresh ones:
  A. 财务经营月报     — 营收/利润/现金流概览
  B. 应收账款分析     — 区域/账龄/客户排行
  C. 供应商付款与应付 — 付款金额/方式/应付账龄

Each report has 4 items (metric + chart + table mix).

Usage:
    python scripts/seed_reports.py
    python scripts/seed_reports.py --keep-existing   # skip delete
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

META_DB = Path(__file__).resolve().parent.parent / "app.db"
DATA_SOURCE_ID = 1  # 财务主题域演示库


def _dump(obj):
    """Serialize Python obj to JSON string (None for None)."""
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


# ---------------------------------------------------------------------------
# Report definitions
# ---------------------------------------------------------------------------

REPORTS = [
    {
        "name": "财务经营月报",
        "description": "展示最近 18 个月的营收、成本、利润、现金流走势及本月关键指标。",
        "items": [
            {
                "name": "本月关键指标",
                "item_type": "metric",
                "table_name": "ads_fin_pl_monthly",
                "fields": [],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "year_month", "direction": "DESC"}],
                "limit": 1,
                "display_config": {"title": "本月关键指标"},
                "custom_sql": (
                    "SELECT "
                    "  ROUND(revenue / 10000, 2) AS \"本月营收(万元)\", "
                    "  ROUND(operating_profit / 10000, 2) AS \"营业利润(万元)\", "
                    "  ROUND(net_profit / 10000, 2) AS \"净利润(万元)\" "
                    "FROM ads_fin_pl_monthly ORDER BY year_month DESC LIMIT 1"
                ),
            },
            {
                "name": "月度利润趋势",
                "item_type": "chart",
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
                "custom_sql": None,
            },
            {
                "name": "月度现金流",
                "item_type": "chart",
                "table_name": "ads_fin_cashflow_monthly",
                "fields": ["year_month", "inflow", "outflow", "net_flow"],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "year_month", "direction": "ASC"}],
                "limit": 100,
                "display_config": {
                    "title": "月度现金流",
                    "chart_type": "bar",
                    "show_legend": True,
                },
                "custom_sql": None,
            },
            {
                "name": "月度利润表",
                "item_type": "table",
                "table_name": "ads_fin_pl_monthly",
                "fields": [],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "year_month", "direction": "ASC"}],
                "limit": 100,
                "display_config": {"title": "月度利润表"},
                "custom_sql": (
                    "SELECT "
                    "  year_month AS \"月份\", "
                    "  ROUND(revenue / 10000, 2) AS \"营收(万元)\", "
                    "  ROUND(cost / 10000, 2) AS \"成本(万元)\", "
                    "  ROUND(expense / 10000, 2) AS \"费用(万元)\", "
                    "  ROUND(operating_profit / 10000, 2) AS \"营业利润(万元)\", "
                    "  ROUND(net_profit / 10000, 2) AS \"净利润(万元)\" "
                    "FROM ads_fin_pl_monthly ORDER BY year_month"
                ),
            },
        ],
    },
    {
        "name": "应收账款分析",
        "description": "按区域、账龄、客户等维度分析应收账款余额分布与风险。",
        "items": [
            {
                "name": "应收余额按区域",
                "item_type": "chart",
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
                "name": "应收账龄分布",
                "item_type": "chart",
                "table_name": "dwd_fin_ar_balance",
                "fields": [
                    "aging_bucket",
                    "COUNT(*) AS cnt",
                    "ROUND(SUM(balance), 2) AS total",
                ],
                "where_conditions": [],
                "group_by": ["aging_bucket"],
                "order_by": [{"field": "total", "direction": "DESC"}],
                "limit": 100,
                "display_config": {
                    "title": "应收账龄分布",
                    "chart_type": "bar",
                },
                "custom_sql": None,
            },
            {
                "name": "客户应收排行Top20",
                "item_type": "table",
                "table_name": "dwd_fin_ar_balance",
                "fields": [],
                "where_conditions": [],
                "group_by": [],
                "order_by": [],
                "limit": 20,
                "display_config": {"title": "客户应收余额排行 Top 20"},
                "custom_sql": (
                    "SELECT "
                    "  c.customer_name AS \"客户名称\", "
                    "  c.industry AS \"行业\", "
                    "  c.region AS \"区域\", "
                    "  COUNT(*) AS \"账单数\", "
                    "  ROUND(SUM(a.orig_amount), 2) AS \"应收原额\", "
                    "  ROUND(SUM(a.paid_amount), 2) AS \"已收款\", "
                    "  ROUND(SUM(a.balance), 2) AS \"应收余额\" "
                    "FROM dwd_fin_ar_balance a "
                    "JOIN dim_customer c ON a.customer_id = c.customer_id "
                    "GROUP BY c.customer_name, c.industry, c.region "
                    "ORDER BY \"应收余额\" DESC LIMIT 20"
                ),
            },
            {
                "name": "应收账龄汇总明细",
                "item_type": "table",
                "table_name": "dws_fin_ar_aging",
                "fields": [
                    "period_date",
                    "customer_id",
                    "amount_30d",
                    "amount_31_60d",
                    "amount_61_90d",
                    "amount_over_90d",
                    "total_balance",
                ],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "total_balance", "direction": "DESC"}],
                "limit": 20,
                "display_config": {"title": "应收账龄汇总（按月×客户）Top 20"},
                "custom_sql": None,
            },
        ],
    },
    {
        "name": "供应商付款与应付分析",
        "description": "供应商付款金额、付款方式、应付账龄及供应商主数据。",
        "items": [
            {
                "name": "供应商付款金额Top10",
                "item_type": "chart",
                "table_name": "dwd_fin_payment",
                "fields": [],
                "where_conditions": [],
                "group_by": [],
                "order_by": [],
                "limit": 10,
                "display_config": {
                    "title": "供应商付款金额 Top 10",
                    "chart_type": "bar",
                },
                "custom_sql": (
                    "SELECT s.supplier_name AS supplier_name, "
                    "       ROUND(SUM(p.amount), 2) AS total_paid "
                    "FROM dwd_fin_payment p "
                    "JOIN dim_supplier s ON p.supplier_id = s.supplier_id "
                    "WHERE p.payment_type = 'payment' "
                    "GROUP BY s.supplier_name ORDER BY total_paid DESC LIMIT 10"
                ),
            },
            {
                "name": "付款方式分布",
                "item_type": "chart",
                "table_name": "dwd_fin_payment",
                "fields": [],
                "where_conditions": [{"field": "payment_type", "operator": "=", "value": "payment"}],
                "group_by": ["payment_method"],
                "order_by": [{"field": "total", "direction": "DESC"}],
                "limit": 100,
                "display_config": {
                    "title": "付款方式分布（金额）",
                    "chart_type": "doughnut",
                },
                "custom_sql": (
                    "SELECT payment_method, "
                    "       COUNT(*) AS cnt, "
                    "       ROUND(SUM(amount), 2) AS total "
                    "FROM dwd_fin_payment WHERE payment_type = 'payment' "
                    "GROUP BY payment_method ORDER BY total DESC"
                ),
            },
            {
                "name": "供应商列表",
                "item_type": "table",
                "table_name": "dim_supplier",
                "fields": [
                    "supplier_code",
                    "supplier_name",
                    "supplier_type",
                    "category",
                    "region",
                    "payment_terms",
                    "credit_limit",
                    "status",
                ],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "supplier_code", "direction": "ASC"}],
                "limit": 100,
                "display_config": {"title": "供应商主数据"},
                "custom_sql": None,
            },
            {
                "name": "应付账龄明细",
                "item_type": "table",
                "table_name": "dwd_fin_ap_balance",
                "fields": [
                    "supplier_id",
                    "orig_amount",
                    "paid_amount",
                    "balance",
                    "due_date",
                    "aging_bucket",
                    "status",
                ],
                "where_conditions": [],
                "group_by": [],
                "order_by": [{"field": "balance", "direction": "DESC"}],
                "limit": 20,
                "display_config": {"title": "应付账款明细 Top 20"},
                "custom_sql": None,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed(keep_existing: bool = False) -> None:
    conn = sqlite3.connect(META_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    if not keep_existing:
        deleted = cur.execute("DELETE FROM reports").rowcount
        print(f"deleted {deleted} existing reports (cascaded items)")

    for r in REPORTS:
        cur.execute(
            """INSERT INTO reports
               (name, description, data_source_id, layout_config,
                is_scheduled, cron_expression, schedule_description,
                output_formats, is_active)
               VALUES (?, ?, ?, ?, 0, NULL, NULL, ?, 1)""",
            (
                r["name"],
                r["description"],
                DATA_SOURCE_ID,
                _dump({}),
                _dump(["excel", "html"]),
            ),
        )
        report_id = cur.lastrowid

        for idx, item in enumerate(r["items"]):
            cur.execute(
                """INSERT INTO report_items
                   (report_id, name, item_type, order_index, table_name,
                    fields, where_conditions, group_by, order_by, "limit",
                    display_config, custom_sql)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id,
                    item["name"],
                    item["item_type"],
                    idx,
                    item["table_name"],
                    _dump(item["fields"]),
                    _dump(item["where_conditions"]),
                    _dump(item["group_by"]),
                    _dump(item["order_by"]),
                    item["limit"],
                    _dump(item["display_config"]),
                    item["custom_sql"],
                ),
            )
        print(f"  + report '{r['name']}' (id={report_id}, {len(r['items'])} items)")

    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="don't delete existing reports before insert",
    )
    args = parser.parse_args()
    seed(keep_existing=args.keep_existing)
    print(f"\nseeded {META_DB}")


if __name__ == "__main__":
    main()