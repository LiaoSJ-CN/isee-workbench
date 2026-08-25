"""FastAPI router for report versioning."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.report import Report
from app.models.report_version import ReportVersion
from app.models.user import User
from app.schemas.report import ReportResponse
from app.schemas.report_version import (
    ReportVersionCreate,
    ReportVersionDiff,
    ReportVersionResponse,
    ReportVersionRestoreResponse,
    ReportVersionSummary,
)
from app.services.audit import log as write_audit_log
from app.services.report import ensure_report_visible, is_owner_or_admin
from app.services.report_version import (
    PinnedVersionError,
    create_snapshot,
    delete_version,
    get_version,
    list_versions,
    restore_version,
)
from app.services.report_version_diff import compute_diff

router = APIRouter(
    prefix="/reports",
    tags=["report-versions"],
)


def _summary(version: ReportVersion) -> ReportVersionSummary:
    return ReportVersionSummary.model_validate(version)


def _full(version: ReportVersion) -> ReportVersionResponse:
    return ReportVersionResponse.model_validate(version)


@router.post(
    "/{report_id}/versions",
    response_model=ReportVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new version snapshot of a Report",
)
def create_version(
    report_id: int,
    payload: ReportVersionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    ensure_report_visible(db, user, report_id)
    version = create_snapshot(db, user=user, report_id=report_id, label=payload.label)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="create_version",
        target_type="report",
        target_id=report_id,
        after={"version_id": version.id, "label": version.label},
    )
    return _full(version)


@router.get(
    "/{report_id}/versions",
    response_model=list[ReportVersionSummary],
    summary="List all versions of a Report, newest first",
)
def list_report_versions(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportVersionSummary]:
    ensure_report_visible(db, user, report_id)
    return [_summary(v) for v in list_versions(db, report_id=report_id)]


@router.get(
    "/{report_id}/versions/{version_id}",
    response_model=ReportVersionResponse,
    summary="Get the full snapshot for one version",
)
def get_report_version(
    report_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportVersionResponse:
    ensure_report_visible(db, user, report_id)
    version = get_version(db, version_id=version_id)
    if version is None or version.report_id != report_id:
        raise HTTPException(status_code=404, detail="Version not found")
    return _full(version)


@router.get(
    "/{report_id}/versions/{version_id}/diff",
    response_model=ReportVersionDiff,
    summary="Diff between this version and another version (or current live Report)",
)
def diff_report_version(
    report_id: int,
    version_id: int,
    against: str | None = Query(default=None, description="Other version id, or 'current'"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportVersionDiff:
    ensure_report_visible(db, user, report_id)
    base = get_version(db, version_id=version_id)
    if base is None or base.report_id != report_id:
        raise HTTPException(status_code=404, detail="Version not found")

    target_version: ReportVersion | None = None
    live_report: Report | None = None
    if against is None or against == "current":
        live_report = db.get(Report, report_id)
    elif against.isdigit():
        other = get_version(db, version_id=int(against))
        if other is None or other.report_id != report_id:
            raise HTTPException(status_code=404, detail="Target version not found")
        target_version = other
    else:
        raise HTTPException(status_code=400, detail="against must be 'current' or a version id")

    payload = compute_diff(
        base_version=base, target_version=target_version, live_report=live_report
    )
    return ReportVersionDiff.model_validate(payload)


@router.post(
    "/{report_id}/versions/{version_id}/restore",
    response_model=ReportVersionRestoreResponse,
    summary="Restore a Report to a chosen version (owner or admin only)",
)
def restore_report_version(
    report_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportVersionRestoreResponse:
    report = ensure_report_visible(db, user, report_id)
    if not is_owner_or_admin(user, report):
        raise HTTPException(status_code=403, detail="Only owner or admin can restore")
    restored = restore_version(db, user=user, report_id=report_id, version_id=version_id)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="restore_version",
        target_type="report",
        target_id=report_id,
        after={"version_id": version_id},
    )
    return ReportVersionRestoreResponse(report=ReportResponse.model_validate(restored).model_dump())


@router.delete(
    "/{report_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a version (owner or admin only; pinned versions reject with 409)",
)
def delete_report_version(
    report_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    report = ensure_report_visible(db, user, report_id)
    if not is_owner_or_admin(user, report):
        raise HTTPException(status_code=403, detail="Only owner or admin can delete")
    version = get_version(db, version_id=version_id)
    if version is None or version.report_id != report_id:
        raise HTTPException(status_code=404, detail="Version not found")
    try:
        delete_version(db, version_id=version_id)
    except PinnedVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
