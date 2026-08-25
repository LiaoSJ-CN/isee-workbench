"""Snapshot / restore / list service for report versions.

Snapshot creation copies the live Report state into ``report_versions``
plus its items + parameters in a single transaction. Restore
(``restore_version``) and delete are appended in T6.
"""

from __future__ import annotations

from typing import Sequence

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.models.report import Report, ReportItem
from app.models.report_parameter import ReportParameter
from app.models.report_version import (
    ReportVersion,
    ReportVersionItem,
    ReportVersionParameter,
)
from app.models.user import User


def create_snapshot(
    db: Session, *, user: User, report_id: int, label: str | None = None
) -> ReportVersion:
    """Copy current live Report + items + parameters into a new snapshot.

    Caller must have already verified visibility via
    :func:`app.services.report.ensure_report_visible`.
    """
    report = db.get(Report, report_id)
    if report is None:
        raise ValueError(f"Report {report_id} not found")

    # next version_number per report
    last_num = (
        db.query(ReportVersion.version_number)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version_number.desc())
        .first()
    )
    next_num = (last_num[0] + 1) if last_num else 1

    version = ReportVersion(
        report_id=report.id,
        version_number=next_num,
        label=label,
        is_pinned=False,
        created_by=user.id,
        # Mirrored Report columns
        name=report.name,
        description=report.description,
        data_source_id=report.data_source_id,
        layout_config=report.layout_config,
        is_scheduled=report.is_scheduled,
        cron_expression=report.cron_expression,
        schedule_description=report.schedule_description,
        notification_config=report.notification_config,
        output_formats=report.output_formats,
        is_active=report.is_active,
        is_demo=report.is_demo,
        visibility=report.visibility,
        owner_user_id=report.owner_user_id,
        org_id=report.org_id,
    )
    db.add(version)
    db.flush()  # populate version.id for child FKs

    for item in report.items:
        db.add(
            ReportVersionItem(
                version_id=version.id,
                name=item.name,
                item_type=item.item_type,
                order_index=item.order_index,
                table_name=item.table_name,
                fields=item.fields,
                where_conditions=item.where_conditions,
                group_by=item.group_by,
                order_by=item.order_by,
                limit=item.limit,
                display_config=item.display_config,
                custom_sql=item.custom_sql,
                original_item_id=item.id,
            )
        )

    for param in report.parameters:
        db.add(
            ReportVersionParameter(
                version_id=version.id,
                name=param.name,
                label=param.label,
                type=param.type,
                required=param.required,
                default=param.default,
                options=param.options,
                order_index=param.order_index,
                original_parameter_id=param.id,
            )
        )

    db.commit()
    db.refresh(version)
    return version


def list_versions(db: Session, *, report_id: int) -> Sequence[ReportVersion]:
    """All snapshots for one report, newest first."""
    return (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version_number.desc())
        .all()
    )


def get_version(db: Session, *, version_id: int) -> ReportVersion | None:
    return db.get(ReportVersion, version_id)


class PinnedVersionError(Exception):
    """Raised when attempting to delete a pinned version."""


def restore_version(db: Session, *, user: User, report_id: int, version_id: int) -> Report:
    """Overwrite live Report + items + parameters with snapshot state.

    Caller MUST have already verified owner/admin via
    :func:`app.services.report.is_owner_or_admin`.
    """
    version = db.get(ReportVersion, version_id)
    if version is None or version.report_id != report_id:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Version not found")

    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Report not found")

    # Overwrite Report scalar columns
    report.name = version.name
    report.description = version.description
    report.data_source_id = version.data_source_id
    report.layout_config = version.layout_config
    report.is_scheduled = version.is_scheduled
    report.cron_expression = version.cron_expression
    report.schedule_description = version.schedule_description
    report.notification_config = version.notification_config
    report.output_formats = version.output_formats
    report.is_active = version.is_active
    # is_demo deliberately preserved — it's a system flag, not user content
    report.visibility = version.visibility
    report.owner_user_id = version.owner_user_id
    report.org_id = version.org_id

    # Replace items: delete current, re-create from snapshot
    db.query(ReportItem).filter(ReportItem.report_id == report_id).delete()
    db.flush()
    for v_item in version.items:
        db.add(
            ReportItem(
                report_id=report_id,
                name=v_item.name,
                item_type=v_item.item_type,
                order_index=v_item.order_index,
                table_name=v_item.table_name,
                fields=v_item.fields,
                where_conditions=v_item.where_conditions,
                group_by=v_item.group_by,
                order_by=v_item.order_by,
                limit=v_item.limit,
                display_config=v_item.display_config,
                custom_sql=v_item.custom_sql,
            )
        )

    # Replace parameters: same pattern
    db.query(ReportParameter).filter(ReportParameter.report_id == report_id).delete()
    db.flush()
    for v_param in version.parameters:
        db.add(
            ReportParameter(
                report_id=report_id,
                name=v_param.name,
                label=v_param.label,
                type=v_param.type,
                required=v_param.required,
                default=v_param.default,
                options=v_param.options,
                order_index=v_param.order_index,
            )
        )

    db.commit()
    db.refresh(report)
    return report


def delete_version(db: Session, *, version_id: int) -> None:
    version = db.get(ReportVersion, version_id)
    if version is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Version not found")
    if version.is_pinned:
        raise PinnedVersionError("Version is pinned; unpin before delete")
    db.delete(version)
    db.commit()
