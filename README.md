# iSee数据分析工作台

支撑数据分析人员连接不同数据源，进行 SQL 数据探索与报表分析。支持可视化拖拽配置，定时任务自动生成与通知，多用户 RBAC 与审计。

## 项目结构

```
isee-workbench/
├── backend/                         # FastAPI + SQLAlchemy + Pydantic（Python ≥ 3.11）
│   ├── app/
│   │   ├── main.py                  # 应用入口（中间件 + lifespan + 路由注册）
│   │   ├── config.py                # 配置（Pydantic-settings，环境变量加载）
│   │   ├── database.py              # 元数据库 engine / session
│   │   ├── deps.py                  # 共享 FastAPI 依赖（auth、token 提取）
│   │   ├── crypto.py                # 数据源密码 Fernet 对称加密
│   │   ├── db_migrations.py         # 运行时列补齐 library 函数（lifespan 不再调用）
│   │   ├── scheduler_runner.py      # 调度器 sidecar 进程入口
│   │   ├── alembic/                 # 数据库迁移（Alembic，正式接管 schema）
│   │   ├── models/                  # SQLAlchemy 模型（10 张表：user / data_source / report /
│   │   │                            #   report_item / report_parameter / report_access /
│   │   │                            #   data_source_access / report_subscription / report_job /
│   │   │                            #   audit_log / revoked_token / rate_limit）
│   │   ├── schemas/                 # Pydantic 请求/响应模型
│   │   ├── routers/                 # API 路由（9 个：auth / data_source / report / jobs /
│   │   │                            #   scheduler / explorer / subscription / audit）
│   │   ├── services/                # 业务逻辑（connection / report_generator / scheduler /
│   │   │                            #   sql_validator / ssrf_guard / jwt_auth / auth_state /
│   │   │                            #   password / audit / subscription / job_queue /
│   │   │                            #   parameter_validator / schema_introspection / data_source /
│   │   │                            #   notification_migration / report）
│   │   └── middleware/              # 中间件（CORS / ProxyHeaders / SecurityHeaders /
│   │                                #   RateLimit / CSRF / RequestID / Metrics / Sentry）
│   ├── tests/                       # pytest 测试套件（685 用例 + 4 跳过）
│   ├── scripts/                     # seed_erp_demo + seed_reports + alembic
│   ├── alembic/                     # 数据库迁移脚本（autogenerate）
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/                        # React 19 + TypeScript + Vite
│   ├── src/
│   │   ├── api/                     # axios 客户端 + 后端接口封装
│   │   ├── components/              # 公共组件（SqlEditor / SchemaTree /
│   │   │                            #   ReportShareModal / DataSourceShareModal /
│   │   │                            #   ReportParameterForm / SubscriptionModal /
│   │   │                            #   Skeleton / ErrorBoundary）
│   │   ├── pages/                   # 页面组件（DataSourceList / DataExplorer /
│   │   │                            #   ReportList / ReportEditor/ / ReportPreview /
│   │   │                            #   Scheduler / MySubscriptions / AuditLog / Login）
│   │   ├── queries/                 # React Query hooks
│   │   ├── types/                   # TypeScript 类型定义
│   │   ├── constants/               # 前端常量（DataExplorer 模板分类等）
│   │   ├── utils/                   # 通用工具
│   │   ├── App.tsx                  # 顶层布局 + 路由
│   │   └── main.tsx                 # React 19 入口
│   ├── package.json
│   └── vite.config.ts
├── deploy/                          # 生产部署配置（systemd / PM2 / prometheus / grafana）
├── docs/ARCHITECTURE.md             # 设计模式与架构决策
├── docker-compose.yml               # 编排 backend + frontend + scheduler/observability/postgres profile
├── Makefile                         # 顶层命令面板（make help 看清单）
├── CHANGELOG.md
├── DEPLOY.md
└── README.md
```

## 快速启动

### 1. 启动后端

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt   # 或 pip install -e ".[dev]"

# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> Web 进程启动时 lifespan 会自动跑 `alembic upgrade head`，**不要**手动 `create_all`。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端地址：http://localhost:5173  
后端地址：http://localhost:8000  
API 文档：http://localhost:8000/docs

### 3. 默认登录

```
用户名：admin
密码：  admin
```

可在 `backend/.env` 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `JWT_SECRET_KEY` 覆盖。Token 走双通道：浏览器自动携带 HttpOnly+SameSite Cookie（推荐）+ `Authorization: Bearer ...` Header 备用。Access 24h，refresh 7d（rotation + jti revoke）。

### 4. Demo 数据（可选）

跑 `seed_erp_demo.py` 生成 `backend/data/erp_demo.db`（12 张财务域 warehouse 表），再跑 `seed_reports.py` 在 `app.db` 里建 3 张示例报表（id=1/2/3，蓝色「示例」Tag）。不跑也能用，UI 是空的；跑完 DataExplorer 模板 + ReportList 都立刻有内容。

```bash
cd backend && source .venv/bin/activate
python scripts/seed_erp_demo.py        # ~5s，建表 + 灌 ~400 行样本
python scripts/seed_reports.py          # 默认指 DataSource 'sqlite_demo' (id=200)
```

跑第二次会 **drop & recreate**（`seed_erp_demo.py` 不带 `--reset` 不动；`seed_reports.py` 默认 delete 后 insert）。

## 功能特性

### 数据源管理
- 支持 OpenGauss、DWS、PostgreSQL、SQLite
- 连接测试、密码 Fernet 加密存储、Schema 内省
- **Clone**：一键克隆已有数据源（仅改 name + owner，复用连接配置）
- **RBAC（批 9.3）**：每个数据源 owner 默认 full，可向其他用户授权 `read` / `write`
- **反向 link（批 D）**：列表 / 详情页展示哪些报表和看板引用了此数据源；删除被引用数据源返回 409

### 数据探索
- SQL 查询执行（仅允许 SELECT，sqlglot AST 校验）
- CodeMirror 6 SQL 编辑器，语法高亮
- 19 个模板分 5 类（维度表 / 业务明细 / 聚合分析 / 跨表 JOIN / 自定义），存 localStorage
- 执行历史（localStorage，100 条 FIFO + 5s dedup）
- 查询结果导出 CSV（RFC 4180）

### 报表配置
- 可视化拖拽配置报表项（@dnd-kit）
- 支持表格、图表、指标卡、文本 4 种类型
- 自动 SQL 生成或自定义 SQL
- 查询条件、排序、分组、参数（`/reports/{id}/parameters`）配置
- **Duplicate**：一键复制整个报表（含所有 item + 参数 + share 配置）
- **报表版本与回滚**：手动「保存为版本」创建完整快照；任意版本可一键恢复；字段级 diff 查看改动（批 report-versioning）
- **ACL（批 9.4）**：报表 owner 默认 full，可设 `visibility=private/link/internal` 或显式 `share` 给指定用户
- **反向 link（批 D）**：详情页展示哪些看板引用了此报表；删除被引用报表返回 409
- **乐观锁（批 3）**：保存时附带 `If-Match: W/"v<N>"`；服务端 version 不匹配返回 `412 Precondition Failed` + 当前远端状态，前端弹三按钮 diff（覆盖 / 放弃 / 复制改）。缺头照样保存 → 老客户端/调度器脚本不受影响

### 报表生成
- HTML 预览（Chart.js 可视化，iframe blob-URL + sandbox）
- Excel 导出（openpyxl，多 sheet）
- **PDF 导出（批 8.1）**：weasyprint 渲染 HTML（需 `libpango / libcairo` 系统依赖，详见 DEPLOY.md）
- 同步生成：`POST /reports/generate`
- **异步生成（批 3a / 批 8.5）**：`POST /reports/{id}/jobs` → 轮询 `GET /jobs/{id}` → `GET /jobs/{id}/download` 拿 worker 产物
- 定时任务自动生成 + Webhook 通知（HMAC-SHA256 + replay window）

### 通知与订阅（批 8.3 / 批 8.4）
- **邮件**：SMTP（STARTTLS 587 / implicit SSL 465），mailpit 本地推荐
- **IM**：钉钉 / 飞书 / 企业微信 三种 variant，富卡片（批 G），飞书可选签名密钥
- **Webhook**：HMAC 签名 + 时间戳，SSRF guard 拒内网 / http（可关）
- **订阅**：每个用户可订阅任意报表到自己信箱，独立 cron + 参数；owner-scoped API

### 全局搜索（批 A）
- 顶栏常驻 Input + `⌘K` / `Ctrl+K` 聚焦（macOS / 其他平台均可用）
- 跨实体联合搜索：报表 / 看板 / 数据源三组结果，每组独立 ACL 过滤
- 250ms debounce；键盘导航（↑↓ Enter Esc）；click-outside 关闭
- 排序：exact > prefix > contains；ties 按 name 长度

### 并发编辑（批 3）
- HTTP 标准乐观锁：`ETag: W/"v<N>"` 响应头 + `If-Match` 请求头，`412 Precondition Failed` 触发冲突
- 整数 `version` 列（`server_default="1"`），每次成功 PUT 自增；不用 `updated_at` 是因为 SQLite `CURRENT_TIMESTAMP` 是秒级 → 同秒两次写无法区分
- 后端用 `db.execute(update(Report).where(Report.version == N))` 原子比较，rowcount=0 → 412；TOCTOU 窗口内并发请求只有一条会命中
- `If-Match` **可选**：缺头照样接受，老客户端 / 调度器 / API 脚本不破坏
- 前端 `useUpdateReport` 自动从缓存 `Report.version` 拼 `W/"v<N>"`；412 → typed `VersionConflictError(message, current)`
- 冲突弹窗 `ConflictModal` 三按钮：
  - **覆盖远端**：用冲突响应里的 `current.version` 重 PUT，本地修改胜出
  - **放弃本地**：关弹窗，缓存 invalidate 重新水合 buffer
  - **复制改**：跳 `/reports/{id}/duplicate`，双方改动都不丢

### 定时调度
- APScheduler 驱动，Cron 表达式（6 字段）
- Sidecar 部署模式（避免多 worker 重复执行）
- Pydantic 层 Cron 字段范围校验
- Reconcile 模式：清理孤儿 job（DB 删 / 订阅暂停）

### 审计日志（批 9.5 / 批 11.1）
- Admin 专属审计表，覆盖用户 CRUD / 数据源变更 / 报表克隆 / 调度变更 / 订阅 CRUD / 任务入队等
- 支持 `actor_user_id` / `action` / `target_type` / `target_id` / `request_id` / `ip_address` / 时间窗口过滤
- `X-Request-ID` 跨日志 + audit row 串联
- 保留期通过 `AUDIT_LOG_RETENTION_DAYS` 控制（lifespan **不**自动 purge，operator 自行接 cron）

### 可观测性（TODO-9）
- `prometheus-fastapi-instrumentator` 暴露 `/metrics`：HTTP latency / status histogram + 4 自定义 metric（`report_generate_*` / `webhook_delivery_*` / `sql_validator_*`）
- 配套 Prometheus + Grafana + Alertmanager（`observability` profile）
- 8 条预置 alert rules（`deploy/prometheus/alerts/isee-workbench.yml`），CI 自动校验 expr 引用的 metric 真实存在

## API 端点

除 `POST /auth/login` 外所有路由均需 `Authorization: Bearer <token>` 或同名 Cookie 鉴权。

### 认证

| 方法 | 路径 | 功能 | 鉴权 |
|------|------|------|------|
| POST | `/auth/login` | 登录，发放 access + refresh token | 无 |
| POST | `/auth/refresh` | 用 refresh token 换新 access（rotation） | Bearer refresh |
| POST | `/auth/logout` | 登出（access jti 进 deny-list + 清 Cookie） | 无 |
| GET | `/auth/me` | 返回当前登录用户 | Bearer access |

### 用户

| 方法 | 路径 | 功能 | 鉴权 |
|------|------|------|------|
| GET | `/users` | 列出全部用户（`id` / `username` / `role`），报表版本化 UI 用此解析 `created_by` 外键为可读用户名 | Bearer access |

### 搜索

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/search` | 联合搜索（批 A）：按 name 模糊匹配，返回 `{ reports, dashboards, data_sources }` 三组结果，每组独立 ACL 过滤 + `limit_per_kind` 上限（默认 8，max 50）。`q` ≤ 255 字符。供顶栏 command palette 用 |

### 数据源

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/data-sources` | 数据源列表（ACL 过滤） |
| POST | `/data-sources` | 创建数据源 |
| GET | `/data-sources/{id}` | 获取数据源 |
| PUT | `/data-sources/{id}` | 更新数据源 |
| DELETE | `/data-sources/{id}` | 删除数据源 |
| POST | `/data-sources/{id}/test` | 测试连接 |
| GET | `/data-sources/{id}/schema` | 拉取目标 DB 的表 / 列 schema（用于编辑器） |
| POST | `/data-sources/{id}/clone` | 克隆数据源（仅改 name + owner） |
| GET | `/data-sources/{id}/reports` | 反向 link（D）：引用此数据源的报表列表（带 ACL 过滤） |
| GET | `/data-sources/{id}/dashboards` | 反向 link（D）：通过 chart-item 或 report-item 引用此数据源的看板列表（去重 + ACL 过滤） |
| GET | `/data-sources/{id}/grants` | 列出该数据源所有授权 |
| POST | `/data-sources/{id}/grants` | 授权（read / write） |
| DELETE | `/data-sources/grants/{grant_id}` | 撤销授权 |

### 报表

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/reports` | 报表列表（ACL 过滤） |
| POST | `/reports` | 创建报表 |
| GET | `/reports/{id}` | 获取报表详情 |
| GET | `/reports/{id}/dashboards` | 反向 link（D）：引用此报表的看板列表（按 dashboard 去重 + ACL 过滤） |
| PUT | `/reports/{id}` | 更新报表（乐观锁：可附 `If-Match: W/"v<N>"`，不匹配返回 412 + 当前远端状态） |
| DELETE | `/reports/{id}` | 删除报表（被 DashboardItem 引用时返回 409） |
| POST | `/reports/{id}/duplicate` | 克隆报表（含 item + 参数 + share） |
| POST | `/reports/{id}/items` | 添加报表项 |
| PUT | `/reports/{id}/items/{item_id}` | 更新报表项 |
| DELETE | `/reports/{id}/items/{item_id}` | 删除报表项 |
| PATCH | `/reports/{id}/items/order` | 批量更新报表项排序（原子事务） |
| GET | `/reports/{id}/parameters` | 列出报表参数定义 |
| POST | `/reports/{id}/parameters` | 添加参数 |
| PATCH | `/reports/{id}/parameters/{param_id}` | 更新参数 |
| DELETE | `/reports/{id}/parameters/{param_id}` | 删除参数 |
| GET | `/reports/{id}/shares` | 列出授权 |
| POST | `/reports/{id}/shares` | 授权（read / write） |
| DELETE | `/reports/shares/{share_id}` | 撤销授权 |
| POST | `/reports/generate` | **同步**生成报表（HTML / Excel） |
| GET | `/reports/{id}/preview` | 预览报表（HTML） |
| GET | `/reports/{id}/export/{format}` | 导出报表（`html` / `xlsx` / `pdf`） |
| POST | `/reports/{id}/jobs` | **异步**生成报表（入队，返回 job id） |
| GET | `/reports/{id}/jobs` | 报表的 job 历史 |
| POST | `/reports/{id}/versions` | **保存为版本**（创建完整快照） |
| GET | `/reports/{id}/versions` | 列出所有版本（newest first） |
| GET | `/reports/{id}/versions/{vid}` | 获取单个版本完整内容 |
| GET | `/reports/{id}/versions/{vid}/diff` | 字段级 diff（`?against=current\|{vid}` 指定对比基准） |
| POST | `/reports/{id}/versions/{vid}/restore` | 恢复版本到 live（owner/admin） |
| DELETE | `/reports/{id}/versions/{vid}` | 删除版本（owner/admin，pinned 则 409） |
| POST | `/reports/{id}/versions/{vid}/pin` | 固定/取消固定版本（owner/admin，body `{ pinned: bool }`） |
| GET | `/reports/templates` | 模板市场列表（批 13：visibility ACL + category / 数据源 / `q` 过滤） |
| POST | `/reports/{id}/save-as-template` | 另存为模板（owner/admin，剥离 scheduler + notification） |
| POST | `/reports/{id}/from-template` | 从模板 fork 出新报表（read ACL 即可） |

> **乐观锁（批 3）**：`GET /reports/{id}` / `POST /reports` / `PUT /reports/{id}` 响应头含 `ETag: W/"v<N>"`（weak ETag，N 为整数 version）。`PUT` 可选请求头 `If-Match: W/"v<N>"`；缺头照常接受（向后兼容老客户端），缺/匹配/不匹配的语义见「并发编辑（批 3）」章节。冲突响应 `412 Precondition Failed`，body 为 `{"detail": {"message": "...", "current": ReportResponse}}`。

### 看板（Dashboard，批 14）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/dashboards` | 看板列表（ACL 过滤，owner + public + org + 授权） |
| POST | `/dashboards` | 创建看板（默认 owner=caller + visibility=private，可选 `items`） |
| GET | `/dashboards/{id}` | 获取看板详情（含所有 items） |
| PUT | `/dashboards/{id}` | 更新看板（write ACL：owner / write-grantee / admin） |
| DELETE | `/dashboards/{id}` | 删除看板（owner / admin；cascade 清 items + shares + subscriptions） |
| POST | `/dashboards/{id}/duplicate` | 克隆看板（read ACL 即可；items 深拷贝；visibility 重置为 private） |
| POST | `/dashboards/{id}/items` | 添加看板项（item_type: report / chart / text） |
| PUT | `/dashboards/{id}/items/{item_id}` | 更新看板项 |
| DELETE | `/dashboards/{id}/items/{item_id}` | 删除看板项 |
| PATCH | `/dashboards/{id}/items/layout` | 批量更新 x/y/w/h（react-grid-layout onLayoutChange 一次性原子事务） |
| POST | `/dashboards/{id}/preview` | 服务端聚合预览 HTML（iframe 直载，含 Chart.js 内联；DS gate 在此强制） |
| GET | `/dashboards/{id}/items/{item_id}/preview` | 单项预览 HTML（DashboardItemCard 每个 cell 各起一个 iframe；前端 axios 取 HTML → blob URL） |
| GET | `/dashboards/{id}/shares` | 列出授权（owner / admin） |
| POST | `/dashboards/{id}/shares` | 授权（read / write，upsert） |
| DELETE | `/dashboards/{id}/shares/{user_id}` | 撤销授权 |

### 看板订阅（批 14.2）

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/dashboard-subscriptions` | 创建看板订阅（owner-scoped） |
| GET | `/dashboard-subscriptions` | 列出**当前用户**的看板订阅 |
| GET | `/dashboard-subscriptions/{id}` | 单条看板订阅 |
| PATCH | `/dashboard-subscriptions/{id}` | 更新订阅（cron / 参数 / 通知 / active） |
| DELETE | `/dashboard-subscriptions/{id}` | 删除订阅 |
| POST | `/dashboard-subscriptions/{id}/pause` | 暂停（active=false） |
| POST | `/dashboard-subscriptions/{id}/resume` | 恢复 |

### 异步任务

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/jobs/{id}` | 单 job 状态 |
| GET | `/jobs/{id}/download` | 下载 worker 产物（批 8.5 起取代 export 同步重渲） |

### 调度器

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/scheduler/status` | 调度器状态 |
| POST | `/scheduler/sync` | 从 DB 同步任务（reconcile） |
| POST | `/scheduler/jobs/{report_id}` | 创建/更新定时任务 |
| GET | `/scheduler/jobs/{report_id}` | 查看任务 |
| DELETE | `/scheduler/jobs/{report_id}` | 删除定时任务 |

### 订阅（owner-scoped）

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/subscriptions` | 创建订阅 |
| GET | `/subscriptions` | 列出**当前用户**的订阅 |
| GET | `/subscriptions/{id}` | 单条订阅 |
| PATCH | `/subscriptions/{id}` | 更新订阅（cron / 参数 / 通知 / active） |
| DELETE | `/subscriptions/{id}` | 删除订阅 |
| POST | `/subscriptions/{id}/pause` | 暂停（active=false） |
| POST | `/subscriptions/{id}/resume` | 恢复 |

### 数据探索

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/explorer/query` | 执行 SELECT 查询（sqlglot AST 校验 + row cap + statement timeout） |

### 审计日志（admin only）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/audit-logs` | 审计事件列表，支持多维度过滤（详见 DEPLOY.md 审计日志段） |

### 监控仪表盘（admin only）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/admin/metrics` | 每个 DataSource 的连接池实时指标（活跃连接 / 借出归还 / 超时 / 平均持有时间 / 健康度评分），供前端监控页与告警基线使用 |
| POST | `/admin/data-sources/{source_id}/rotate-password` | Admin 轮换指定 DataSource 的连接密码（admin-only，泄漏响应/定期轮换场景）。支持 admin 传新密码或服务器生成随机密码并 one-time 返回明文；同时清空 cached SQLAlchemy engine 并写入 `data_source.password_rotated` 审计行 |

### 用户管理（admin only，批 user-management Stage 1）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/admin/users` | 列出用户，支持 `role` / `disabled` / `q`（username 或 role 子串）过滤与 `limit` / `offset` 分页。响应中**不返回** `password_hash` |
| POST | `/admin/users` | 新建用户（username 1-255，password 8-255，role ∈ admin/editor/viewer）。重复 username 返回 409；弱口令/未知 role 返回 422 |
| GET | `/admin/users/{id}` | 获取单个用户。不存在返回统一 404（admin 操作不暴露存在性） |
| PATCH | `/admin/users/{id}` | 改 `role` 和/或 `disabled`。username 不可改（保留审计 FK 可读性）。**自保护**：唯一 admin 不能降级或禁用自己，否则 403 |
| DELETE | `/admin/users/{id}` | 软删除（`disabled=true`），硬删除会让审计 FK 失效。同一 row 二次 DELETE 幂等且不写审计行 |
| POST | `/admin/users/{id}/reset-password` | 两模式重置密码：admin 传新密码（不回显）或服务端生成（one-time 返回 24 字符随机串）。审计 `after` 只写 `rotation_method`，绝不携带明文 |

### 集中授权与 ACL 聚合（admin only，批 user-management Stage 2）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/admin/users/{id}/grants` | 聚合该用户持有的全部授权（DataSource + Report + Dashboard），返回统一 `UserAclView` 信封，每项带 `resource_type` / `resource_name` / `permission` / `granted_by_username` |
| GET | `/admin/grants?resource_type=&resource_id=` | 列出指定资源（data_source/report/dashboard）上的全部授权；admin UI 在「+集中授权」弹窗里直接调用 |
| POST | `/admin/grants` | 集中授权，body `{resource_type, resource_id, target_user_id, permission}`，幂等 upsert（重复 POST 升级权限而非撞 unique 约束）。审计 action 复用 `data_source.grant` / `report.share` / `dashboard.share`，与 per-resource share 端点同源 |
| DELETE | `/admin/grants/{resource_type}/{grant_id}` | 集中撤销，按底层 access 行 id 撤销。`{resource_type}` 限定 `data_source` / `report` / `dashboard`。审计 action 复用 `*_revoke` 系列 |
| GET | `/data-sources?q=...` | 列表新增 `q` 参数（name ILIKE 子串，ACL 过滤之后）。`X-Total-Count` 反映 ACL+`q` 后总数 |
| GET | `/reports?q=...` | 列表新增 `q` 参数（name ILIKE 子串，ACL 过滤之后） |

## 测试

```bash
cd backend
source .venv/bin/activate
pip install pytest pytest-asyncio httpx

pytest                  # 全部测试（685 用例 + 4 跳过）
pytest -k xss           # 关键字过滤
pytest --lf             # 只跑上次失败的
pytest --cov=app        # 覆盖率
```

前端：

```bash
cd frontend
npm test                # vitest（45 用例）
npm run test:e2e        # playwright e2e
npm run lint
```

CI 还跑一次 PostgreSQL 容器上的 alembic upgrade + PG-safe pytest 子集（23 文件）。

详见 [CLAUDE.md](CLAUDE.md) 测试注意事项。

## Docker 部署

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，设置 JWT_SECRET_KEY / ENCRYPTION_KEY
docker compose up -d
# 访问 http://localhost:8080
```

可选 profile：

```bash
docker compose --profile scheduler up -d     # 调度器 sidecar
docker compose --profile postgres up -d     # PostgreSQL 元数据库
docker compose --profile observability up -d  # Prometheus + Grafana + Alertmanager
```

详细说明见 [DEPLOY.md](DEPLOY.md)。设计模式与架构决策见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。环境变量完整列表见 `backend/.env.example`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。