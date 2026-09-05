# iSee Workbench — 产品功能特性演进 (Product Roadmap)

> **状态**：候选方向 — **未经用户确认，未承诺优先级或时间表**
>
> 2026-08-25 起从「技术 / 工程化硬化」阶段转入「产品功能特性演进」阶段。
> 10 批 + 10.x + 11.x + 4 轮 audit + doc/format drift guard 全部完成（685 pytest + 45 vitest，CI 全绿）。
> 接下来的工作应该围绕"对用户更有价值的新功能"而不是"再 audit 一轮 / 再加固一次基础设施"。

## 如何使用本文档

- 用户回到 session 说"规划产品功能" / "下一步做啥" / "新功能" → **先读本文档**
- 跟用户确认要走哪个方向（不要自动挑）
- 选定后开 plan 文件 + TaskCreate，按既有 batch 流程走
- 历史 batch 记录在 `docs/IMPROVEMENT_PLAN.md`；架构 / 设计模式见 `docs/ARCHITECTURE.md`

## 候选方向（观察到的需求，未排优先级）

### 1. 报表版本/历史 (Report Versioning) — **【已实现】**

> Implemented: 2026-08-26.
> Spec: `docs/superpowers/specs/2026-08-25-report-versioning-design.md`
> Plan: `docs/superpowers/plans/2026-08-25-report-versioning.md`

**现状**：`Report` 模型没有 `version` 字段，每次编辑都是 in-place 覆盖。

**痛点**：误删 / 误改 / 错误 SQL 上线后没法快速回滚；多人协作时谁动了什么无从追溯。

**可能方案**：
- 加 `report_versions` 表（`report_id` + `version` + `snapshot_json` + `created_by` + `created_at`）
- ReportEditor 保存时自动 snapshot 当前版本
- ReportPreview 提供"历史版本"侧栏 + diff 视图
- 提供"回滚到此版本"操作

**工作量**：~3-5 天（含前端 UI）

---

### 2. 报表模板市场 (Report Template Marketplace) — **【已实现】**

> Implemented: 批 13 (2026-08-27).
> Commits: `dee13ee` (backend schema + migration) / `865f2d5` (backend save-as-template + fork + gallery endpoints) / `dfe9da6` (frontend template gallery + integrations) / `a1b3b46` (alembic + PRAGMA FK isolation).
> Endpoints:
> - `GET /reports/templates` — public pool list (filterable)
> - `POST /reports/{id}/save-as-template` — turn own report into template
> - `POST /reports/template/{template_id}/from-template` — fork into new report
> Frontend: `/report-templates` page (`ReportTemplates.tsx`) + ReportList 「新建报表」二级菜单 (从空白 / 从模板) + 「另存为模板」按钮。

**结论**：✅ 已落地 — admin 自定义模板 + 普通用户 fork 的双向循环完成。

**未来可能增强**：
- 模板版本号 + 升级提示（fork 后模板改动用户不知道）
- 模板预览缩略图（当前只显示元数据）
- 模板分类 / 标签 / 搜索

---

### 3. 协作编辑 (Collaborative Editing)

**现状**：ACL 已就位（批 9.3/9.4），但 Report 仍只能 owner 单人改。

**痛点**：多人协作场景下，编辑冲突全靠后写覆盖前写，无任何提示或锁。

**可能方案**（由易到难）：
- **乐观锁**：保存时检查 `updated_at`，冲突返回 409 + 当前版本 → 客户端弹 diff 让用户选
- **悲观锁**：编辑页打开时占锁，其他人只读
- **CRDT / OT**：完整实时协作（最高成本，可参考 Figma / Notion）

**工作量**：乐观锁 ~1 天；悲观锁 ~0.5 天；CRDT ~2 周+

---

### 4. 数据源连接池监控 UI — **【已实现】**

> Implemented: 批 12 (2026-08-27).
> Commits: `75ea73e` (backend pool-metrics + admin /metrics endpoint) / `0e0735c` (frontend AdminMetrics dashboard + route) / `86e8689` (Pydantic v2 BucketStats hotfix).
> Backend: `GET /admin/metrics` — admin only，返回 per-DataSource 连接池快照（`size` / `checked_out` / `overflow` / `pool_timeout_total`）。
> Frontend: `/admin/metrics` 页 (`AdminMetrics.tsx` + `useAdminMetrics.ts`) — antd `Statistic` + `Table` 卡片，每 30s 自动轮询。
> 互补而非替代：Prometheus `/metrics` + Grafana dashboard 还在（批 9a/9b），这是给「没 Grafana 的部署」和「快速看一眼」场景兜底。

**结论**：✅ 已落地。

**未来可能增强**：
- 时间序列折线（当前是快照数值 + 轮询）
- 阈值高亮 / 健康度色块（当前只有原始数字）
- 一键跳到对应数据源编辑页

---

### 5. 报表订阅投递更多渠道 — **【部分已实现：邮件 + 钉钉 / 飞书 / 企业微信 + Webhook】**

> Implemented: 2026-09-05 (`4cde064` — 飞书 / 企业微信 / 钉钉富卡片 + 协议 fix).
> Earlier: 批 8.3 / 8.4 — 邮件 + Webhook + 三家 IM variant。
> Remaining scope: 海外渠道 — Slack / Discord（schema + sender + tests per channel）。

**现状**：邮件（批 8.3） + 钉钉 / 飞书 / 企业微信 三家 IM variant 含富卡片 + 通用 Webhook（均带 HMAC + SSRF guard）。

**痛点**：海外用户 / Slack 团队 / Discord 社区用不上。

**可能方案**：
- 加 `SlackConfig` / `DiscordConfig` variant（schema + sender + tests）
- 每个新渠道约 0.5-1 天
- 需要 `webhook_url` + （可选）签名密钥，SSRF guard 复用

**工作量**：每个渠道 ~0.5-1 天

---

### 6. AI 助手 — 自然语言 → SQL

**现状**：DataExplorer 有 19 个手写模板分 5 类（批 10.2），但用户写非模板查询还是要手敲 SQL。

**痛点**：业务人员不懂 SQL，但懂业务问题（"上个月华东区销售额 Top 10 客户"）。

**可能方案**：
- DataExplorer 顶部加 AI 输入框
- 调 LLM（OpenAI / Claude / 本地 Ollama）生成 SQL
- 走现有 `sqlglot` 校验（防 LLM 写出 `DROP`）
- 失败回退：把错误回给 LLM 让它自己改（最多 2 轮）
- 注意：数据隐私 — 用户 SQL 会被发送到 LLM provider；需要明确告知 + 隐私模式开关

**工作量**：MVP ~3-5 天；打磨（含 schema 自动拼 context） ~1-2 周

---

### 7. 报表导出更多格式

**现状**：HTML / Excel / PDF（同步 + 异步路径都覆盖）。

**痛点**：业务人员偶尔需要 CSV（per-item 进 Excel 太重）/ JSON（给 BI 工具喂数）/ Markdown（贴飞书）。

**可能方案**：
- 复用现有 job 队列，加 `JobOutputFormat.CSV` / `.JSON` / `.MD`
- 每个新格式 ~半天
- CSV 需要 RFC 4180 转义（已有 `csvEscape` helper）

**工作量**：每个格式 ~0.5 天

---

### 8. Dashboard 模式 — 多 Report 拼装 landing page — **【已实现】**

> Implemented: 2026-09-05.
> Commits: `695d0ea` (看板模型 + UI) / `5ebc42a` (批 D 跨实体反向 link) / `38fde06` (批 user-mgmt 集中授权) / `4cde064` (IM 通知 + 看板订阅)。
> Spec: `docs/superpowers/specs/2026-08-25-report-versioning-design.md`（看板部分）。
> Frontend: `/dashboards/:id` 网格拼装 + Report/Chart item + 看板订阅 (跟 Report 同机制)。
> Remaining scope: 报表"标记为 KPI"快速入口、管理员把 Report 提升到默认 Dashboard。

**现状**：Dashboard 模型完整落地，多 Report / Chart 拼装为看板首页；owner-scoped CRUD + 可见性 + 集中授权。

**痛点**：已解决 — KPI 多的场景（销售总监 / 运维 leader）一眼可见。

**未来可能增强**：
- 报表"标记为 KPI"快速入口（管理员可以把 Report 提升到默认 Dashboard）
- Dashboard 内 item 的"实时刷新"（当前走刷新整页）

**工作量**：KPI 入口 ~1 天；实时刷新 ~2-3 天

---

### 9. 全局联合搜索 (Command Palette) — **【已实现】**

> Implemented: 2026-09-05 — `5051d65` (批 A)。
> Backend: `GET /search?q=&limit_per_kind=` fan-out 三个 list helper，返回 `{ reports, dashboards, data_sources }` 三组结果。
> Frontend: 顶栏常驻 Input + ⌘K / Ctrl+K 聚焦；250ms debounce；custom div popover (z=1100)；3 分组渲染 + 键盘导航 (↑↓ Enter Esc) + click-outside 关闭 + 路由变化关闭。
> ACL ordering: ACL 先 `q` 后（项目惯例），防 filter probe。
> Sort: exact > prefix > contains；ties 按 name 长度。
> 不做：search history / 子串高亮 / description 字段（保持 name-only 与现有 list endpoint 一致）。

**痛点**：跨实体的"找一个"入口 — 找报表 / 看板 / 数据源都得离开当前页 + 翻列表。

**结论**：✅ 已落地。

---

### 10. 跨实体反向 Link — **【已实现】**

> Implemented: 2026-09-05 — `5ebc42a` (批 D)。
> Endpoints:
> - `GET /data-sources/{id}/reports` / `GET /data-sources/{id}/dashboards`
> - `GET /reports/{id}/dashboards`（按 dashboard 去重）
> - `DELETE /data-sources/{id}` → 409 当被报表引用（`94fbbbb`）
> - `DELETE /reports/{id}` → 409 当被 DashboardItem 引用
> Frontend: 数据源 / 报表 / 看板列表页底部 + Drawer「References」面板；看板 item 用 `DashboardItemSourceLink` 一键跳引用源。
> ACL ordering：ACL 先 `q` 后，防未授权 caller 通过 filter 组合探测。

**痛点**：删数据源时不知道谁在用 → 误删报表失联；找"哪些报表用了这个数据源"要全文 grep SQL。

**结论**：✅ 已落地。

---

## 不在候选范围内的方向（explicitly out of scope）

以下方向已被故意排除，避免回退到 hardening 阶段：

- **再起一轮 audit / 加 P0-X / 加 TODO-N** — drift guard 才落地，format / docs 已 CI 闭环，无必要
- **更细粒度的 RBAC**（按字段 / 按行级权限）— 当前 owner / grant / visibility 模型覆盖 95% 场景；过度抽象反而拖累主路径
- **前端框架升级**（React Compiler / Next.js migration / TS strict 模式）— 当前 React 19 + TS 模式已经够用，迁移 ROI 低
- **后端单仓拆微服务** — 单体 FastAPI 完全 hold 得住当前规模，拆微服务只会增加部署成本

## 推荐引入流程（选定方向后）

```
1. 用户确认方向
2. 新建 plan 文件 ~/.claude/plans/<id>-<name>.md
3. IMPROVEMENT_PLAN.md 加新批次行 + Resume Point 更新
4. TaskCreate 子项 → 逐项实施
5. 每项完成后跑 make test-fast && make lint && make typecheck && make build
6. 完成后：
   - CHANGELOG [未发布] 段加条目
   - 本文档 (ROADMAP.md) 把候选方向标记 [已选] / [已实现]
   - 写新 checkpoint memory
7. 攒够 3-5 个方向后做一次 ROADMAP 复盘（哪些用户用了、哪些没有）
```

## How to update this file

- 添加新候选方向：附在"候选方向"列表末尾，标"未排优先级"
- 选定方向后：把对应段标题改为 `**【已选】<name>**` 并加 commit 链接
- 实现完成后：从"候选"移到末尾的"已实现"段（新建段）

---

*Last reviewed: 2026-09-05 — 10 candidate directions. #1 #2 #4 #5(部分：邮件 + IM + Webhook；Slack/Discord 待做) #8 #9 #10 已实现。剩余候选: #3 协作编辑 / #5 Slack+Discord / #6 AI NL→SQL / #7 导出 CSV/JSON/MD。*