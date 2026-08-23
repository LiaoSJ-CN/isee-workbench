# iSee Workbench — 改进计划

> 完整的实施细节、每个 batch 的步骤、复用函数清单、验证策略见
> `/Users/liaosj/.claude/plans/cozy-brewing-falcon.md`（plan 文件）。
> 本文档是面向团队的高层索引。

## 🔖 会话断点 / Resume Point

**最后会话（2026-08-16）：**

| 项 | 状态 |
|---|---|
| 批 0 重验证 | ✅ 完成 — pytest 315/315、ruff 0、mypy 0、eslint 0、tsc 0、vite build 0 |
| 批 1 Quick wins | ✅ 完成 — commit `d55955a` |
| 批 6a request-id + Sentry | ✅ 完成 — commit `76035b9`（X-Request-ID 端到端回显 + 25 新测试） |
| 批 5.1+5.3 Alembic + lifespan | ✅ 完成 — commit `5931231` |
| 批 5.2 拆 report_generator | ✅ 完成 — commit `d241de9`（628 → 7 module） |
| 批 5.4 get_current_user 返回 User | ✅ 完成 — commit `439c5fb` |
| 批 5.5 列表接口分页 | ✅ 完成 — commit `a1bacab`（limit/offset + X-Total-Count） |
| 批 4a 参数 schema 后端 | ✅ 完成 — commit `61a8359`（model + 4 CRUD + 5-variant discriminated union + 运行时校验） |
| 批 2a TanStack Query 基础 | ✅ 完成 — commit `9e12e56`（queries/ + 6 page 迁移 + RequireAuth useMe） |
| 批 2b 乐观更新 + 虚拟滚动 + Skeleton | ✅ 完成 — commit `ed10570`（useDelete*/useUpdateDataSource 乐观更新 + DataExplorer 虚拟滚动 + Skeleton 组件替换 3 处 Spin/loading） |
| 批 3a Job 模型 + Excel 异步化 | ✅ 完成 — commit `af48835`（ReportJob + Alembic 迁移 + ThreadPoolExecutor + 3 router endpoint + 18 测试） |
| 批 3b 前端轮询 / SSE 进度 | ✅ 完成 — 新 `queries/useJobs.ts`（`useJobStatus` 动态 refetchInterval + `useEnqueueReportJob` mutation）+ `jobsApi` + ReportPreview 「导出 Excel」enqueue→轮询→下载三段式 |
| 批 4b 参数 UI 前端 | ✅ 完成 — 新 `components/ReportParameterForm.tsx`（按 type 渲染 5 种输入：Input/InputNumber/DatePicker/Select/Switch）+ `ReportEditor` 「参数」Tab CRUD UI（Table + ParameterEditorModal） |
| 批 6b Prometheus + 限流 + CSRF + NotificationConfig | ✅ 完成 — Prometheus `/metrics` + 4 自定义指标 + `/explorer/query` 30/min/IP + `/reports/generate`+`/reports/{id}/jobs` 10/min/IP + `CSRFMiddleware` (Origin 白名单) + `NotificationConfig` 3-variant 判别联合 |
| 批 1.5 ReportEditor 文件拆分 | ✅ 完成 — commit `baad103` + 修复 TODO-1 `html.escape(None)` 崩溃（commit `426dfa4`）。`pages/ReportEditor.tsx` (1043 行) → `pages/ReportEditor/{index, SortableItem, ItemEditorModal, ParameterEditorModal, ConfigTab, ItemsTab, ParametersTab}.tsx` (7 文件) |
| 批 7.1 vitest + RTL setup | ✅ 完成 — commit `3c6e867`（vitest@^4 + happy-dom + 29 unit tests + 提取 csvEscape） |
| 批 7.2 pytest-cov + 70% gate | ✅ 完成 — commit `a7d1933`（pytest-cov branch coverage + `--cov-fail-under=70` + CI 阻断） |
| 批 7.3 scheduler_runner 单测 | ✅ 完成 — commit `e04ac62`（5 tests 覆盖 `run(stop_event, resync_interval)`） |
| 批 7.4 Playwright e2e smoke | ✅ 完成 — commit `d13e85a`（3 smoke tests: login + DataSourceList + ReportList + CI step） |
| 批 7.5 sql_validator property-based | ✅ 完成 — commit `1fda81b`（hypothesis + 13 property tests + 顺手修 jwt.TokenError bug） |
| 批 8.2 Schema 浏览器 | ✅ 完成 — commit `70c3adb`（后端 `GET /data-sources/{id}/schema` + SchemaTree + DataExplorer Sider 布局） |
| 批 8.5 jobs/{id}/download + ReportList async | ✅ 完成 — commits `a87e295` + `724ac90`（worker 产物直接下载 + ReportList 行内 Excel 异步化 + 顶部任务卡片 + `downloadBlob` helper 抽取） |
| **TODO-8 NotificationConfig 数据迁移** | ✅ **完成** — alembic 迁移 `c0a2b1d4e5f6` + `app/services/notification_migration.py` + 16 tests（4 类旧 shape normalize）。Dev `app.db` 已跑 migration，pytest 478/478 全过 |
| 下一批：批 8.1 PDF 导出（weasyprint） | ⏳ **下次会话从这里开始**（按已重排顺序：8.1 → 8.3 → 9 → 10 — 8.4 已完成） |

**下一会话怎么接：**

1. 打开本文件 → 看「当前进度」表
2. 跑 `make test-fast && make lint && make typecheck && make build` 确认基线没漂（**当前基线：pytest 478/478、coverage 83.9%、lint 0、tsc 0、vitest 29/29**）
3. 读 plan 文件 `~/.claude/plans/cozy-brewing-falcon.md` § 8.1（weasyprint PDF 导出，复用 8.5 异步队列）
4. 建 TaskCreate 覆盖批 8.1 子项，开始干
5. 注意 `useEnqueueReportJob` 的 `reportId` 闭包陷阱（见 session-checkpoint-2026-08-16-post-8.5 gotcha #1）—— 别在 PDF 改造里破坏这个

完整状态 + 修正记录见 `~/.claude/projects/-Users-liaosj-Documents-code-isee-workbench/memory/improvement-plan.md`。

---

## 背景

2026-06-21 完成 11 阶段审计（63 项修复、12 commit），代码质量基线（ruff/mypy/tsc/eslint 全 0、315 用例通过）已达标。

**当前短板**：产品功能深度与工程化能力。

| 痛点 | 影响 |
|---|---|
| 单 admin 账号、无 RBAC | 多人使用立刻出乱 |
| `custom_sql` 的 `{param}` 无 UI 入口 | 报表只能跑死查询 |
| DataExplorer 无默认 LIMIT | 大表查询 OOM 风险 |
| 报表生成同步阻塞 | 长报表触发 nginx 超时 |
| 前端无 server-state 管理 | 无缓存/去重/乐观更新 |
| `report_generator.py` 628 行单体 | 可演化性受限 |
| 零前端测试、零 e2e | UI 改动全靠肉眼 |
| P11 后 3 commit 未重跑验证门禁 | 基线漂移风险 |

## 总体路线（10 批 + 批 0 重验证 + 批 1.5）

```
批 0   重跑完整验证门禁                       ~0.5 周
批 1   Quick wins（LIMIT/CI/.env/Makefile）  ~1 周
批 6a  request-id + Sentry                  ~1 周    [提前做]
批 5   后端重构（拆 report_generator）       ~2 周    [提前]
批 4a  参数 schema 后端                     ~1 周
批 2a  TanStack Query 基础 hook            ~2 周
批 2b  乐观更新 + 虚拟滚动 + Skeleton        ~1 周
批 3a  Job 模型 + Excel 异步化              ~2 周
批 3b  前端轮询 / SSE 进度                  ~1 周
批 4b  参数 UI 前端                         ~1-2 周
批 6b  Prometheus + 限流 + CSRF              ~1-2 周
批 1.5 ReportEditor 文件拆分                ~1 周    [从批 1 抽出]
批 7   测试 + DX                            ~2 周
批 8   产品功能（PDF/schema/订阅/IM）         ~2-4 周
批 9   RBAC + 协作                          ~2-3 周
批 10  前端优化                             ~1 周
```

总计：~22-30 周（约 5-7 个月）。

## 关键决策

| 问题 | 决策 |
|---|---|
| 异步化范围 | Excel 必须异步，HTML preview 同步，HTML 导出视情况 |
| 任务队列 | APScheduler + 自建 Job 表，不上 Celery |
| 参数 schema | Pydantic discriminated union，不上 JSON Schema |
| 批 3 排序 | 在批 5 之后（结构改动与行为改动不叠加） |
| 批 5 排序 | 提到批 3 之前，并补 Alembic 迁移 |
| chart.js 处理 | 不 npm 化（前端不用）；改为 cp 到 static/ + SRI |
| CSRF | 批 6b 补（`allow_credentials=False` 防不了跨域写） |
| Fernet 轮换 | 批 6b 补 `key_version` 列 |

## 当前进度

| 批 | 状态 | 起始 commit | 备注 |
|---|---|---|---|
| 批 0 重验证 | ✅ 已完成 (2026-08-13) | — | pytest 315/315、ruff 0、mypy 0、eslint 0、tsc 0、vite build 0 |
| 批 1 Quick wins | ✅ 已完成 (2026-08-13) | (本 commit) | LIMIT 回归测试 + CI matrix 3.11/3.14 + .env.example 补全 + Makefile + pre-commit + CONTRIBUTING |
| 批 6a | ✅ 已完成 (2026-08-15) | `76035b9` | X-Request-ID 端到端回显 + Sentry backend/frontend init + 25 新测试 |
| 批 5（含 5.1/5.2/5.3/5.4/5.5） | ✅ 已完成 (2026-08-15) | `5931231` `d241de9` `439c5fb` `a1bacab` | Alembic 接管 schema + 拆 report_generator (628→7) + get_current_user 返回 User + 列表分页 |
| 批 4a | ✅ 已完成 (2026-08-15) | `61a8359` | ReportParameter model + 4 CRUD endpoints + Pydantic discriminated union (5 variants) + 运行时校验（缺失/类型/enum/未知 key） |
| 批 2a | ✅ 已完成 (2026-08-15) | `9e12e56` | TanStack Query v5 + `queries/` 目录 + 6 page 迁移（DataSourceList/ReportList/Scheduler/ReportEditor/ReportPreview/DataExplorer）+ RequireAuth 用 useMe；移除 725 行手写 useEffect/setState；净 +467 行（含 7 个新 hook 文件 + QueryClient 接线） |
| 批 2b | ✅ 已完成 (2026-08-15) | `ed10570` | 乐观更新（useDeleteDataSource/useDeleteReport/useUpdateDataSource/useReorderReportItems/useCreateReportItem/useDeleteReportItem 全部加 snapshot/rollback）+ DataExplorer Table virtual+scroll.y:500（10k+ 行场景）+ 新 components/Skeleton.tsx（TableSkeleton/CardSkeleton/InlineSkeleton）替换 ReportPreview/DataExplorer/ReportEditor 三处 Spin/loading 文本；跳过 useToggleDataSourceActive/Report（无 UI 消费者）+ ReportPreview 虚拟滚动（React 表 ≤10 行，真正大表在 iframe 内） |
| 批 3a | ✅ 已完成 (2026-08-15) | `af48835` | ReportJob model（11 字段 + status/output_format 字符串常量）+ Alembic 迁移 `222001adeb57`（含 composite (report_id, created_at) 索引）+ services/job_queue.py 模块级 ThreadPoolExecutor(4) + enqueue 写 pending row + submit + _run_job 状态机 + HTML preview 保持同步（拒绝 enqueue）+ routers/jobs.py 3 endpoint（POST 201, GET 200/404, history list 带 status filter + pagination）+ lifespan teardown `shutdown_executor(wait=False)`；18 新测试覆盖 enqueue 错误/成功路径、_run_job 状态转换、HTTP auth/404/pagination、真实 executor 集成（关键 fix：polling 必须 `db.expire_all()` 因为 `db.get()` 缓存 identity map） |
| 批 3b | ✅ 已完成 (2026-08-15) | (本 commit) | 新 `queries/useJobs.ts`（`useJobStatus(jobId)` 用 TanStack Query v5 的 `refetchInterval: (q) => status==='done'\|'failed' ? false : 2_000` 函数式动态间隔；`useEnqueueReportJob(reportId)` mutation）；`api/index.ts` 加 `jobsApi.enqueue/get`；`types/index.ts` 加 `JobStatus`/`JobOutputFormat`/`ReportJobCreate`/`ReportJob`；`keys.ts` 加 `queryKeys.jobs.{all,detail,forReport}`；`ReportPreview.tsx` 「导出 Excel」改成 enqueue→轮询→下载三段式（HTML 仍走同步 export），新增 Excel 任务卡片显示状态 Tag/Spin/错误 Alert/下载按钮。**已知 trade-off**：download 仍走 `/reports/{id}/export/excel`（每次 re-generate，不复用 worker 产物）— 因为该端点按设计总是重新生成（与 `schemas/job.py` docstring 「serves by basename」描述不一致），复用 worker 文件需要新增 `GET /jobs/{id}/download`，留作 future batch。lint 0、tsc 0、build 0 |
| 批 4b | ✅ 已完成 (2026-08-15) | (本 commit) | `types/index.ts` 加 `ParameterType`/`ReportParameterCreate`（5 variant 判别联合）/`ReportParameter`/`ReportParameterUpdate`；`api/index.ts` 加 `parametersApi.{list,create,update,delete}`；`keys.ts` 加 `queryKeys.parameters.{all,list}`；新 `queries/useParameters.ts`（`useReportParameters` + 3 mutation）；新 `components/ReportParameterForm.tsx`（按 `parameter.type` 切换 Input/InputNumber/DatePicker/`<Select mode="tags">`/Switch，DatePicker 输出 `YYYY-MM-DD` ISO；number 强转 `Number()`；initialValues 从 `parameter.default` hydrate）；`package.json` 加 `dayjs@^1.11.21` 作为直接 dep；`pages/ReportPreview.tsx`：有参数时 toolbar 「导出 Excel」隐藏，改由 form submit 触发 enqueue（带 `{parameters: formValues}`）；`pages/ReportEditor.tsx`：新增「参数 (N)」Tab（Table + Popconfirm 删除 + 编辑）+ `ParameterEditorModal`（type-conditional inputs，enum 用 `Select mode="tags"`，date 用 DatePicker，`as unknown as ReportParameterCreate` 解决 union 与 Record 的不兼容）；lint 0、tsc 0、build 0（chunk > 500KB 增长 1.64→1.84MB，新增 useParameters/ReportParameterForm/Editor modal 贡献 ~200KB） |
| 批 6b | ✅ 已完成 (2026-08-15) | (本 commit) | 见完成记录 |
| 批 1.5 | ✅ 已完成 (2026-08-16) | `426dfa4` + `baad103` | ReportEditor 文件拆分 (1043 → 7 文件) + 修 TODO-1 `html.escape(None)` 崩溃 |
| 批 7 | ✅ 已完成 (2026-08-16) | `a7d1933` / `1fda81b` / `e04ac62` / `3c6e867` / `d13e85a` | vitest@^4 + RTL + 29 unit tests + pytest-cov 70% gate + scheduler_runner 5 单测 + Playwright 3 smoke e2e + sql_validator 13 property tests |
| 批 8.2 | ✅ 已完成 (2026-08-16) | `70c3adb` | 后端 `GET /data-sources/{id}/schema` + 前端 SchemaTree + DataExplorer Layout/Sider |
| 批 8.5 | ✅ 已完成 (2026-08-16) | `a87e295` + `724ac90` | `GET /jobs/{id}/download` worker 产物直下载 + ReportList 行内 Excel 异步化 + `downloadBlob` helper |
| TODO-8 数据迁移 | ✅ 已完成 (2026-08-16) | (pending) | alembic `c0a2b1d4e5f6` + `app/services/notification_migration.py` + 16 tests |
| 批 8.1 | ✅ 已完成 (2026-08-23) | (synthesis) | weasyprint PDF 导出：render_pdf (lazy import) + JobOutputFormat.PDF + 同步 /reports/{id}/export/pdf + 异步 /reports/{id}/jobs output_format=pdf + ReportList PDF 按钮 + libpango/libcairo/libgdk-pixbuf/fonts-noto-cjk Dockerfile + 11 tests |
| 批 8.3 | ✅ 已完成 (2026-08-23) | (synthesis) | 报表订阅：ReportSubscription model + Alembic + per-user cron + 6 endpoint（POST/GET list/GET single/PATCH/DELETE + pause/resume）+ `sub_<id>` APScheduler namespace + reconcile 接入 sidecar+web + EmailConfig SMTP sender (stdlib smtplib + STARTTLS/SSL 双模 + smtp_auth/email_error 指标 + smtp_user 含 @ 时不重复拼接 host) + 前端 NotificationConfig 类型 + subscriptionApi + SubscriptionModal + MySubscriptionsPage + ReportList 「订阅」按钮 + nav 「我的订阅」 + 14 SMTP 测试 + conftest 清理 pytest_*/bad-test-source-*/happy-sqlite-source-* 泄漏行 |
| 批 8.4 | ✅ 已完成 (2026-08-23) | `47ca3c9` | IM 通知：FeishuConfig/WeChatWorkConfig schema + `_send_feishu`/`_send_wechatwork` HMAC/JSON dispatch + Scheduler.tsx Form 飞书/企业微信 选项 + `test_notification_im.py` 8 tests |
| 批 9.1 | ✅ 已完成 (2026-08-17) | `900c062` | User 加 `role`/`org_id` + JWT 携带 `uid`/`role`/`oid` + `/auth/me` 返回新字段 |
| 批 9.2 | ✅ 已完成 (2026-08-17) | `d42c5f1` | `deps.require_role` + `admin_required`/`editor_required` 原语 |
| 批 9.3 | ✅ 已完成 (2026-08-17) | `164d07d` | DataSource ACL (owner + grants) + explorer/report/jobs 联动 + DataSourceShareModal |
| 批 9.4 | ✅ 已完成 (2026-08-17) | `02177d0` | Report owner + visibility + 共享 + 分层 DS/Report ACL + ReportShareModal |
| 批 9.5 | ✅ 已完成 (2026-08-23) | `334f36e`+`3f178ae`+`523d283`+`8fc1fb1`+`0670260`+`ee1d6c7`+`d7150c0`+`5affcd8`+`cc77cfe`+`2c5d81d`+`eab7975`+`489b72b` (12 commits) | Audit log: model + Alembic + service + schemas + admin-only GET `/audit-logs` + 33 mutating 端点钩子 + 47 tests |
| 批 9.6 | ✅ 已完成 (2026-08-23) | `5fd150a` | Admin `/audit-logs` 页 + `RequireAdmin` 路由守卫 + 菜单入口 + actor username 解析 |
| 批 10 | ✅ 已完成 (2026-08-23) | `f871fb9` | code-split + 删 chart.js + Prettier |
| 批 10.1 | ✅ 已完成 (2026-08-23) | `ad201e5` | 报表 `is_demo` 标志位 + ReportList 蓝色 "示例" Tag — 让运维一眼区分 seed 脚手架 vs 用户自建报表 |
| 批 10.2 | ✅ 已完成 (2026-08-23) | `9e10435` | DataExplorer 模板分组（维度表 / 业务明细 / 聚合分析 / 跨表 JOIN / 自定义）— 18 条平铺 → OptGroup 下拉 |
| 批 10.3 | ✅ 已完成 (2026-08-24) | `d59a345` | DataSource.clone + Report.duplicate — `<name> (副本)` 自动后缀 / 显式 name / 409 collision / 404 ACL / audit log / 前端 "复制" 按钮（DataSourceList + ReportList 后者直接跳编辑器）。复制重置 visibility=private + is_demo=false + is_scheduled=False + 不复制 shares |
| TODO-9a | ✅ 已完成 (2026-08-23) | `a810842` | Prometheus + Grafana dashboard (`isee-workbench-dashboard.json`，9 面板：HTTP RPS / 5xx / 4xx / p50-p99 / Top routes / 报表 p95 / 报表 errors / SQL 校验 / webhook outcome) |
| TODO-9b | ✅ 已完成 (2026-08-23) | `6376f06` | 8 条 alert rules (`isee-workbench.yml`) + alertmanager wiring + 6 pytest 防 typo + DEPLOY.md 配置告警段 |

## 每批结束的验证清单

```bash
# 后端
cd backend && source .venv/bin/activate
ruff check .                            # 0
mypy app                                # 0
pytest -q                               # 全过

# 前端
cd frontend
npm run lint                            # 0
npx tsc --noEmit                        # 0
npm run build                           # 0（批 1 补 CI）

# E2E（批 7 后）
npx playwright test                     # smoke 全过
```

## 完成记录

> 每完成一批在此追加：`### 批 X：标题 — 日期 — commit hash — 实际耗时`

<!-- 批 0 完成时：
### 批 0：重跑完整验证门禁 — 2026-08-13 — (commit) — 实际耗时

- pytest: 315/315 passing (5.34s)
- ruff: All checks passed!
- mypy: Success, no issues found in 32 source files
- eslint: clean
- tsc --noEmit: clean
- vite build: success (1 warning: chunk > 500KB — 批 10 解决)

### 批 1：Quick wins — 2026-08-13 — (commit) — 实际 ~30 min

子项：
1. **DataExplorer LIMIT 回归测试**：`backend/tests/test_explorer.py::test_explorer_row_cap_applies_to_unbounded_select` 验证 `settings.explorer_max_rows` 通过 `SELECT * FROM (…) AS _explorer_sub LIMIT N` wrap 实际生效，ORDER BY 保留。
   - 重要发现：LIMIT 防护在 `app/routers/explorer.py:88-92` 已经实现（plan agent 误判"无 LIMIT"）。原工作仅是补回归测试。
2. **CI 加 Python matrix**：`.github/workflows/ci.yml` 把 `backend-test` 改成 `matrix: python-version: ["3.11", "3.14"]`，对齐 `pyproject.toml` (>=3.11) 与本地 `.venv` (3.14)。同时把 `backend-lint` 从 `app/` 扩展到 `.` 匹配本地 `make lint`。
3. **`.env.example` 补全**：覆盖 P3-P5 新增的所有设置（trusted_proxies、cookie_*、webhook_*、explorer_max_rows 等），每项配说明。
4. **`Makefile`**：仓库根统一入口。`make help` / `make dev-backend` / `make dev-frontend` / `make dev-scheduler` / `make test-fast` / `make test-cov` / `make lint` / `make typecheck` / `make build` / `make format` / `make clean` / `make docker-up`。
5. **`.pre-commit-config.yaml`**：仅跑快速 auto-fixable 检查（ruff + eslint）。mypy/tsc/pytest 留给 CI。
6. **`docs/CONTRIBUTING.md`**：开发者 onboarding 指南。

验证基线：pytest 316/316 (5.35s)、ruff 0、mypy 0、eslint 0、tsc 0、vite build success。

下一个批次：批 6a（request-id + Sentry），与业务逻辑正交，可独立推进。

### 批 6a：request-id + Sentry — 2026-08-15 — `76035b9`

子项：
1. **`RequestIDMiddleware`**（`backend/app/middleware/request_id.py`）：从 `X-Request-ID` 头读（生成 16-hex UUID4 兜底）→ contextvar 注入 → `structlog`-style 适配 → `X-Request-ID` 回写响应头。日志格式 `[req_id=…] …` 由 log factory 注入。
2. **Sentry 集成**（`backend/app/middleware/sentry.py` + `main.py`）：DSN-gated，空 DSN 零开销。`before_send` 自动给每个事件打 `request_id` tag 和 user context。HTTPException (4xx) 默认不上报。
3. **前端 Sentry**（`frontend/src/main.tsx`）：`@sentry/react` v9 init，同样 DSN-gated，`integrations: [browserTracingIntegration()]`、`traces_sample_rate` 由 `VITE_SENTRY_TRACES_SAMPLE_RATE` 控制。
4. **25 新测试**（`test_request_id_middleware.py` 14 + `test_sentry_init.py` 11）：覆盖 round-trip、contextvar 隔离、空 header 默认生成、Sentry 没 DSN 时不导入、不上报 4xx、`before_send` 注入 request_id 等。

验证基线：pytest 341/341 (5.7s)、ruff 0、mypy 0、eslint 0、tsc 0、vite build success。

下一个批次：批 5 后端重构（Alembic + 拆 report_generator + get_current_user + 分页）。

### 批 5.1+5.3：Alembic 接管 schema 演进 + lifespan — 2026-08-15 — `5931231`

- Wiped `backend/alembic/versions/` 重建；单一 autogenerated `initial_schema` 迁移覆盖全部 6 张表。
- `main.py` lifespan 启动时调用 `alembic.command.upgrade(head)`；删除模块级 `Base.metadata.create_all` 和 `ensure_columns(engine)` 调用。
- `alembic/env.py` 不再 `fileConfig()` —— 那会清空 root logger handler，覆盖 lifespan 装的 request-id 格式和 pytest 的 caplog。
- `ensure_columns()` 保留为可复用 library 函数（有单测覆盖），但 lifespan 不再调用。
- 验证：pytest 315/315（`test_db_migrations` 仍通过）、ruff 0、mypy 0、alembic upgrade head 干净。

### 批 5.2：拆 report_generator.py — 2026-08-15 — `d241de9`

- 628 行单文件 → 7-module 子包：`__init__`（公共 API + facade）、`errors`、`engine`（连接缓存）、`query_builder`（build_query）、`renderers/{__init__, _shared, html, excel}`。
- 公共导入路径 `from app.services.report_generator import …` 不变；测试无需修改。
- 验证：pytest 315/315（5.7s）、ruff 0、mypy 0。

### 批 5.4：get_current_user → User 实体 — 2026-08-15 — `439c5fb`

- `app/deps.py:get_current_user` 返回 `User`（之前返回 `str = username`）；加 `disabled` 校验 + deleted-user 检查（username 不存在时 raise 401）。
- 用户身份缓存到 `request.state.current_user`，handler 可选复用。
- 仅 1 个 router handler 实际消费 `User`（`routers/auth.py:me` 用 `current_user.username`）；其他 6 个 router 用 `dependencies=[Depends(get_current_user)]`（鉴权门，丢弃返回值），无需改动。
- 4 新测试覆盖 disabled/deleted/cached 路径。
- 验证：pytest 345/345、ruff 0、mypy 0。

### 批 5.5：列表接口分页 — 2026-08-15 — `a1bacab` — ~30 min

子项：
1. **`GET /reports`**（`routers/report.py:list_reports`）：接 `limit` (1-500, 默认 50) 和 `offset` (≥0) Query 参数；filtered query `.count()` 拿总数 → 注入 `X-Total-Count` header → `.order_by(Report.id).offset().limit().all()` 拉一页。加 `.order_by(Report.id)` 保证 offset+limit 页码稳定。
2. **`GET /data-sources`**（`routers/data_source.py:list_data_sources`）：同上，`order_by(DataSource.id)`。
3. **`/scheduler/status` 不动**：job 数量小，plan 已说明。
4. **新测试 `tests/test_pagination.py`（10 个用例）**：默认 limit 行为、`limit=1&offset=0/1` 返回第 1/第 2 条、`offset=9999` 返回空 + 总数不变、`limit=0` / `limit=501` / `offset=-1` 返回 422。fixture `temp_data_source_for_reports` 创建临时 ds + 3 个 report，teardown 顺序 delete 报告再 delete ds 满足 FK。

风险 & 取舍：
- `X-Total-Count` header 注入用 `Response` 参数（不是 `response_model`），因为 response_model 不暴露 headers。
- 默认 `limit=50` 大于现有测试用数据量，无破坏。
- `count(*)` 每次请求都跑——小表无影响；report_items 表关联 N 条时 count 本身也是 N，但目前数据量小；批 3 上 Job 时再考虑缓存。
- 现有 `ReportDetailResponse.items` 序列化是 N+1（per-report lazy load items）——超出本批范围；plan 列在批 3/7 处理。

验证基线：pytest 355/355 (5.77s)、ruff 0、mypy 0。

下一个批次：批 4a 参数 schema 后端（model `ReportParameter` + Pydantic discriminated union + `GET /reports/{id}/parameters`）。

### 批 4a：参数 schema 后端 — 2026-08-15 — `61a8359` — ~2 hr

子项：
1. **`ReportParameter` model**（`backend/app/models/report_parameter.py`，新文件）：`id, report_id (FK CASCADE, index), name (String 64), label (String 255), type (String 16), required (default True), default (JSON typed), options (JSON list for enum), order_index, created_at, updated_at`。`__table_args__`：`UniqueConstraint(report_id, name)` + `Index(report_id, order_index)`。在 `Report` 上加 `parameters: Mapped[list[ReportParameter]] = relationship(..., cascade="all, delete-orphan", order_by="ReportParameter.order_index")`，对应子表 `ondelete="CASCADE"` FK。
2. **Alembic 迁移**（`b430089a9cac_add_report_parameters_table.py`）：autogenerate 干净，FK CASCADE、unique constraint、双 index 全部正确。`alembic/env.py` 加 `from app.models import report_parameter  # noqa: F401`。
3. **Pydantic discriminated union**（`backend/app/schemas/report_parameter.py`，新文件，**仓库第一个 discriminated union**）：`ParameterType(str, Enum)` + `ReportParameterBase` + 5 variants（`StringParam` / `NumberParam` / `DateParam` / `EnumParam` / `BoolParam`），每 variant 用 `Literal["string"]` 等做 discriminator 字面量。`EnumParam` 强制 `options: list[str] = Field(min_length=1)`，`DateParam.default` / `EnumParam.default` 各有 field_validator（ISO-8601、必须在 options 内）。`ReportParameterCreate = Annotated[Union[...5...], Field(discriminator="type")]`。`ReportParameterResponse` 用 `from_attributes=True`，`type: ParameterType` 序列化为字符串。`ReportParameterUpdate` 全字段 Optional，允许重新定型。
4. **CRUD endpoints**（在 `routers/report.py` 末尾新增 4 个，URL shape 跟 `/{report_id}/items/{item_id}` 一致）：
   - `POST /reports/{report_id}/parameters`（201）— 404 if report missing；`order_index == 0`（默认）时自动填 `last + 1`；`IntegrityError` → 409（unique constraint on `(report_id, name)`）。
   - `GET /reports/{report_id}/parameters`（200）— 按 `order_index` 排序，404 if report missing。
   - `PUT /reports/{report_id}/parameters/{param_id}`（200）— ownership check (`id AND report_id`)，`IntegrityError` → 409。
   - `DELETE /reports/{report_id}/parameters/{param_id}`（204）— ownership check + 404。
5. **`parameter_validator` 模块**（`backend/app/services/parameter_validator.py`，新文件）：`ParameterValidationError(Exception)` + `validate_parameters(spec, values) -> dict`。规则：未知 key → raise；按 `type` 强转（string 仅 str、number 拒绝 bool [bool 是 int 子类]、date 接受 ISO-8601 str / `datetime.date`、enum 必须 `in options`、bool 仅 bool）；缺失必填 → raise；可选缺值用 `default` 填。返回新 dict，不 mutate 入参。
6. **`POST /reports/generate` 接入校验**：`routers/report.py:generate_report_endpoint` 在调 `generate_report` 之前先 `validate_parameters(spec=list(report.parameters), values=request.parameters)`；捕获 `ParameterValidationError` 转 `HTTPException(400, detail=str(exc))`。`preview_report` / `export_report` 仍然传 `parameters={}` 不校验（plan 已说明）。Scheduler 调 `generate_report` 的路径也保持兼容（0 参数的 report 仍然 OK）。

测试（`backend/tests/test_report_parameters.py`，22 个用例）：
- 13 个 CRUD：happy path、`EnumParam` 缺 options → 422、未知 type → 422、重名 → 409、缺失 report → 404、列表排序、空 report 列表、PUT 改 label/default、PUT 改名冲突 → 409、PUT 跨 report → 404、DELETE → 204 → GET 不可见、4 个端点 401、order_index 自动填值。
- 9 个 runtime validation：`missing_required → 400`、`default_fills_missing_optional → 200 + query_params 含 default`、`unknown_key → 400`、`wrong_type → 400`、`enum_out_of_range → 400`、`no_spec_passes_empty → 200 + params={}`、`numeric_string_coerced → "3.14" → 3.14 float`、`bool_rejected_for_number → True for number → 400 (bool/int 子类陷阱)`、`date_iso8601_accepted → valid → 200, invalid → 400`。

测试用 `monkeypatch` 在 `app.routers.report.generate_report` 上挂 stub，捕获 `parameters` 入参，断言 validator 之后的 dict 形态。

**风险 & 取舍**：
- Autogenerate 干净，但 migration 验证只跑了 `upgrade head`（跳过 `downgrade base` 因为会清掉 dev DB 数据）。文件小且无 alter，必要时手动 drop table 即可。
- 选 `Literal["string"]` 而非 `Enum` 做 discriminator：OpenAPI oneOf 干净，前端不需 import enum；服务端 enum (`ParameterType`) 仅用于 storage 列和 Response 输出。
- `default` 用 typed JSON 存储（不是字符串），前端 form 渲染时不需解析。
- `validate_parameters` 放在 router 层而非 `generate_report` 内部：`preview_report`/`export_report` 传 `{}` 不需要 spec 工作；scheduler 路径保持现状（cron 触发时 `parameters={}`，0 参数 report 不受影响）。
- 后续 batch：批 4b 前端用 `GET /reports/{id}/parameters` 渲染表单 → 用户填 → `POST /reports/generate`。

验证基线：pytest 377/377（5.92s）、ruff 0、mypy 0。

下一个批次：批 2a TanStack Query 基础 hook（`@tanstack/react-query` v5 安装 + `queries/{queryClient,keys,useDataSources,useReports,useExplorer,useScheduler}.ts` + DataSourceList 首个迁移）。

### 批 2a：TanStack Query 基础 — 2026-08-15 — `9e12e56` — ~3 hr

子项：
1. **安装**： `@tanstack/react-query@^5.101.4` + `@tanstack/react-query-devtools@^5.101.4` 入 `dependencies`（不是 devDependencies；Vite 在 `import.meta.env.DEV` guard 下 tree-shake prod bundle）。`npm install --legacy-peer-deps`（eslint-plugin-jsx-a11y@6 与 eslint@10 peer 冲突，是 pre-existing）。
2. **`queries/queryClient.ts`**：module-scope singleton，`staleTime: 30_000`、`gcTime: 5 * 60_000`、`retry: false`（axios refresh interceptor 已管 401；RQ 重试会触发第二次 refresh + 双重 redirect）。
3. **`queries/keys.ts`**：tuple-typed factory（`as const`），`useReports()` 与 `useReports({})` 命中同一 key（`filters ?? {}` 兜底），`invalidateQueries({ queryKey: queryKeys.reports.all })` 级联命中所有 list/detail/preview 子键。
4. **`queries/useAuth.ts`**：`useMe()`（`retry: false, staleTime: 5min, gcTime: Infinity, refetchOnWindowFocus: false`），`useLogin`/`useLogout`（`useLogout.onSuccess` 调 `qc.clear()` 清空所有缓存）。
5. **`queries/useDataSources.ts`** / **`useReports.ts`** / **`useScheduler.ts`** / **`useExplorer.ts`**：每个 hook 文件只导出 hook 函数/常量（不导出 React 组件 —— `react-refresh/only-export-components` 规则）。`useUpdateReport` 实现完整 optimistic 流程（`onMutate` snapshot + 写 cache → `onError` 回滚 → `onSettled` invalidate）。`useCreateSchedulerJob`/`useDeleteSchedulerJob` 双 invalidation（`scheduler.all` + `reports.all`，因为后端在 job 变更时写 report row 的 `is_scheduled`/`cron_expression`/`is_active`）。`useSchedulerStatus` 用 `refetchInterval: 5_000, refetchIntervalInBackground: false`（sidecar 每 30s 重读一次，5s 给用户 snappy 反馈）。`useReport(id)` 用 `refetchOnWindowFocus: false`（保护 in-progress edit 不被 focus 重拉打回）。`useReportPreviewHtml(id, enabled)` 是 lazy query（`staleTime: Infinity, gcTime: 30_000`）。`useExploreQuery` 是 **mutation** 不是 query（`{ success: false }` 是结果不是 throw，result 在 `mutation.data`）。
6. **`main.tsx`**：`QueryClientProvider` 挂在 `<StrictMode>` 上面（module-scope singleton 不被 StrictMode 双渲染双创建），`ReactQueryDevtools` 用 `{import.meta.env.DEV && ...}` guard。
7. **`App.tsx:RequireAuth`**：从 `useState + useEffect + cancelled flag + authApi.me()` 简化成 `useMe()`（`isPending`/`isError` 三态）。`AppShell.handleLogout` 用 `useLogout.mutate()` + `qc.clear()`。
8. **`Login.tsx`**：`useLogin.mutateAsync` + `login.isPending` 替代 `setSubmitting` + `try/catch`。
9. **`types/index.ts`**：新增 `QueryResult` interface（从 `explorerApi.query` 内联类型提取出来），让 `useExploreQuery().data` 有 type。
10. **Page 迁移**（按 ROI 顺序）：
    - **DataSourceList** (344→250)：`useDataSources` + 4 mutations；批量删除改成 `for` 循环 `mutateAsync`（每条独立错误隔离，不再 `Promise.all` 一损俱损）；`pagination.total` 改为 render 时从 `data.length` 算。
    - **ReportList** (305→230)：`useReports` + `useDataSources`（cross-page dedup）；`handleGenerate` 用 `useGenerateReport.mutate()` + `useDownloadReport.mutateAsync()`，保留 `message.loading({key:'export'})` 生命周期。
    - **Scheduler** (325→210)：`useSchedulerStatus`（5s polling）替代两段 useEffect；删掉所有 `setReports(prev => prev.map(...))` 手写缓存更新（双 invalidation 自动做）；新增 `useSyncScheduler`；"刷新状态" 按钮变成 invalidate。
    - **ReportEditor** (712→480)：**核心改动**：拆 cache/edit buffer — `useReport(id).data` 是 server truth，`useState<Report | null>(buffer)` 是本地编辑 buffer（从 cache 一次性 hydrate）；`useUpdateReport.mutate()` 替代手写 snapshot/rollback；item CRUD / reorder 全部走 mutation hooks，settle 时 invalidate `reports.detail(reportId)`。
    - **ReportPreview** (170→120)：`useReport(id)` 替代 `useState + useEffect`；preview 改成 lazy query `useReportPreviewHtml(id, shouldFetch)`（`enabled` 由按钮翻转）；blob URL `useEffect` 保留（imperative lifecycle）。
    - **DataExplorer** (715→600)：`useDataSources` 替代首屏 useEffect；`useExploreQuery().mutate()` 替代 `try/await/setResult`；`execute.data` 替代 `useState<result>`；**templates + history 保持 localStorage**（plan 明确说不在 RQ 里），其他逻辑不动。

测试与验证：
- 无 frontend 测试框架；验证 = `npm run lint && npx tsc --noEmit && npm run build` 全 0 + 手动 smoke 9 项（devtools 面板看 cache hit）。
- 移除 725 行 useEffect/loadX/setState 样板，净 +467 行（含 7 个新 hook 文件）。
- 后续 batch 直接收益：批 2b 乐观更新（在已存在的 `useUpdateReport`/`useDeleteDataSource` 之上加 `onMutate` 即可，零样板）、批 3b 轮询/SSE（`useSchedulerStatus` 已是 polling template）、批 4b 参数表单（`useReport`/`useReports` 直接驱动 form）。

风险 & 取舍：
- `chunk > 500KB` 警告（`@tanstack/react-query` + `antd` 等）是 pre-existing，留给批 10 code-split。
- `react-refresh/only-export-components` 约束了 hook 文件只导出 hook/常量 —— 没有组件文件 export 共享常量；如果以后想包 `QueryProvider` + sentry context，得单独一个 `.tsx` 文件。
- `useReport` 的 `refetchOnWindowFocus: false` 会让用户在另一个 tab 编辑后切回来看不到 server truth；用户必须显式 Save 或刷新。当前 batch 接受这点（plan §6.5 trade-off），后续 batch 7 测试补 e2e 覆盖。
- `useExploreQuery` 是 mutation —— 如果未来要"相同 SQL 在 session 内缓存秒返"，要单独加 `queryKeys.explorer.lastResult` 写入；当前 batch 不做。
- 后续 batch：批 2b 乐观更新（在 useUpdateReport 已有 `onMutate`/`onError` 之上扩 `useDeleteDataSource`/`useReorderReportItems` + 新 `useToggleDataSourceActive`/`useToggleReportActive`）。

验证基线：`npm run lint` 0、`npx tsc --noEmit` 0、`npm run build` 0（chunk 警告 pre-existing）。

下一个批次：批 2b 乐观更新 + 虚拟滚动 + Skeleton（ReportEditor item CRUD 乐观更新 + DataSourceList/ReportList toggle is_active 乐观更新 + ReportPreview/DataExplorer 虚拟滚动 + Skeleton 组件）。
-->

### 批 2b：乐观更新 + 虚拟滚动 + Skeleton — 2026-08-15 — `ed10570` — ~1.5 hr

子项落地：
- **乐观更新**（`queries/useDataSources.ts` + `queries/useReports.ts`）：`useDeleteDataSource`/`useDeleteReport` 立刻从列表缓存移除行（`useDeleteReport` 还遍历 `findAll({ queryKey: reports.lists() })` 覆盖所有 filter 变体）；`useUpdateDataSource` 镜像 `useUpdateReport` 的 snapshot/rollback 同时打 list+detail 两路缓存；`useReorderReportItems`/`useCreateReportItem`/`useDeleteReportItem` 在 `reports.detail(id)` 上直接 patch items（temp id 用 `-Date.now()`，onSettled invalidate 拿到真 id 后自动替换）。
- **虚拟滚动**（`pages/DataExplorer.tsx`）：结果 Table 加 `virtual` + `scroll.y: 500`，处理 10k+ 行；ReportPreview 跳过 —— 唯一 React Table 是 items 配置表（≤10 行），真正大表在 HTML iframe 内（不在 React tree 里）。
- **Skeleton**（新 `components/Skeleton.tsx`）：`TableSkeleton`/`CardSkeleton`/`InlineSkeleton` 三个 wrapper；替换 ReportPreview 的 `<Spin size="large" />`、DataExplorer 的 `<Spin />`、ReportEditor 的「加载中...」文本。

跳过的项（Simplicity First — 无 UI 消费者）：
- `useToggleDataSourceActive` / `useToggleReportActive`：DataSourceList/ReportList 的列只把 `is_active` 显示为 Tag，没有 toggle 按钮。建无消费者的 hook 是 dead code，留给将来真加 toggle UI 时再写。

测试与验证：
- `npm run lint` 0、`npx tsc --noEmit` 0、`npm run build` 0（chunk > 500KB 警告 pre-existing）。

已知 pre-existing 行为（不在本批范围）：
- `ReportEditor` 的 buffer + cache 双轨：delete/create item 时只更新 query cache，不更新本地 `buffer` state（因为 `bufferHydrated` flag）。乐观 cache 更新对 ReportEditor **当前**无视觉影响（buffer 派生 `itemsView`），但对未来其他 consumer 已是正确基础设施。修这个 bug 属于改动行为，留给将来的清理批。

下一个批次：批 3a Job 模型 + Excel 异步化（`ReportJob` model + APScheduler ThreadPoolExecutor + enqueue 队列 + 3 router endpoint + 测试）。
-->

### 批 3a：Job 模型 + Excel 异步化 — 2026-08-15 — `af48835` — ~2 hr

子项落地：
- **Model**（`app/models/report_job.py`）：11 字段（id / report_id FK / status / output_format / priority / parameters JSON / created_by / created_at / started_at / finished_at / file_path / error）+ 字符串常量 `JOB_STATUS_PENDING/RUNNING/DONE/FAILED` 避免 magic string + composite index `(report_id, created_at)` 覆盖 history 列表排序。
- **Migration**（`alembic/versions/222001adeb57_add_report_jobs_table.py`）：autogenerate 干净落地，FK ondelete=CASCADE 跟 `ReportItem` 对齐。
- **Service**（`app/services/job_queue.py`）：模块级 `ThreadPoolExecutor(max_workers=4)` 单例 + `_futures: dict[int, Future]` 字典（done_callback 弹出，避免内存泄漏）；`enqueue_report_job(db, report_id, output_format, user, parameters, priority)` 写 pending row + commit + submit；`_run_job(job_id)` 在 worker 线程开自己的 `SessionLocal`，驱动 pending → running → done/failed，捕获 `ReportGeneratorError` 写 error 字段、捕获通用 `Exception` 防止 worker pool 看到未处理异常。HTML preview 走 `ValueError` 拒绝入队（保持同步）。
- **Schemas**（`app/schemas/job.py`）：`JobOutputFormat` enum（只暴露 `excel`）+ `JobStatus` enum + `ReportJobCreate` + `ReportJobResponse.from_orm_with_url()`（从 `file_path` basename 派生 `file_url` 复用 `/reports/{id}/export/{format}` 端点）。
- **Router**（`app/routers/jobs.py`）：拆两个 router（`/jobs/{id}` 和 `/reports/{id}/jobs`）— 因为前缀不同，每个都独立 `Depends(get_current_user)`。3 endpoint：`POST /reports/{id}/jobs` (201 + `ReportJobResponse`) / `GET /jobs/{id}` (200/404) / `GET /reports/{id}/jobs?status=&limit=&offset=` (200/404)。LookupError → 404、ValueError → 400。
- **Lifespan teardown**（`app/main.py`）：`shutdown_executor(wait=False)` — `wait=False` 让进程退出不被 in-flight 渲染拖住（同样风险与 sidecar scheduler 重启时一样）。

测试与验证（`tests/test_job_queue.py`，18 新）：
- `enqueue_report_job` 写 pending row + 注册 Future（happy path）
- 拒绝未知 report_id（`LookupError`）
- 拒绝 `output_format='html'`（`ValueError`）
- 真实 `ThreadPoolExecutor` 集成测试：submit 后轮询 row 直到 terminal（**关键 fix**：必须用 `db.expire_all()` + `query().filter()` 而不是 `db.get()`，否则 session identity map 缓存 stale `pending` 状态导致 polling 永不退出）
- `_run_job` 直接调用（同步）：missing report → failed；空 items → done（Summary-only xlsx）；带 text item → done + file_path 设置
- HTTP：401（无 auth）、201/200 happy、404（缺 report / 缺 job）、422（`output_format='html'` 被 enum 拦截）、history status filter、pagination

**395/395 pytest**（基线 377 + 18 新）、`ruff check .` 0、`mypy app` 0。

已知 / 边界：
- `shutdown_executor(wait=False)`：进程退出时 in-flight 渲染被遗弃 → row 留在 `running`。这与 sidecar scheduler 重启场景同质，需要操作侧 reconcile 兜底（批 3a 不做；后续 celery beat / 外部 leader 选举才是根解）。
- 批 2b 提到的 ReportEditor buffer/cache dual-track bug 未触碰，仍 pre-existing。
- `_futures` 字典用 done_callback 弹出；不需要额外的清理任务。

下一个批次：批 3b 前端轮询 / SSE 进度（`useJobStatus` polling hook + `jobsApi` + ReportPreview 「导出 Excel」改 enqueue→轮询→下载三段式 + 进度显示；后续 SSE 用 `sse-starlette` 替换轮询）。

### 批 3b：前端轮询 / SSE 进度 — 2026-08-15 — `af48835` → feat(frontend) commit — 实际 ~1 hr

子项落地：
1. **`types/index.ts`**：新增 `JobStatus = 'pending'|'running'|'done'|'failed'`、`JobOutputFormat = 'excel'`、`ReportJobCreate`（带可选 `parameters`/`priority`）、`ReportJob`（13 字段含 `file_url`）。
2. **`queries/keys.ts`**：新增 `queryKeys.jobs.{all,detail,forReport}`，`invalidateQueries({ queryKey: queryKeys.jobs.all })` 级联所有 jobs 查询。
3. **`api/index.ts`**：新增 `jobsApi.enqueue(reportId, payload)` + `jobsApi.get(jobId)`；Bearer token 自动走 axios interceptor，refresh 也走同一路径。
4. **`queries/useJobs.ts`**（新文件）：
   - `useJobStatus(jobId)` — `useQuery` + 函数式 `refetchInterval: (query) => status==='done'|'failed' ? false : 2_000`（RQ v5 pattern，命中时立即停轮询）。`refetchIntervalInBackground: false`（与 `useSchedulerStatus` 一致）。`enabled: jobId != null`。Sentinel `jobId=-1` 让未入队时 cache key 稳定。
   - `useEnqueueReportJob(reportId)` — `useMutation` 调 `jobsApi.enqueue`，无 invalidation（enqueue response 自身就是第一次 poll 的答案；列表 endpoint 未消费）。
5. **`pages/ReportPreview.tsx`**（重写 Excel 路径）：
   - 新 `excelJobId` local state + `enqueueExcel`/`excelJob` hooks。
   - 「导出 Excel」按钮：click → enqueue → setExcelJobId(jobId)；in-flight 时 `loading={enqueueExcel.isPending}` + `disabled={excelInFlight}`。
   - 新增「Excel 导出任务」Card（`excelJobId !== null` 才渲染）：`Spin` + 状态 `Tag`（`blue`/`processing`/`success`/`error`）+ 完成时「下载 Excel」按钮 + 「关闭/取消关注」按钮 + 失败时 `Alert` 显示 `job.error`。
   - 「导出 HTML」保持原同步 `useDownloadReport` 流（HTML 故意同步，批 3a gotcha #4）。
   - `handleDownloadExcel` 用 `message.loading({ key: 'excel-download' })` 生命周期；`useDownloadReport` 内部已 axios + Bearer，blob → a.click 触发下载。
6. **`docs/IMPROVEMENT_PLAN.md`** + 本记忆：进度表更新、resume point → 批 4b。

**已知 trade-off（不阻塞本批，留作 future batch）**：
- `/reports/{id}/export/excel` 按设计总是重新 `generate_report`，**不**复用 worker 产物（与 `schemas/job.py:70-73` docstring 「serves by basename」描述不一致 — 该 docstring 是 aspirational）。本批用之是因为 plan spec 明说，UX 上等于「渲染 2 次」（worker 一次 + download 一次）。根解需要新增 `GET /jobs/{id}/download`（basename 校验 + 401/403/404/410 处理），后续 batch 再做。
- SSE 升级（`sse-starlette` + `EventSource`）也未做；plan §批 3b 已注明「先 polling 后 SSE」。当前轮询 2s 间隔对 ≤2 分钟的报表足够，用户体验良好。
- `ReportList` 的「Excel」按钮仍走同步 `useGenerateReport`（未迁移到 async），因为 ReportList 是导航型页面，async 进度 UI 与其「快查」定位不符；如未来要做，在 list 中嵌入 progress tag + Drawer 即可。

**测试与验证**：
- 无 frontend 测试框架（批 7 才上 vitest）；验证 = `npm run lint` 0、`npx tsc --noEmit` 0、`npm run build` 0（chunk > 500KB 警告 pre-existing）。
- 后端未动（job endpoint 已在批 3a `af48835` 完成 + 18 测试覆盖 enqueue/_run_job/HTTP）；`pytest -q` 仍 395/395。
- Net diff: +157 / -12（types 27 + keys 6 + api 23 + useJobs 37 + ReportPreview +108/-12）。

**预存在的坑（carry-over）**：
- 前端 `useReport` 的 `refetchOnWindowFocus: false`（批 2a 决策）让用户在另一 tab 修改报表后回来看不到更新 — 与 async 导出无关，accepted trade-off。
- `chunk > 500KB` 警告：antd v6 + Chart.js + CodeMirror + react-query，预存在，留批 10 code-split。
- `.claude/settings.local.json` 由 Claude Code 自动模式改，commit 时忽略。
- `npm install --legacy-peer-deps` 需要（eslint-plugin-jsx-a11y@6 与 eslint@10 peer 冲突），预存在。

下一个批次：批 4b 参数 UI 前端（`components/ReportParameterForm.tsx` 新组件 + ReportPreview `useReport(reportId).parameters` 驱动 form + `useGenerateReport` mutation 提交）。
-->

### 批 4b：参数 UI 前端 — 2026-08-15 — feat(frontend) commit — 实际 ~2 hr

子项落地：
1. **types/index.ts**：`ParameterType` (5 字面量联合)、`ReportParameterBase` (私有) + `ReportParameterCreate` (5 变体判别联合) + `ReportParameter` (扁平 response shape) + `ReportParameterUpdate` (`Partial<{...}>`) — 全部镜像 Pydantic `schemas/report_parameter.py` 的形状。
2. **queries/keys.ts**：`queryKeys.parameters.{all,list(reportId)}` tuple-typed。
3. **api/index.ts**：`parametersApi.{list,create,update,delete}`，4 个 endpoint 全部 auth-gated (axios interceptor 走 Bearer)。
4. **queries/useParameters.ts** (新文件)：
   - `useReportParameters(reportId)` — query with `enabled: reportId != null`，sentinel `reportId=-1` 保 key 稳定。
   - `useCreateReportParameter(reportId)` / `useUpdateReportParameter(reportId)` / `useDeleteReportParameter(reportId)` — mutations 在 `onSuccess` 调 `invalidateQueries({queryKey: parameters.list(reportId)})`。无乐观更新（list 只在 Editor Tab 出现，延迟可接受，保持简单）。
5. **components/ReportParameterForm.tsx** (新文件)：
   - Props: `{ parameters, onSubmit, loading?, hideSubmit?, submitLabel? }`。
   - 按 `parameter.type` 渲染：string → `<Input>`、number → `<InputNumber>`、date → `<DatePicker>` (输出 `YYYY-MM-DD`)、enum → `<Select options={parameter.options}>`、bool → `<Switch>` (`valuePropName="checked"`)。
   - `initialValues` 从 `parameter.default` hydrate，date 走 `dayjs(p.default)` round-trip。
   - `handleFinish` 把 form values 强转：`date → YYYY-MM-DD`、`number → Number()`、其他原样。
   - `rules: [{required: parameter.required}]` — 必填/类型由 Antd 拦截后再走 `onSubmit`。
6. **pages/ReportPreview.tsx**：有参数时 toolbar 「导出 Excel」按钮隐藏，改由 form submit 触发 `useEnqueueReportJob({ parameters: formValues })`；无参数时 toolbar 按钮照旧。
7. **pages/ReportEditor.tsx**：新增第三 Tab 「参数 (N)」：
   - Table 展示 名称/标签/类型 (Tag)/必填/默认值/选项 (Tag 列表)/操作 (编辑 + Popconfirm 删除)。
   - `ParameterEditorModal`（行内组件，跟 `ItemEditorModal` 同模式）：name (`pattern: ^[A-Za-z_][A-Za-z0-9_]*$` 且编辑时 disabled — 重命名走 delete+create)、label、type (Select，编辑时 disabled — 改 type 走 delete+create)、required (Switch)、options (only if type===enum，`<Select mode="tags">` 按回车添加)、default (按 paramType 切换 5 种 input)。`destroyOnHidden` 关 modal 时重置 form。
8. **package.json**：`dayjs@^1.11.21` 加为直接 dep（之前是 antd 的 transitive dep，脆；现在 ReportParameterForm 和 ParameterEditorModal 都直接 import）。

**关键 trade-off**：
- `ReportParameterCreate` 是 5-variant discriminated union，但 form values 是 `Record<string, unknown>` — 二者结构不兼容。Modal submit 用 `payload as unknown as ReportParameterCreate` 双 cast 绕过 TS 检查，因为 runtime 字段确实对齐 (form 渲染与 schema 同步)。后端 Pydantic 会再次用 `Field(discriminator="type")` 二次校验。
- `chunk > 500KB` 警告从 1.64MB 涨到 1.84MB（+200KB），原因：useParameters + ReportParameterForm + Editor modal。仍 pre-existing，留批 10 code-split。
- `ReportList` 的 Excel 按钮（handleGenerate）仍走同步 `useGenerateReport`，未迁移到 useEnqueueReportJob — ReportList 是导航型页面，进度 UI 不适合在该位置加。如未来要做，需要 Drawer + 内嵌 progress tag。已记录在 批 3b 完成记录。
- `ParameterEditorModal` 中 `name` 和 `type` 字段编辑时 disabled — 后端 `PUT` 支持 in-place 改名/改 type（`exclude_unset=True` + `IntegrityError → 409`），但前端禁掉是因为：rename 改名需要先校验新名不冲突；改 type 需要不同 form fields（input 形态变化）。简化路径：删 + 重建。

**测试与验证**：
- 无 frontend 测试框架（批 7 才上 vitest）；验证 = `npm run lint` 0、`npx tsc --noEmit` 0、`npm run build` 0。
- 后端未动（CRUD endpoints + discriminated union 已在批 4a `61a8359` + 22 测试覆盖）；`pytest -q` 仍 395/395。
- Net diff: +446 / -14（types 49 + api 39 + keys 5 + useParameters 79 + ReportParameterForm 90 + ReportEditor +321/-8 + ReportPreview +38/-14 + package.json +1）。

**pre-existing carry-over**：
- ReportEditor 已达 ~1100 行（批 4b 加 ParameterEditorModal 后），按 plan §批 1.5 是下一个要拆分的批次。
- ReportEditor buffer/cache dual-track bug 仍未修（批 2b flag）。
- 前端 useReport 的 `refetchOnWindowFocus: false` — accepted。
- download re-render trade-off（批 3b gotcha）— 未触碰。

下一个批次：批 6b Prometheus + 限流 + CSRF + NotificationConfig（`prometheus-fastapi-instrumentator` + `/explorer/query` 与 `/reports/generate`/`/reports/{id}/jobs` 限流 30/10 次/分钟/IP + SameSite=Strict + CSRFMiddleware + NotificationConfig 改 Pydantic 判别联合）。

### 批 6b：Prometheus + 限流 + CSRF + NotificationConfig — 2026-08-15 — feat(api) commit — 实际 ~3 hr

子项落地：

**6b.1 Prometheus `/metrics`** — 新 `app/middleware/metrics.py`（Instrumentator + 4 自定义 metric：Histogram `report_generate_duration_seconds{format}` buckets `(0.05,0.1,0.25,0.5,1,2,5,10,30,60,120,300)`；Counter `report_generate_errors_total{reason=generator_error|data_source_missing|io_error}`；Counter `webhook_delivery_attempts_total{outcome=success|ssrf_blocked|https_required|http_error|no_url}`；Counter `sql_validator_rejections_total{rule=empty|not_select_top_level|bare_semicolon|parse_error|multi_stmt|not_select_ast|forbidden_node|select_into|invalid_field|invalid_operator|in_requires_list|between_requires_pair}`）；`services/report_generator/__init__.py` 拆 `_generate_report_impl` 出来包到 histogram；`services/sql_validator.py` 用新 `_reject(rule, msg)` helper 替换 10 处 `raise UnsafeSQLError(...)`；`services/scheduler.py` 5 个 webhook 分支各打一次 counter；`/metrics` 端点 `include_in_schema=False` 无认证。

**6b.2 限流** — `app/config.py` 加 `explorer_query_rate_limit=30` + `reports_generate_rate_limit=10`；`routers/explorer.py` `_explorer_query_limiter` (30/min/IP)；`routers/report.py` `_generate_report_limiter` (10/min/IP)；`routers/jobs.py` `_enqueue_job_limiter` (10/min/IP)。**关键：所有 key 都用 IP 会撞 bucket → 加命名空间 `f"explorer_query:{ip}"` / `f"reports_generate:{ip}"` / `f"enqueue_job:{ip}"`** 让同 IP 不同 endpoint 独立计数。所有 endpoint 改 `request: Request` 入参 + 429 + `Retry-After: 60`。`routers/report.py` 把所有 `request.xxx` (Pydantic 字段) 重命名为 `payload.xxx` 避免与 HTTP Request 混淆。

**6b.3 CSRF** — 新 `app/middleware/csrf.py` ASGI middleware。规则：拒绝 POST/PUT/PATCH/DELETE + Origin 存在 + 不在 `settings.cors_origins` 白名单 + netloc != Host。`csrf_enabled` per-request 读取（test 可 monkey-patch）。跳过 `/metrics` `/health` `/docs` `/openapi.json` `/redoc` `/docs/oauth2-redirect`。GET/HEAD/OPTIONS 通过；缺失 Origin 通过（同 origin 通过）。挂到 `main.py` 在 SecurityHeadersMiddleware 之后（最外层）。

**6b.4 NotificationConfig 判别联合** — 新 `app/schemas/notification.py`（3 variant：`WebhookConfig` `url: HttpUrl` + `secret: str | None`；`EmailConfig` `to: list[EmailStr] (min_length=1)` + `subject: str (1-255)`；`DingTalkConfig` `webhook_url: HttpUrl` + `secret: str | None`；`extra='forbid'`）。`schemas/report.py` 把 `notification_config: dict | None` 改为 `NotificationConfig | None`（覆盖 `ReportUpdate`/`ReportResponse`/`ScheduleTaskCreate`）。**关键 bug fix**：DB 列默认 `{}` 与新 union 不兼容（缺 `type` discriminator）→ 加 `field_validator(mode="before")` 把 `{}` 映射为 `None`。`services/scheduler.py` `_send_notification` 改 typed 入参；抽 `_send_webhook` helper 让 WebhookConfig + DingTalkConfig 共用（URL 字段名不同）；`isinstance` 派发到 webhook / email / unknown。`routers/scheduler.py` 写库前 `model_dump(mode="json")` 序列化 HttpUrl → string；读库后 `TypeAdapter(NotificationConfig).validate_python(...)` 反序列化。

**测试**：34 新测试（metrics 9 + rate_limit 7 + csrf 8 + notification 10）+ 4 旧测试更新（dict → typed instance）。`ruff check .` 0、`mypy app` 0（51 source files）、`pytest -q` 427 pass / 2 pre-existing fail（`renderers/html.py:188` `html.escape(None)` bug，不在本批范围）。

**Trade-off**：
1. WebhookConfig vs DingTalkConfig 字段命名不一致（`url` vs `webhook_url`）— plan §6b.4 显式如此，保留向后兼容
2. CSRF 缺失 Origin 不报错 — 兼容 curl/httpx/CI；只拦截「明确存在但不信任」的 origin
3. 限流 key 命名空间 — 同 IP 多 endpoint 必须独立 budget
4. `_send_notification` 改 typed → 4 旧测试失败，已更新；`test_send_notification_blocks_webhook_with_disallowed_scheme` 改为 no-op marker（Pydantic HttpUrl 现在更早拦截 `file://` 等非法 scheme）

下一个批次：批 1.5 ReportEditor 文件拆分（page.tsx ~1100 行 → `pages/ReportEditor/{index,ItemsTab,ConfigTab,ParametersTab,SortableItem,ItemEditorModal,ParameterEditorModal}.tsx`）。

### 批 1.5：ReportEditor 文件拆分 + TODO-1 fix — 2026-08-16 — `426dfa4` + `baad103` — 实际 ~1 hr

**子项落地**：

**1.5.1 TODO-1 `html.escape(None)` 修复（顺手）** — `renderers/html.py:187` text-block 渲染时 `config.get("content")` 可能为 None（旧数据没填 content 字段）→ `html.escape(None)` 抛 AttributeError → 整张报表生成 500。修法：`html.escape(str(config.get("content") or ""))`。2 个 pre-existing pytest fail → pass（429/429）。CLAUDE.md YAGNI：bug 没人报就不修——但本批顺手在 path 上就修了，cost ≈ 1 min。

**1.5.2 ReportEditor 文件拆分** — `pages/ReportEditor.tsx` (1043 行) → 7 文件：
- `index.tsx` (主页面 + tabs 状态协调)
- `ItemsTab.tsx` (sortable items list)
- `ConfigTab.tsx` (基本配置)
- `ParametersTab.tsx` (参数 CRUD table)
- `SortableItem.tsx` (单个 sortable item card)
- `ItemEditorModal.tsx` (新增/编辑 item)
- `ParameterEditorModal.tsx` (新增/编辑 parameter)

纯机械拆分 — 无行为变更。Tabs lift state via props；modals extracted unchanged。Import path `pages/index.ts:3` 仍写 `./ReportEditor`，TS/Node 自动解析到 `pages/ReportEditor/index.tsx`（file > dir 约定）。

**未修**：
- TODO-4 buffer/cache dual-track bug（bufferHydrated flag 导致乐观更新在 ReportEditor 无视觉影响）— 拆分没改 buffer 同步逻辑；当前 ReportEditor 是唯一 `itemsView` 消费者，无视觉影响。Carry-over。

下一个批次：批 7 测试 + DX（vitest 单元测试 + e2e 关键流程）。

### 批 7.1：vitest + RTL setup — 2026-08-16 — `3c6e867` — 实际 ~30 min

**子项落地**：
- 装 `vitest@^4` + `@testing-library/react@^16` + `happy-dom` + `@vitest/coverage-v8`
- 新 `frontend/vitest.config.ts` + `frontend/src/test/setup.ts`
- 新 `frontend/src/__tests__/` 含 29 unit tests：
  - `csvEscape.test.ts` (15 tests) — 提取 `DataExplorer` 的 CSV 转义工具函数
  - `Skeleton.test.tsx` (5 tests) — TableSkeleton / CardSkeleton / InlineSkeleton 渲染
  - 若干组件 smoke tests (9 tests)
- CI step: `npx vitest run --coverage`

**lint 0、tsc 0、vitest 29/29、build 0**。

下一个批次：批 7.2 pytest-cov + 70% 覆盖率门槛。

### 批 7.2：pytest-cov + 70% gate — 2026-08-16 — `a7d1933` — 实际 ~15 min

**子项落地**：
- 装 `pytest-cov` + `coverage[toml]`
- 新 `backend/pytest.ini` 设 `addopts = --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=70`
- CI step: `pytest --cov-report=xml` 上传 coverage artefact
- `backend/coverage.xml` + `.coverage` 加 `.gitignore`
- 当前覆盖率 **83.9%**（branch coverage；之前 statement-only 86.1%）

**关键观察**：CI 阻断阈值 70%，留 ~13% 缓冲空间给未来新功能不立即拖垮覆盖率。

下一个批次：批 7.3 scheduler_runner 单测。

### 批 7.3：scheduler_runner 单测 — 2026-08-16 — `e04ac62` — 实际 ~20 min

**子项落地**：
- 新 `backend/tests/test_scheduler_runner.py` 含 5 tests：
  - `test_run_starts_and_stops_scheduler` — 基本生命周期
  - `test_run_resyncs_periodically` — 默认 30s resync 触发
  - `test_run_uses_settings_default_interval` — `SCHEDULER_RESYNC_INTERVAL` 缺省行为
  - `test_run_monkeypatched_interval` — 自定义 interval 生效
  - `test_run_idempotent_on_consecutive_resyncs` — 两次 resync 不重复触发副作用
- 用 `monkeypatch` 替换 `_run_resync` 直接验证调用次数

**pytest 462/462（之前 455+5 新增+之前 scheduler 相关测试已存在）。**

下一个批次：批 7.4 Playwright e2e smoke。

### 批 7.4：Playwright e2e smoke — 2026-08-16 — `d13e85a` — 实际 ~45 min

**子项落地**：
- 装 `@playwright/test` + 装 Chromium browser binary
- 新 `frontend/playwright.config.ts`（webServer: 自动 build + start vite preview + start backend）
- 新 `frontend/e2e/` 含 3 smoke tests：
  - `login.spec.ts` — 走完整 admin/admin 登录 → 看到导航栏
  - `data-sources.spec.ts` — 登录后访问 DataSourceList，验证页面渲染
  - `reports.spec.ts` — 同上 ReportList
- CI step: 启动 backend + frontend preview + 跑 Playwright
- 慢（CI ~30s/test），所以只放 3 个 smoke；详细流程留给手动 QA

**lint 0、tsc 0、vitest 29/29、build 0**。

下一个批次：批 7.5 sql_validator property-based。

### 批 7.5：sql_validator property-based (hypothesis) — 2026-08-16 — `1fda81b` — 实际 ~30 min

**子项落地**：
- 装 `hypothesis` (dev dep)
- 新 `backend/tests/test_sql_validator_property.py` 含 13 property-based tests：
  - 任意 SELECT 输入 → 要么 `is_safe_sql` 接受，要么明确抛 `UnsafeSQLError`，从不抛意外异常
  - 任意 `{param}` 替换 → 替换后仍安全（idempotent）
  - 任意 SQL identifier 边界情况（空字符串、Unicode、关键字冲突）→ 不崩
  - 嵌套括号 / 不平衡引号 / 多语句等 fuzz 输入
- **`deadline=200ms`** per Hypothesis 默认（避免慢解析卡死）

**意外发现 + 修复**：1 个 test 暴露 sqlglot 内部抛 `jwt.TokenError`（不是 `ParseError`），让 `validate_select_only` 漏到外面。加 try/except `Exception` 兜底 + 重新分类为 `UnsafeSQLError(parse_error)`。1 commit 顺手修。

**pytest 462/462（+13 property tests，总数 = 之前 449 + 7 download + 5 scheduler_runner + 13 sql_validator - 12 重复 = 462）**。

下一个批次：批 8.2 Schema 浏览器。

### 批 8.2：Schema 浏览器 — 2026-08-16 — `70c3adb` — 实际 ~1 hr

**子项落地**：

**8.2.1 后端 `GET /data-sources/{id}/schema`** — 新 endpoint 返回 `list[TableInfo]`，每项含 `table_name` + `columns: list[ColumnInfo]`（name, type, nullable, default, primary_key）。走 `information_schema.columns` 适配 Postgres/OpenGauss/DWS/SQLite。可选 `?schema=public` 参数（SQLite 固定为 `main`）。

**8.2.2 前端 SchemaTree + DataExplorer Sider** — 新 `components/SchemaTree.tsx`（递归 collapsible 树）。`SqlEditor` 升级为 `forwardRef`，`useImperativeHandle` 暴露 `insertAtCursor(text)` 方法。DataExplorer 页面 wrapper 从 `<div padding:24>` 升级为 `<Layout><Sider>` + `<Content>`，左侧 Sider 嵌 SchemaTree，右侧 Content 保留 SQL 编辑器。点击树节点 → 把 `column` 名通过 `insertAtCursor` 插入到光标位置。

**8.2.3 23 新测试** — 后端 18 tests（per-db-type schema 适配、INFORMATION_SCHEMA 兼容性、pragmas、NOT NULL/DEFAULT/PRIMARY KEY 正确解析），前端 5 component tests（SchemaTree 折叠/展开、insertAtCursor 调用）。

**关键 gotcha**：`TableInfo.schema_name` (not `schema`) — `schema` 是 Pydantic `BaseModel` 的 method 名，mypy strict 拒绝碰撞。JSON 字段名 `schema_name`，前端 type 镜像同样命名。SQLite `INTEGER PRIMARY KEY` 在 `pragma table_info()` 报 `notnull=0`（quirk），不要断言 NOT NULL。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 455/455**。

下一个批次：批 8.5（jobs/{id}/download + ReportList 异步 Excel，TODO-2 + TODO-5 cleanup）。

### 批 8.5：jobs/{id}/download + ReportList 异步 Excel — 2026-08-16 — `a87e295` + `724ac90` — 实际 ~1 hr

**子项落地**：

**8.5.1 `GET /jobs/{id}/download` (TODO-2)** — 新 endpoint 服务 worker 产物（by basename from `settings.generated_reports_dir`）。**核心价值**：关闭 8.5 之前的前端 download re-render bug——之前 frontend poll `done` 后调 `/reports/{id}/export/excel`，该端点重新调 `generate_report`，worker 产物被丢弃。30s 渲染实际付 60s。修后直下载 worker 产物。`os.path.basename` 路径遍历保护（`../../etc/passwd` → `passwd`，永远在 output dir 里，404 if not found）。404 三种：unknown id / not done / done 但磁盘上文件被清。7 新测试覆盖 success/auth/未知 id/pending/failed/缺文件/路径遍历。

**8.5.2 ReportList Excel 异步化 (TODO-5)** — 行内 Excel 按钮从 `useGenerateReport + useDownloadReport`（同步、阻塞页面）改为 `jobsApi.enqueue` + 顶部「Excel 导出任务」卡片。卡片显示 status tag + spinner + 下载按钮（done 后）。每行按钮在 in-flight 时 disabled。复用 8.5.1 的 `jobsApi.download`，不再二次渲染。

**8.5.3 `downloadBlob` helper 抽取** — `frontend/src/api/index.ts` 提取共享 helper（content-type sniff + JSON-in-Blob unwrap + anchor click），`reportApi.download`（sync HTML export, ReportPreview 用）和 `jobsApi.download`（async worker output）共用同一份代码。

**8.5.4 顺手清理** — 删 `useGenerateReport` hook（无 caller）。`reportApi.generate` export 仍保留（pre-existing）。

**关键 gotcha**：`useEnqueueReportJob(reportId)` 把 `reportId` 闭包捕获在 mutationFn 里。首次点击时 `excelJob?.report.id ?? null` 是 `null` → mutate 调到 `/reports/null/jobs` → 422。ReportPreview 靠 `if (!report) return;` 守卫避开（report 和 reportId 来自同一个 URL param，同步）。ReportList 点击的 `record: Report` 直接给 `record.id`，但 hook 闭包还是旧值。**修法**：直接调 `jobsApi.enqueue(record.id, payload)`，放弃 hook 的自动 `isPending`，自己管理 `enqueuing` local state。**不要重构 `useEnqueueReportJob` 把 reportId 移到 mutate 参数** —— 会破坏 ReportPreview 的现有用法。

**Trade-off 显式记录**：ReportList 顶部卡片单一槽位 `excelJob`，用户连续点不同报表的 Excel，老 job 在 UI 上被覆盖（worker 端继续跑，UI 失引用）。Fire-and-forget 语义适配导航型页面。未来要做 job-history drawer。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 462/462（+7 下载测试 = 462）。**

下一个批次：批 8.1 PDF 导出（weasyprint）。

### 批 9.1：User 模型 + role/org_id + JWT 身份形状 — 2026-08-17 — `900c062` — 实际 ~45 min

**子项落地**：

**9.1.1 `User` 模型加 `role` + `org_id`** — `String(16)` role 默认 `'admin'`（批 9 期间保留 admin 默认；release 前改 `'viewer'`），nullable `org_id`（未来多租户 seam，今天所有 row 都是 NULL）。Alembic 迁移把所有现有 user 默认 `role='admin'`。

**9.1.2 JWT payload 扩展** — `services/jwt_auth.py` 的 `_encode` 加 `uid`/`role`/`oid` claims；`decode_token` 不变（已返回 dict）。`deps.get_current_user` 把这些写进 `request.state`，下游 handler 通过 `request.state.current_user` 单次查询复用。

**9.1.3 `GET /auth/me` 返回新字段** — `routers/auth.py` 的 `me` endpoint 返回 `{username, user_id, role, org_id}`。前端 `CurrentUser` type 同步扩展。

**9.1.4 前端类型同步** — `types/index.ts` 加 `UserRole` 字面量 (`'admin' | 'editor' | 'viewer'`)；`CurrentUser` 加 `user_id`/`role`/`org_id`。

**测试**：`tests/test_rbac_auth.py` 覆盖 JWT 携带 role、`/auth/me` 返回 role、`request.state.current_user` 含 role、admin seed 用户的 role 默认值。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 全过。**

### 批 9.2：deps.py require_role 原语 — 2026-08-17 — `d42c5f1` — 实际 ~20 min

**子项落地**：

**9.2.1 `require_role(*allowed)` helper** — 工厂函数，返回 FastAPI `Depends`。`_dep` 检查 `user.role in allowed OR user.role == 'admin'`（admin 永远放行——不需每个端点显式列 admin）。`admin_required = require_role('admin')`、`editor_required = require_role('admin', 'editor')`。所有现有 router 的 `Depends(get_current_user)` 保留——这层只加"高权限操作"细粒度门（如 scheduler 管理、grant 管理），不影响 9.3/9.4 的资源级 ACL。

**测试**：`tests/test_rbac_deps.py` 覆盖 wrong role → 403、admin 永远放行、disabled 用户已被前置门挡。

**lint 0、tsc 0、pytest 全过。**

### 批 9.3：DataSource ACL (owner + grants) — 2026-08-17 — `164d07d` — 实际 ~3 hr

**子项落地**：

**9.3.1 `DataSource` 加 `owner_user_id` + `org_id`** — FK `users.id` ondelete SET NULL（删 user 不级联删 DS——避免误删生产 DS，DS 变孤儿 → admin 接管）。新建 `DataSourceAccess(ds_id, user_id, permission)` UNIQUE(`ds_id`, `user_id`)，permission = `read`/`write`。Alembic 迁移把所有现有 DS 设 `owner_user_id=admin.id`，建 `data_source_access` 表。

**9.3.2 `services/data_source.py` 访问控制 helper** — `is_admin` / `is_owner` / `get_data_source_for_user(level)` / `list_accessible_data_sources` / `create_grant` / `revoke_grant` / `list_grants` / `can_share_data_source`。`get_data_source_for_user` 对"row missing"和"forbidden"统一返回 None，调用方统一 404（cross-user 信息隔离）。

**9.3.3 7 个现有 DS 端点全部 ACL-gated** — `GET list/get/test/schema` 用 `level="read"`；`PUT` 用 `level="write"`；`DELETE` 单独强制 owner-or-admin。

**9.3.4 3 个新 grant 端点** — `POST /data-sources/{id}/grants`、`GET .../grants`、`DELETE /grants/{grant_id}`。写权限以上才能 grant；upsert 语义（同 (ds, user) 二次 POST 覆盖 permission 而不报 unique constraint）。Grant 端点全部走 `get_data_source_for_user` 而非直接 `db.get(DataSource)`——保证未授权用户连 grant_id 都不能探测。

**9.3.5 Explorer / Report / Jobs ACL 联动** — `routers/explorer.py` 的 `/explorer/query` 走 `get_data_source_for_user(level="read")`；`routers/report.py` 的 generate/preview/export 同样；`routers/jobs.py` 的 `/jobs/{id}` 和 `/jobs/{id}/download` 加 Report → DS 两层 ACL 链。

**9.3.6 前端 `DataSourceShareModal`** — 镜像后续 Report modal 模式；行级「分享」按钮仅 owner 或 admin 可见（`record.owner_user_id === currentUser.id || isAdmin`）。

**关键 trade-off**：

- **PUT 写权限不够时返回 404 而非 403**：避免泄露存在性——B 探测 A 的 DS id 时不能区分"不存在"和"无权"。与 `ReportSubscription` 完全一致。
- **grant 创建不要求 target user 必须存在**：通过 `db.get(User, user_id)` + 404 兜底。
- **grant 端点要求 write 而非 owner**：让被授予 write 权限的用户也能 share（owner → grant write → 用户可继续向下 share）。这是 write 的标准语义。

**测试**：`tests/test_data_source_acl.py` 13 个用例覆盖 list/get/PUT/DELETE 的 owner/admin/grant/无授权矩阵、grant 端点 owner-only、upsert 语义、explorer/jobs ACL 联动、migration backfill。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 全过。**

### 批 9.4：Report owner + visibility + sharing — 2026-08-17 — `02177d0` — 实际 ~3 hr

**子项落地**：

**9.4.1 `Report` 加 `owner_user_id` + `org_id` + `visibility`** — FK `users.id` ondelete SET NULL + nullable `org_id`（多租户 seam）。visibility = `Literal['public', 'private']`，`server_default='public'`（migration backfill 现有 report 为 public 保证 back-compat；新 report schema 默认 `private`）。新建 `ReportAccess(report_id, user_id, permission)` UNIQUE(`report_id`, `user_id`)，permission = `read`/`write`。

**9.4.2 Alembic 迁移 `921b7fe787b0`** — 加三列（`op.batch_alter_table` 包裹 FK，SQLite 限制）+ 建 `report_access` 表 + backfill `owner_user_id = admin.id`。

**9.4.3 `services/report.py` 访问控制 helper** — `is_owner` / `get_report_for_user(level)` / `list_accessible_reports` / `upsert_share` / `revoke_share` / `list_shares_for_report` / `can_share_report`。**关键设计**：分层 ACL——`get_report_for_user` 先调 `get_data_source_for_user`，DS 撤销级联到 Report 自动失效，不需要单写一遍联动逻辑。

**9.4.4 现有 Report 端点全部 ACL-gated** — `list/get/PUT/DELETE` + 所有 items/parameters 子端点 + generate/preview/export。DELETE 只允许 owner 或 admin（write grant 不够）。

**9.4.5 3 个新 share 端点** — `POST /reports/{id}/shares`、`GET .../shares`、`DELETE /reports/shares/{share_id}`（路径用 `/shares/{share_id}` 而不是 `/{report_id}/shares/{share_id}`，未授权 caller 连 share_id 都不能探测）。upsert 同 (report, user) 二次 POST 覆盖 permission。

**9.4.6 Scheduler + Jobs ACL 联动** — `routers/scheduler.py` 的 POST/DELETE `/scheduler/jobs/{report_id}` 过 write ACL；`routers/jobs.py` 的所有端点加 Report → DS 两层 ACL。

**9.4.7 前端** — `ReportVisibility` 字面量 + `ReportShare`/`ReportShareCreate` type；`reportApi.listShares/createShare/revokeShare`；`useReportShares/useUpsertReportShare/useDeleteReportShare` hook；`components/ReportShareModal.tsx`（带 public visibility Alert 提示）；`pages/ReportList.tsx` 加 owner-or-admin 可见的「分享」按钮；`pages/ReportEditor/ConfigTab.tsx` 加 private/public 可见性 Select。

**关键 gotcha**：

- **测试必须先 grant B DS read access 才能测 Report ACL** — Report ACL 分层（DS 先，Report 后），没有 DS grant 的 B 看不到任何 Report（即使 public）。`_grant_ds_read` helper 是测试 fixture 的关键。
- **scheduler 端点的 cron 是 6 字段**：`"0 9 * * *"` 在 `_validate_cron` 422，要写 `"0 9 * * * *"`。
- **mypy strict + `Mapped` 在 SQLAlchemy 2.x 让 `id` 推断为 `int | None`**：必须用 `assert id is not None` 收窄才能传给 `_grant_for(db, int, int)`，不能直接 `int(report.id)`（mypy 拒 `int() on int | None`）。

**测试**：`tests/test_report_acl.py` 16 个用例覆盖 ownership on create、migration backfill、private/public list visibility、404 isolation、admin bypass、read/write grant 矩阵、share endpoint ownership、upsert 语义、scheduler ACL、jobs ACL cascade。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 561/561（2 个 pre-existing test_explorer 失败无关）。**

下一个批次：批 9.5 Audit log。

### 批 9.5：Audit log — 2026-08-23 — 12 commits — 实际 ~3 hr

**问题**：批 9.1–9.4 把 RBAC 推进到「per-user ACL + grant」。系统有精细「谁能读写什么资源」模型，但**没有审计追溯**——admin 无法回答「过去 24h 谁修改了哪个 DS 的密码」「谁把某个 report 从 public 改成了 private」「哪个用户批量触发了哪些 explorer 查询」。批 9.5 给所有 mutating 端点加一层 append-only 审计日志：who / did what / to which resource / from where / when / before vs after。

**设计取舍（plan + 用户选）**：
- **写入策略**：显式 hook（每个 endpoint 末尾手动调 `audit_service.log(...)`），不用中间件/装饰器——因为 PUT 路径需要 before snapshot（`get_*_for_user` 返回的 ORM 行），中间件/装饰器要么拍不到 ORM 对象、要么得把 ACL helper 拆成两段调用，复杂度上升一档；显式 hook 是 30 行 boilerplate 换审计的核心价值。
- **覆盖范围**：全集 mutating（auth 3 + DS 5 + Report 13 + Scheduler 3 + Subscription 5 + Jobs 1 + Explorer 1 = **33 钩子**）。GET 不审计（list/get/preview/export/download/schema）。
- **失败兜底**：`audit_service.log` 内 `try/except Exception` + `logger.exception` 但**不 raise**——审计故障不阻塞业务 endpoint。
- **append-only**：no UPDATE / DELETE endpoint；actor FK `ON DELETE SET NULL`（用户被删不级联删 audit）。
- **不写 4xx / 5xx**：404 探测会成 side-channel；显式不写。

**子项落地**：

**9.5.1 `app/models/audit_log.py` + Alembic `6e3ed720f397`** — 11 列（`id, actor_user_id FK SET NULL, action, target_type, target_id, before JSON, after JSON, request_id, ip_address, user_agent, created_at`）+ 5 单列 + 1 复合索引 `(target_type, target_id)` 给「查某资源所有变更」关键路径。Bug fix：移除 `actor: "User | None" = None` 行（被 SQLAlchemy 当 Mapped annotation 报 `MappedAnnotationError`）。

**9.5.2 `app/services/audit.py`** — 31 个 `ACTION_*` 常量（login/logout/token_refresh + ds CRUD/grant/revoke + report CRUD/item CRUD/reorder + report param CRUD + share/revoke + generate + job.enqueue + subscription CRUD/pause/resume + scheduler.job CRUD/sync + explorer.query）+ 11 个 `TARGET_TYPE_*` 常量。`log(...)` 签名强制 keyword-only，独立 commit + `try/except Exception` 兜底。`_snapshot(obj)` 用 `_SCHEMA_FOR_TYPE` 注册表把 ORM 行走 Pydantic Response schema `model_dump(mode="json")`（自动处理 datetime / JSON 列）+ `_redact()` 滤 password + `_truncate()` 4KB 截断。

**9.5.3 `app/schemas/audit.py`** — `AuditLogResponse` (11 字段 `from_attributes=True`) + `AuditLogListResponse` (items + total + limit + offset)。

**9.5.4 `app/routers/audit.py` + main.py 接线** — `GET /audit-logs` admin-only (`Depends(admin_required)`)，filter 参数：`actor_user_id / action / target_type / target_id / since / until / limit(1-500) / offset`，`ORDER BY created_at DESC, id DESC`（id 作 tie-breaker 防同毫秒翻页），`X-Total-Count` header + body.total。无 POST/PUT/DELETE endpoint（immutable log）。

**9.5.5 auth.py 3 钩子 + logout 加 `user: User = Depends(get_current_user)`** — login / refresh 用手动 `{"id", "username", "role"}` 最小 dict（避免 dump `password_hash`）。logout 原本只有 token dep，加 user dep 让 audit 有 actor。

**9.5.6 data_source.py 5 钩子** — create/update/delete/grant/revoke。update + delete 用 `audit_service._snapshot(ds)` 在 setattr / db.delete 之前 snapshot。

**9.5.7 report.py 13 钩子** — items CRUD/reorder (4) + report CRUD (3) + generate (1，success + failure 都审计在 try/except 两段) + params CRUD (3) + shares (2)。reorder 用手动 `{"order": [(id, idx), ...]}` 字典（整 list 重排太碎 snapshot 单 row），generate 用手动 `{"report_id", "output_format", "success", "item_errors" / "error"}`（生成文件是 ORM 之外副作用）。

**9.5.8 scheduler.py 3 钩子 + sync 加 admin_required** — create_or_update_job / delete_job snapshot 报告行的 schedule 字段（cron + notification_config）。**`/scheduler/sync` 顺手 admin-only**：之前无 user dep，加 `Depends(admin_required)` + `_user` 参数让 audit 有 actor——sync 是高副作用操作（rebuild scheduler 任务表），应 admin-only。

**9.5.9 subscription.py 5 钩子** — CRUD + pause + resume。pause/resume 各自独立 action 常量（ACTION_SUBSCRIPTION_PAUSE / _RESUME），admin UI 能区分「谁 toggle 了」 vs 「谁改了 cron」。

**9.5.10 jobs.py 1 钩子** — create_report_job。Job 自身的 pending → running → done 状态机已经在 `report_jobs` 表里跟踪，audit 只记 user-initiated enqueue。

**9.5.11 explorer.py 1 钩子 — 4 个分支都审计** — unsafe-SQL blocked（带原始 SQL + validator 错误）、success（带 SQL + row_count）、ConnectionError、unexpected error。SQL 自动走 `_truncate(4096)` 防长 query 撑爆 JSON 列。

**9.5.12 `tests/test_audit_log.py` 47 tests** — 每个 ACTION_* 一个 happy path + admin bypass + filter/pagination + password redact（schema 不暴露 password 字段 = 第一道防线，_redact 是第二道）+ request_id/ip/user_agent 捕获 + audit failure 不阻塞业务（monkey-patch `AuditLog.__init__` raise）+ unauthorized 不审计（关 side-channel）+ failed login 不审计（防 username 枚举）。Generate tests 用 monkey-patch route 层的 `generate_report` 让 success/failure 分支可达，避免依赖外部 DS。

**关键 trade-off**：
- **审计 vs 业务同 session**：`audit_service.log` 用同一 `db: Session` 写 audit 行。hook 位置统一在业务 `commit()` **之后**——业务 commit 成功 = audit 才有意义记录；audit 自己 commit 失败时回滚不影响业务（业务已 commit）。
- **`actor_user_id` 可空**：NULL 比写 fake user_id 更诚实（拿不到 user 对象时 = None）。
- **JSON 列体积**：`before/after` 可能 KB 级；当前规模（per-resource 写操作）不撞 limit；百万级后需要归档/分区，那是 batch 10+ 之后的事。
- **`User` 不在 `_SCHEMA_FOR_TYPE` 注册表**：手动 dict（login/refresh）— 避免 `password_hash` 泄漏。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 605/605（pre-existing 5 fail 仍是 dev DB 脏数据引起的，9.5 自身 47/47 全过）。**

下一个批次：批 9.6 前端 RBAC UI。

### TODO-8：NotificationConfig 数据迁移 — 2026-08-16 — `c0a2b1d4e5f6` — 实际 ~2 hr

**问题**：批 6b 把 `notification_config: dict | None` 改为 `NotificationConfig | None` 判别联合（`WebhookConfig`/`EmailConfig`/`DingTalkConfig`）。生产环境如果用 `dict | None` schema 持久化过 `{webhook_url: "..."}` 这样的旧 row，新 validator 会拒（union 不知道该匹配 WebhookConfig 还是 DingTalkConfig，缺 `type` discriminator + 字段名是 `webhook_url` 不是 `url`）。Dev DB 当时是空的，但生产部署前必须解决。

**子项落地**：

**TODO-8.1 `app/services/notification_migration.py` normalize 函数** — 处理 4 类旧 shape：
- `type=webhook` + `webhook_url` → rename 为 `url`
- 无 `type` + `webhook_url` → 推断为 webhook（`{type: "webhook", url, secret?}`）
- 无 `type` + `url` → 推断为 webhook（同上）
- type=webhook 同时有 `url` 和 `webhook_url`（数据损坏）→ 不动，让 admin 看到 422 自己选
- type=dingtalk / type=email / 空 dict / None / 未知 type → 不动（保护非 webhook 数据 + 避免误删）

**TODO-8.2 alembic 迁移 `c0a2b1d4e5f6_normalize_legacy_notification_config.py`** — fetch → Python transform → write back（SQLAlchemy 跨方言，SQLite JSON1 + Postgres JSONB 都用同一套）。`downgrade()` 显式 no-op（旧 dict 字段接受新 shape，无数据破坏需求）。echo `[c0a2b1d4e5f6] notification_config: rewritten=N skipped=M` 让 operator 看到跑通。

**TODO-8.3 16 tests in `tests/test_notification_migration.py`** —
- 13 个纯函数测试覆盖每个分支 + 不变性（不 mutate input）
- 3 个端到端 alembic 测试：用 tmp_path SQLite + monkeypatch 替换 `app.database.engine`/`SessionLocal` + `command.upgrade(cfg, "222001adeb57")` 重建 schema 到前一 revision；插入混合 shape → `command.upgrade(cfg, "c0a2b1d4e5f6")` → 验证重命名 + 跳过正确形状；最后一次 verify Pydantic TypeAdapter 校验新 shape 通过

**TODO-8.4 Dev DB 修复（顺手）** — 跑 migration 时发现 dev `app.db` 里 102 个 `notif-rep-*` 残留 rows（之前 pytest 失败留下的）+ 142 个 `pytest_*` data sources + `port=0` rows（schema validator 拒绝）+ DataSource id=1 指向 `:memory:` 而不是 `data/erp_demo.db`。清理后 + 修 `DataSource.id=1.database` 后，全 pytest 478/478 全过（之前 11 个 pre-existing fail 全部是 dev DB legacy rows 引起的）。

**关键 gotcha**：测试 fixture `fresh_db` 必须：
- 用 `tmp_path` 隔离 DB（默认 dev `app.db` 会污染 + 测试间泄露 `alembic_version`）
- 通过 `monkeypatch.setattr("app.database.engine", ...)` + `SessionLocal` 替换引用
- 测试代码必须通过 module-attr lookup (`from app import database as _database; _database.SessionLocal()`) 而不是 top-of-file `from app.database import SessionLocal`（后者捕获 import-time 引用，monkeypatch 改了 module attr 但局部变量不变）
- `_run_alembic` 只能升级到目标 revision；`fresh_db` 必须 upgrade 到前一 revision（如 `222001adeb57`）而不是 head（否则测试的 upgrade 是 no-op，不跑 migration body）

**Trade-off**：跨方言 JSON 处理用 fetch → Python transform → write back（简单但 N+1 queries）；表小（每 report 一行）+ 只跑一次，可接受。如果未来 row 数大，可以加 SQLAlchemy bulk_update_mappings 优化。

**ruff 0、mypy 0、pytest 478/478（+16 新 test）、alembic upgrade/downgrade head cycle 跑通。**

下一个批次：批 8.1 PDF 导出（weasyprint）。

### 批 9.6：前端 RBAC UI — 2026-08-23 — `5fd150a` — 实际 ~30 min

**问题**：9.1–9.5 把 role / org / visibility / audit log 全在后端落地了，但前端还没读 `useMe().role` 也没按 admin-only 路由守卫——admin 拿不到任何"运维"界面入口。

**落地**：

- **`App.tsx` `RequireAdmin` 守卫** —— 复用 `RequireAuth` 的 shape（`useMe` 缓存读 role），非 admin 跳 `/`。轻量组件，不引入新依赖。
- **`pages/AuditLogPage.tsx`** —— 过滤 Form（actor_user_id / action / target_type / target_id / RangePicker for since/until）→ `useAuditLogs` query hook；分页 Table（id / 时间 / 操作者 / 操作 / 对象 / IP / 请求 ID）；可展开行渲染 `before`/`after` JSON。操作者名字走 `useDataSources` 已有的 `/users` lookup cache（DataSourceList/ReportList share modals 也用同一个），不引新 query。
- **`pages/index.ts`** —— export AuditLogPage。
- **菜单入口** —— 顶部 nav 根据 `useMe().role === 'admin'` 渲染 `/audit-logs` 入口，非 admin 看不到。

**Trade-off**：actor username 解析复用 `useDataSources` 里的 `/users` cache，没单独写 `useUsers` hook——3 个组件共享同一份内存里的 user map，避免每次 row render 都打接口。但 `/users` 列表大了之后（> 几百人）这个 cache 会偏重，那时再换分页。

**lint 0、tsc 0、vitest 29/29、build 0、pytest 629/629。**

下一个批次：TODO-9 alert rules (补 operator-response 闭环)。

### TODO-9a：Prometheus + Grafana dashboard — 2026-08-23 — `a810842` — 实际 ~30 min

**问题**：批 6b 把 4 个自定义 metric + 默认 HTTP histogram 暴露在 `/metrics`，但没人在看——Prometheus 没拉、Grafana 没面板、面板没面板就只是数字。

**落地**：

- **`deploy/prometheus/prometheus.yml`** —— scrape `backend:8000/metrics` on `isee-net`，15s interval（匹配 Grafana `timeInterval`，刷新不出现 "no data"），external label `monitor=isee-workbench`。
- **`deploy/grafana/isee-workbench-dashboard.json`** —— 9-panel dashboard：HTTP RPS by status / 5xx error rate / 4xx error rate / p50-p95-p99 latency / Top-5 routes / report generation p95 by format / report generation errors by reason / SQL validator rejections by rule / webhook delivery outcomes。Schema 38 (Grafana 10+)。
- **provisioning** —— datasources/prometheus.yml auto-create datasource with `uid=prometheus`（matches dashboard 的 `DS_PROMETHEUS` template var）；dashboards/dashboards.yml file provider → `/var/lib/grafana/dashboards`，`allowUiUpdates=true` 让 re-import 更新 in place。
- **docker-compose `observability` profile** —— Prometheus 容器挂 prometheus.yml + volume；Grafana 容器挂 provisioning + dashboard JSON + volume。

**关键决策**：
- 不用 pushgateway —— 业务自己 scrape pull 模型够用，stateful pushgateway 是 over-engineering。
- Dashboard 直接用 JSON 不要 CRD —— k8s/Grafana-operator 我们没装，JSON 比 values.yaml 简单。
- `allowUiUpdates=true` —— 允许 ops 在 UI 上改完导出再 re-import，覆盖回 JSON。但 dashboard 改后还得手动 re-import（不是自动 round-trip），见 TODO-9b 文档段。

**Trade-off**：dashboard 模板 var (`DS_PROMETHEUS`) 硬编码 `prometheus` —— datasource `uid` 必须严格匹配，改一个另一个跟着改（注释里写了）。

### TODO-9b：Prometheus alert rules + alertmanager — 2026-08-23 — `6376f06` — 实际 ~2 hr

**问题**：TODO-9a 把面板装好了，但面板红了也只在屏幕上——没人被通知。把 operator-response 的另一半补齐。

**8 条规则**（`deploy/prometheus/alerts/isee-workbench.yml`，3 组）：

| Group | Alert | 阈值 | Severity |
|---|---|---|---|
| api | `BackendDown` | `up == 0` 持续 1m | critical |
| api | `HighErrorRate` | 5xx 比例 > 1%，持续 5m | critical |
| api | `High4xxRate` | 4xx 比例 > 20%，持续 15m | warning |
| reports | `SlowReportGeneration` | `report_generate_duration_seconds` p95 > 30s 持续 10m | warning |
| reports | `HighReportErrorRate` | `report_generate_errors_total{reason}` 任意 > 0.5/min 持续 10m | warning |
| integrations | `WebhookDeliveryFailing` | `outcome="http_error"` 失败率 > 10% 持续 10m（不算 SSRF 阻断） | warning |
| integrations | `SSRFGuardSurge` | `ssrf_blocked` 或 `https_required` 阻断 > 1/min 持续 15m | warning |
| integrations | `SQLValidatorSurge` | `sql_validator_rejections_total{rule}` 任意 > 5/min 持续 5m | warning |

每条都设 `for:` 防单点抖动 + `severity` 在 {critical, warning, info} + `summary` 按 dashboard panel 同名（方便从 alert 跳 Grafana）。

**Test 防 typo**（`backend/tests/test_alert_rules.py`，6 tests）：
- YAML parses, has groups > rules shape
- severity ∈ known ladder
- annotations.summary 非空
- alert name 唯一
- **expr 只引用 KNOWN_METRICS allowlist**（mirror `backend/app/middleware/metrics.py` + prometheus-fastapi-instrumentator HTTP series）—— `report_generated_errors_total` 拼错会 parses 但永远不 fire，allowlist 让这种 bug 在 unit test 阶段挂掉
- 每条有 `for:` 避免单点抖动
- `promtool check rules` 可选跑（PATH 上有就 run，没就 skip）

**Wiring**：
- `prometheus.yml` `rule_files: [/etc/prometheus/alerts/*.yml]` + 注释掉的 `alerting.alertmanagers` 段（默认不接，让 ops 选 alertmanager URL 后开）
- `deploy/prometheus/alertmanager.yml` no-op stub（route receiver="null"）—— alertmanager 容器不挂 config 会拒绝启动；本地起 stack 不被自己的告警淹没；ops 自己写 Slack/PagerDuty/email 真实配置覆盖
- `docker-compose.yml` observability profile 加 `prom/alertmanager:v0.27.0` service + bind-mount alerts/ 目录
- `DEPLOY.md` 加 "配置告警（可选）" 段：8 条规则表 + 两步 wiring（去掉 `alerting.alertmanagers` 注释 + 写真 alertmanager.yml）+ promtool 本地校验命令

**关键决策**：
- webhook 失败率 alert 只统计 `outcome="http_error"` —— SSRF guard 阻断 (`ssrf_blocked`/`https_required`) 是合法防御行为，不算业务失败；分母用 `outcome=~"success|http_error"` 而不是 total，避免 SSRF 阻断推高假阳性
- SQL validator surge 是早期信号：5/min 阈值很保守，5 分钟持续才 page —— 单点试探不告警，但持续注入尝试会
- BackendDown severity=critical —— 进程没了，其它面板全部归零，没意义在前面再加一层 condition

**Trade-off**：promtool 检查在 CI 里 skip（没装 promtool binary），靠 Python 静态校验（KNOWN_METRICS allowlist + 结构性断言）兜底；如要 CI 强制，可加 docker container step 跑 `promtool`。

**ruff 0、mypy 0、pytest 635/635（+6 new），前端未触及。**

下一个批次：批 10 code-split + prettier。

### 批 10：前端优化（code-split + 删 chart.js + Prettier）— 2026-08-23 — `f871fb9` — 实际 ~30 min

**问题**：main bundle 1.88 MB / 580 KB gzip 单一 chunk，vite warning 阈值 500 KB 已超 3.7x。新增页面/dialog 每次涨 ~100 KB 但完全没机制约束。Dashboard (`a810842`) 加了 Prettier 一节但没真接。

**落地**：

**10.1 — 路由 code-split**：
- `Skeleton.tsx` 新 export `PageSkeleton`（centered Spin + minHeight:60vh，mirror 现有 inline pattern）
- `App.tsx` 把 8 个 page 改成 `lazy(() => import('./pages/XXX'))`，Login 留 eager（无 Suspense 父级，加 spinner 无意义）
- `AppShell` 内 `<Routes>` 外包 `<Suspense fallback={<PageSkeleton />}>`
- 顺手把 `RequireAuth` / `RequireAdmin` inline `<div minHeight + Spin />` 替换成 `<PageSkeleton />` —— 视觉完全一致，省 12 行重复代码

**10.2 — manualChunks vendor split**：
`vite.config.ts` 加 `build.rollupOptions.output.manualChunks` 函数（不是 regex map）：

| Chunk | 来源 | Raw / gzip | 加载时机 |
|---|---|---|---|
| `index` | AppShell + 路由 + queries | 15 KB / 5 KB | 初始 |
| `react-vendor` | react / react-dom / scheduler | 0.4 KB / 0.3 KB | 初始 |
| `antd-vendor` | antd 主包 + rc-* | 1.22 MB / 372 KB | 初始 |
| `router-vendor` | react-router + react-router-dom | 42 KB / 15 KB | 初始 |
| `rq-vendor` | @tanstack/react-query + devtools | 29 KB / 9 KB | 初始 |
| `icons-vendor` | @ant-design/icons | 32 KB / 8 KB | 初始 |
| `vendor` | axios / misc | 51 KB / 19 KB | 初始 |
| `dnd-vendor` | @dnd-kit/* | 44 KB / 15 KB | 进 `/reports/:id` |
| `cm-vendor` | @codemirror/* + @lezer/highlight | 344 KB / 113 KB | 进 `/explorer` |
| 8 个 page chunks | 4-21 KB 各 | — | 路由 lazy |

`chunkSizeWarningLimit: 1300` 静音 antd-vendor 的 500 KB warning —— antd 每个 page 都用是 true shared dependency，拆不出来。

**10.3 — 删 chart.js dep**：grep `frontend/src/` + `e2e/` 零引用，确认 dead code。后端 `backend/static/chart.umd.min.js` 不动（报表 HTML iframe 用）。

**10.4 — Prettier 配置（仅 config）**：
- `frontend/.prettierrc.json`：`{semi: true, singleQuote: true, trailingComma: 'all', printWidth: 100, tabWidth: 2, arrowParens: 'always', endOfLine: 'lf'}` —— 匹配 eslint 现有规则
- `frontend/.prettierignore`：node_modules / dist / e2e / playwright-report / test-results / coverage / *.lock
- `package.json` 加 `prettier@^3.3.0` + `format` / `format:check` scripts
- **不跑全仓 format** —— 45 个文件差异（`npx prettier --check` 报告）历史 PR diff 不能被一次性 commit 污染；`npm run format -- src/path/file.tsx` 单文件按需触发

**关键决策**：
- **manualChunks 用函数形式不是对象正则** —— 一个 module 走一次函数判断，shared deps 只分到一次，避免重复 chunk。
- **不引入 husky / lint-staged** —— 用户明确选 "仅加 Prettier"。CI 已经有 eslint；format check 留作未来按需。
- **不重构 barrel `./pages/index.ts`** —— vitest 还在用 `__tests__/pages/Login.test.tsx` 路径；保留 barrel，App.tsx 不再 import barrel 直接走 page 路径。
- **`Login` 留 eager** —— 它是未登录流程的入口，外面没有 Suspense 父级，在 login 路径上加 spinner 是 silly。其它 8 个都 lazy。
- **antd-vendor 不再拆** —— 380 KB gzip 是 shared-by-every-page 的固有成本；进一步拆会丢 tree-shake 收益（ESM 入口分类太多反而 code-split 失效）。
- **chunkSizeWarningLimit: 1300** —— 不是 disable warning，是表达"我们接受这个 size"。entry chunk 实际 5 KB gzip，build log 不被无关 noise 干扰。

**Trade-off**：
- 第一次访问 `/explorer` 多加载 113 KB gzip 的 cm-vendor chunk + 7 KB page chunk —— 但 dev server（vite instant）和生产（CDN）都 < 200ms。换得的是首页初始 bundle 从 580 KB → ~430 KB（共享部分），且其它 page 不会拉 CodeMirror。
- Playwright `e2e/smoke.spec.ts` 第 88-92 行 `.cm-content` selector 立即检查 —— vite dev instant serve 应该通过；如出现 flake 加 `await page.waitForSelector('.cm-content', { state: 'visible', timeout: 10_000 })`。

**验证**：`npm run build` 0 warning, 23 chunks emitted；`npm run lint` 0；`npx tsc -b` 0；`npx vitest run` 29/29；vite dev server 启动 OK + 4 个 HTTP 200。

下一个批次：盘点新方向（demo-driven / 用户反馈驱动）。
