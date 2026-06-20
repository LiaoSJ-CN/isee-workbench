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

数据分析应用iSee — 一个配置化的数据分析应用系统。支持连接数据库、SQL 数据探索、ELT 数据加工处理，以及通过可视化拖拽编辑器构建报表。

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

### 运行测试

当前仓库没有测试文件。后续添加测试后，使用以下命令运行：

```bash
cd backend
source .venv/bin/activate
pytest

# 运行单个测试
pytest path/to/test_file.py::test_function_name
```

### 后端类型检查与代码检查

```bash
cd backend
source .venv/bin/activate

ruff check .
mypy app
```

## 架构说明

### 后端 (`backend/app/`)

- `main.py` — FastAPI 入口。注册路由、配置 CORS、启动时创建 SQLAlchemy 表，并启动 APScheduler 单例。
- `config.py` — 基于 Pydantic-settings 的配置，从 `backend/.env` 加载。
- `database.py` — 元数据库的 SQLAlchemy engine/session 设置。
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
- `services/` — 核心业务逻辑：
  - `connection.py` — 构建 SQLAlchemy 连接 URL 并测试连通性。将 `opengauss`/`dws`/`postgresql` 映射为 `postgresql+psycopg2`。
  - `report_generator.py` — 根据 `ReportItem` 配置构建参数化 SQL、执行查询，并渲染 HTML（Chart.js）或 Excel（openpyxl）输出。
  - `scheduler.py` — 封装 APScheduler 的 `ReportScheduler` 单例；启动时从数据库加载已启用调度的报表，并通过 `generate_report` 生成报表。

### 前端 (`frontend/src/`)

- `App.tsx` — 顶层布局，使用 Ant Design 导航和 React Router 路由。
- `api/index.ts` — Axios 客户端，以及对后端接口的封装。
- `types/index.ts` — 与后端 Pydantic schema 对应的 TypeScript 类型。
- `pages/` — 页面级组件：
  - `DataSourceList.tsx` — 管理数据源连接。
  - `DataExplorer.tsx` — SQL 编辑器（CodeMirror），模板保存在 `localStorage`，支持 CSV 导出。
  - `ReportList.tsx` — 报表列表和创建。
  - `ReportEditor.tsx` — 使用 `@dnd-kit` 的拖拽式报表构建器。
  - `ReportPreview.tsx` — 渲染生成的 HTML 预览；使用 DOMPurify 防止 XSS。
  - `Scheduler.tsx` — 基于 Cron 表达式的调度界面。
- `components/SqlEditor.tsx` — 可复用的 CodeMirror 6 SQL 编辑器。

### 报表生成流程

1. 每个 `Report` 绑定一个 `DataSource`。
2. 每个 `ReportItem` 可配置 `custom_sql`，或通过 `table_name`、`fields`、`where_conditions`、`group_by`、`order_by`、`limit` 自动生成查询。
3. `ReportGenerator.build_query` 构建 SQL。自动生成的 WHERE 子句使用参数绑定；`custom_sql` 仅通过字符串替换 `{parameter}` 占位符。
4. `generate_report` 生成 HTML（内嵌 Chart.js）或多 sheet Excel 文件，输出到 `backend/generated_reports/`。

### 定时调度

- APScheduler 运行在后端进程内。
- 启动时 `main.py` 调用 `scheduler.sync_with_database()` 加载所有已启用调度的活跃报表。
- Cron 表达式使用 6 个字段：`min hour dom mon dow year`。
- 定时报表支持 webhook 通知（按任务配置）。

## 配置说明

后端配置从 `backend/.env`（可选）读取。关键变量：

| 变量名 | 默认值 | 说明 |
|----------|---------|-------------|
| `APP_NAME` | `Business Analysis Report Backend` | 应用标题 |
| `DEBUG` | `false` | FastAPI 调试模式 |
| `DATABASE_URL` | `sqlite:///./app.db` | 元数据库连接 URL |
| `CORS_ORIGINS` | `http://localhost:5173`, `http://127.0.0.1:5173` | 允许的跨域来源 |

示例 `backend/.env`：

```env
APP_NAME=经营分析报表系统
DEBUG=false
DATABASE_URL=sqlite:///./app.db
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
```

## 开发注意事项

- 后端虚拟环境为 `backend/.venv/`（当前实际使用 Python 3.14，但 `pyproject.toml` 要求 ≥ 3.11）。
- 前端开发服务运行在 `http://localhost:5173`，后端在 `http://localhost:8000`。
- **默认登录** `admin` / `admin`；改 `backend/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `JWT_SECRET_KEY`。Token 存前端 localStorage（access 24h，refresh 7d）。
- 所有 API 强制 JWT 鉴权（HS256），除 `auth.py` 自身。`deps.get_current_user` 兼容 `Authorization` header 和 `?token=` query 参数。
- `DataSource` 模型即使对 SQLite 也要求填写 `host`、`port`、`username`、`password`；配置 SQLite 数据源时可使用占位值。
- 数据探索器会拒绝不以 `SELECT` 开头或包含危险关键字的 SQL。
- 报表预览走 iframe `src=` 直接加载后端生成的 HTML；**后端已用 `html.escape` 转义所有用户数据**。iframe 加 `sandbox="allow-scripts"` 防逃逸到父页面。
- `backend/scripts/` 下的 smoke 测试：`smoke_xss_check.py`（恶意 payload 转义）、`smoke_xss_regression.py`（seed 数据回归）、`smoke_path_traversal.py`（文件名 sanitize）。
