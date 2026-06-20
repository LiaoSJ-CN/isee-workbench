# 更新日志

本项目的所有重要变更都会记录在此文件中。

## [未发布]

### 安全改进
- 移除 `deps.get_current_user` 中已无用的 `?token=` query-param fallback（`ReportPreview` 改为走 `Authorization` 头 + blob URL iframe 后该入口已无消费者）

### 测试
- 新增 `backend/scripts/smoke_preview_endpoint.py`：通过 FastAPI `TestClient` 走完整 router 路径调用 `/reports/{id}/preview`，断言响应含 `<base href=...>`、`Authorization` 头认证通过、`?token=` fallback 已拒绝（401）。补足既有 smoke 只直接调 `generate_report` 而绕过 router 留下的覆盖盲区

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
