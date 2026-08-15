# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 编程行为守则

写代码、code review、refactor 时强制遵循。通用对话不受约束。

### 1. Think Before Coding — 先思考再写

不要假设，不要藏困惑，把 tradeoffs 摆出来。

- 不确定就问，不要默认
- 多重理解 → 列出来让用户挑，别自己挑
- 有更简单的方案 → 直接说，不要迎合
- 卡住了 → 停下来，说清楚哪里卡住，问

### 2. Simplicity First — 简洁优先

最小代码解决问题，不写投机性的东西。

- 不写用户没要的功能
- 不为一次性代码做抽象
- 不写没要求的"灵活性"和"可配置性"
- 不给不可能的场景写错误处理
- 200 行能 50 行搞定？重写
- 自问："高级工程师会不会嫌过度复杂？"

### 3. Surgical Changes — 精准修改

只动必要的，清理只清自己造成的。

- 不顺手"改进"无关代码、注释、格式
- 不重构没坏的东西
- 现有风格 = 你的风格，即使你不会那么写
- 你改了导致孤儿代码 → 你负责清；旧的孤儿别动（除非用户说）
- 测试：每一行改动都能追溯到用户需求

### 4. Goal-Driven Execution — 目标驱动

把任务翻译成可验证的目标，循环直到通过。

- "加 validation" → "写无效输入的测试，让它过"
- "修 bug" → "写复现测试，让它过"
- "重构 X" → "重构前后测试都过"
- 多步任务：先给 1-2-3 计划 + 每步验证方式

## 项目概述

iSee数据分析工作台 — 支撑数据分析人员连接不同数据源，进行 SQL 数据探索和报表分析。支持可视化拖拽编辑器构建报表，以及定时任务自动生成与通知。

设计模式与架构决策详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

- **后端**: FastAPI + SQLAlchemy + Pydantic，Python ≥ 3.11
- **前端**: React 19 + TypeScript + Vite + Ant Design + Chart.js + CodeMirror 6
- **元数据库**: 默认 SQLite（`backend/app.db`），可通过 `DATABASE_URL` 配置
- **支持的数据源**: OpenGauss、DWS、PostgreSQL、SQLite

## 常用命令

### 后端

```bash
cd backend
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
# 或以可编辑模式安装
pip install -e ".[dev]"

# 启动开发服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# API 文档地址
# http://localhost:8000/docs
```

### 前端

```bash
cd frontend
npm install

# 启动开发服务
npm run dev

# 生产构建
npm run build

# 代码检查
npm run lint
```

### Docker

```bash
# 构建并启动全部服务
docker compose up -d

# 仅构建镜像（不启动）
docker compose build

# 启动调度器 sidecar（仅在需要定时报表时）
docker compose --profile scheduler up -d

# 停止
docker compose down
```

启动后访问 `http://localhost:8080`。

### 数据库迁移（Alembic）

批 5 起改用 Alembic 正式管理 schema。Web 进程启动时 lifespan 自动跑 `alembic upgrade head`，**不要再手动 `create_all`**。`ensure_columns()` 已废弃。

```bash
cd backend
source .venv/bin/activate

# 生成迁移（自动检测模型变化）
python -m alembic revision --autogenerate -m "描述"

# 执行迁移到最新版本
python -m alembic upgrade head

# 查看当前版本
python -m alembic current

# 回滚一个版本（罕见）
python -m alembic downgrade -1
```

迁移工作流：
1. 改 `app/models/*.py` 后跑 `python -m alembic revision --autogenerate -m "..."`
2. 检查生成的 `alembic/versions/*.py`，autogenerate 有时会漏 server_default / CHECK 约束
3. `python -m alembic upgrade head` 本地验证（开发时），或直接重启 web 进程让 lifespan 跑
4. 提交 migration + model 改动一起

**注意**：`alembic/env.py` 不再 `fileConfig()` —— 那会清空 root logger handler，覆盖 lifespan 装的 request-id 格式和 pytest 的 caplog。

### 运行测试

后端测试套件位于 `backend/tests/`（pytest，80 个用例，~0.2s 跑完）。先装依赖：

```bash
cd backend
source .venv/bin/activate
pip install pytest pytest-asyncio httpx
```

常用命令：

```bash
# 全部测试
pytest

# 关键字过滤
pytest -k xss

# 单个测试
pytest tests/test_engine_cache.py::test_evict_engine_unknown_id_is_noop

# 只跑上次失败的
pytest --lf
```

注意：
- `conftest.py` 在 import 阶段把 `JWT_SECRET_KEY` 设成 `pytest-secret-do-not-use-in-prod`，保证测试间 token 稳定；不要在生产环境用这个值
- `test_xss_regression` / `test_preview_endpoint` / `test_explorer` 依赖 seed 数据（`scripts/seed_reports.py`），无 seed 时自动 `pytest.skip`，不会失败
- `engine_cache_cleanup` fixture 会清空模块级 engine cache，避免跨测试串扰
- 旧的 `backend/scripts/smoke_*.py` 已在 `308e97a` 删掉，不要再恢复

### 后端类型检查与代码检查

```bash
cd backend
source .venv/bin/activate

ruff check .
mypy app
```

## 架构说明

### 后端 (`backend/app/`)

- `main.py` — FastAPI 入口。注册路由、配置 CORS、统一日志配置（`RotatingFileHandler` → `logs/app.log`）。启动时创建 SQLAlchemy 表 + 补齐缺失列。默认 `SCHEDULER_DISABLED=true` 不启动调度器。
- `crypto.py` — Fernet 对称加密工具，用于数据源密码的静态加密存储。解密时自动识别存量明文（向后兼容）。
- `db_migrations.py` — `ensure_columns(engine)` 仍保留为可复用 library 函数（有单测覆盖），但 **web 进程 lifespan 不再调用它**——Alembic 接管 schema 演进。
- `scheduler_runner.py` — Sidecar 进程入口（`python -m app.scheduler_runner`）。独占 APScheduler tick 循环，配合 web 进程的 `SCHEDULER_DISABLED=true` 解决 `gunicorn -w N` 下 job 跑 N 次的问题。
- `config.py` — 基于 Pydantic-settings 的配置，从 `backend/.env` 加载。
- `database.py` — 元数据库的 SQLAlchemy engine/session 设置。对非 SQLite 数据库启用 `pool_pre_ping`。
- `middleware/` — 可复用中间件：`rate_limit.py`（内存滑动窗口限流器）、`security_headers.py`（安全响应头）、`proxy_headers.py`（反向代理 IP 信任）。
- `models/` — SQLAlchemy 模型：
  - `DataSource` — 外部数据库连接信息。
  - `Report` — 报表定义、调度配置、输出格式。
  - `ReportItem` — 报表中的单个组件（表格/图表/文本/指标卡），包含查询和展示配置。
- `schemas/` — Pydantic 请求/响应模型。
- `routers/` — FastAPI 路由模块：
  - `data_source.py` — 数据源的 CRUD 和连接测试。
  - `report.py` — 报表 CRUD、报表项 CRUD、报表生成/导出接口。
  - `scheduler.py` — APScheduler 任务管理和同步接口。
  - `explorer.py` — 对数据源执行 `SELECT` 查询并返回表格结果。
  - `auth.py` — 登录、Token 刷新、登出、当前用户接口。
- `services/` — 核心业务逻辑：
  - `connection.py` — 构建 SQLAlchemy 连接 URL 并测试连通性。将 `opengauss`/`dws`/`postgresql` 映射为 `postgresql+psycopg2`。
  - `report_generator.py` — 根据 `ReportItem` 配置构建参数化 SQL、执行查询，并渲染 HTML（Chart.js）或 Excel（openpyxl）输出。
  - `scheduler.py` — 封装 APScheduler 的 `ReportScheduler` 单例；启动时从数据库加载已启用调度的报表，并通过 `generate_report` 生成报表。
  - `sql_validator.py` — sqlglot AST 校验，拦截非 SELECT / 多语句 / 危险操作。
  - `ssrf_guard.py` — Webhook URL SSRF 防护（scheme 校验、内网/保留 IP 阻断、DNS 重绑定检测）。
  - `auth_state.py` — 服务端 Token 状态追踪（登出失效）。
  - `jwt_auth.py` — JWT 签发与校验（access + refresh token）。
  - `password.py` — 密码哈希与校验（passlib bcrypt）。
- `alembic/` — 数据库迁移脚本目录（Alembic），`env.py` 从 `app.config.settings` 动态获取 `database_url`，支持 autogenerate。

### 前端 (`frontend/src/`)

- `App.tsx` — 顶层布局，使用 Ant Design 导航和 React Router 路由。
- `api/index.ts` — Axios 客户端，以及对后端接口的封装。
- `types/index.ts` — 与后端 Pydantic schema 对应的 TypeScript 类型。
- `pages/` — 页面级组件：
  - `DataSourceList.tsx` — 管理数据源连接。
  - `DataExplorer.tsx` — SQL 编辑器（CodeMirror），模板保存在 `localStorage`，支持 CSV 导出；执行历史面板在 `localStorage:sqlHistory:v1`，100 条 FIFO + 5s dedup。
  - `ReportList.tsx` — 报表列表和创建。
  - `ReportEditor.tsx` — 使用 `@dnd-kit` 的拖拽式报表构建器。
  - `ReportPreview.tsx` — 通过 `Authorization` 头取 HTML → blob URL → iframe（`sandbox="allow-scripts"`）。XSS 防护在后端（`html.escape`），不在前端。
  - `Scheduler.tsx` — 基于 Cron 表达式的调度界面。
- `components/SqlEditor.tsx` — 可复用的 CodeMirror 6 SQL 编辑器。

### API 代理机制

前端通过 `/api` 相对路径调用后端，由代理层剥离前缀后转发：

- **开发环境**：`vite.config.ts` 的 `server.proxy` 把 `/api` 转发到 `http://localhost:8000`，自动 `rewrite` 去除前缀
- **Docker 生产环境**：`frontend/nginx.conf` 的 `location /api/` 通过 `proxy_pass http://backend:8000/`（尾部斜杠剥离前缀）
- **手动部署**：见 `DEPLOY.md` 方式二的 nginx 示例

`VITE_API_BASE_URL` 环境变量可在构建时覆盖默认值 `/api`。

### 报表生成流程

1. 每个 `Report` 绑定一个 `DataSource`。
2. 每个 `ReportItem` 可配置 `custom_sql`，或通过 `table_name`、`fields`、`where_conditions`、`group_by`、`order_by`、`limit` 自动生成查询。
3. `ReportGenerator.build_query` 构建 SQL。自动生成的 WHERE 子句使用参数绑定；`custom_sql` 仅通过字符串替换 `{parameter}` 占位符。
4. `generate_report` 生成 HTML（内嵌 Chart.js）或多 sheet Excel 文件，输出到 `backend/generated_reports/`。

### 定时调度

- APScheduler 默认在 web 进程中禁用（`SCHEDULER_DISABLED=true`）。单进程开发时可设为 `false` 直接在 web 进程中跑。启动时 `main.py`（若启用）调用 `scheduler.sync_with_database()` 加载所有已启用调度的活跃报表。
- Cron 表达式使用 6 个字段：`min hour dom mon dow year`；由 `ScheduleTaskCreate._validate_cron` 在 Pydantic 层用 `CronTrigger` 校验每段范围（越界返回 422）。
- `sync_with_database()` 是 reconcile，不是纯 add：会清理 DB 里已不再 active 的孤立 job（DELETE 后的孤儿）。
- 定时报表支持 webhook 通知（按任务配置）。

#### 多 worker 部署（sidecar）

`app/services/scheduler.py` 的 `_scheduler` 是进程内单例。`gunicorn -w N` 下每个 worker 会独立跑 APScheduler，同一个 job 每个 tick 执行 N 次。修复方案：

1. Web 进程保持默认 `SCHEDULER_DISABLED=true`（无需额外配置），启动时跳过 `scheduler.start()` 和 `sync_with_database()`。`/scheduler/*` API 端点仍然可用（它们操作 DB 和 in-process APScheduler 实例的元数据，不实际 tick）。单进程开发时可设 `SCHEDULER_DISABLED=false`。
2. 单独跑 `python -m app.scheduler_runner` 一个 sidecar 进程。该进程独占调度器 tick 循环，每 `SCHEDULER_RESYNC_INTERVAL` 秒（默认 30）从 DB 重读一次，幂等 reconcile。SIGTERM/SIGINT 触发 graceful shutdown。

⚠️ sidecar 必须只跑一个实例；跑多个 = 原 bug 重现。需要多实例 HA 时升级到 celery beat / 外部 leader 选举。

## 配置说明

后端配置从 `backend/.env`（可选）读取。关键变量：

| 变量名 | 默认值 | 说明 |
|----------|---------|-------------|
| `APP_NAME` | `iSee Data Analysis Workbench` | 应用标题 |
| `DEBUG` | `false` | FastAPI 调试模式 |
| `DATABASE_URL` | `sqlite:///./app.db` | 元数据库连接 URL |
| `CORS_ORIGINS` | `http://localhost:5173`, `http://127.0.0.1:5173` | 允许的跨域来源 |
| `SCHEDULER_DISABLED` | `true` | web 进程跳过调度器 tick（sidecar 模式） |
| `SCHEDULER_RESYNC_INTERVAL` | `30` | sidecar 从 DB 重读调度的间隔（秒） |
| `ADMIN_USERNAME` | `admin` | 管理员用户名 |
| `ADMIN_PASSWORD` | `admin` | 管理员密码（**生产必改**） |
| `JWT_SECRET_KEY` | (自动生成) | JWT 签名密钥（**生产必设，否则重启 token 全失效**） |
| `ENCRYPTION_KEY` | (自动生成) | 数据源密码加密密钥（**生产必设，否则重启后已存密码不可读**） |
| `LOGIN_RATE_LIMIT` | `10` | 每 IP 每分钟最大登录尝试次数 |
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `SENTRY_DSN` | (空) | 后端 Sentry DSN。空 = 禁用。设置后自动 init，自动给每个事件打 `request_id` tag |
| `SENTRY_ENVIRONMENT` | (空) | Sentry 环境标签，如 `production`/`staging`。空 = Sentry 默认 `development` |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | 性能追踪采样率（0.0-1.0）。0 = 禁用 |
| `DB_POOL_SIZE` | `5` | 数据库连接池大小（仅 PostgreSQL） |
| `DB_MAX_OVERFLOW` | `10` | 连接池溢出上限（仅 PostgreSQL） |
| `GENERATED_REPORTS_DIR` | `backend/generated_reports/` | 报表输出目录 |

前端 Sentry 通过 `VITE_SENTRY_DSN` / `VITE_SENTRY_ENVIRONMENT` / `VITE_SENTRY_TRACES_SAMPLE_RATE`（`frontend/.env` 或构建时变量）配置；空 DSN = 禁用，bundle 零开销。

示例 `backend/.env`：

```env
APP_NAME=iSee数据分析工作台
DEBUG=false
DATABASE_URL=sqlite:///./app.db
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme
JWT_SECRET_KEY=<生成一个长随机串>
ENCRYPTION_KEY=<生成一个 Fernet key>
```

## 开发注意事项

- 后端虚拟环境为 `backend/.venv/`（当前实际使用 Python 3.14，但 `pyproject.toml` 要求 ≥ 3.11）。
- 前端开发服务运行在 `http://localhost:5173`，后端在 `http://localhost:8000`。
- **默认登录** `admin` / `admin`；改 `backend/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `JWT_SECRET_KEY`。Token 存前端 localStorage（access 24h，refresh 7d）。
- 所有 API 强制 JWT 鉴权（HS256），除 `auth.py` 自身。`deps.get_current_user` 只接受 `Authorization: Bearer ...` 头；早期的 `?token=` query-param fallback 已在 `515bbd9` 移除（消除 token 出现在浏览器历史/访问日志的泄漏面）。
- `DataSource` 模型即使对 SQLite 也要求填写 `host`、`port`、`username`、`password`；配置 SQLite 数据源时可使用占位值。
- 数据探索器会拒绝不以 `SELECT` 开头或包含危险关键字的 SQL。
- 报表预览走 iframe `src=` 直接加载后端生成的 HTML；**后端已用 `html.escape` 转义所有用户数据**。iframe 加 `sandbox="allow-scripts"` 防逃逸到父页面。
- Docker 相关文件：`backend/Dockerfile`、`frontend/Dockerfile`、`docker-compose.yml`、`frontend/nginx.conf`。前端默认构建到 `/usr/share/nginx/html`，nginx 监听 80 端口。
- `deploy/` 目录含 systemd service 和 PM2 config，用于生产环境管理调度器 sidecar 进程。
- `.github/workflows/ci.yml` — GitHub Actions 自动执行 lint、类型检查、测试和构建。
- 新增后端模块时需 import 到 `backend/alembic/env.py`，否则 autogenerate 不会检测到新表。
- `backend/alembic.ini` 已精简，sqlalchemy.url 由 `env.py` 从 `settings.database_url` 动态获取。
