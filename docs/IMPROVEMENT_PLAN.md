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
| 批 6a request-id + Sentry | ✅ 完成 — commit `<即将填入>`（X-Request-ID 端到端回显 + 25 新测试） |
| 下一批：批 5 后端重构 | ⏳ **下次会话从这里开始**（按已重排顺序：5 → 4a → 2a → 2b → 3a → 3b → 4b → 6b → 1.5 → 7 → 8 → 9 → 10） |

**下一会话怎么接：**

1. 打开本文件 → 看「当前进度」表
2. 跑 `make test-fast && make lint && make typecheck && make build` 确认基线没漂
3. 读 plan 文件 `~/.claude/plans/cozy-brewing-falcon.md` 中「批 5」章节
4. 建 TaskCreate 覆盖批 5 子项（Alembic 正式启用 + 拆 report_generator + lifespan 改写 + get_current_user 返回 User + 分页），开始干

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
| 批 6a | 未开始 | — | |
| 批 5 | 未开始 | — | 核心：拆 report_generator.py |
| 批 4a | 未开始 | — | |
| 批 2a | 未开始 | — | TanStack Query 基础 |
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
-->
