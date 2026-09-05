# iSee 工作台 — 设计模式与架构决策

本文档记录项目核心设计模式、架构决策及其 tradeoff 考量，供后续开发和维护参考。

## 架构全景

```
请求                                         响应
  │                                            ▲
  ▼                                            │
┌──────────────────────────────────────────────────┐
│  Middleware Layer (洋葱模型)                       │
│  CORS → ProxyHeaders → SecurityHeaders            │
├──────────────────────────────────────────────────┤
│  Router Layer (FastAPI APIRouter)                 │
│  auth / data_source / report / scheduler /        │
│  explorer                                         │
├──────────────────────────────────────────────────┤
│  Service Layer                                    │
│  connection / report_generator / scheduler /      │
│  sql_validator / ssrf_guard / jwt_auth /          │
│  auth_state / password                            │
├──────────────────────────────────────────────────┤
│  Data Layer                                       │
│  SQLAlchemy ORM + Pydantic Schema                 │
└──────────────────────────────────────────────────┘
```

---

## 1. 分层架构

三层严格分离，依赖方向自上而下：Router → Service → Data。

### 目录映射

| 层 | 目录 | 职责 |
|----|------|------|
| 路由 | `app/routers/` | HTTP 请求处理、参数校验、调用 Service、构造响应（9 个：auth / data_source / report / jobs / scheduler / explorer / subscription / audit） |
| 服务 | `app/services/` | 业务逻辑、SQL 构建、报表生成、JWT 签发、SSRF 防护、订阅 reconcile、job queue、parameter validator、schema introspection、audit 写入 |
| 数据 | `app/models/` + `app/schemas/` | ORM 映射、Pydantic 请求/响应校验 |
| 中间件 | `app/middleware/` | 请求预处理（CORS / ProxyHeaders / SecurityHeaders / RateLimit / CSRF / RequestID / Metrics / Sentry） |

### 关键入口

- `main.py:108` — lifespan 管理启动/关闭
- `deps.py:36` — 依赖注入工厂
- `database.py:54` — `get_db()` session 生成器

**设计权衡**：Router 层目前直接使用 SQLAlchemy Session 查询（不是通过 Repository 抽象）。当前项目规模下足够清晰，如果 DAO 逻辑变复杂可提取 Repository 层。

---

## 2. 依赖注入

FastAPI `Depends()` 实现，是贯穿全项目的核心模式。

### 认证依赖链

```
_bearer = HTTPBearer(auto_error=False)
       ↓
_credentials_from_request(request)      ← cookie → header 双通道
       ↓
get_current_user(request, db) → str    ← 返回 username（含 jti revoke 检查）
get_current_token(request) → str       ← 返回原始 token（logout 用）
```

### DB Session 管理

```python
# database.py — Generator 模式保证 finally close
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 路由使用方式

```python
@router.get("/reports")
def list_reports(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[str, Depends(get_current_user)],
):
```

### 前端镜像 — Axios Interceptor

```
请求 interceptor → 注入 Authorization header
响应 interceptor → 401? → refresh token → 重试（单次，防无限循环）
```

---

## 3. 单例模式 + 惰性初始化

### ReportScheduler（进程级单例）

```python
# services/scheduler.py
_scheduler: ReportScheduler | None = None

def get_scheduler() -> ReportScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ReportScheduler()
    return _scheduler
```

**设计意图**：全局唯一实例，避免多 worker 重复 tick。全项目通过 `get_scheduler()` 访问，不直接引用 `_scheduler`。

### Engine Cache（连接池缓存）

```python
# services/report_generator.py
_engine_cache: dict[int, Engine] = {}
_engine_cache_lock = threading.Lock()

def _get_or_create_engine(data_source: DataSource) -> Engine:
    # Double-checked locking
    cached = _engine_cache.get(id)       # 快速路径：无锁
    if cached: return cached
    with _engine_cache_lock:
        cached = _engine_cache.get(id)   # 慢路径：加锁二次检查
        if cached: return cached
        engine = create_engine(url, ...)
        _engine_cache[id] = engine
        return engine

def evict_engine(data_source_id: int) -> None:
    """DataSource 变更时调用，dispose 旧连接池并移除缓存"""
```

**设计意图**：同一 DataSource 的所有报表生成复用连接池，DataSource 更新/删除时显式 `evict_engine()` 失效。

---

## 4. Context Manager 模式

### ReportGenerator — 上下文管理器

```python
class ReportGenerator:
    def __enter__(self):
        self.engine = _get_or_create_engine(self.data_source)
        return self
    def __exit__(self, ...):
        pass  # 不 dispose — engine 是全局缓存的

# 使用
with ReportGenerator(data_source) as gen:
    df = gen.execute_query(query, params)
    html = gen.render_html(data, report)
```

### FastAPI Lifespan — async context manager

```python
@asynccontextmanager
async def lifespan(app):
    _configure_logging()                 # startup
    _seed_admin_user()
    if not scheduler_disabled:
        scheduler.start()
    yield                               # 运行
    scheduler.shutdown()                # shutdown
```

**设计意图**：启动/关闭逻辑与请求处理分离，避免 import 时副作用。

---

## 5. 中间件洋葱模型

注册顺序 = 执行顺序（外 → 内）：

```python
# main.py
app.add_middleware(CORSMiddleware)             # 最外层: 跨域
app.add_middleware(ProxyHeadersMiddleware)      # 中层: 真实 IP
app.add_middleware(SecurityHeadersMiddleware)   # 内层: 安全响应头
```

### ProxyHeadersMiddleware
- 信任反向代理的 `X-Forwarded-For`，重写 `request.client`
- 让下游 rate limiter 和日志看到真实 IP

### SecurityHeadersMiddleware
- 每次响应自动附加安全响应头（X-Content-Type-Options、X-Frame-Options、CSP 等）
- 不阻塞请求流，仅在响应阶段注入

---

## 6. JWT Token 生命周期管理

### 流转图

```
  login                    refresh                   logout
    │                         │                         │
    ▼                         ▼                         ▼
create_access_token     decode old refresh       get jti from access
create_refresh_token    revoke old jti            add to revoked_jti
set HttpOnly cookies    issue new pair            clear cookies
return TokenPair        set new cookies           return ok
```

### 关键决策

| 决策 | 实现 | 原因 |
|------|------|------|
| 双通道运输 | cookie (HttpOnly+SameSite) 主通道，Authorization header 备用 | SPA 自动携带 cookie；CLI/curl 用 header |
| Refresh Rotation | 每次 refresh 发新 jti，旧 jti 进 deny-list | 防 refresh token 重放（单次使用） |
| Logout 撤销 | access token jti 进 `revoked_jti` 表，每次请求检查 | 无状态 JWT 实现即时失效 |
| 密码哈希 | passlib bcrypt | 行业标准，防彩虹表 |
| 数据源密码 | Fernet 对称加密（静态存储） | 运行时解密在内存 |

### 关键模块

- `services/jwt_auth.py` — JWT 签发与校验
- `services/auth_state.py` — jti deny-list 管理
- `services/password.py` — bcrypt 哈希
- `deps.py` — `get_current_user` 含 jti revoke 检查

---

## 7. SQL 安全防线

### 三道防线

```
用户输入 (custom_sql / table_name / fields / where_conditions)
    │
    ▼
【防线1】sqlglot AST 解析 (sql_validator.py)
    — 多语句检测（; 分隔符）
    — 注释注入检测
    — 非 SELECT AST 节点拒绝
    — 标识符安全校验（is_safe_qualified_identifier）
    │
    ▼
【防线2】参数化查询 (report_generator.build_query)
    — WHERE: 运算符白名单 + 参数绑定
    — LIMIT: 整数校验 + 参数绑定
    — ORDER BY direction: ASC/DESC 白名单
    │
    ▼
【防线3】输出转义 (report_generator.render_html)
    — html.escape() 转义所有用户数据
    — iframe sandbox="allow-scripts"
    — blob-URL 加载（消除 token 泄漏）
```

**设计要点**：防线 1 使用 AST 而非正则，无法用字符串 trick 绕过。防线 2 的运算符白名单由 Explorer 和 Report 共享。防线 3 在前端不需要信任后端输出。

---

## 8. Pipeline 模式 — 报表生成

```
1. build_query(item, params)  →  (sql, bound_params)
         ↓
2. execute_query(sql, params) →  DataFrame
         ↓ (逐 item 执行，失败不中断)
3. render_html({ name: df }, report, errors) → HTML 字符串
         ↓
4. _safe_filename(report.name) → 安全文件名
         ↓
5. write to generated_reports/
```

**容错设计**：每个 item 独立处理，错误收集到 `item_errors`，在 HTML 中以红色横幅展示失败 item，其他 item 继续渲染。

| 输出格式 | 实现 | 场景 |
|----------|------|------|
| HTML | Chart.js 内嵌可视化 | 浏览器预览、在线分享 |
| Excel | openpyxl，多 sheet | 下载、邮件附件 |
| PDF | weasyprint 渲染 HTML（需 `libpango` / `libcairo` 系统依赖） | 邮件附件、归档 |

---

## 9. Reconcile 模式 — 调度器同步

`sync_with_database()` 是幂等的 reconcile 操作（非纯 add）：

```python
def sync_with_database(db: Session) -> None:
    db.expire_all()                     # 刷新旧缓存
    
    active = db.query(Report).filter(   # 查 DB 当前活跃报表
        is_scheduled & is_active & cron_expression.isnot(None)
    ).all()
    
    for report in active:
        add_report_job(...)             # 步骤1: 添加/更新（幂等）
    
    for job in scheduler.get_jobs():    # 步骤2: 清理孤儿 job
        if job.report_id not in active_ids:
            scheduler.remove_job(job)
```

### Sidecar 部署模型

```
 Web 进程 (SCHEDULER_DISABLED=true)    Sidecar 进程 (scheduler_runner.py)
 ┌─────────────────────────┐          ┌──────────────────────────┐
 │ 不启动 APScheduler       │          │ 独占 tick 循环             │
 │ /scheduler/* API 可用    │          │ 每 30s reconcile DB → job │
 │ 操作 DB + 元数据          │          │ SIGTERM → graceful stop   │
 └─────────────────────────┘          └──────────────────────────┘
         │                                       │
         └─────────────── 共享 DB ───────────────┘
```

**设计意图**：`gunicorn -w N` 下每 worker 独立跑 APScheduler 会导致同一 job 执行 N 次。Sidecar 确保仅一个进程执行定时任务。⚠️ sidecar 必须只跑一个实例。

### 订阅 reconcile（批 8.3）

`ReportSubscription` 复用同一 APScheduler 实例，但有自己的 job-id 命名空间（`sub_<id>` 与 `report_<id>` 隔离）：

```python
# app/services/subscription.py
def sync_subscriptions_with_database(db):
    db.expire_all()
    active = db.query(ReportSubscription).filter(is_active=True).all()
    for sub in active:
        _schedule_subscription(sub)         # sub_<id> 注册
    for job in scheduler.get_jobs():
        if job.id.startswith("sub_") and int(job.id[4:]) not in {s.id for s in active}:
            scheduler.remove_job(job)       # 清理孤儿 sub
```

调度器 tick 在 `scheduler_runner.py` 和 `main.py` lifespan 里和 `sync_with_database` 串行调用 —— 一个 reconcile 出错不会阻塞另一个。每个订阅 owner-scoped（创建时绑定 `owner_user_id`），触发时调 `_execute_subscription` 用订阅自身的 `parameters` + `notification_config` 生成 Excel 走邮件 / webhook / IM 投递。

### IM 通知卡片与各渠道协议差异（批 G）

三个 IM 渠道（飞书 / 钉钉 / 企微）在 cron tick 触发后都走结构化卡片，不再发纯文本：

| 渠道 | payload 形状 | 标题 / 操作 |
|------|-------------|------------|
| 飞书 | `msg_type: "interactive"` + `card{header, elements}` | `header.title` 用 `plain_text`（不渲染 markdown，避免报表名含 `**` / `` ` `` 时被当成格式）；有 `PUBLIC_BASE_URL` 时 elements 末加 `action` 按钮 |
| 钉钉 | `msg_type: "actionCard"` (`singleTitle` + `singleURL`) | 有 `PUBLIC_BASE_URL` 时带跳转按钮；否则回落 `msg_type: "markdown"` |
| 企微 | `msgtype: "template_card"` (`card_type: "text_notice"` + `card_action`) | **`template_card` 的 `card_action` 必填**；无 `PUBLIC_BASE_URL` 时回落 `msgtype: "markdown"`（内容仍走 `escape_markdown`，名字里的 `**` 显示为字面量） |

卡片正文固定含：生成时间（UTC）、文件列表（只 basename，不暴露服务端路径）、「查看报表 / 查看看板」按钮（看板订阅通过 `kind="dashboard"` 区分）。URL 形状：`{PUBLIC_BASE_URL}/reports/{id}` 或 `/dashboards/{id}`。

**钉钉签名差异（这版必须修的协议 bug）** —— 三个渠道的签名协议形似而实不同：

| 渠道 | HMAC key | HMAC msg | 时间戳粒度 | 签名位置 |
|------|---------|---------|-----------|---------|
| 飞书 | `f"{ts}\n{secret}"` | `b""`（空串） | 秒 | JSON body 顶层 |
| 钉钉 | `secret` | `f"{ts}\n{secret}"` | **毫秒** | URL query string |
| 通用 webhook | 全局 `WEBHOOK_SECRET`（非各 config `secret`） | `f"{timestamp}.{payload}"` | 秒 | `X-Webhook-Signature` 请求头 |

批 G 之前钉钉走的是通用 webhook 路径（`X-Webhook-Signature` 头 + `{report_name, report_id, ...}` 通用 JSON），钉钉机器人返回 `errcode 40035 缺少参数 msgtype` —— 生产通知从未送达。修复后 `_send_dingtalk` 独立 sender，三段 SSRF 闸顺序保持不变；URL 通过 `urlsplit` / `parse_qsl` / `urlencode` 重新组装，**保留原有的 `access_token=` query param**，新追加的 `timestamp` / `sign` 由 `urllib.parse.quote_plus` 自动 percent-escape（base64 里的 `+/=` 字符不会破 URL 语法）。

`PUBLIC_BASE_URL` 配置说明、`.env` 示例见 CLAUDE.md 配置表。

---

## 10. SSRF 防护策略

Webhook URL 在发送前经 `ssrf_guard.py` 多层校验：

```
validate_webhook_url(url)
    │
    ├─ scheme 校验: 只允许 http/https
    ├─ IP 解析: ipaddress 库解析 host
    ├─ 内网阻断: 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    ├─ 回环阻断: ::1, 127.0.0.1
    └─ DNS 重绑定检测: 解析后 IP 再次校验
```

HTTP 客户端禁用重定向跟随（`follow_redirects=False`），防 302 跳转到内网。

---

## 11. 设计权衡表

| 领域 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| ORM 查询 | SQLAlchemy text() + DataFrame | ORM Model 查询 | DataFrame 输出需要原始 SQL |
| 认证 | 自建 JWT | OAuth2 Provider | 单用户/小团队，复杂度可控 |
| SQL 校验 | sqlglot AST | 正则黑名单 | 语法树级别，无法绕过 |
| 调度器 | APScheduler sidecar | Celery + Redis | 当前规模不需要消息队列 |
| 引擎缓存 | 模块级 dict + Lock | Redis | 简单、无额外依赖 |
| 前端状态 | localStorage + axios | Redux/Zustand | 状态简单 |
| 部署 | Docker Compose | Kubernetes | 单机部署 |

---

## 12. 目录索引

| 文件 | 用途 |
|------|------|
| `app/main.py` | 应用入口、middleware 注册、lifespan |
| `app/config.py` | Pydantic-settings 配置 |
| `app/database.py` | 元数据库 engine/session |
| `app/deps.py` | 共享依赖（auth、token 提取） |
| `app/crypto.py` | Fernet 加密工具 |
| `app/db_migrations.py` | 运行时列补齐 library 函数（lifespan 不再调用；Alembic 接管 schema） |
| `app/scheduler_runner.py` | Sidecar 进程入口 |
| `app/middleware/` | 中间件 |
| `app/models/` | SQLAlchemy ORM 模型 |
| `app/schemas/` | Pydantic 校验模型 |
| `app/routers/` | API 路由 |
| `app/services/` | 业务逻辑 |
| `tests/` | pytest 测试套件（685 用例 + 4 跳过，含 PG-safe 子集 + alert rules 校验） |

### Report versioning (批 report-versioning)

3 fully-normalized tables `report_versions` / `report_version_items` /
`report_version_parameters` mirror the live Report schema. Manual snapshot
via "保存为版本" button; restore is an atomic overwrite of the live state.
Diff is field-level with items/parameters paired by `name`. ACL: list/get
follow Report visibility; restore/delete require owner or admin.

Endpoints:

- `POST /reports/{id}/versions` — create snapshot (visibility-gated)
- `GET /reports/{id}/versions` — list snapshots newest first
- `GET /reports/{id}/versions/{vid}` — fetch full snapshot
- `GET /reports/{id}/versions/{vid}/diff?against=<id|current>` — diff
- `POST /reports/{id}/versions/{vid}/restore` — restore (owner/admin)
- `DELETE /reports/{id}/versions/{vid}` — delete (owner/admin, 409 if pinned)

See `docs/superpowers/specs/2026-08-25-report-versioning-design.md` for
schema SQL, endpoint contracts, and diff algorithm.

### Reverse-link endpoints (批 D)

`DashboardItem` already holds `report_id` / `data_source_id` FKs, but
until 批 D the only navigation direction was forward — dashboards could
see their items, but a Report or DataSource had no way to learn
"which dashboards reference me". The batch adds three reverse-listing
endpoints plus two `DELETE` 409 guards so a referenced entity can't
be silently orphaned.

The ORM layer adds `viewonly=True` reverse relationships:

- `Report.dashboard_items → DashboardItem` (via `DashboardItem.report_id`)
- `DataSource.dashboard_items → DashboardItem` (via
  `DashboardItem.data_source_id`)
- `DashboardItem.report` and `DashboardItem.data_source` (nullable
  one-to-one) so a single item card can name its source.

`viewonly=True` keeps the relationships out of the unit-of-work so a
manual `session.delete(report)` still relies on the existing
`ON DELETE SET NULL` FK behavior — no cascade widening.

Endpoints:

- `GET /reports/{id}/dashboards` — list dashboards whose items
  reference this report. Deduped by `Dashboard.id`; per-dashboard ACL
  via `get_dashboard_for_user`.
- `GET /data-sources/{id}/reports` — list reports bound to this DS
  through `Report.data_source_id`. Uses `list_accessible_reports` so
  per-report ACL applies.
- `GET /data-sources/{id}/dashboards` — dashboards that touch this DS,
  either directly (chart item) or transitively (report item whose
  `Report.data_source_id` is this DS). UNION of the two paths,
  `DISTINCT` on `Dashboard.id`, then per-dashboard ACL filter.

Two new 409 paths (front of the existing report-delete / DS-delete
handlers):

- `DELETE /reports/{id}` — if any `DashboardItem.report_id == {id}`,
  return 409 with a sampled list of dashboards (mirrors the existing
  `test_delete_data_source_with_reports_returns_409` pattern).
- `DELETE /data-sources/{id}` — if any `DashboardItem.data_source_id`
  is set (direct chart refs), return 409 with a sampled list. The
  transitive report-item path is already covered by the pre-existing
  report-ref check.

Frontend wiring: `DataSourceList` gains an expandable row rendering
`DataSourceReferencesPanel` plus a new "被引用" column that shows
`N 报表 / M 看板` counts at a glance. `ReportEditor` adds an inline
`ReportReverseLinkSection` below the tabs. `DashboardItemCard`
renders a small icon button (link / database glyph) that calls
`onOpenSource(item)`; the parent page (`DashboardView` /
`DashboardEdit`) handles navigation: report items land in
`/reports/{id}` (editor), chart items land in `/data-sources` (no
DS detail page exists yet — upgrade by changing one line).

---

## 13. 相关文档

- [README.md](../README.md) — 项目概述与快速启动
- [DEPLOY.md](../DEPLOY.md) — 部署指南
- [CHANGELOG.md](../CHANGELOG.md) — 版本变更记录
- `backend/.env.example` — 环境变量说明（含注释）
