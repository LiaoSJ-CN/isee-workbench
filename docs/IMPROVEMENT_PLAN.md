# iSee Workbench — 改进计划

> 完整的实施细节、每个 batch 的步骤、复用函数清单、验证策略见
> `/Users/liaosj/.claude/plans/cozy-brewing-falcon.md`（plan 文件）。
> 本文档是面向团队的高层索引。

## 🔖 会话断点 / Resume Point

**最后会话（2026-08-15）：**

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
| 下一批：批 2b 乐观更新 + 虚拟滚动 + Skeleton | ⏳ **下次会话从这里开始**（按已重排顺序：2b → 3a → 3b → 4b → 6b → 1.5 → 7 → 8 → 9 → 10） |

**下一会话怎么接：**

1. 打开本文件 → 看「当前进度」表
2. 跑 `make test-fast && make lint && make typecheck && make build` 确认基线没漂
3. 读 plan 文件 `~/.claude/plans/cozy-brewing-falcon.md` 中「批 2b」章节
4. 建 TaskCreate 覆盖批 2b 子项（ReportEditor 拖拽排序乐观更新 + ReportEditor item CRUD 乐观更新 + DataSourceList/ReportList toggle is_active 乐观更新 + ReportPreview/DataExplorer 虚拟滚动 + 全局 Skeleton 组件），开始干

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
| 批 2b | 未开始 | — | |
| 批 3a | 未开始 | — | Job 队列 |
| 批 3b | 未开始 | — | |
| 批 4b | 未开始 | — | |
| 批 6b | 未开始 | — | |
| 批 1.5 | 未开始 | — | ReportEditor 文件拆分 |
| 批 7 | 未开始 | — | vitest + cov + e2e |
| 批 8 | 未开始 | — | 4 子项可并行 |
| 批 9 | 未开始 | — | 先做数据隔离模型设计 |
| 批 10 | 未开始 | — | |

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
