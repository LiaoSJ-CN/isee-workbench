# iSee数据分析工作台

支撑数据分析人员连接不同数据源，进行 SQL 数据探索与报表分析。支持可视化拖拽配置，定时任务自动生成与通知。

## 项目结构

```
isee-workbench/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py            # 应用入口
│   │   ├── config.py          # 配置（Pydantic-settings）
│   │   ├── database.py        # 元数据库连接
│   │   ├── crypto.py          # 数据源密码加密（Fernet）
│   │   ├── db_migrations.py   # 启动期 schema 自愈（补齐缺失列）
│   │   ├── scheduler_runner.py # 调度器 sidecar 进程入口
│   │   ├── models/            # SQLAlchemy 模型
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   ├── routers/           # API 路由（data_source, report, scheduler, explorer, auth）
│   │   ├── services/          # 业务逻辑（连接、报表生成、调度、SQL 校验、SSRF 防护等）
│   │   └── middleware/        # 中间件（限流、安全头、代理头）
│   ├── tests/                 # pytest 测试套件（~200 用例）
│   ├── scripts/               # 开发辅助脚本（seed_demo + seed_reports）
│   ├── alembic/               # 数据库迁移
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/                  # React 19 + TypeScript + Vite
│   ├── src/
│   │   ├── api/              # API 调用封装
│   │   ├── components/       # 公共组件（SqlEditor 等）
│   │   ├── pages/            # 页面组件
│   │   ├── types/            # TypeScript 类型定义
│   │   └── App.tsx           # 顶层布局 + 路由
│   └── package.json
├── deploy/                    # 生产部署配置（systemd、PM2）
├── docker-compose.yml
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

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端地址: http://localhost:5173
后端地址: http://localhost:8000
API 文档: http://localhost:8000/docs

### 3. 默认登录

```
用户名: admin
密码:   admin
```

可在 `backend/.env` 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `JWT_SECRET_KEY` 覆盖。Token 存浏览器 localStorage（access 24h，refresh 7d）。

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
- 连接测试、密码加密存储（Fernet）

### 数据探索
- SQL 查询执行（仅允许 SELECT，sqlglot AST 校验）
- CodeMirror 6 SQL 编辑器，语法高亮
- 模板管理 + 执行历史（localStorage，100 条 FIFO）
- 查询结果导出 CSV（RFC 4180）

### 报表配置
- 可视化拖拽配置报表项（@dnd-kit）
- 支持表格、图表、指标卡、文本 4 种类型
- 自动 SQL 生成或自定义 SQL
- 查询条件、排序、分组配置

### 报表生成
- HTML 预览（Chart.js 可视化，iframe blob-URL + sandbox）
- Excel 导出（openpyxl，多 sheet）
- 定时任务自动生成 + Webhook 通知（HMAC 签名）

### 定时调度
- APScheduler 驱动，Cron 表达式（6 字段）
- Sidecar 部署模式（避免多 worker 重复执行）
- Pydantic 层 Cron 字段范围校验

## API 端点

所有 `/data-sources` `/reports` `/scheduler` `/explorer` 路由需 `Authorization: Bearer <token>` 头。

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | /data-sources | 数据源列表 |
| POST | /data-sources | 创建数据源 |
| GET | /data-sources/{id} | 获取数据源 |
| PUT | /data-sources/{id} | 更新数据源 |
| DELETE | /data-sources/{id} | 删除数据源 |
| POST | /data-sources/{id}/test | 测试连接 |
| GET | /reports | 报表列表 |
| POST | /reports | 创建报表 |
| GET | /reports/{id} | 获取报表详情 |
| PUT | /reports/{id} | 更新报表 |
| DELETE | /reports/{id} | 删除报表 |
| POST | /reports/{id}/items | 添加报表项 |
| PUT | /reports/{id}/items/{item_id} | 更新报表项 |
| DELETE | /reports/{id}/items/{item_id} | 删除报表项 |
| PATCH | /reports/{id}/items/order | 批量更新报表项排序（原子事务） |
| POST | /reports/generate | 生成报表 |
| GET | /reports/{id}/preview | 预览报表 |
| GET | /reports/{id}/export/{format} | 导出报表（html / xlsx） |
| GET | /scheduler/status | 调度器状态 |
| POST | /scheduler/sync | 同步调度器 |
| POST | /scheduler/jobs/{report_id} | 创建/更新定时任务 |
| DELETE | /scheduler/jobs/{report_id} | 删除定时任务 |
| POST | /explorer/query | 执行 SQL 查询 |

### 认证端点

| 方法 | 路径 | 功能 | 鉴权 |
|------|------|------|------|
| POST | /auth/login | 登录，发放 access + refresh token | 无 |
| POST | /auth/refresh | 用 refresh token 换新 access token | Bearer refresh token |
| POST | /auth/logout | 登出（客户端丢弃 token） | 无 |
| GET | /auth/me | 返回当前登录用户 | Bearer access token |

## 测试

```bash
cd backend
source .venv/bin/activate
pip install pytest pytest-asyncio httpx

pytest                  # 全部测试（~200 用例）
pytest -k xss           # 关键字过滤
pytest --lf             # 只跑上次失败的
```

详见 [CLAUDE.md](CLAUDE.md) 测试注意事项。

## Docker 部署

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，设置 JWT_SECRET_KEY / ENCRYPTION_KEY
docker compose up -d
# 访问 http://localhost:8080
```

详细说明见 [DEPLOY.md](DEPLOY.md)。设计模式与架构决策见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。环境变量完整列表见 `backend/.env.example`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。
