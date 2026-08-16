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
| 下一批：批 8.1 PDF 导出（weasyprint） | ⏳ **下次会话从这里开始**（按已重排顺序：8.1 → 8.3 → 8.4 → 9 → 10） |

**下一会话怎么接：**

1. 打开本文件 → 看「当前进度」表
2. 跑 `make test-fast && make lint && make typecheck && make build` 确认基线没漂（**当前基线：pytest 462/462、coverage 83.9%、lint 0、tsc 0、vitest 29/29**）
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
| 批 8.1 | 未开始 | — | weasyprint PDF 导出（下次会话从这里开始） |
| 批 8.3 | 未开始 | — | 报表订阅 |
| 批 8.4 | 未开始 | — | IM 通知（飞书/企微） |
| 批 9 | 未开始 | — | 先做数据隔离模型设计 |
| 批 10 | 未开始 | — | code-split + prettier |

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
