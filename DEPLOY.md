# 经营分析报表系统 - 部署指南

## 项目结构

```
business_analysis_report_tools/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py        # 应用入口
│   │   ├── config.py      # 配置
│   │   ├── database.py    # 数据库
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic schemas
│   │   ├── routers/      # API 路由
│   │   └── services/     # 业务服务
│   ├── requirements.txt   # Python 依赖
│   └── generated_reports/ # 生成的报表文件
├── frontend/              # React + Vite 前端
│   ├── src/
│   │   ├── api/          # API 调用
│   │   ├── pages/        # 页面组件
│   │   └── types/        # TypeScript 类型
│   ├── package.json
│   └── dist/             # 构建产物（生产环境用）
└── README.md
```

## 快速部署

### 方式一：开发环境部署

#### 1. 克隆项目

```bash
git clone <repository-url> business_analysis_report_tools
cd business_analysis_report_tools
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

---

### 方式二：生产环境部署

#### 1. 克隆并构建前端

```bash
cd business_analysis_report_tools/frontend
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
        root /path/to/business_analysis_report_tools/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket 支持（如果需要）
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

### 方式三：Docker 部署

#### Dockerfile (后端)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### docker-compose.yml

```yaml
version: '3.8'

services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    ports:
      - "8000:8000"
    volumes:
      - ./backend/generated_reports:/app/generated_reports
    environment:
      - DATABASE_URL=sqlite:///./app.db

  frontend:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
```

---

## 环境变量配置

后端配置通过 `.env` 文件或环境变量设置：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `APP_NAME` | Business Analysis Report Backend | 应用名称 |
| `DEBUG` | false | 调试模式 |
| `DATABASE_URL` | sqlite:///./app.db | 数据库连接 URL |
| `CORS_ORIGINS` | http://localhost:5173 | 允许的跨域来源 |

示例 `.env` 文件：

```env
APP_NAME=经营分析报表系统
DEBUG=false
DATABASE_URL=sqlite:///./app.db
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
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

- [ ] 修改默认 `APP_SECRET_KEY`
- [ ] 启用 HTTPS
- [ ] 配置防火墙规则
- [ ] 设置日志轮转
- [ ] 配置备份策略
- [ ] 监控服务状态
