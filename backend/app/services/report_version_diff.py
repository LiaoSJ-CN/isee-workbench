"""Diff algorithm for report versions.

Pairs items / parameters by ``name`` (snapshot rows have no stable live
ID, only the original live id captured at snapshot time which may be
stale). Field-level diff uses an explicit whitelist to avoid noise
from metadata fields like ``created_at``.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.models.report import Report
from app.models.report_version import (
    ReportVersion,
    ReportVersionItem,
    ReportVersionParameter,
)

# Whitelist of Report scalar columns to include in diff.
REPORT_DIFF_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "data_source_id",
    "layout_config",
    "is_scheduled",
    "cron_expression",
    "schedule_description",
    "notification_config",
    "output_formats",
    "is_active",
    "visibility",
    "owner_user_id",
    "org_id",
)

ITEM_DIFF_FIELDS: tuple[str, ...] = (
    "item_type",
    "order_index",
    "table_name",
    "fields",
    "where_conditions",
    "group_by",
    "order_by",
    "limit",
    "display_config",
    "custom_sql",
)

PARAM_DIFF_FIELDS: tuple[str, ...] = (
    "label",
    "type",
    "required",
    "default",
    "options",
    "order_index",
)


def _values_equal(a: Any, b: Any) -> bool:
    """JSON-aware equality (lists / dicts compared structurally)."""
    if type(a) is not type(b):
        return False
    return bool(a == b)


def _field_changes(base_obj: Any, target_obj: Any, fields: Iterable[str]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for f in fields:
        b = getattr(base_obj, f)
        t = getattr(target_obj, f)
        if not _values_equal(b, t):
            changes.append({"field": f, "old_value": b, "new_value": t})
    return changes


def diff_report_fields(
    base: ReportVersion, target: ReportVersion | Report
) -> list[dict[str, Any]]:
    return _field_changes(base, target, REPORT_DIFF_FIELDS)


def _index_by_name(rows: Iterable[Any]) -> dict[str, Any]:
    return {getattr(r, "name"): r for r in rows}


def diff_items(
    base_items: list[ReportVersionItem],
    target_items: list[Any],
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    base_map = _index_by_name(base_items)
    target_map = _index_by_name(target_items)

    added_names = sorted(set(target_map) - set(base_map))
    removed_names = sorted(set(base_map) - set(target_map))
    common_names = sorted(set(base_map) & set(target_map))

    added = [target_map[n] for n in added_names]
    removed = [base_map[n] for n in removed_names]
    modified: list[dict[str, Any]] = []
    for n in common_names:
        changes = _field_changes(base_map[n], target_map[n], ITEM_DIFF_FIELDS)
        if changes:
            modified.append({"name": n, "changes": changes})
    return added, removed, modified


def diff_parameters(
    base_params: list[ReportVersionParameter],
    target_params: list[Any],
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    base_map = _index_by_name(base_params)
    target_map = _index_by_name(target_params)

    added_names = sorted(set(target_map) - set(base_map))
    removed_names = sorted(set(base_map) - set(target_map))
    common_names = sorted(set(base_map) & set(target_map))

    added = [target_map[n] for n in added_names]
    removed = [base_map[n] for n in removed_names]
    modified: list[dict[str, Any]] = []
    for n in common_names:
        changes = _field_changes(base_map[n], target_map[n], PARAM_DIFF_FIELDS)
        if changes:
            modified.append({"name": n, "changes": changes})
    return added, removed, modified


def compute_diff(
    base_version: ReportVersion,
    target_version: ReportVersion | None = None,
    live_report: Report | None = None,
) -> dict[str, Any]:
    """Build the full diff structure. ``target_version`` takes precedence;
    fall back to ``live_report`` if provided. Exactly one must be set."""
    target: ReportVersion | Report
    if target_version is not None:
        target = target_version
    elif live_report is not None:
        target = live_report
    else:
        raise ValueError("Either target_version or live_report must be provided")

    items_added, items_removed, items_modified = diff_items(base_version.items, target.items)
    params_added, params_removed, params_modified = diff_parameters(
        base_version.parameters, target.parameters
    )

    return {
        "base_version": base_version.version_number,
        "target_version": target.version_number if isinstance(target, ReportVersion) else None,
        "report_changes": diff_report_fields(base_version, target),
        "items_added": items_added,
        "items_removed": items_removed,
        "items_modified": items_modified,
        "parameters_added": params_added,
        "parameters_removed": params_removed,
        "parameters_modified": params_modified,
    }


def serialize_full(version: ReportVersion) -> dict[str, Any]:
    """JSON-ready snapshot for the «查看完整快照» toggle."""
    return {
        "version": version.version_number,
        "label": version.label,
        "is_pinned": version.is_pinned,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "report": {
            "name": version.name,
            "description": version.description,
            "data_source_id": version.data_source_id,
            "layout_config": version.layout_config,
            "is_scheduled": version.is_scheduled,
            "cron_expression": version.cron_expression,
            "schedule_description": version.schedule_description,
            "notification_config": version.notification_config,
            "output_formats": version.output_formats,
            "is_active": version.is_active,
            "visibility": version.visibility,
            "owner_user_id": version.owner_user_id,
            "org_id": version.org_id,
        },
        "items": [
            {
                "name": i.name,
                "item_type": i.item_type,
                "order_index": i.order_index,
                "table_name": i.table_name,
                "fields": i.fields,
                "where_conditions": i.where_conditions,
                "group_by": i.group_by,
                "order_by": i.order_by,
                "limit": i.limit,
                "display_config": i.display_config,
                "custom_sql": i.custom_sql,
            }
            for i in version.items
        ],
        "parameters": [
            {
                "name": p.name,
                "label": p.label,
                "type": p.type,
                "required": p.required,
                "default": p.default,
                "options": p.options,
                "order_index": p.order_index,
            }
            for p in version.parameters
        ],
    }
