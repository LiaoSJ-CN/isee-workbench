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

# 启动服务（web 进程；lifespan 自动 alembic upgrade head）
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 另起一个终端跑调度器 sidecar（当 SCHEDULER_DISABLED=true 时必须）
python -m app.scheduler_runner
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

#### 5. 灌示例数据（可选）

```bash
cd backend && source .venv/bin/activate
python scripts/seed_erp_demo.py        # 建 backend/data/erp_demo.db（12 张财务域 warehouse 表）
python scripts/seed_reports.py          # 在 app.db 里建 3 张示例报表（id=1/2/3，蓝色「示例」Tag）
```

不灌也能用，UI 空；灌完后 DataExplorer 模板 + ReportList 立刻有内容。

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
| `backend/Dockerfile` | Python 3.11 后端镜像（含 weasyprint 系统依赖） |
| `backend/.dockerignore` | 排除 venv、测试等无关文件 |
| `frontend/Dockerfile` | 多阶段构建：Node 编译 + Nginx 服务 |
| `frontend/.dockerignore` | 排除 node_modules、dist |
| `frontend/nginx.conf` | Nginx 配置（SPA fallback + API 代理） |
| `docker-compose.yml` | 编排 backend + frontend + 可选 scheduler/postgres/observability |
| `deploy/prometheus/` | Prometheus 配置 + scrape config + alert rules |
| `deploy/grafana/` | Grafana provisioning + 预置 dashboard JSON |
| `deploy/` | systemd service / PM2 config（裸机部署用） |

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

sidecar 必须只跑一个实例；跑多个 = 原 bug（每 worker 跑一次 job）重现。

#### 启动 Prometheus + Grafana（可选）

后端通过 `prometheus-fastapi-instrumentator` 暴露 `/metrics`（默认 HTTP latency / status histogram）+ 4 个自定义 metric（`report_generate_*` / `webhook_delivery_*` / `sql_validator_*`）。`observability` profile 拉起 Prometheus 抓取 `/metrics` 并附带一个预置 dashboard 的 Grafana：

```bash
docker compose --profile observability up -d
```

| 服务 | 端口（容器内 / 主机映射） | 用途 |
|---|---|---|
| Prometheus | `9090` / `${PROMETHEUS_PORT:-9091}` | 抓取 `backend:8000/metrics`，默认 15s 间隔 |
| Grafana | `3000` / `${GRAFANA_PORT:-3001}` | 预置 `isee-workbench` dashboard（9 面板：HTTP RPS / 错误率 / 延迟 p50-p99 / Top 路由 / 报表生成 / SQL 校验 / Webhook 投递） |
| Alertmanager | `9093` / `${ALERTMANAGER_PORT:-9093}` | 接收 Prometheus firing alerts，按 severity 路由（默认 no-op） |

首次访问 `http://localhost:3001`，用 `admin` / `admin` 登录（`GF_SECURITY_ADMIN_PASSWORD` 改成自己的）。Dashboard 在 Home → "iSee数据分析工作台"。

如果已有外部 Prometheus 实例，只需要把 `deploy/prometheus/prometheus.yml` 的 `scrape_configs` 段贴进它的配置里，然后 `deploy/grafana/isee-workbench-dashboard.json` 通过 UI "Import dashboard" 导入。

#### 配置告警（可选）

`observability` profile 还会拉起一个 `alertmanager` 容器，规则文件 `deploy/prometheus/alerts/isee-workbench.yml` 会被 Prometheus 自动加载（通过 `prometheus.yml` 的 `rule_files`）。当前 ship 的 **8 条规则**：

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

> 上表 8 条规则。每条规则的 summary / description 都按 dashboard panel 同名设置，方便从告警跳 Grafana 面板查细节。

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

后端配置通过 `.env` 文件或环境变量设置。`backend/.env.example` 始终是最新源（带详细注释），下面这份表格是精简参考。

### 应用 / 数据库

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `APP_NAME` | `iSee Data Analysis Workbench` | 应用名称 |
| `DEBUG` | `false` | 调试模式 |
| `DATABASE_URL` | `sqlite:///./app.db` | 元数据库连接 URL（PG 也支持，见方式三末段） |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | 允许的跨域来源（JSON 数组字符串） |
| `DB_POOL_SIZE` | `5` | 数据库连接池大小（仅 PostgreSQL） |
| `DB_MAX_OVERFLOW` | `10` | 连接池溢出上限（仅 PostgreSQL） |

### 鉴权 / CSRF / Cookie

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ADMIN_USERNAME` | `admin` | 管理员用户名 |
| `ADMIN_PASSWORD` | `admin` | 管理员密码（**生产必改**） |
| `JWT_SECRET_KEY` | （未设则随机生成 + 警告） | JWT HS256 签名密钥；**生产必须显式设置**，否则重启 token 全失效 |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `ACCESS_TOKEN_MINUTES` | `1440` (1 天) | Access token 有效期 |
| `REFRESH_TOKEN_DAYS` | `7` | Refresh token 有效期 |
| `CSRF_ENABLED` | `true` | `CSRFMiddleware` 是否拒绝非 `cors_origins` 来源的 state-changing 请求 |
| `COOKIE_AUTH_ENABLED` | `true` | 登录 / refresh 是否发 HttpOnly + SameSite Cookie；Header 通道始终备用 |
| `COOKIE_SECURE` | `false` | Cookie `Secure` 标志；**生产 HTTPS 必为 `true`** |
| `COOKIE_SAMESITE` | `lax` | Cookie `SameSite` 策略（`lax` 同时防 CSRF + 允许同站 GET） |
| `ACCESS_COOKIE_NAME` | `access_token` | Access token Cookie 名（多部署同主机时再改） |
| `REFRESH_COOKIE_NAME` | `refresh_token` | Refresh token Cookie 名 |
| `TRUSTED_PROXIES` | （空） | 逗号分隔的 IP / CIDR，nginx / HAProxy 配此项后 `ProxyHeadersMiddleware` 才能从 `X-Forwarded-For` 还原真实 IP；默认空（直连部署安全） |
| `LOGIN_RATE_LIMIT` | `10` | 每 IP 每分钟最大登录尝试次数 |
| `DEFAULT_ORG_ID` | `null` | 批 13 多租户：写入种子 `admin` 用户的 `org_id`，启用 `org` 可见性档位。空 = 单租户部署，`org` tier 模板视为跨租户不命中。仅对首次 seed 的 admin 生效；存量用户须手动 SQL 修 |

### API 限流（批 6b.2）

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `EXPLORER_QUERY_RATE_LIMIT` | `30` | `/explorer/query` 每 IP 每分钟上限 |
| `REPORTS_GENERATE_RATE_LIMIT` | `10` | `/reports/generate` + `/reports/{id}/jobs` 入队每 IP 每分钟上限（共用预算） |

### 安全头 / Webhook

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SECURITY_HEADERS_ENABLED` | `true` | `SecurityHeadersMiddleware` 是否附加 `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` / `Permissions-Policy` |
| `WEBHOOK_SECRET` | （空） | HMAC-SHA256 共享密钥；接收方用同密钥校验 `X-Webhook-Signature` |
| `WEBHOOK_HTTPS_ONLY` | `false` | Webhook URL 必须 HTTPS；本地测试可关 |
| `WEBHOOK_TIMESTAMP_MAX_AGE` | `300` | Webhook 时间戳最大允许秒数（防 replay），5 min 默认 |
| `PUBLIC_BASE_URL` | （空） | 对外站点根地址，如 `https://isee.example.com`。IM 通知卡片用它拼「查看报表/看板」按钮；留空则卡片不带按钮，企业微信额外回落为 markdown（`template_card` 的 `card_action` 需要 URL） |

### 数据源 / 报表

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ENCRYPTION_KEY` | （未设则随机生成 + 警告） | 数据源密码 Fernet 密钥；**生产必设** |
| `EXPLORER_MAX_ROWS` | `10000` | `/explorer/query` 行上限（防 OOM） |
| `EXPLORER_STATEMENT_TIMEOUT` | `30` | Explorer PG 类查询 statement timeout（秒）；`0` = 无超时；SQLite 忽略 |
| `GENERATED_REPORTS_DIR` | `backend/generated_reports/` | 报表输出目录 |

#### 数据源密码轮换（批 E）

admin-only 端点 `POST /admin/data-sources/{id}/rotate-password`，用于响应泄漏 / 定期轮换 / 离职交接：

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

# 模式 1：admin 已知新密码（运维同步过来的）
curl -s -X POST localhost:8000/admin/data-sources/1/rotate-password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"new_password":"hunter2"}' | jq
# {"data_source_id":1,"rotation_method":"admin_supplied",
#  "rotated_at":"...","generated_password":null}

# 模式 2：服务器生成强随机密码，明文只显示一次
curl -s -X POST localhost:8000/admin/data-sources/1/rotate-password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' | jq
# {"data_source_id":1,"rotation_method":"server_generated",
#  "rotated_at":"...","generated_password":"<24-字符随机串>"}
```

UI 入口：DataSource 列表每行 `[轮换密码]` 按钮（admin-only），弹窗内可二选一。

**行为**：旋转后立即清空 cached SQLAlchemy 引擎（下次连接用新凭据重建）；写入 `data_source.password_rotated` 审计行；新密码**不会**写进 audit snapshot（仅 `rotation_method` metadata）。

**审计追溯**：`GET /audit-logs?action=data_source.password_rotated` 即可过滤所有轮换事件。

### 调度器

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SCHEDULER_DISABLED` | `true` | web 进程跳过调度器（sidecar 模式） |
| `SCHEDULER_RESYNC_INTERVAL` | `30` | sidecar 从 DB 重读调度的间隔（秒） |

### Demo 数据自动恢复

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SEED_DEMO_ON_STARTUP` | `false` | dev 用：lifespan 启动时若 `data_sources` 表为空，自动重建 ERP 演示 warehouse + `sqlite_demo` DataSource 行 + 3 张 demo 报表。生产**必须保持 `false`**；空表判断兜底，prod 即便误开也不会覆盖已有数据。 |

### 日志 / 监控

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `SENTRY_DSN` | （空） | 后端 Sentry DSN；空 = 禁用；设置后自动 init，每个事件打 `request_id` tag |
| `SENTRY_ENVIRONMENT` | （空） | Sentry 环境标签（`production` / `staging`）；空 = SDK 默认 `development` |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | 性能追踪采样率（0.0-1.0)；0 = 禁用 |

### Sentry 错误监控

iSee 前/后端均支持 Sentry（默认关闭）。打开后未处理异常与性能追踪会上报到 Sentry 项目；空 DSN 时 SDK 不加载，runtime / bundle 零开销。

#### 1. 申请 DSN

在 [sentry.io](https://sentry.io)（或自托管 Sentry）创建项目，得到形如 `https://<key>@o<org>.ingest.sentry.io/<project>` 的 DSN。前/后端**使用同一个 DSN**（自动按 `environment` 区分来源）。

#### 2. 后端配置

在 `backend/.env` 设置三个环境变量：

```env
SENTRY_DSN=https://<key>@o<org>.ingest.sentry.io/<project>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1   # 0.0 = 关闭性能追踪；高流量部署用 0.05 即可
```

后端 `app.middleware.sentry.init_sentry()` 在 lifespan 启动时检测 DSN：
- DSN 空 → 直接 `return False`，SDK 不导入，零运行时成本
- DSN 设 → 注册 FastAPI / Starlette / logging 三组 integration，并通过 `before_send` 把当前 `request_id`（来自 request-id middleware）打到事件 `tags.request_id` 上

事件过滤：所有 `HTTPException`（包括 4xx 响应）通过 `before_send` 自动丢弃，避免 404 / 422 噪音冲掉 issue 流。

启动后日志形如 `Sentry initialized (environment=production, traces_sample_rate=0.1)` 表示已生效。

#### 3. 前端配置

前端通过 Vite 构建时变量注入（写入 `frontend/.env` 或构建命令前 `VITE_SENTRY_*=...`）：

```env
VITE_SENTRY_DSN=https://<key>@o<org>.ingest.sentry.io/<project>
VITE_SENTRY_ENVIRONMENT=production
VITE_SENTRY_TRACES_SAMPLE_RATE=0.1
```

前端 `src/utils/sentry.ts::initSentry()` 在 `main.tsx` 启动时检测：
- `VITE_SENTRY_DSN` 空 → 直接 `return false`，`@sentry/react` 模块仍然在 bundle 里但 `Sentry.init` 不调用，无网络流量
- DSN 设 → 调用 `Sentry.init`，启用 React Router / BrowserTracing integration

`tracesSampleRate` 未设置时**默认 0.1**（不同于后端的 0.0）—— 前端性能追踪对单用户影响小，开默认能更快收集到 perf 数据。

#### 4. 验证

启动后端 / 重建前端后，手动触发一个未处理异常（任意 endpoint 故意 throw 即可），Sentry UI 的 **Issues** 页应在 10 秒内出现该事件，**Tags** 面板显示 `request_id`（后端）或 `release`（前端）。前端可在浏览器 DevTools Network 面板观察 `ingest.sentry.io` 出站请求。

#### 关闭

直接把 DSN 设为空字符串或删除环境变量即可。无需改代码——SDK 检测到 DSN 空后完全跳过初始化。

### 邮件（订阅邮件投递）

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SMTP_HOST` | （空） | SMTP 服务器主机（订阅邮件通知）；未配置时 EmailConfig 通知记 `smtp_unconfigured` 指标并不发送 |
| `SMTP_PORT` | `587` | SMTP 端口（STARTTLS 用 587；implicit SSL 用 465） |
| `SMTP_USER` | （空） | SMTP 用户名；空 = 匿名（mailpit 等本地测试） |
| `SMTP_PASSWORD` | （空） | SMTP 密码 |
| `SMTP_FROM_ADDRESS` | （空） | `From:` 地址；空时回落 `${SMTP_USER}@${SMTP_HOST}` 或 `noreply@${SMTP_HOST}` |
| `SMTP_FROM_NAME` | （空） | `From:` 显示名；空时回落 `APP_NAME` |
| `SMTP_USE_STARTTLS` | `true` | 587 端口明文连接后升级 TLS；生产一般保持 `true` |
| `SMTP_USE_SSL` | `false` | 465 端口隐式 TLS；与 STARTTLS 二选一 |

### 审计

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `AUDIT_LOG_RETENTION_DAYS` | `0` | 审计日志保留天数；`0` = 永久保留（需 operator 手动 cron purge）。`> 0` 时 web 进程 lifespan **不**自动清——见 `app.services.audit.purge_old_audit_logs`，operator 自行接 cron / sidecar |

### 前端镜像变量

后端变量在 `backend/.env` 设；前端运行时变量在 `frontend/.env` 设（构建时通过 Vite 注入）。空值 = 禁用，bundle 零开销。前端 Sentry 配置见下一节。

### 示例 `.env`（生产最小集）

```env
APP_NAME=iSee数据分析工作台
DEBUG=false
DATABASE_URL=sqlite:///./app.db
CORS_ORIGINS=["https://your-domain.com"]
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<强密码>
JWT_SECRET_KEY=<secrets.token_urlsafe(48)>
ENCRYPTION_KEY=<Fernet.generate_key().decode()>
SCHEDULER_DISABLED=true
LOG_LEVEL=INFO
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
TRUSTED_PROXIES=10.0.0.0/8,127.0.0.1/32   # nginx 同主机时

# 邮件订阅（不设 = 邮件订阅仅记错误指标）
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=alerts@example.com
# SMTP_PASSWORD=<smtp 密码>
# SMTP_FROM_ADDRESS=alerts@example.com

# 审计保留期（0 = 永久）
# AUDIT_LOG_RETENTION_DAYS=0
```

---

## 通知与订阅（批 8.3 / 批 8.4）

报表可以绑定 4 种通知渠道 + 订阅投递；**邮件**靠环境变量，**IM / Webhook** 走 Report 的 `notification_config` 字段（每个报表独立配）。

### 邮件通知（订阅邮件投递）

- 走 `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM_ADDRESS` 等环境变量
- 未设置时 `EmailConfig` 通知仅记 `smtp_unconfigured` 指标 + ERROR 日志，**不抛异常**
- 本地开发推荐 [mailpit](https://github.com/axllent/mailpit)（SMTP 收信 UI + REST API）：`docker run -p 1025:1025 -p 8025:8025 axllent/mailpit:latest`，`.env` 设 `SMTP_HOST=localhost SMTP_PORT=1025 SMTP_USE_STARTTLS=false SMTP_USE_SSL=false SMTP_USER= SMTP_PASSWORD=`
- 端口选择：`587` + STARTTLS（推荐）、`465` + implicit SSL；两者二选一

### IM 机器人通知（钉钉 / 飞书 / 企业微信）

- **无需环境变量**——每个报表在编辑器里配自己的机器人
- 钉钉 (DingTalk) / 飞书 (Feishu / Lark) / 企业微信 (WeChatWork) 三种独立 variant（见 `app/schemas/notification.py`），按机器人类型选 `type`，填 `webhook_url`
- 飞书可选 `secret`（机器人安全设置里那个 sign secret）开启签名；钉钉 / 企微可选 `secret`
- **SSRF 防护**：`webhook_https_only` 默认 `false` 允许 http；内网地址（127.0.0.0/8、10.0.0.0/8、172.16/12、192.168/16、::1、169.254/16 等保留段）会被 `ssrf_guard` 模块**直接拒**，无论 IM 还是通用 Webhook——别指向 `localhost:8025` 这种本地测试端，部署到同主机会被拦
- 通用 Webhook（自定义 HMAC 签名 POST）走 `WebhookConfig` variant，配 `url` + 可选 `secret`

### 报表订阅（批 8.3）

- 端点：`POST/GET/PATCH/DELETE /subscriptions` —— "我订阅这个报表，每次跑完推送给我"
- 不需要单独启——`/api/subscriptions` 跟随 web 进程自动可用
- 前端入口：左侧导航 "我的订阅" 或报表详情页 "订阅" 按钮 → 选 IM / 邮件渠道 + 接收人
- 投递失败会重试 3 次（指数退避），仍失败写 ERROR 日志 + `webhook_delivery_*` metric，不会阻塞下次调度

---

## 看板 Dashboard（批 14）

看板（Dashboard）把多个报表 / 图表 / 文本块拼成一个 grid，支持 cron 订阅聚合推送。**部署前了解三点**：

### 看板是什么 / 怎么用

- **看板** = 一个 12 列 grid，每个 cell 是一个 item，三种 `item_type`：
  - `report` —— 嵌入一张已有报表（`report_id`）
  - `chart` —— 在看板里直接写 SQL 跑图表（`data_source_id` + `table_name` / `fields` / `where_conditions` / `group_by` / `order_by` / `limit` / 可选 `custom_sql`）
  - `text` —— 静态标题 / 说明文字（不参与增量 dedup）
- ACL 跟报表对齐：三种 `visibility`（`private` / `org` / `public`）+ 显式 `DashboardAccess` 列表
- 端点：`/dashboards` / `/dashboards/{id}` / `/dashboards/{id}/items` / `/dashboards/{id}/layout` / `/dashboards/{id}/shares` / `/dashboards/{id}/preview` / `/dashboards/{id}/items/{item_id}/preview` / `/dashboards/{id}/duplicate`
- 前端入口：左侧导航 "看板" → 列表 / 编辑 / 详情三页；详情页每个 cell 内嵌 iframe（axios 取 HTML → blob URL；批 14.7 修复 iframe auth 401 问题）

### 看板订阅（批 14.4 — 增量 dispatch）

- 端点：`/dashboard-subscriptions`（CRUD，跟报表订阅同形）
- **增量去重**：dispatcher 计算整张看板的 fingerprint（hash of `report.updated_at` + `chart rows hash`，text 不参与），与上次 tick 相同则 **不发** 通知——只刷 `last_run_at`。该列（`last_fingerprint`）写入 `dashboard_subscriptions` 表（migration `e4f1b2c3a5d6`），首次必发
- **共享通知渠道**：复用 8.4 那套 webhook / 钉钉 / 飞书 / 企微 / 邮件 sender。dashboard 通过 `SimpleNamespace(id=, name=)` shim 喂给 sender，只用 `.id` + `.name` 两个字段
- **输出文件**：rendered HTML 写到 `settings.generated_reports_dir`（默认 `backend/generated_reports/`），命名 `<safe>_<ts>_<rand>.html`，跟报表输出同目录——glob 清理脚本不用改
- **dispatcher 位置**：sidecar 进程跑（`python -m app.scheduler_runner`）；跟报表 dispatch 共享同一个 APScheduler 实例，job id 用 `dsub_<id>` 命名空间防止冲突

### 已知限制

- **iframe 预览局限**：看板 HTML 自带 `<script src="https://cdn.jsdelivr.net/.../chart.umd.min.js">`——CDN 不可达时图表空白但页面其它部分正常。如要完全离线部署可把 chart.js 静态文件放进 `backend/static/` 然后改 `services/dashboard.py` 里的 URL（search-replace：`chart.js@4.4.1/dist/chart.umd.min.js`，全文两处）
- **chart fingerprint SQL 执行**：增量去重对每个 chart item 跑一次 SQL。dashboard 含 N 个 chart 时，每 tick 多 N 次 query——SQL 大 / 实时性差时考虑加大 cron 间隔
- **chart hash SQL 失败**：执行异常时 fingerprint token 记为 `err:<repr>` 强制下次发送——操作员能在通知 HTML 里看到 inline error chart
- **text item 改文字**不会触发通知（按设计——静态文本不算"看板变了"）

---

## RBAC 与审计日志（批 9.3 / 9.4 / 9.5 / 11.1）

### 数据源 RBAC（批 9.3）

- 每个 DataSource 默认仅 owner 可访问
- Owner 可通过 `/data-sources/{id}/grants` 授权其他用户 `read` 或 `write`
- 删除 owner = 数据源变孤儿，admin 可读不可改

### 报表 RBAC（批 9.4）

- 报表默认仅 owner 可访问
- 三种 `visibility`：`private` / `link` / `internal`，加显式 `share` 列表叠加判定
- `/reports/{id}/duplicate` 复制报表时复制所有 item + 参数 + share 配置（owner 变执行者）

### 审计日志（批 9.5 / 批 11.1）

管理员专属审计表（用户 CRUD / 数据源变更 / 报表克隆 / 调度变更 / 订阅 CRUD / 任务入队等高敏动作）。**部署前了解两点**：

- **端点**：`GET /audit-logs`（admin only，非 admin 403）。前端入口在左侧导航 "审计日志"（admin 用户可见）。支持 `actor_user_id` / `action` / `target_type` / `target_id` / `request_id` / `ip_address` / `since` / `until` 过滤
- **保留期**：见上文 `AUDIT_LOG_RETENTION_DAYS`。lifespan 不自动 purge——operator 自行接 cron 调 `purge_old_audit_logs(db, days)`，或 `0` 表示永久保留
- **request_id 跨链**：每条 HTTP 请求的 `X-Request-ID` 写入所有相关 audit row + 日志，方便从日志反查所有审计事件
- **索引**：`actor_user_id` / `target_type+target_id` / `created_at` 都建了索引，过滤查询走索引扫描（O(log n)）

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

### PostgreSQL / OpenGauss 兼容性验证（批 11.2）

CI（`.github/workflows/ci.yml` 的 `backend-test-pg`）每次 push 都跑 PG16 容器：

- `alembic upgrade head` — DDL/FK/CHECK 与 Postgres 方言对齐检查
- 23 个 PG-safe pytest 文件（auth/csrf/jwt/RBAC/validator/scheduler_runner/metrics/sentry/...），不依赖 SQLite 默认宽容的 FK 行为
- 完整 685 用例仍在 SQLite 上跑（部分 cleanup 路径靠 SQLite 容错，等单独清理）

**手动验证 OpenGauss**（CI 还没接 OpenGauss image，需 operator 自验）：

```bash
cd backend
source .venv/bin/activate

# 1. 起一个 openGauss 容器（任一官方镜像，例如 opengauss/opengauss:3.0.0）
docker run -d --name isee-og -e GS_PASSWORD=isee@123 -p 5432:5432 \
  opengauss/opengauss:3.0.0

# 2. alembic upgrade head —— 这一步如有 DDL/fk 差异会直接挂
DATABASE_URL='postgresql+psycopg2://gaussdb:isee@123@localhost:5432/postgres' \
  alembic upgrade head

# 3. 跑 PG-safe pytest 子集
DATABASE_URL='postgresql+psycopg2://gaussdb:isee@123@localhost:5432/postgres' \
  pytest tests/test_db_migrations.py tests/test_jwt.py tests/test_auth.py \
    tests/test_rbac_auth.py tests/test_rbac_deps.py \
    tests/test_sql_validator.py tests/test_sql_validator_property.py \
    -q
```

OpenGauss 是 PG 9.2 fork，绝大多数 SQL + Alembic 兼容。差异（`ANALYZE` 行为、`PG_PARTITION`）只在 SaaS 扩展用到时才需要修。

---

## 数据库迁移（Alembic）

项目已初始化 Alembic 迁移框架（`backend/alembic/`）。Web 进程启动时 lifespan 自动跑 `alembic upgrade head`，**不要再手动 `create_all`**。`ensure_columns()` 已废弃。

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

GitHub Actions 工作流（`.github/workflows/ci.yml`）每次 push 自动执行：

- 后端：ruff lint + **ruff format --check** + mypy 类型检查 + pytest（685 用例 + 4 跳过）
- 后端：PG16 容器跑 `backend-test-pg` job（alembic + 23 PG-safe pytest）
- 前端：eslint + prettier --check + tsc 类型检查 + Vite 构建 + vitest
- 缓存：pip + npm cache 复用，minimize wall-clock

格式 / 文档不一致会被 CI 拦下：

- `ruff format --check .` 飘 → 后端 reformat 没做
- `prettier --check` 飘 → 前端 reformat 没做
- 故意改 doc 看 `scripts/diff_docs_vs_code.py` 飘 → README/DEPLOY 与代码不同步

---

## 报表文件输出

生成的报表保存在 `backend/generated_reports/` 目录：

```
backend/generated_reports/
├── 月度销售报表_20260619_162900.html
├── 月度销售报表_20260619_162900.xlsx
└── 月度销售报表_20260619_162900.pdf
```

可以配置 NFS 或云存储进行集中管理。

---

## 异步报表任务（批 3a / 批 8.5）

- 入队：`POST /reports/{id}/jobs` → 返回 `ReportJob`（status=`pending`）
- 轮询：`GET /jobs/{id}` 看 status（`pending` → `running` → `done` / `failed`）
- 下载：**直接走 worker 产物** `GET /jobs/{id}/download`（批 8.5 起取代 export 同步重渲，避免 worker 白做功）
- 历史：`GET /reports/{id}/jobs?status=done&limit=20`

worker 跑在 web 进程内（线程池），不需要单独 sidecar。

---

## 常见问题

### 1. 端口被占用

```bash
lsof -i :8000
lsof -i :5173
kill -9 <PID>
```

### 2. 前端无法连接后端

检查 `CORS_ORIGINS` 是否包含前端地址，反代模式下检查 `TRUSTED_PROXIES` 是否设上。

### 3. 数据库连接失败

检查数据库服务是否运行，连接 URL 是否正确；PG 还需确认 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` 没打爆。

### 4. PDF 导出失败

最常见是 weasyprint 系统库没装：`brew install pango cairo gdk-pixbuf libffi`（macOS）或 `apt-get install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 fonts-noto-cjk`（Debian）。Dockerfile 已经装好。

---

## 生产环境检查清单

- [ ] 设置 `JWT_SECRET_KEY` 为随机长字符串（至少 32 字节）
- [ ] 设置 `ENCRYPTION_KEY` 为 Fernet 密钥
- [ ] 修改默认 `ADMIN_PASSWORD`
- [ ] 调整 `LOGIN_RATE_LIMIT`（默认 10 次/分钟）
- [ ] 启用 HTTPS
- [ ] `COOKIE_SECURE=true`、`COOKIE_SAMESITE=lax`
- [ ] `TRUSTED_PROXIES` 配上 nginx 网段
- [ ] 配置防火墙规则
- [ ] 设置日志轮转
- [ ] 配置数据库备份策略
- [ ] 监控服务状态（启用 `observability` profile + Prometheus scrape backend `/metrics`）
- [ ] 若启用邮件订阅：设置 `SMTP_HOST` + `SMTP_PASSWORD` + `SMTP_FROM_ADDRESS`（不设则订阅邮件仅记错误）
- [ ] 若启用 IM 通知：webhook URL 不能指向内网（SSRF guard 拒）；钉钉/飞书/企微机器人"安全设置"加 IP 白名单或签名验证
- [ ] 决定 `AUDIT_LOG_RETENTION_DAYS`：不设 = 永久保留；>0 时必须配 cron 调 `purge_old_audit_logs` 防止日志无限增长
- [ ] 调度器 sidecar 只跑一个实例（多实例 = 同一 job 每个 tick 跑 N 次）
- [ ] PDF 导出要装 weasyprint 系统依赖（Dockerfile 已装，裸机部署需手动）