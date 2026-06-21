# 更新日志

本项目的所有重要变更都会记录在此文件中。

## [未发布]

### Bug 修复
- 前端 `DisplayConfig` 字段命名统一为 snake_case：之前表单用 `showLegend`/`legendPosition`/`showGrid` 等 camelCase，但后端 Pydantic 是 snake_case，Pydantic v2 默认 `extra='ignore'` 会**静默丢弃**用户切换的图例/网格线等开关。新增 3 个回归测试（`test_display_config_drops_unknown_camelcase_keys` 等）锁住「camelCase 必须被忽略」这条契约，避免后续加 `populate_by_name` 时回退

### 计划中
见 `~/.claude/projects/-Users-liaosj-Documents-code-business-analysis-report-tools/memory/known-todos.md`

---

## [0.2.1] - 2026-06-21

### 安全修复
- 修复 `report_generator.py` 中 `table_name` 的 SQL 注入面（虽然其他子句已用参数化，但 table_name 是拼接进 SQL 字符串的）。正则 typo `0-_` 收紧为 `0-9_`

### 新增
- 调度器 sidecar 进程 (`python -m app.scheduler_runner`)：web 进程设 `SCHEDULER_DISABLED=true` 跳过 tick，多 worker 部署不再重复执行同一 job
- Cron 表达式字段范围在 Pydantic 层校验（`ScheduleTaskCreate._validate_cron` 委托给 `CronTrigger`），越界值 422
- `Report.notification_config` 持久化 + UI「通知方式」Select（`none`/`webhook`/`email`）+ 条件 webhook URL 输入
- `ReportGenerator.sync_with_database` 改为 reconcile（DELETE 后的孤儿 job 会被清掉）
- DataExplorer 执行历史面板（localStorage `sqlHistory:v1`，100 条 FIFO + 5s dedup）
- 模块级 SQLAlchemy engine cache（按 `DataSource.id` 复用连接池），DataSource PUT/DELETE 时 `evict_engine()` 显式失效

### Bug 修复
- `ReportGenerator._format_value` 用 `numbers.Integral`/`numbers.Real` ABC（numpy ≥ 2.0 移除了 `np.int64`/`np.float64` 对 `int`/`float` 的继承）

### 测试
- `backend/tests/` pytest 套件取代 `scripts/smoke_*.py`（80 个用例，~0.2s）

---

## [0.2.0] - 2026-06-20

### 安全修复
- **严重**: 修复 `report_generator.py` 中的 SQL 注入漏洞，使用参数化查询
- **严重**: 在 `ReportPreview.tsx` 中添加 XSS 防护，使用 DOMPurify 进行 HTML 消毒
- 修复 `scheduler.py` 中的异常吞没问题，改为正确的日志记录
- 预览 iframe 改为 blob-URL 模式：前端用 `Authorization` 头取 HTML → `URL.createObjectURL(new Blob([html]))` → `iframe.src`，消除 `?token=` 出现在 URL 中泄漏到浏览器历史/访问日志的风险
- `report_generator.render_html` 接受可选 `base_url` 参数并在 HTML head 注入 `<base href>`，保证相对路径 `/static/chart.umd.min.js` 在 blob-URL iframe 上下文（以及导出的离线 HTML 文件）中能正确解析到后端

### 代码质量
- 修复 ESLint 警告 (set-state-in-effect, exhaustive-deps)
- 修复后端 ruff 检查问题
- 修复 `formatSql` 函数的幂等性问题
- 清理 `backend/app/main.py` 中重复的 `app.mount("/static", ...)` 块
- 修复 `report_generator.py` 中 `html_parts.extend([...])` 缺少闭括号的语法错误（该文件之前无法被 import）

### 前端优化
- DataExplorer 用户体验优化：内联模板编辑，无需弹窗
- 模板名称始终可编辑
- 保存按钮同时支持新建和更新模板
- 添加未保存更改状态跟踪 (`isDirty`)

### 依赖更新
- 添加 `isomorphic-dompurify` 用于 HTML 消毒
- 添加 `dompurify` 类型定义

---

## [0.1.0] - 2026-06-19

### 新增
- 经营分析报表系统 MVP 初始版本
- 后端：FastAPI + SQLAlchemy
- 前端：React + TypeScript + Vite
- 数据源管理（支持 PostgreSQL、SQLite、OpenGauss、DWS）
- 报表定义和生成
- 报表预览（Chart.js 可视化）
- SQL 数据探索器（语法高亮）
- 定时任务执行
- Excel 和 HTML 导出格式


## [0.2.0] - 2026-06-20

### 安全修复
- **严重**: 修复 `report_generator.py` 中的 SQL 注入漏洞，使用参数化查询
- **严重**: 在 `ReportPreview.tsx` 中添加 XSS 防护，使用 DOMPurify 进行 HTML 消毒
- 修复 `scheduler.py` 中的异常吞没问题，改为正确的日志记录
- 预览 iframe 改为 blob-URL 模式：前端用 `Authorization` 头取 HTML → `URL.createObjectURL(new Blob([html]))` → `iframe.src`，消除 `?token=` 出现在 URL 中泄漏到浏览器历史/访问日志的风险
- `report_generator.render_html` 接受可选 `base_url` 参数并在 HTML head 注入 `<base href>`，保证相对路径 `/static/chart.umd.min.js` 在 blob-URL iframe 上下文（以及导出的离线 HTML 文件）中能正确解析到后端

### 代码质量
- 修复 ESLint 警告 (set-state-in-effect, exhaustive-deps)
- 修复后端 ruff 检查问题
- 修复 `formatSql` 函数的幂等性问题
- 清理 `backend/app/main.py` 中重复的 `app.mount("/static", ...)` 块
- 修复 `report_generator.py` 中 `html_parts.extend([...])` 缺少闭括号的语法错误（该文件之前无法被 import）

### 前端优化
- DataExplorer 用户体验优化：内联模板编辑，无需弹窗
- 模板名称始终可编辑
- 保存按钮同时支持新建和更新模板
- 添加未保存更改状态跟踪 (`isDirty`)

### 依赖更新
- 添加 `isomorphic-dompurify` 用于 HTML 消毒
- 添加 `dompurify` 类型定义

---

## [0.1.0] - 2026-06-19

### 新增
- 经营分析报表系统 MVP 初始版本
- 后端：FastAPI + SQLAlchemy
- 前端：React + TypeScript + Vite
- 数据源管理（支持 PostgreSQL、SQLite、OpenGauss、DWS）
- 报表定义和生成
- 报表预览（Chart.js 可视化）
- SQL 数据探索器（语法高亮）
- 定时任务执行
- Excel 和 HTML 导出格式
