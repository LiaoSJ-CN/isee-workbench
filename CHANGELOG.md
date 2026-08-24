# 更新日志

本项目的所有重要变更都会记录在此文件中。

## [未发布]

### 新增
- `ensure_columns` 启动期 schema 自愈：补齐 `create_all` 漏掉的「已存在表 + 新增列」（`Base.metadata.create_all` 只建表不补列）。新模块 `app/db_migrations.py`，在 `main.py` 启动时于 `create_all` 之后调用。幂等，`NOT NULL` 无 `server_default` 的列会 log warning + 跳过（要求人工迁移）
- 调度器暂停/恢复 UI：Scheduler 页面每个定时任务增加 Pause/Resume 按钮和绿/橙状态标签，通过现有 POST 端点传递 `is_active` 控制启停
- **RBAC（批 9.1–9.4）**：多人协作基础
  - `User` 模型加 `role` (`admin`/`editor`/`viewer`) 和 nullable `org_id`（多租户 seam）；JWT payload 带 `uid`/`role`/`oid`；`GET /auth/me` 返回新字段
  - `deps.require_role()` 原语 + `admin_required` / `editor_required` 高阶依赖
  - DataSource ACL：每行 `owner_user_id` + `data_source_access` grants 表（`read`/`write`，upsert 语义）。所有 DS 端点、explorer 查询、report generate/preview/export、jobs 端点全部 ACL-gated。Grant 端点 owner-or-admin only
  - Report ACL：每行 `owner_user_id` + `visibility` (`public`/`private`) + `report_access` grants 表。Report 端点 ACL 沿用 DS→Report 分层检查（DS 撤销级联到 Report 自动失效）。3 个 share 端点
  - 前端 `DataSourceShareModal` / `ReportShareModal` + 行级「分享」按钮（仅 owner 或 admin）+ ReportEditor 可见性切换

### 安全修复
- **SSRF 防护**：调度器 webhook URL 在发送前校验，阻断非 http/https scheme、IPv4/IPv6 内网/回环/保留地址、DNS 解析到被屏蔽 IP 的域名。新模块 `app/services/ssrf_guard.py`（`validate_webhook_url()`），含 30 个单元测试 + 4 个集成测试。`_send_notification` 禁用重定向跟随（`follow_redirects=False`）
- **Explorer SQL 多语句注入**：`is_safe_sql` 先剥离 SQL 注释再拒绝任何 `;` 字符，堵住 `SELECT 1; DROP TABLE users` 绕过。已有的关键字检查（`\bDROP\b` 等）保留为纵深防御
- **DataSource / Report ACL（批 9.3/9.4）**：单用户 demo 升级到多人协作。失败 / 越权一律返回 404（不区分"不存在"和"无权"，避免 ID 探测）。Dev DB 迁移：所有现有 DS/Report backfill `owner_user_id = admin.id`，Report 保留 `visibility='public'`（向后兼容）

### Bug 修复
- 前端 `DisplayConfig` 字段命名统一为 snake_case：之前表单用 `showLegend`/`legendPosition`/`showGrid` 等 camelCase，但后端 Pydantic 是 snake_case，Pydantic v2 默认 `extra='ignore'` 会**静默丢弃**用户切换的图例/网格线等开关。新增 3 个回归测试（`test_display_config_drops_unknown_camelcase_keys` 等）锁住「camelCase 必须被忽略」这条契约，避免后续加 `populate_by_name` 时回退
- **导出按钮坏掉**：`ReportPreview.tsx`「导出 Excel/HTML」用 `window.open(getExportUrl(...))` 打开 JWT-gated 端点，但 `window.open` 不会附 `Authorization` header → 401 跳登录页。修：改用 axios-backed `reportApi.download`（同步修了 `api/index.ts` 的 `download` 之前用裸 `fetch` 也没附 token，整条 download 流都坏——`ReportList` 也受益）
- DataExplorer 查询错误提示条件写反：原条件 `result.success && result.error` 是死代码（API 设 `success=false` 才回 `error`），改成 `!result.success && result.error`，SQL 执行错误才会显示详情
- DataExplorer CSV 导出不符合 RFC 4180：多行单元格值、含 `,`/`"` 的值会破格式。引入 `csvEscape` helper（命中 `,`/`"`/CR/LF 时整段加引号，内部 `"` 双写），行结束符统一 `\r\n`
- 报表编辑器拖拽排序从 N 并发 PUT 改为单次 `PATCH /reports/{id}/items/order`：后端单事务原子更新 `order_index`，所有 `item_id` 必须属于目标 report（否则 422 整批拒绝）。消除部分写导致的不一致，同时去掉 N+1 请求；上下移按钮 `handleMoveItem` 同步切到该端点
- 报表预览逐项错误展示：`generate_report()` 收集每个 item 的查询失败原因，`render_html(errors=...)` 在 HTML 中渲染红色错误横幅（`html.escape` 转义防 XSS）。`ReportGenerateResponse` 新增 `item_errors` 字段供编程调用方使用。Excel 路径保持现有行为（空白 sheet）
- **UI 对齐跨页 sweep**（`5094ab9`）：解决 AuditLogPage filter 对齐后用户问"其他页面有没有类似问题"。扫 8 个页面找到 3 个真问题：
  - `/reports` 操作列 619px / 8 按钮溢出 403px → 把 复制 / 订阅 / 导出 Excel / 导出 PDF 合到「更多」Dropdown，操作列 ~270px
  - `/data-sources` 表溢出 73px（数据库列 514px 撑爆，描述列 60px 被压扁）→ `tableLayout="fixed"` + scroll.x=1280 + 显式列宽
  - `/reports/:id/edit` 死链白屏 → 复制成功回调改 `/reports/{id}` + 加 `NavigateToReports` 重定向处理历史书签
- ReportList dropdown 重构后清理死代码：删 `enqueuing` / `enqueuingPdf` state 和它们的 setter（4 处）

### 计划中
见 `~/.claude/projects/-Users-liaosj-Documents-code-isee-workbench/memory/`

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
