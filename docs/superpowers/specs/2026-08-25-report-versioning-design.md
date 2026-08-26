# 报表版本/历史 — Design Spec

**日期**: 2026-08-25
**作者**: Claude (brainstorming → design)
**状态**: 已批准 → 待 writing-plans 出实施计划
**关联**: `docs/ROADMAP.md` §1 报表版本/历史

## 1. 目标与背景

iSee Data Workbench 的 `Report` 模型当前是 in-place 覆盖的：每次 `PUT /reports/{id}` 直接改主表，没有版本概念。真实痛点：

- **误改 SQL 上线**：业务人员改了 custom_sql，预览看着没问题，但定时跑起来才发现数据错了。已无回滚路径。
- **多人协作冲突**：后写覆盖前写，没有"刚才那版还能看到"。
- **审计追溯弱**：现有 `AuditLog` 只记"改了字段 X→Y"，但不记"删了哪些 item""SQL 改成了什么样子"。

**本特性目标**：手动触发 + 完整快照 + 原子 restore + 字段级 diff，让用户能在任意时刻回退到指定的历史版本。

## 2. 关键设计决策（已与用户对齐）

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | Snapshot 触发时机 | **纯手动**：用户点「保存为版本」按钮才生成 | 用户掌控粒度，不爆仓。代价：误改 SQL 上线场景需要用户记得点 |
| 2 | Snapshot 内容范围 | **完整快照**：Report + items + parameters（schedule 字段在 Report 行内，自然包含）| Restore 是原子恢复，状态完整 |
| 3 | 存储结构 | **完全规范化**：3 张版本表逐列镜像主表 | 用户偏好 SQL 直接 diff、不解析 JSON |
| 4 | ACL | **list/get/create/diff 跟随 Report 可见性**；**restore/delete 仅 owner + admin** | 与现有 `list_accessible_reports` 对齐，restore 是破坏性操作需收紧 |
| 5 | Diff 粒度 | **字段级 diff（默认）+ 完整快照切换** | 用户友好 + 可深入看 SQL 全文 |

## 3. 数据模型

### 3.1 三张表

```sql
-- 镜像 Report 主表
CREATE TABLE report_versions (
    id              INTEGER PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,           -- per-report 自增：v1, v2, v3...
    label           VARCHAR(255),                -- 用户给的名字，nullable
    is_pinned       BOOLEAN NOT NULL DEFAULT 0,  -- true = 不允许删除
    created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at      DATETIME NOT NULL,

    -- 完全镜像 Report 所有标量列（除 id / created_at / updated_at）：
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    data_source_id      INTEGER NOT NULL REFERENCES data_sources(id),
    layout_config       JSON,
    is_scheduled        BOOLEAN DEFAULT 0,
    cron_expression     VARCHAR(100),
    schedule_description VARCHAR(255),
    notification_config JSON,
    output_formats      JSON,
    is_active           BOOLEAN DEFAULT 1,
    is_demo             BOOLEAN NOT NULL DEFAULT 0,
    visibility          VARCHAR(16) NOT NULL DEFAULT 'public',
    owner_user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    org_id              INTEGER,

    UNIQUE (report_id, version_number)
);

-- 镜像 ReportItem
CREATE TABLE report_version_items (
    id              INTEGER PRIMARY KEY,
    version_id      INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,

    name            VARCHAR(255) NOT NULL,
    item_type       VARCHAR(50) NOT NULL,
    order_index     INTEGER DEFAULT 0,
    table_name      VARCHAR(255),
    fields          JSON,
    where_conditions JSON,
    group_by        JSON,
    order_by        JSON,
    limit           INTEGER,
    display_config  JSON,
    custom_sql      TEXT,
    # 没有 live id 列；diff 时按 name 匹配。
    # 仅作审计回溯用，nullable（旧版本或 item 被删后无对应 id）。
    original_item_id INTEGER
);

-- 镜像 ReportParameter
CREATE TABLE report_version_parameters (
    id              INTEGER PRIMARY KEY,
    version_id      INTEGER NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,

    name            VARCHAR(64) NOT NULL,
    label           VARCHAR(255) NOT NULL,
    type            VARCHAR(16) NOT NULL,
    required        BOOLEAN NOT NULL DEFAULT 1,
    default         JSON,
    options         JSON,
    order_index     INTEGER NOT NULL DEFAULT 0,
    # 仅作审计回溯用，nullable。
    original_parameter_id INTEGER
);

CREATE INDEX ix_report_versions_report_id ON report_versions(report_id);
CREATE INDEX ix_report_version_items_version_id ON report_version_items(version_id);
CREATE INDEX ix_report_version_parameters_version_id ON report_version_parameters(version_id);
```

### 3.2 关键不变量

- `version_number` 对每个 `report_id` 单调递增，从 1 开始。创建新版本时取 `MAX(version_number) + 1`，无并发问题（手动触发）。
- `is_pinned = 1` 的版本不被 DELETE 端点接受（409 Conflict）。
- `Report` 删除时 `ON DELETE CASCADE` 清理全部版本；audit log 仍记 Report 删除事件。
- 旧版本"冻结"——主表加新列时不影响已存在的 snapshot 行；后续 snapshot 自动含新列。

### 3.3 迁移策略

新增 Alembic 迁移 `alembic/versions/<rev>_add_report_versions.py`，内容：建 3 张表 + 索引。无数据迁移（首次部署无版本记录，老 Report 默认无版本，用户手动创建即可）。

## 4. API 设计

| 方法 | 路径 | 权限 | 行为 |
|------|------|------|------|
| `POST` | `/reports/{id}/versions` | **owner 或 admin** | 创建新版本（body: `{label?: string}`）。返回新版本摘要 |
| `GET` | `/reports/{id}/versions` | 可见 Report 的人 | 列表（按 `version_number DESC`）。含 summary，不含 items/parameters 全文 |
| `GET` | `/reports/{id}/versions/{vid}` | 可见 Report 的人 | 单版本完整快照（含 items + parameters 全文）|
| `GET` | `/reports/{id}/versions/{vid}/diff?against=<other_vid\|current>` | 可见 Report 的人 | 字段级 diff。`against` 缺省 = `current` |
| `POST` | `/reports/{id}/versions/{vid}/restore` | **owner 或 admin** | 覆盖当前 Report + items + parameters 为版本快照。返回新 Report |
| `DELETE` | `/reports/{id}/versions/{vid}` | **owner 或 admin** | 删版本。`is_pinned=1` → 409 |

### 4.1 Pydantic Schemas

```python
class ReportVersionSummary(BaseModel):
    id: int
    report_id: int
    version_number: int
    label: str | None
    is_pinned: bool
    created_by: int | None
    created_at: datetime
    # 不含 items/parameters 全文 — list 视图够用

class ReportVersionResponse(ReportVersionSummary):
    # Report 字段（name/description/data_source_id/...）
    items: list[ReportVersionItemResponse]
    parameters: list[ReportVersionParameterResponse]

class ReportVersionCreate(BaseModel):
    label: str | None = Field(default=None, max_length=255)

class ReportVersionRestoreResponse(BaseModel):
    # restore 后返回的当前 Report，跟 PUT /reports/{id} 一致
    report: ReportResponse

class ReportVersionDiff(BaseModel):
    base_version: int              # version_number
    target_version: int | None     # None = current
    report_changes: list[FieldChange]
    items_added: list[ReportVersionItemResponse]
    items_removed: list[ReportVersionItemResponse]
    items_modified: list[ItemDiff]
    parameters_added: list[ReportVersionParameterResponse]
    parameters_removed: list[ReportVersionParameterResponse]
    parameters_modified: list[ParameterDiff]

class FieldChange(BaseModel):
    field: str
    old_value: Any | None
    new_value: Any | None

class ItemDiff(BaseModel):
    name: str               # 配对 key
    changes: list[FieldChange]
```

### 4.2 错误码

- 401: 未登录
- 403: 无权限（list/get 走 Report 可见性；restore/delete 走 owner/admin）
- 404: Report / version 不存在
- 409: `DELETE /versions/{vid}` 时 `is_pinned=1`（restore 不做乐观锁——见 §7）

## 5. ACL 集成

### 5.1 可见性检查（list / get / create / diff）

复用现有 `services/report.py:list_accessible_reports(user)` 的可见性逻辑。新增 helper：

```python
def ensure_report_visible(user: User, report_id: int) -> Report:
    """按 Report 可见性返回 Report；不可见则 403/404。"""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404)
    if user.role == ROLE_ADMIN:
        return report
    if report.owner_user_id == user.id:
        return report
    if report.visibility == VISIBILITY_PUBLIC:
        return report
    if db.query(ReportAccess).filter(
        ReportAccess.report_id == report_id,
        ReportAccess.user_id == user.id,
    ).first():
        return report
    raise HTTPException(403)
```

### 5.2 Restore / Delete 权限

新增 helper：

```python
def is_owner_or_admin(user: User, report: Report) -> bool:
    if user.role == ROLE_ADMIN:
        return True
    return report.owner_user_id == user.id
```

Restore/Delete 端点显式调用 → 否则 403。

## 6. Diff 算法

新文件：`backend/app/services/report_version_diff.py`

### 6.1 Report 字段 diff

```python
def diff_report_fields(
    base: ReportVersion, target: ReportVersion | Report
) -> list[FieldChange]:
    """对比 Report 字段。target 可以是另一个 version 或当前 live Report。"""
```

比对字段集（白名单，避免 `created_at` 等元数据产生噪音）：
`name, description, data_source_id, layout_config, is_scheduled, cron_expression, schedule_description, notification_config, output_formats, is_active, visibility, owner_user_id, org_id`

`is_demo` 不参与 diff（系统标记，用户不可改）。

### 6.2 Items diff

```python
def diff_items(
    base_items: list[ReportVersionItem],
    target_items: list[ReportVersionItem | ReportItem]
) -> tuple[added, removed, modified]:
```

- 用 `name` 作为配对 key（item 没稳定 ID，跨快照只能用 name）
- `added`: 在 target 不在 base
- `removed`: 在 base 不在 target
- `modified`: 都有且字段有差异 → 字段级 diff（比对 `item_type, order_index, table_name, fields, where_conditions, group_by, order_by, limit, display_config, custom_sql`）

### 6.3 Parameters diff

同 items，按 `name` 配对。

### 6.4 完整快照序列化

```python
def serialize_full(version: ReportVersion) -> dict:
    """返回 JSON-ready dict，供前端「查看完整快照」切换使用。"""
    return {
        "version": version.version_number,
        "label": version.label,
        "created_at": version.created_at.isoformat(),
        "report": {...},
        "items": [...],
        "parameters": [...],
    }
```

前端可序列化为 YAML / JSON（`json.dumps(..., indent=2)`）。

## 7. Restore 流程

```
POST /reports/{id}/versions/{vid}/restore
  1. 检查权限（owner or admin）
  2. 取 base 版本的 ReportVersion + items + parameters
  3. 取当前 live Report 的 updated_at
  4. （optional）乐观锁：body 带 expected_updated_at，不一致 → 409
  5. 在 transaction 内：
     a. 删当前 Report.items + parameters（cascade 会清，但显式删保证 order）
     b. 覆盖 Report 主表字段
     c. 按 base.items 重建 ReportItem 行（新 live id）
     d. 按 base.parameters 重建 ReportParameter 行
     e. flush
  6. 写 audit_log: action="restore_version", target_report_id=id, version_id=vid
  7. 返回新 ReportResponse
```

**乐观锁**：v1 不实现，注释标"future work"——restore 是 owner/admin 操作，低频并发。

## 8. 创建 Snapshot 流程

```
POST /reports/{id}/versions  body: {label?}
  1. 检查权限（ensure_report_visible）
  2. 取 live Report + items + parameters
  3. transaction:
     a. SELECT MAX(version_number) FROM report_versions WHERE report_id=:id FOR UPDATE
     b. INSERT report_versions 行（next_version_number, + 全部 Report 字段）
     c. INSERT report_version_items 行（N 条）
     d. INSERT report_version_parameters 行（M 条）
  4. 写 audit_log: action="create_version", target_report_id=id, version_id=new.id
  5. 返回 ReportVersionResponse
```

## 9. 前端 UI

### 9.1 ReportEditor 工具栏

新增按钮：「保存为版本」(icon: `<SaveOutlined />`)，位置：紧邻现有「保存」按钮。

点击 → Modal：
- 输入框 label（可选，最大 255 字符）
- 提示文字：「创建后可在「报表历史」中查看和恢复」
- 提交 → POST，成功后 message.success 并刷新 metadata

### 9.2 报表历史页 `/reports/{id}/history`

新页面（新路由）。布局：

```
+-------------------------------------------------------+
| [← 返回报表]    报表历史 — {report.name}                |
+-------------------------------------------------------+
| [表格]                                                |
| 版本号  | 标签    | 创建人  | 创建时间     | 操作       |
| v3 (当前)| —      | alice   | 2026-08-25 | [查看] [删除]|
| v2      | Q1 报表| bob     | 2026-08-20 | [查看][恢复][删除]|
| v1 📌   | 初版   | alice   | 2026-08-01 | [查看][删除]|
+-------------------------------------------------------+
```

- 「查看」→ 跳转 `/reports/{id}/history/{vid}`
- 「恢复」→ 确认 Modal → POST restore → 成功跳转回 ReportPreview
- 「删除」→ 确认 Modal → DELETE → 刷新
- 「📌」标记 is_pinned；pinned 行的「删除」按钮置灰 + Tooltip

### 9.3 Diff 视图 `/reports/{id}/history/{vid}?against=<other_vid|current>`

布局：

```
+-------------------------------------------------------+
| [← 返回历史]    版本 v3 vs current                     |
+-------------------------------------------------------+
| 对比目标: [下拉: current / v2 / v1]                    |
+-------------------------------------------------------+
| 报表字段差异                                          |
|   name: 'Q1' → 'Q1 报表 v2'                            |
|   cron_expression: '0 9 * * *' → '0 10 * * *'         |
+-------------------------------------------------------+
| 报表项差异 (新增 1 / 删除 0 / 修改 2)                 |
|   + [新] 转化率漏斗                                    |
|   ~ [改] 销售概览                                      |
|       table_name: 'orders' → 'order_items'             |
|       custom_sql: <line-diff 视图>                    |
+-------------------------------------------------------+
| 参数差异 (无)                                          |
+-------------------------------------------------------+
| [切换] 查看完整快照                                    |
+-------------------------------------------------------+
```

「查看完整快照」展开 Drawer / 折叠区，显示 JSON 全文。

### 9.4 新文件清单

```
frontend/src/pages/ReportHistory/
├── index.tsx                -- 列表页
├── DiffView.tsx             -- diff 详情页
├── VersionTable.tsx         -- 表格组件
└── RestoreConfirmModal.tsx

frontend/src/components/
└── SaveVersionModal.tsx     -- ReportEditor 工具栏用的 Modal

frontend/src/api/index.ts    -- 加 reportVersionsApi namespace
frontend/src/types/index.ts  -- 加 ReportVersion / ReportVersionDiff / 等 TS 类型
frontend/src/queries/        -- 加 useReportVersions / useReportVersion / useCreateVersion / useRestoreVersion / useDeleteVersion
```

## 10. 测试计划

### 10.1 后端 pytest（≥18 个）

**`test_report_version_crud.py`**（基础 CRUD）
- `test_create_version_happy`: 200 + version_number=1 + label 写入
- `test_create_version_increments`: 多次创建，version_number 单调
- `test_create_version_no_label`: label=null 允许
- `test_create_version_label_too_long`: 256 字符 → 422
- `test_list_versions_ordered`: list 按 version_number DESC
- `test_get_version_full_snapshot`: items + parameters 都包含
- `test_delete_version`: 204
- `test_delete_pinned_version`: 409
- `test_create_version_invisible_report`: 403

**`test_report_version_acl.py`**（ACL）
- `test_list_versions_admin_sees_all`: admin 跨 owner 也能看
- `test_list_versions_owner_sees_own`: owner 能看自己的
- `test_list_versions_grantee_sees`: grantee 能看
- `test_list_versions_public_everyone`: visibility=public 任何登录用户能看
- `test_restore_non_owner_403`: 普通 grantee 不能 restore
- `test_restore_admin_allowed`: admin 即使非 owner 也能 restore
- `test_delete_non_owner_403`: 同上

**`test_report_version_restore.py`**（Restore 逻辑）
- `test_restore_overwrites_items`: restore v1 → 当前 Report.items 等于 v1.items
- `test_restore_overwrites_parameters`: 同上
- `test_restore_audit_log_written`: 检查 audit_log 行

**`test_report_version_diff.py`**（Diff 算法）
- `test_diff_no_changes`: 空 diff
- `test_diff_report_field_change`: name 改了
- `test_diff_items_added_removed_modified`: 三种情况一起
- `test_diff_items_matched_by_name`: 改 table_name 但 name 同 → 算 modified
- `test_diff_items_renamed_treated_as_remove_plus_add`: name 变了 → add+remove
- `test_diff_parameters_modified`: 参数改了

**`test_report_version_migration.py`**（Alembic）
- `test_alembic_upgrade_downgrade_roundtrip`: 3 张表可升可降

### 10.2 前端 vitest（≥6 个）

- `SaveVersionModal.test.tsx`: 输入 label / 取消 / 提交
- `VersionTable.test.tsx`: 行渲染 / 操作按钮 enabled/disabled
- `DiffView.test.tsx`: 字段 diff 渲染 / 切换完整快照
- `useReportVersions.test.ts`: hook happy / error

### 10.3 集成验证

- `make test-fast`（pytest + vitest + ruff + mypy + eslint + tsc）
- `python scripts/diff_docs_vs_code.py --strict --quiet`：本文档加了 `report_versions` 路径，需在 diff script 白名单或文档补完整
- 手动 UI 流程：登录 → 编辑报表 → 点「保存为版本」→ 进历史页 → 看 diff → restore → 确认报表回滚

## 11. 迁移 / 部署

- Alembic 自动 migration（web 进程启动时跑 `alembic upgrade head`）
- 旧 Report 行无对应版本——前端历史页空表格 + tooltip 提示「还没有历史版本，点编辑器里「保存为版本」开始记录」
- 无需后端配置变更
- 无需前端 env 变更

## 12. 风险与未来工作

| 风险 | 应对 |
|------|------|
| Restore 期间其他人编辑 → 覆盖丢失 | v1 不实现乐观锁；owner/admin 操作低频；后续若需要可加 `expected_updated_at` |
| 完整快照体积大（items × 数十 + 长 custom_sql）| 接受——手动触发；监控行字节，> 500KB 时 TODO 提醒 |
| 主表加新列时旧版本缺字段 | 故意冻结；前端 diff 用「字段不存在」表示缺失 |
| 多 Report 同时 restore 同一 data_source | 无共享锁——并发 update 走数据库事务隔离 |
| 用户手动 create 一堆版本撑爆 | 已选纯手动；用户自负责；未来可加 `is_pinned` 配额 |

**Future work（不在 v1）**：
- 乐观锁 restore
- 版本对比 `against=current` 自动取 latest snapshot
- "恢复前自动 snapshot 当前" 选项
- 版本树 / 分支（v3 → v3.1 改 SQL 不影响 v3 主线）

## 13. 实施切片（将由 writing-plans skill 拆任务）

```
1. Backend 模型 + 迁移（Alembic 3 表 + 索引）
2. Backend Pydantic schemas
3. Backend ACL helpers (ensure_report_visible, is_owner_or_admin)
4. Backend service: snapshot create + restore
5. Backend service: diff 算法
6. Backend router: 6 端点
7. Backend tests (≥18 pytest)
8. Frontend types + api + queries
9. Frontend SaveVersionModal + ReportEditor 集成
10. Frontend ReportHistory 列表页
11. Frontend DiffView 页
12. Frontend tests (≥6 vitest)
13. Docs 同步 (README / ROADMAP / ARCHITECTURE)
14. Integration verify (make test-fast + UI 手动)
```

---

**下一步**：调用 `superpowers:writing-plans` skill 把上面的 14 步拆成可验证的实施计划。