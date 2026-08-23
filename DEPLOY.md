# iSee数据分析工作台 - 部署指南

项目结构详见 [README.md](README.md)。环境变量配置详见 `backend/.env.example`（含注释）。

## 快速部署

### 方式一：开发环境部署

#### 1. 克隆项目

```bash
git clone <repository-url> isee-workbench
cd isee-workbench
```

#### 2. 部署后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
# Linux/Mac:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### 3. 部署前端

```bash
cd frontend

# 安装依赖
npm install

# 开发模式运行
npm run dev
```

#### 4. 访问应用

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

> **PDF 导出（批 8.1）**：本地开发需装 weasyprint 系统依赖。
> macOS: `brew install pango cairo gdk-pixbuf libffi`；
> Debian/Ubuntu: 见 `backend/Dockerfile` 的 `apt-get install` 行（`libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 fonts-noto-cjk`）。
> `pip install -r requirements.txt` 装的 `weasyprint` 是 Python 包，**系统库**单独装。

---

### 方式二：生产环境部署

#### 1. 克隆并构建前端

```bash
cd isee-workbench/frontend
npm install
npm run build
```

构建产物在 `frontend/dist/` 目录。

#### 2. 配置后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 使用 Gunicorn + Uvicorn workers（推荐）
pip install gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

#### 3. 使用 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端静态文件
    location / {
        root /path/to/isee-workbench/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API — trailing slash strips the /api prefix before forwarding
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 后端静态文件（Chart.js 等）
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        proxy_set_header Host $host;
    }
}
```

---

### 方式三：Docker 部署

项目已包含完整的 Docker 配置文件，开箱即用。

#### 文件说明

| 文件 | 用途 |
|------|------|
| `backend/Dockerfile` | Python 3.11 后端镜像 |
| `backend/.dockerignore` | 排除 venv、测试等无关文件 |
| `frontend/Dockerfile` | 多阶段构建：Node 编译 + Nginx 服务 |
| `frontend/.dockerignore` | 排除 node_modules、dist |
| `frontend/nginx.conf` | Nginx 配置（SPA fallback + API 代理） |
| `docker-compose.yml` | 编排 backend + frontend + 可选 scheduler/postgres |

#### 架构

```
浏览器 :8080 → frontend (nginx:80)
                  ├── /            → 前端静态文件 (React SPA)
                  ├── /api/*       → 剥离前缀后 proxy_pass → backend:8000/*
                  └── /static/*    → proxy_pass → backend:8000/static/*
```

#### 启动

```bash
# 1. 配置环境变量（参考 backend/.env.example）
cp backend/.env.example backend/.env
# 编辑 backend/.env，至少设置 JWT_SECRET_KEY 和 ENCRYPTION_KEY
vi backend/.env

# 2. 构建并启动
docker compose up -d

# 3. 访问
# http://localhost:8080

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

#### 启动调度器（可选）

默认 web 进程不运行定时任务。如有报表需定时生成，启动 scheduler sidecar：

```bash
docker compose --profile scheduler up -d
```

#### 启动 Prometheus + Grafana（可选）

后端通过 `prometheus-fastapi-instrumentator` 暴露 `/metrics`（默认 HTTP latency / status histogram）+ 4 个自定义 metric（`report_generate_*` / `webhook_delivery_*` / `sql_validator_*`）。`observability` profile 拉起 Prometheus 抓取 `/metrics` 并附带一个预置 dashboard 的 Grafana：

```bash
docker compose --profile observability up -d
```

| 服务 | 端口（容器内 / 主机映射） | 用途 |
|---|---|---|
| Prometheus | `9090` / `${PROMETHEUS_PORT:-9091}` | 抓取 `backend:8000/metrics`，默认 15s 间隔 |
| Grafana | `3000` / `${GRAFANA_PORT:-3001}` | 预置 `isee-workbench` dashboard（9 面板：HTTP RPS / 错误率 / 延迟 p50-p99 / Top 路由 / 报表生成 / SQL 校验 / Webhook 投递） |

首次访问 `http://localhost:3001`，用 `admin` / `admin` 登录（`GF_SECURITY_ADMIN_PASSWORD` 改成自己的）。Dashboard 在 Home → "iSee数据分析工作台"。

如果已有外部 Prometheus 实例，只需要把 `deploy/prometheus/prometheus.yml` 的 `scrape_configs` 段贴进它的配置里，然后 `deploy/grafana/isee-workbench-dashboard.json` 通过 UI "Import dashboard" 导入。

#### 配置告警（可选）

`observability` profile 还会拉起一个 `alertmanager` 容器，规则文件 `deploy/prometheus/alerts/isee-workbench.yml` 会被 Prometheus 自动加载（通过 `prometheus.yml` 的 `rule_files`）。当前 ship 的 7 条规则：

| Alert | 阈值 | 含义 | 默认 severity |
|---|---|---|---|
| `BackendDown` | `up == 0` 持续 1 分钟 | `/metrics` 抓不到，进程崩溃 / OOM / 网络断 | critical |
| `HighErrorRate` | 5xx 比例 > 1%，持续 5 分钟 | 后端在持续抛 5xx | critical |
| `High4xxRate` | 4xx 比例 > 20%，持续 15 分钟 | 客户端参数错 / Token 过期 / 限流触发 | warning |
| `SlowReportGeneration` | `report_generate_duration_seconds` p95 > 30s，持续 10 分钟 | 报表生成慢，可能是数据源慢 / item 多 | warning |
| `HighReportErrorRate` | `report_generate_errors_total` 任意 reason > 0.5/min，持续 10 分钟 | 报表生成持续失败 | warning |
| `WebhookDeliveryFailing` | HTTP 投递失败率 > 10%（仅 `outcome="http_error"`，不含 SSRF guard 阻断），持续 10 分钟 | Webhook 接收端不可达 / 签名过期 | warning |
| `SSRFGuardSurge` | `ssrf_blocked` 或 `https_required` 阻断 > 1/min，持续 15 分钟 | Webhook URL 配错（指向内网或 http） | warning |
| `SQLValidatorSurge` | `sql_validator_rejections_total` 任意 rule > 5/min，持续 5 分钟 | 有客户端 / 用户在试探写非 SELECT | warning |

> 上表 7 条规则；表格列 8 项是因为 SSRFGuardSurge 在 webhook family 里独立一行。每条规则的 summary / description 都按 dashboard panel 同名设置，方便从告警跳 Grafana 面板查细节。

接告警通道前要先做两件事：

1. **打开 prometheus → alertmanager 的 wiring**：`deploy/prometheus/prometheus.yml` 里 `alerting.alertmanagers` 段默认是注释掉的。去掉 `#` 注释，让 Prometheus 把 firing alerts 推给 alertmanager：

   ```yaml
   alerting:
     alertmanagers:
       - static_configs:
           - targets:
               - alertmanager:9093
   ```

2. **写真正的 alertmanager 配置**：`deploy/prometheus/alertmanager.yml` 当前是 no-op stub（所有 alert 都被 `null` receiver 静默 drop，方便本地起 stack 不被自己的告警淹没）。复制成你自己的 `alertmanager.yml`，按 severity 路由到 Slack / 邮件 / PagerDuty：

   ```yaml
   global:
     resolve_timeout: 5m
   route:
     receiver: ops-default
     group_by: [alertname, service]
     routes:
       - matchers: [severity="critical"]
         receiver: pager-critical       # PagerDuty / 短信
       - matchers: [severity="warning"]
         receiver: slack-warnings       # Slack #ops-warnings
   receivers:
     - name: ops-default
     - name: pager-critical
       pagerduty_configs:
         - service_key: <PAGERDUTY_KEY>
     - name: slack-warnings
       slack_configs:
         - api_url: <SLACK_WEBHOOK_URL>
           channel: "#ops-warnings"
   ```

   写好后 bind-mount 进 alertmanager 容器（`docker-compose.yml` 已经预留 mount），重启 `isee-alertmanager` 即可。

**调整阈值**：直接改 `deploy/prometheus/alerts/isee-workbench.yml`，改完 `docker compose restart prometheus`，Prometheus 会自动 reload rules。CI 会跑 `backend/tests/test_alert_rules.py` 校验文件 schema（包括 expr 引用的 metric 必须在 backend 实际发射的清单里），避免 typo 让告警静默失效。

**用 promtool 本地校验**：

```bash
promtool check rules deploy/prometheus/alerts/isee-workbench.yml
```

#### 使用 PostgreSQL（可选）

编辑 `backend/.env`，设置 `DATABASE_URL` 为 PostgreSQL 连接串，然后取消 `docker-compose.yml` 中 `db` 服务的注释：

```bash
docker compose --profile postgres up -d
```

---

## 环境变量配置

后端配置通过 `.env` 文件或环境变量设置：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `APP_NAME` | iSee Data Analysis Workbench | 应用名称 |
| `DEBUG` | false | 调试模式 |
| `DATABASE_URL` | sqlite:///./app.db | 数据库连接 URL |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | 允许的跨域来源（JSON 数组字符串） |
| `ADMIN_USERNAME` | `admin` | 管理员用户名 |
| `ADMIN_PASSWORD` | `admin` | 管理员密码（**生产必改**） |
| `JWT_SECRET_KEY` | （未设则随机生成 + 警告） | JWT HS256 签名密钥；**生产必须显式设置，否则重启 token 全失效** |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `ACCESS_TOKEN_MINUTES` | `1440` (1 天) | Access token 有效期 |
| `REFRESH_TOKEN_DAYS` | `7` | Refresh token 有效期 |
| `ENCRYPTION_KEY` | （未设则随机生成 + 警告） | 数据源密码加密密钥（Fernet）；**生产必须显式设置** |
| `LOGIN_RATE_LIMIT` | `10` | 每 IP 每分钟最大登录尝试次数 |
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `DB_POOL_SIZE` | `5` | 数据库连接池大小（仅 PostgreSQL） |
| `DB_MAX_OVERFLOW` | `10` | 连接池溢出上限（仅 PostgreSQL） |
| `SCHEDULER_DISABLED` | `true` | web 进程跳过调度器（sidecar 模式） |
| `SCHEDULER_RESYNC_INTERVAL` | `30` | sidecar 从 DB 重读调度的间隔（秒） |
| `GENERATED_REPORTS_DIR` | `backend/generated_reports/` | 报表输出目录 |
| `SMTP_HOST` | （空） | 邮件服务器主机（**订阅邮件通知需要设置**）；未配置时 EmailConfig 通知会记 `smtp_unconfigured` 指标并不发送 |
| `SMTP_PORT` | `587` | SMTP 端口（STARTTLS 用 587；implicit SSL 用 465） |
| `SMTP_USER` | （空） | SMTP 认证用户名；留空 = 匿名（适用于 mailhog/mailpit 等本地测试） |
| `SMTP_PASSWORD` | （空） | SMTP 认证密码 |
| `SMTP_FROM_ADDRESS` | （空） | `From:` 邮件地址；空时回落到 `${SMTP_USER}@${SMTP_HOST}` 或 `noreply@${SMTP_HOST}` |
| `SMTP_FROM_NAME` | （空） | `From:` 显示名；空时回落到 `APP_NAME` |
| `SMTP_USE_STARTTLS` | `true` | 587 端口明文连接后升级到 TLS；生产环境一般保持 `true` |
| `SMTP_USE_SSL` | `false` | 465 端口隐式 TLS；与 `SMTP_USE_STARTTLS` 二选一 |

示例 `.env` 文件：

```env
APP_NAME=iSee数据分析工作台
DEBUG=false
DATABASE_URL=sqlite:///./app.db
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-in-production
JWT_SECRET_KEY=<生成一个长随机串>
ENCRYPTION_KEY=<生成一个 Fernet key>
SCHEDULER_DISABLED=true
LOG_LEVEL=INFO

# 邮件通知（订阅邮件投递需要；未设置时 EmailConfig 通知仅记录错误）
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=alerts@example.com
# SMTP_PASSWORD=<smtp 密码>
# SMTP_FROM_ADDRESS=alerts@example.com
# SMTP_FROM_NAME=Alerts Bot
# SMTP_USE_STARTTLS=true
# SMTP_USE_SSL=false
```

---

## 数据库说明

### SQLite（默认）

默认使用 SQLite，数据库文件为 `backend/app.db`。适合开发和小规模使用。

### PostgreSQL（生产环境推荐）

```env
DATABASE_URL=postgresql+psycopg2://username:password@localhost:5432/dbname
```

### 支持的数据库类型

- SQLite（本地文件）
- PostgreSQL
- OpenGauss
- DWS (华为云数据仓库)

---

## 数据库迁移（Alembic）

项目已初始化 Alembic 迁移框架（`backend/alembic/`）。

```bash
cd backend

# 生成迁移（自动检测模型变更）
python -m alembic revision --autogenerate -m "描述"

# 执行迁移
python -m alembic upgrade head

# 回滚一步
python -m alembic downgrade -1
```

---

## 调度器 sidecar 部署

生产环境需单独运行调度器进程。`deploy/` 目录提供两种方式：

### systemd

```bash
sudo cp deploy/isee-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now isee-scheduler
sudo systemctl status isee-scheduler
```

### PM2

```bash
pm2 start deploy/ecosystem.config.js
pm2 save
```

---

## CI/CD

GitHub Actions 工作流（`.github/workflows/ci.yml`）自动执行：

- 后端：ruff lint + mypy 类型检查 + pytest
- 前端：eslint + tsc 类型检查 + Vite 构建

---

## 报表文件输出

生成的报表保存在 `backend/generated_reports/` 目录：

```
backend/generated_reports/
├── 月度销售报表_20260619_162900.html
└── 月度销售报表_20260619_162900.xlsx
```

可以配置 NFS 或云存储进行集中管理。

---

## 定时任务

定时任务使用 APScheduler，需要保持后端服务运行。

查看定时任务状态：

```bash
curl http://localhost:8000/scheduler/status
```

---

## 常见问题

### 1. 端口被占用

```bash
# 查找占用端口的进程
lsof -i :8000
# 或
lsof -i :5173

# 杀死进程
kill -9 <PID>
```

### 2. 前端无法连接后端

检查 CORS 配置是否包含前端地址。

### 3. 数据库连接失败

检查数据库服务是否运行，连接 URL 是否正确。

---

## 生产环境检查清单

- [ ] 设置 `JWT_SECRET_KEY` 为随机长字符串（至少 32 字节）
- [ ] 设置 `ENCRYPTION_KEY` 为 Fernet 密钥
- [ ] 修改默认 `ADMIN_PASSWORD`
- [ ] 调整 `LOGIN_RATE_LIMIT`（默认 10 次/分钟）
- [ ] 启用 HTTPS
- [ ] 配置防火墙规则
- [ ] 设置日志轮转
- [ ] 配置数据库备份策略
- [ ] 监控服务状态
