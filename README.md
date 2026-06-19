# 经营分析报表系统

配置化生成经营分析报表的系统，支持可视化拖拽配置。

## 项目结构

```
business_analysis_report_tools/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # 应用入口
│   │   ├── config.py       # 配置
│   │   ├── database.py     # 数据库
│   │   ├── models/         # SQLAlchemy 模型
│   │   │   ├── data_source.py
│   │   │   └── report.py
│   │   ├── schemas/        # Pydantic schemas
│   │   │   ├── data_source.py
│   │   │   └── report.py
│   │   ├── routers/        # API 路由
│   │   │   ├── data_source.py
│   │   │   ├── report.py
│   │   │   └── scheduler.py
│   │   └── services/       # 业务服务
│   │       ├── connection.py
│   │       ├── report_generator.py
│   │       └── scheduler.py
│   └── pyproject.toml
├── frontend/               # React + Vite 前端
│   ├── src/
│   │   ├── api/           # API 调用
│   │   ├── components/
│   │   ├── pages/         # 页面组件
│   │   │   ├── DataSourceList.tsx
│   │   │   ├── ReportList.tsx
│   │   │   ├── ReportEditor.tsx
│   │   │   ├── ReportPreview.tsx
│   │   │   └── Scheduler.tsx
│   │   ├── types/
│   │   └── App.tsx
│   └── package.json
└── README.md
```

## 快速启动

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # 或直接 pip install -e .

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

## 功能特性

### 数据源管理
- 支持 OpenGauss、DWS、PostgreSQL
- 数据库连接测试
- 密码加密存储

### 报表配置
- 可视化拖拽配置报表项
- 支持表格、图表、指标卡、文本 4 种类型
- 自动 SQL 生成或自定义 SQL
- 查询条件、排序、分组配置

### 报表生成
- HTML 预览
- Excel 导出
- 支持定时任务自动生成

### 定时任务
- APScheduler 驱动
- Cron 表达式配置
- Webhook 通知

## API 端点

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
| POST | /reports/generate | 生成报表 |
| GET | /reports/{id}/preview | 预览报表 |
| GET | /reports/{id}/export/{format} | 导出报表 |
| GET | /scheduler/status | 调度器状态 |
| POST | /scheduler/sync | 同步调度器 |
| POST | /scheduler/jobs/{report_id} | 创建定时任务 |
| DELETE | /scheduler/jobs/{report_id} | 删除定时任务 |
