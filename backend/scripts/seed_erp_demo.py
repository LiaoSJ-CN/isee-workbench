"""Seed a persistent SQLite database with finance-domain warehouse tables.

Output: backend/data/erp_demo.db

Layers:
- DIM  (dimension tables)
- DWD  (detail / transactional)
- DWS  (summary)
- ADS  (application / mart)

Usage:
    python scripts/seed_erp_demo.py
    python scripts/seed_erp_demo.py --reset   # drop & recreate
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "erp_demo.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL = [
    # ---- DIM ----
    """
    CREATE TABLE dim_supplier (
        supplier_id      INTEGER PRIMARY KEY,
        supplier_code    TEXT NOT NULL UNIQUE,
        supplier_name    TEXT NOT NULL,
        supplier_type    TEXT,                -- 原材料 / 服务 / 设备 / 外包
        category         TEXT,                -- 制造业 / 物流 / IT ...
        region           TEXT,                -- 华东 / 华南 / 华北 / 西部
        contact_person   TEXT,
        contact_phone    TEXT,
        tax_no           TEXT,                -- 税号
        bank_name        TEXT,
        bank_account     TEXT,
        payment_terms    TEXT,                -- 货到付款 / 月结30天 / 月结60天
        credit_limit     DECIMAL(18,2),
        status           TEXT DEFAULT 'active',
        created_date     DATE,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE dim_customer (
        customer_id      INTEGER PRIMARY KEY,
        customer_code    TEXT NOT NULL UNIQUE,
        customer_name    TEXT NOT NULL,
        customer_type    TEXT,                -- 企业 / 个人 / 政府
        industry         TEXT,
        region           TEXT,
        contact_person   TEXT,
        contact_phone    TEXT,
        tax_no           TEXT,
        credit_rating    TEXT,                -- AAA / AA / A / BBB / BB
        credit_limit     DECIMAL(18,2),
        payment_terms    TEXT,
        status           TEXT DEFAULT 'active',
        created_date     DATE,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE dim_account (
        account_id       INTEGER PRIMARY KEY,
        account_code     TEXT NOT NULL UNIQUE,
        account_name     TEXT NOT NULL,
        account_type     TEXT,                -- 资产 / 负债 / 权益 / 收入 / 成本
        parent_code      TEXT,
        level            INTEGER,
        direction        TEXT,                -- 借 / 贷
        is_active        INTEGER DEFAULT 1
    )
    """,
    """
    CREATE TABLE dim_cost_center (
        cost_center_id   INTEGER PRIMARY KEY,
        cost_center_code TEXT NOT NULL UNIQUE,
        cost_center_name TEXT NOT NULL,
        department_id    INTEGER,
        manager          TEXT,
        is_active        INTEGER DEFAULT 1
    )
    """,
    """
    CREATE TABLE dim_department (
        department_id    INTEGER PRIMARY KEY,
        department_code  TEXT NOT NULL UNIQUE,
        department_name  TEXT NOT NULL,
        parent_id        INTEGER,
        manager          TEXT
    )
    """,
    # ---- DWD ----
    """
    CREATE TABLE dwd_fin_voucher (
        voucher_id       INTEGER PRIMARY KEY,
        voucher_no       TEXT NOT NULL UNIQUE,
        voucher_date     DATE NOT NULL,
        fiscal_year      INTEGER,
        fiscal_period    INTEGER,
        voucher_type     TEXT,                -- 收 / 付 / 转
        summary          TEXT,
        total_debit      DECIMAL(18,2),
        total_credit     DECIMAL(18,2),
        prepared_by      TEXT,
        reviewed_by      TEXT,
        posted_by        TEXT,
        status           TEXT,                -- draft / posted / reversed
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE dwd_fin_voucher_line (
        line_id          INTEGER PRIMARY KEY,
        voucher_id       INTEGER NOT NULL,
        line_no          INTEGER,
        account_id       INTEGER NOT NULL,
        cost_center_id   INTEGER,
        department_id    INTEGER,
        supplier_id      INTEGER,
        customer_id      INTEGER,
        summary          TEXT,
        debit_amount     DECIMAL(18,2) DEFAULT 0,
        credit_amount    DECIMAL(18,2) DEFAULT 0,
        currency         TEXT DEFAULT 'CNY',
        exchange_rate    DECIMAL(10,4) DEFAULT 1.0
    )
    """,
    """
    CREATE TABLE dwd_fin_payment (
        payment_id       INTEGER PRIMARY KEY,
        payment_no       TEXT NOT NULL UNIQUE,
        payment_date     DATE NOT NULL,
        payment_type     TEXT,                -- receipt(收款) / payment(付款)
        party_type       TEXT,                -- customer / supplier
        customer_id      INTEGER,
        supplier_id      INTEGER,
        account_id       INTEGER,             -- 资金科目（银行存款/现金）
        amount           DECIMAL(18,2) NOT NULL,
        payment_method   TEXT,                -- cash / transfer / check / wire
        bank_account     TEXT,
        reference_no     TEXT,                -- 关联单据号
        summary          TEXT,
        status           TEXT,                -- success / pending / failed
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE dwd_fin_invoice (
        invoice_id       INTEGER PRIMARY KEY,
        invoice_no       TEXT NOT NULL UNIQUE,
        invoice_date     DATE NOT NULL,
        invoice_type     TEXT,                -- sales(销项) / purchase(进项)
        party_type       TEXT,
        customer_id      INTEGER,
        supplier_id      INTEGER,
        amount_excl_tax  DECIMAL(18,2),
        tax_amount       DECIMAL(18,2),
        amount_incl_tax  DECIMAL(18,2),
        tax_rate         DECIMAL(5,2),
        summary          TEXT,
        status           TEXT                 -- issued / cancelled / verified
    )
    """,
    """
    CREATE TABLE dwd_fin_ar_balance (
        ar_id            INTEGER PRIMARY KEY,
        customer_id      INTEGER NOT NULL,
        invoice_id       INTEGER,
        orig_amount      DECIMAL(18,2),
        paid_amount      DECIMAL(18,2) DEFAULT 0,
        balance          DECIMAL(18,2),
        issue_date       DATE,
        due_date         DATE,
        aging_bucket     TEXT,                -- 30天内 / 31-60天 / 61-90天 / 90天以上
        status           TEXT                 -- open / closed / overdue
    )
    """,
    """
    CREATE TABLE dwd_fin_ap_balance (
        ap_id            INTEGER PRIMARY KEY,
        supplier_id      INTEGER NOT NULL,
        invoice_id       INTEGER,
        orig_amount      DECIMAL(18,2),
        paid_amount      DECIMAL(18,2) DEFAULT 0,
        balance          DECIMAL(18,2),
        issue_date       DATE,
        due_date         DATE,
        aging_bucket     TEXT,
        status           TEXT
    )
    """,
    # ---- DWS ----
    """
    CREATE TABLE dws_fin_ar_aging (
        aging_id         INTEGER PRIMARY KEY,
        period_date      DATE,
        customer_id      INTEGER,
        amount_30d       DECIMAL(18,2) DEFAULT 0,
        amount_31_60d    DECIMAL(18,2) DEFAULT 0,
        amount_61_90d    DECIMAL(18,2) DEFAULT 0,
        amount_over_90d  DECIMAL(18,2) DEFAULT 0,
        total_balance    DECIMAL(18,2)
    )
    """,
    """
    CREATE TABLE dws_fin_ap_aging (
        aging_id         INTEGER PRIMARY KEY,
        period_date      DATE,
        supplier_id      INTEGER,
        amount_30d       DECIMAL(18,2) DEFAULT 0,
        amount_31_60d    DECIMAL(18,2) DEFAULT 0,
        amount_61_90d    DECIMAL(18,2) DEFAULT 0,
        amount_over_90d  DECIMAL(18,2) DEFAULT 0,
        total_balance    DECIMAL(18,2)
    )
    """,
    # ---- ADS ----
    """
    CREATE TABLE ads_fin_cashflow_monthly (
        record_id        INTEGER PRIMARY KEY,
        year_month       TEXT,                -- 2025-01
        inflow           DECIMAL(18,2),      -- 流入
        outflow          DECIMAL(18,2),      -- 流出
        net_flow         DECIMAL(18,2),      -- 净流入
        ending_balance   DECIMAL(18,2)
    )
    """,
    """
    CREATE TABLE ads_fin_pl_monthly (
        record_id        INTEGER PRIMARY KEY,
        year_month       TEXT,
        revenue          DECIMAL(18,2),
        cost             DECIMAL(18,2),
        expense          DECIMAL(18,2),
        operating_profit DECIMAL(18,2),
        net_profit       DECIMAL(18,2)
    )
    """,
    # ---- indexes ----
    "CREATE INDEX idx_voucher_date         ON dwd_fin_voucher(voucher_date)",
    "CREATE INDEX idx_voucher_line_voucher ON dwd_fin_voucher_line(voucher_id)",
    "CREATE INDEX idx_payment_date         ON dwd_fin_payment(payment_date)",
    "CREATE INDEX idx_invoice_date         ON dwd_fin_invoice(invoice_date)",
    "CREATE INDEX idx_ar_customer          ON dwd_fin_ar_balance(customer_id)",
    "CREATE INDEX idx_ap_supplier          ON dwd_fin_ap_balance(supplier_id)",
]

# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

SUPPLIERS = [
    ("SUP001", "上海钢铁集团有限公司",     "原材料", "制造业", "华东", "张建国", "021-58881111", "91310115MA1K0XYZ12", "工商银行上海分行", "6222001234567890", "月结30天", 500000.00),
    ("SUP002", "深圳市华信电子科技",     "原材料", "电子",   "华南", "李海涛", "0755-26661234", "91440300MA5G8ABC34", "招商银行深圳分行", "6225887654321098", "月结60天", 300000.00),
    ("SUP003", "北京神州物流有限公司",     "服务",   "物流",   "华北", "王晓东", "010-82334455", "91110108MA019XYZ78", "建设银行北京分行", "6217002234560098", "货到付款", 200000.00),
    ("SUP004", "广州白云包装制品厂",       "原材料", "制造业", "华南", "陈丽华", "020-87775566", "91440101MA59UPQR56", "中国银行广州分行", "6217856000123456", "月结30天", 150000.00),
    ("SUP005", "杭州云栖信息技术服务",     "服务",   "IT",     "华东", "赵敏",   "0571-89998888", "91330106MA28TYZ910", "浦发银行杭州分行", "6225210098765432", "月结30天", 250000.00),
    ("SUP006", "成都西部机械制造",        "设备",   "制造业", "西部", "刘大壮", "028-86667788", "91510104MA6CPQR234", "交通银行成都分行", "6222621112233445", "月结60天", 800000.00),
    ("SUP007", "苏州精工精密部件",        "原材料", "制造业", "华东", "周文斌", "0512-65554433", "91320594MA1NCDE567", "中信银行苏州分行", "6226908800116677", "月结30天", 400000.00),
    ("SUP008", "厦门海联进出口贸易",      "外包",   "贸易",   "华南", "黄志强", "0592-21212121", "91350200MA31UVWX89", "兴业银行厦门分行", "6229083344556677", "月结90天", 600000.00),
]

CUSTOMERS = [
    ("CUS001", "北京北方电气股份",     "企业", "电气",       "华北", "刘工",   "010-65550000", "91110000MA00A001XX", "AAA", 1000000.00, "月结60天"),
    ("CUS002", "上海浦东建设集团",     "企业", "建筑",       "华东", "陈经理", "021-68888000", "91310115MA00B002YY", "AA",  800000.00,  "月结30天"),
    ("CUS003", "广州南方汽车销售",     "企业", "汽车",       "华南", "吴总",   "020-83330000", "91440101MA00C003ZZ", "A",   500000.00,  "月结30天"),
    ("CUS004", "深圳科创电子",         "企业", "电子",       "华南", "钱工",   "0755-26665000", "91440300MA00D004WW", "AA",  600000.00,  "月结60天"),
    ("CUS005", "杭州西子实业",         "企业", "制造业",     "华东", "孙经理", "0571-87000000", "91330106MA00E005VV", "AAA", 1200000.00, "月结90天"),
    ("CUS006", "成都西部能源",         "企业", "能源",       "西部", "李工",   "028-86666000", "91510104MA00F006UU", "BBB", 300000.00,  "货到付款"),
    ("CUS007", "武汉光谷光电",         "企业", "光电",       "华中", "郑总",   "027-87888000", "91420100MA00G007TT", "A",   450000.00,  "月结30天"),
    ("CUS008", "南京金陵机械",         "企业", "机械",       "华东", "冯经理", "025-84445000", "91320100MA00H008SS", "AA",  550000.00,  "月结60天"),
    ("CUS009", "西安古城文化传媒",     "企业", "传媒",       "西部", "韩总",   "029-85555000", "91610100MA00I009RR", "BB",  150000.00,  "月结30天"),
    ("CUS010", "青岛海尔工业园",       "企业", "家电",       "华北", "杜工",   "0532-85858000", "91370200MA00J010QQ", "AAA", 2000000.00, "月结90天"),
    ("CUS011", "大连港务局采购中心",   "政府", "港口",       "华北", "杨处",   "0411-82222000", "91210200MA00K011PP", "AAA", 1500000.00, "月结60天"),
    ("CUS012", "天津滨海物流公司",     "企业", "物流",       "华北", "马经理", "022-23333000", "91120100MA00L012OO", "A",   400000.00,  "月结30天"),
    ("CUS013", "厦门海沧台商投资区",   "企业", "综合",       "华南", "蔡董",   "0592-21212000", "91350200MA00M013NN", "AA",  900000.00,  "月结60天"),
    ("CUS014", "郑州铁路局物资公司",   "政府", "铁路",       "华中", "宋处",   "0371-67777000", "91410100MA00N014MM", "AAA", 1800000.00, "月结90天"),
    ("CUS015", "长沙三一重工采购部",   "企业", "重工",       "华中", "蒋经理", "0731-84444000", "91430100MA00O015LL", "AA",  700000.00,  "月结60天"),
]

# 常用科目（参考中国企业会计准则）
ACCOUNTS = [
    # 资产类
    ("1001", "库存现金",       "资产", "",      1, "借"),
    ("1002", "银行存款",       "资产", "",      1, "借"),
    ("1012", "其他货币资金",   "资产", "",      1, "借"),
    ("1121", "应收账款",       "资产", "",      1, "借"),
    ("1122", "预付账款",       "资产", "",      1, "借"),
    ("1221", "其他应收款",     "资产", "",      1, "借"),
    ("1401", "材料采购",       "资产", "",      1, "借"),
    ("1403", "原材料",         "资产", "",      1, "借"),
    ("1405", "库存商品",       "资产", "",      1, "借"),
    ("1601", "固定资产",       "资产", "",      1, "借"),
    ("1701", "无形资产",       "资产", "",      1, "借"),
    # 负债类
    ("2001", "短期借款",       "负债", "",      1, "贷"),
    ("2201", "应付票据",       "负债", "",      1, "贷"),
    ("2202", "应付账款",       "负债", "",      1, "贷"),
    ("2203", "预收账款",       "负债", "",      1, "贷"),
    ("2211", "应付职工薪酬",   "负债", "",      1, "贷"),
    ("2221", "应交税费",       "负债", "",      1, "贷"),
    # 权益类
    ("4001", "实收资本",       "权益", "",      1, "贷"),
    ("4002", "资本公积",       "权益", "",      1, "贷"),
    ("4101", "盈余公积",       "权益", "",      1, "贷"),
    # 损益类
    ("5001", "主营业务收入",   "收入", "",      1, "贷"),
    ("5051", "其他业务收入",   "收入", "",      1, "贷"),
    ("5401", "主营业务成本",   "成本", "",      1, "借"),
    ("5402", "其他业务成本",   "成本", "",      1, "借"),
    ("5501", "销售费用",       "成本", "",      1, "借"),
    ("5502", "管理费用",       "成本", "",      1, "借"),
    ("5503", "财务费用",       "成本", "",      1, "借"),
]

DEPARTMENTS = [
    ("D001", "财务部",     None,  "周慧"),
    ("D002", "采购部",     None,  "吴磊"),
    ("D003", "销售部",     None,  "郑涛"),
    ("D004", "生产部",     None,  "钱进"),
    ("D005", "仓储部",     None,  "冯波"),
    ("D006", "研发部",     None,  "蒋帆"),
    ("D007", "人力资源部", None,  "韩梅"),
    ("D008", "行政部",     None,  "杨柳"),
]

COST_CENTERS = [
    ("CC001", "总部管理",     1, "周慧"),
    ("CC002", "采购中心",     2, "吴磊"),
    ("CC003", "销售一区",     3, "郑涛"),
    ("CC004", "销售二区",     3, "郑涛"),
    ("CC005", "生产一线",     4, "钱进"),
    ("CC006", "生产二线",     4, "钱进"),
    ("CC007", "仓储管理",     5, "冯波"),
    ("CC008", "研发项目组",   6, "蒋帆"),
    ("CC009", "人力行政",     7, "韩梅"),
    ("CC010", "市场推广",     3, "郑涛"),
]

VOUCHER_TYPES = ["收", "付", "转"]
PREPARED_BY = ["周慧", "李娜", "张敏"]
REVIEWED_BY = ["王芳", "陈刚"]


def rand_money(lo: float, hi: float) -> float:
    return round(random.uniform(lo, hi), 2)


def rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def aging_bucket(due: date, ref: date | None = None) -> str:
    ref = ref or date.today()
    days = (ref - due).days
    if days <= 30:
        return "30天内"
    if days <= 60:
        return "31-60天"
    if days <= 90:
        return "61-90天"
    return "90天以上"


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # ---- DIM: supplier ----
    cur.executemany(
        """INSERT INTO dim_supplier
           (supplier_id, supplier_code, supplier_name, supplier_type, category,
            region, contact_person, contact_phone, tax_no, bank_name,
            bank_account, payment_terms, credit_limit, status, created_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (i + 1, code, name, stype, cat, region, contact, phone, tax, bank, acct, terms, limit_, "active",
             rand_date(date(2023, 1, 1), date(2024, 6, 1)).isoformat())
            for i, (code, name, stype, cat, region, contact, phone, tax, bank, acct, terms, limit_) in enumerate(SUPPLIERS)
        ],
    )

    # ---- DIM: customer ----
    cur.executemany(
        """INSERT INTO dim_customer
           (customer_id, customer_code, customer_name, customer_type, industry,
            region, contact_person, contact_phone, tax_no, credit_rating,
            credit_limit, payment_terms, status, created_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (i + 1, code, name, ctype, ind, region, contact, phone, tax, rating, limit_, terms, "active",
             rand_date(date(2023, 1, 1), date(2024, 6, 1)).isoformat())
            for i, (code, name, ctype, ind, region, contact, phone, tax, rating, limit_, terms) in enumerate(CUSTOMERS)
        ],
    )

    # ---- DIM: account ----
    cur.executemany(
        """INSERT INTO dim_account
           (account_id, account_code, account_name, account_type, parent_code, level, direction, is_active)
           VALUES (?,?,?,?,?,?,?,1)""",
        [(i + 1, code, name, atype, parent, lvl, direction)
         for i, (code, name, atype, parent, lvl, direction) in enumerate(ACCOUNTS)],
    )

    # ---- DIM: department ----
    cur.executemany(
        """INSERT INTO dim_department
           (department_id, department_code, department_name, parent_id, manager)
           VALUES (?,?,?,?,?)""",
        [(i + 1, code, name, parent, mgr)
         for i, (code, name, parent, mgr) in enumerate(DEPARTMENTS)],
    )

    # ---- DIM: cost_center ----
    cur.executemany(
        """INSERT INTO dim_cost_center
           (cost_center_id, cost_center_code, cost_center_name, department_id, manager, is_active)
           VALUES (?,?,?,?,?,1)""",
        [(i + 1, code, name, dept, mgr)
         for i, (code, name, dept, mgr) in enumerate(COST_CENTERS)],
    )

    # ---- DWD: voucher + lines ----
    voucher_id = 0
    line_id = 0
    vouchers = []
    lines = []
    for i in range(40):
        voucher_id += 1
        vdate = rand_date(date(2024, 7, 1), date(2025, 12, 31))
        vtype = random.choice(VOUCHER_TYPES)
        # 2-3 行
        n_lines = random.randint(2, 3)
        total_debit = 0.0
        total_credit = 0.0
        for _ in range(n_lines):
            line_id += 1
            amount = rand_money(1000, 80000)
            is_debit = random.random() < 0.5
            debit = amount if is_debit else 0.0
            credit = amount if not is_debit else 0.0
            total_debit += debit
            total_credit += credit
            acc_id = random.randint(1, len(ACCOUNTS))
            cost_id = random.randint(1, len(COST_CENTERS))
            dept_id = random.randint(1, len(DEPARTMENTS))
            sup_id = random.randint(1, len(SUPPLIERS)) if random.random() < 0.4 else None
            cus_id = random.randint(1, len(CUSTOMERS)) if random.random() < 0.4 else None
            lines.append((
                line_id, voucher_id, random.randint(1, n_lines),
                acc_id, cost_id, dept_id, sup_id, cus_id,
                random.choice(["采购材料", "支付货款", "收到货款", "报销差旅", "计提工资", "领用材料", "销售商品", "结转成本"]),
                debit, credit, "CNY", 1.0,
            ))
        # 借方 = 贷方 (尾差调整)
        if abs(total_debit - total_credit) > 0.01:
            adj = abs(total_debit - total_credit)
            line_id += 1
            if total_debit > total_credit:
                lines.append((line_id, voucher_id, n_lines + 1, random.randint(1, len(ACCOUNTS)),
                              random.randint(1, len(COST_CENTERS)), random.randint(1, len(DEPARTMENTS)),
                              None, None, "尾差调整", 0.0, adj, "CNY", 1.0))
                total_credit += adj
            else:
                lines.append((line_id, voucher_id, n_lines + 1, random.randint(1, len(ACCOUNTS)),
                              random.randint(1, len(COST_CENTERS)), random.randint(1, len(DEPARTMENTS)),
                              None, None, "尾差调整", adj, 0.0, "CNY", 1.0))
                total_debit += adj
        vouchers.append((
            voucher_id,
            f"PZ-{vdate.strftime('%Y%m')}-{voucher_id:04d}",
            vdate.isoformat(),
            vdate.year,
            vdate.month,
            vtype,
            random.choice(["采购付款", "销售收款", "费用报销", "工资发放", "材料领用", "结转损益"]),
            round(total_debit, 2),
            round(total_credit, 2),
            random.choice(PREPARED_BY),
            random.choice(REVIEWED_BY),
            random.choice(REVIEWED_BY),
            random.choice(["posted", "posted", "posted", "draft"]),
        ))

    cur.executemany(
        """INSERT INTO dwd_fin_voucher
           (voucher_id, voucher_no, voucher_date, fiscal_year, fiscal_period,
            voucher_type, summary, total_debit, total_credit,
            prepared_by, reviewed_by, posted_by, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        vouchers,
    )
    cur.executemany(
        """INSERT INTO dwd_fin_voucher_line
           (line_id, voucher_id, line_no, account_id, cost_center_id,
            department_id, supplier_id, customer_id, summary,
            debit_amount, credit_amount, currency, exchange_rate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        lines,
    )

    # ---- DWD: payment ----
    payments = []
    for i in range(50):
        ptype = random.choice(["receipt", "payment"])
        party = "customer" if ptype == "receipt" else "supplier"
        pdate = rand_date(date(2024, 7, 1), date(2025, 12, 31))
        amount = rand_money(2000, 200000)
        party_id = random.randint(1, len(CUSTOMERS)) if party == "customer" else random.randint(1, len(SUPPLIERS))
        payments.append((
            i + 1,
            f"PAY-{pdate.strftime('%Y%m')}-{i+1:04d}",
            pdate.isoformat(),
            ptype,
            party,
            party_id if party == "customer" else None,
            party_id if party == "supplier" else None,
            random.choice([2, 3]),  # 银行存款 / 其他货币资金
            amount,
            random.choice(["transfer", "transfer", "wire", "check"]),
            f"622588{random.randint(10000000, 99999999)}",
            f"REF-{random.randint(100000, 999999)}",
            random.choice(["销售回款", "采购付款", "服务费支付", "收到预付款", "退款"]),
            random.choice(["success", "success", "success", "pending"]),
        ))
    cur.executemany(
        """INSERT INTO dwd_fin_payment
           (payment_id, payment_no, payment_date, payment_type, party_type,
            customer_id, supplier_id, account_id, amount, payment_method,
            bank_account, reference_no, summary, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        payments,
    )

    # ---- DWD: invoice ----
    invoices = []
    for i in range(40):
        itype = random.choice(["sales", "purchase"])
        party_type = "customer" if itype == "sales" else "supplier"
        idate = rand_date(date(2024, 7, 1), date(2025, 12, 31))
        excl = rand_money(5000, 300000)
        rate = random.choice([0.13, 0.09, 0.06, 0.03])
        tax = round(excl * rate, 2)
        incl = round(excl + tax, 2)
        party_id = random.randint(1, len(CUSTOMERS)) if party_type == "customer" else random.randint(1, len(SUPPLIERS))
        invoices.append((
            i + 1,
            f"INV-{idate.strftime('%Y%m')}-{i+1:05d}",
            idate.isoformat(),
            itype,
            party_type,
            party_id if party_type == "customer" else None,
            party_id if party_type == "supplier" else None,
            excl, tax, incl, rate,
            random.choice(["销售商品", "提供服务", "采购原料", "采购设备", "采购服务"]),
            random.choice(["verified", "verified", "issued", "cancelled"]),
        ))
    cur.executemany(
        """INSERT INTO dwd_fin_invoice
           (invoice_id, invoice_no, invoice_date, invoice_type, party_type,
            customer_id, supplier_id, amount_excl_tax, tax_amount,
            amount_incl_tax, tax_rate, summary, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        invoices,
    )

    # ---- DWD: ar_balance / ap_balance ----
    today = date(2025, 12, 31)
    ar_rows = []
    for i in range(30):
        cid = random.randint(1, len(CUSTOMERS))
        issue = rand_date(date(2024, 1, 1), date(2025, 11, 30))
        due = issue + timedelta(days=random.choice([30, 60, 90]))
        orig = rand_money(10000, 500000)
        paid = rand_money(0, orig * 0.7) if random.random() < 0.6 else 0.0
        balance = round(orig - paid, 2)
        ar_rows.append((
            i + 1, cid, random.randint(1, len(invoices)),
            orig, round(paid, 2), balance,
            issue.isoformat(), due.isoformat(),
            aging_bucket(due, today),
            "open" if balance > 0 else "closed",
        ))
    cur.executemany(
        """INSERT INTO dwd_fin_ar_balance
           (ar_id, customer_id, invoice_id, orig_amount, paid_amount, balance,
            issue_date, due_date, aging_bucket, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ar_rows,
    )

    ap_rows = []
    for i in range(25):
        sid = random.randint(1, len(SUPPLIERS))
        issue = rand_date(date(2024, 1, 1), date(2025, 11, 30))
        due = issue + timedelta(days=random.choice([30, 60, 90]))
        orig = rand_money(10000, 300000)
        paid = rand_money(0, orig * 0.5) if random.random() < 0.7 else 0.0
        balance = round(orig - paid, 2)
        ap_rows.append((
            i + 1, sid, random.randint(1, len(invoices)),
            orig, round(paid, 2), balance,
            issue.isoformat(), due.isoformat(),
            aging_bucket(due, today),
            "open" if balance > 0 else "closed",
        ))
    cur.executemany(
        """INSERT INTO dwd_fin_ap_balance
           (ap_id, supplier_id, invoice_id, orig_amount, paid_amount, balance,
            issue_date, due_date, aging_bucket, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ap_rows,
    )

    # ---- DWS: ar_aging / ap_aging (按月汇总过去 18 个月) ----
    ar_agg: dict[tuple[str, int], list[float]] = {}
    for ar in ar_rows:
        _, cid, _, _, _, bal, issue, due, bucket, _ = ar
        period = issue[:7]  # YYYY-MM
        key = (period, cid)
        if key not in ar_agg:
            ar_agg[key] = [0.0, 0.0, 0.0, 0.0]
        if bucket == "30天内":    ar_agg[key][0] += bal
        elif bucket == "31-60天": ar_agg[key][1] += bal
        elif bucket == "61-90天": ar_agg[key][2] += bal
        else:                     ar_agg[key][3] += bal

    ar_aging_rows = []
    aid = 0
    for (period, cid), amts in ar_agg.items():
        aid += 1
        ar_aging_rows.append((
            aid, f"{period}-01", cid,
            round(amts[0], 2), round(amts[1], 2), round(amts[2], 2), round(amts[3], 2),
            round(sum(amts), 2),
        ))
    cur.executemany(
        """INSERT INTO dws_fin_ar_aging
           (aging_id, period_date, customer_id, amount_30d, amount_31_60d,
            amount_61_90d, amount_over_90d, total_balance)
           VALUES (?,?,?,?,?,?,?,?)""",
        ar_aging_rows,
    )

    ap_agg: dict[tuple[str, int], list[float]] = {}
    for ap in ap_rows:
        _, sid, _, _, _, bal, issue, due, bucket, _ = ap
        period = issue[:7]
        key = (period, sid)
        if key not in ap_agg:
            ap_agg[key] = [0.0, 0.0, 0.0, 0.0]
        if bucket == "30天内":    ap_agg[key][0] += bal
        elif bucket == "31-60天": ap_agg[key][1] += bal
        elif bucket == "61-90天": ap_agg[key][2] += bal
        else:                     ap_agg[key][3] += bal

    ap_aging_rows = []
    apid = 0
    for (period, sid), amts in ap_agg.items():
        apid += 1
        ap_aging_rows.append((
            apid, f"{period}-01", sid,
            round(amts[0], 2), round(amts[1], 2), round(amts[2], 2), round(amts[3], 2),
            round(sum(amts), 2),
        ))
    cur.executemany(
        """INSERT INTO dws_fin_ap_aging
           (aging_id, period_date, supplier_id, amount_30d, amount_31_60d,
            amount_61_90d, amount_over_90d, total_balance)
           VALUES (?,?,?,?,?,?,?,?)""",
        ap_aging_rows,
    )

    # ---- ADS: cashflow_monthly / pl_monthly ----
    months = []
    for y, m in [(2024, m) for m in range(7, 13)] + [(2025, m) for m in range(1, 13)]:
        months.append(f"{y}-{m:02d}")

    cf_rows = []
    cfid = 0
    balance = 5000000.0
    for ym in months:
        cfid += 1
        inflow = rand_money(800000, 3000000)
        outflow = rand_money(600000, 2800000)
        net = round(inflow - outflow, 2)
        balance = round(balance + net, 2)
        cf_rows.append((cfid, ym, round(inflow, 2), round(outflow, 2), net, balance))
    cur.executemany(
        """INSERT INTO ads_fin_cashflow_monthly
           (record_id, year_month, inflow, outflow, net_flow, ending_balance)
           VALUES (?,?,?,?,?,?)""",
        cf_rows,
    )

    pl_rows = []
    plid = 0
    for ym in months:
        plid += 1
        revenue = rand_money(1500000, 5000000)
        cost = round(revenue * random.uniform(0.55, 0.7), 2)
        expense = round(revenue * random.uniform(0.08, 0.15), 2)
        op = round(revenue - cost - expense, 2)
        net = round(op - rand_money(50000, 200000), 2)
        pl_rows.append((plid, ym, round(revenue, 2), cost, expense, op, net))
    cur.executemany(
        """INSERT INTO ads_fin_pl_monthly
           (record_id, year_month, revenue, cost, expense, operating_profit, net_profit)
           VALUES (?,?,?,?,?,?,?)""",
        pl_rows,
    )

    conn.commit()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop existing tables first")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"removed existing {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()
        if args.reset:
            for (name,) in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall():
                cur.execute(f"DROP TABLE IF EXISTS [{name}]")
        for ddl in DDL:
            cur.execute(ddl)
        seed(conn)

        tables = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print(f"\nseeded {DB_PATH}")
        print(f"  size: {DB_PATH.stat().st_size:,} bytes")
        print(f"  tables:")
        for (t,) in tables:
            n = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            print(f"    {t:<28} {n:>5} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    main()