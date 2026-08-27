"""Tests for batch 13 — template marketplace endpoints.

Coverage matrix (mirrors :mod:`tests.test_clone_duplicate` pattern):

* ``POST /reports/{id}/save-as-template``:
  - owner can publish → 201, cloned row has ``is_template=true``,
    original row untouched, scheduler fields + notification_config
    stripped, ``template_source_id`` NULL on the new template.
  - admin can publish someone else's report.
  - non-owner (and non-admin) → 403.
  - missing source → 404 (uniform with the rest of the API).
  - ``visibility='org'`` + caller without ``org_id`` → 400.
* ``GET /reports/templates``:
  - public templates appear for any caller.
  - ``org``-tier templates appear only when caller's ``org_id`` matches
    the template's AND both are non-null (NULL = mismatch).
  - private templates appear only for the owner.
  - admin sees every template regardless of visibility.
  - filter wiring: ``?category=`` and ``?q=`` narrow the result set.
* ``POST /reports/{id}/from-template``:
  - read ACL on the template is sufficient (not owner-or-admin).
  - resulting fork is private + caller-owned + ``is_template=false``,
    with ``template_source_id`` pointing back at the template.
  - missing template → 404.
* Audit log: ``report.save_as_template`` and ``report.fork`` actions
  emitted with the right ``before``/``after`` payload.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import (
    VISIBILITY_ORG,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    Report,
)
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db_setup() -> tuple[Session, User]:
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


@pytest.fixture
def non_admin_user() -> User:
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_template_user"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.delete(user)
        db.commit()
        db.close()


@pytest.fixture
def data_source() -> DataSource:
    db = SessionLocal()
    ds = DataSource(
        name=_unique("pytest_tmpl_ds"),
        db_type="sqlite",
        host="h",
        port=1,
        database=":memory:",
        username="u",
        password="p",
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    try:
        yield ds
    finally:
        db.delete(ds)
        db.commit()
        db.close()


def _grant_ds_read(user: User, ds: DataSource) -> None:
    """Insert a read grant for ``user`` on ``ds`` so the viewer's
    request passes the data-source ACL layer in :func:`get_report_for_user`
    (Layer 1) — without this grant the admin-owned DS hides every report
    from the non-admin viewer and ``get_report_for_user`` returns
    ``None`` before the report-level ACL check runs, masking the
    intended 403."""
    db = SessionLocal()
    try:
        access = DataSourceAccess(
            data_source_id=ds.id,
            user_id=user.id,
            permission="read",
        )
        db.add(access)
        db.commit()
    finally:
        db.close()


def _mint_token(user: User) -> str:
    return create_access_token(
        user.username,
        user_id=int(user.id),
        role=str(user.role),
        org_id=user.org_id,
    )


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint_token(user)}"}


def _make_report(
    db: Session,
    *,
    owner: User,
    ds: DataSource,
    name: str | None = None,
    visibility: str = VISIBILITY_PUBLIC,
    org_id: int | None = None,
) -> Report:
    r = Report(
        name=name or _unique("pytest_tmpl_report"),
        data_source_id=ds.id,
        owner_user_id=owner.id,
        visibility=visibility,
        org_id=org_id,
        is_demo=False,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _cleanup_report(db: Session, report_id: int | None) -> None:
    """Remove a report row by id. ``Report.delete`` cascade-drops items
    via the relationship, but parameters + shares also cascade, so a
    plain ``db.delete`` is enough.

    Accepts ``None`` so ``finally`` blocks can call it unconditionally
    when the request that would have produced the id failed first
    (mypy can't otherwise prove the int was assigned).
    """
    if report_id is None:
        return
    r = db.get(Report, report_id)
    if r is not None:
        db.delete(r)
        db.commit()


# ---- save-as-template -------------------------------------------------------


def test_save_as_template_owner_publishes_and_strips_scheduler(
    client: TestClient,
    db_setup: tuple[Session, User],
    data_source: DataSource,
) -> None:
    """Owner publishing their own report: cloned row is marked as
    template, scheduler fields + notification_config are stripped, the
    source row stays untouched."""
    db, admin = db_setup
    source = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_PUBLIC,
    )
    # Configure the source with values that must NOT survive the
    # save-as-template: a scheduled job + a webhook config.
    source.is_scheduled = True
    source.cron_expression = "0 0 * * *"
    source.schedule_description = "daily at midnight"
    source.notification_config = {"type": "webhook", "url": "https://x"}
    db.commit()

    try:
        r = client.post(
            f"/reports/{source.id}/save-as-template",
            headers=_auth_headers(admin),
            json={"visibility": "public", "category": "示例报表"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["is_template"] is True
        assert body["template_category"] == "示例报表"
        assert body["template_source_id"] is None
        # Scheduler + notification stripped (forks the *fresh* template
        # shape — the operator wires up scheduling on derived reports).
        assert body["is_scheduled"] is False
        assert body["cron_expression"] is None
        assert body["schedule_description"] is None
        assert body["notification_config"] is None

        # Source untouched (visibility, scheduler, etc. all stay).
        db.refresh(source)
        assert source.is_template is False
        assert source.is_scheduled is True
        assert source.cron_expression == "0 0 * * *"
        assert source.notification_config == {"type": "webhook", "url": "https://x"}
    finally:
        _cleanup_report(db, source.id)
        if "body" in locals():
            _cleanup_report(db, body["id"])


def test_save_as_template_non_owner_non_admin_returns_403(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """Non-owner + non-admin → 403. They can still clone the source
    via the regular ``/duplicate`` endpoint."""
    db, admin = db_setup
    source = _make_report(
        db, owner=admin, ds=data_source, visibility=VISIBILITY_PUBLIC
    )
    # Grant DS read so the viewer passes Layer 1 of get_report_for_user
    # and reaches the is_admin-or-is_owner 403 check (without this
    # grant, Layer 1 returns None and the request 404s before the 403
    # gate ever runs).
    _grant_ds_read(non_admin_user, data_source)
    try:
        r = client.post(
            f"/reports/{source.id}/save-as-template",
            headers=_auth_headers(non_admin_user),
            json={"visibility": "public"},
        )
        assert r.status_code == 403, r.text
    finally:
        _cleanup_report(db, source.id)
        # Sweep the DataSourceAccess row we inserted. The conftest
        # autouse fixture only prunes leaked DataSource rows (Layer 1),
        # not access rows.
        db.query(DataSourceAccess).filter(
            DataSourceAccess.user_id == non_admin_user.id,
            DataSourceAccess.data_source_id == data_source.id,
        ).delete()
        db.commit()


def test_save_as_template_missing_source_returns_404(
    client: TestClient,
    db_setup: tuple[Session, User],
) -> None:
    db, admin = db_setup
    r = client.post(
        "/reports/99999999/save-as-template",
        headers=_auth_headers(admin),
        json={"visibility": "public"},
    )
    assert r.status_code == 404, r.text


def test_save_as_template_org_visibility_requires_caller_org_id(
    client: TestClient,
    db_setup: tuple[Session, User],
    data_source: DataSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller picks ``visibility='org'`` but has no ``org_id`` — the
    router rejects with 400 so the operator knows the template would
    be invisible to everyone (NULL on the viewer's side never matches
    NULL on the template's side per the org-tier semantics)."""
    from app.config import settings

    db, admin = db_setup
    # Strip any org_id the bootstrap admin might carry.
    monkeypatch.setattr(settings, "default_org_id", None)
    source = _make_report(db, owner=admin, ds=data_source)
    try:
        r = client.post(
            f"/reports/{source.id}/save-as-template",
            headers=_auth_headers(admin),
            json={"visibility": "org", "category": "示例报表"},
        )
        assert r.status_code == 400, r.text
        assert "org_id" in r.json()["detail"]
    finally:
        _cleanup_report(db, source.id)


def test_save_as_template_emits_audit_log_entry(
    client: TestClient,
    db_setup: tuple[Session, User],
    data_source: DataSource,
) -> None:
    """Audit log gets ``report.save_as_template`` with the cloned row
    as the target. Snapshot the audit row count before + after to
    avoid coupling to other concurrent audit emissions."""
    from app.services import audit as audit_service

    db, admin = db_setup
    source = _make_report(db, owner=admin, ds=data_source)
    before = (
        db.query(AuditLog)
        .filter(AuditLog.action == audit_service.ACTION_REPORT_SAVE_AS_TEMPLATE)
        .count()
    )
    try:
        r = client.post(
            f"/reports/{source.id}/save-as-template",
            headers=_auth_headers(admin),
            json={"visibility": "public", "category": "示例报表"},
        )
        assert r.status_code == 201, r.text
        new_id = r.json()["id"]
        after = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == audit_service.ACTION_REPORT_SAVE_AS_TEMPLATE,
                AuditLog.target_id == new_id,
            )
            .count()
        )
        assert after == before + 1, "save-as-template should emit exactly one audit row"
    finally:
        _cleanup_report(db, source.id)
        if "new_id" in locals():
            _cleanup_report(db, new_id)


# ---- GET /reports/templates -------------------------------------------------


def test_list_templates_public_visible_to_anyone(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """Public template appears in the gallery for any caller (admin,
    the publisher themselves, or a third party)."""
    db, admin = db_setup
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_PUBLIC,
    )
    template.is_template = True
    template.template_category = "示例报表"
    db.commit()
    _grant_ds_read(non_admin_user, data_source)
    try:
        r = client.get(
            "/reports/templates",
            headers=_auth_headers(non_admin_user),
        )
        assert r.status_code == 200, r.text
        ids = [t["id"] for t in r.json()]
        assert template.id in ids
    finally:
        _cleanup_report(db, template.id)
        db.query(DataSourceAccess).filter(
            DataSourceAccess.user_id == non_admin_user.id,
            DataSourceAccess.data_source_id == data_source.id,
        ).delete()
        db.commit()


def test_list_templates_org_visibility_matches_matching_org(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """Org-tier template is visible iff caller's ``org_id`` matches
    the template's AND both are non-null. Two callers: same-org
    viewer should see it; cross-org viewer should NOT."""
    db, admin = db_setup
    # Caller has org_id=7. Commit on the user's OWN session (where
    # the row was loaded) — committing via the db_setup session
    # silently no-ops because the User instance isn't in that
    # session's identity map.
    non_admin_user_db = SessionLocal()
    try:
        u = non_admin_user_db.get(User, non_admin_user.id)
        assert u is not None
        u.org_id = 7
        non_admin_user_db.commit()
        non_admin_user.org_id = 7  # mirror in-memory for token mint
    finally:
        non_admin_user_db.close()

    # Template is org-tier with org_id=7 (matches)
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_ORG,
        org_id=7,
    )
    template.is_template = True
    db.commit()

    # Mismatched-org caller
    other_user = User(
        username=_unique("pytest_template_other"),
        password_hash="x",
        role=ROLE_VIEWER,
        org_id=999,
    )
    db.add(other_user)
    db.commit()
    db.refresh(other_user)
    # Both viewers need DS read so they pass Layer 1 of the
    # report ACL. Without the grant the reports stay invisible
    # and the org-tier check is masked by the 404 from Layer 1.
    _grant_ds_read(non_admin_user, data_source)
    _grant_ds_read(other_user, data_source)
    try:
        # Same-org viewer sees the template
        r1 = client.get(
            "/reports/templates", headers=_auth_headers(non_admin_user)
        )
        assert r1.status_code == 200
        assert template.id in [t["id"] for t in r1.json()]

        # Cross-org viewer does NOT
        r2 = client.get(
            "/reports/templates", headers=_auth_headers(other_user)
        )
        assert r2.status_code == 200
        assert template.id not in [t["id"] for t in r2.json()]
    finally:
        _cleanup_report(db, template.id)
        db.query(DataSourceAccess).filter(
            DataSourceAccess.data_source_id == data_source.id
        ).delete()
        db.commit()
        db.delete(other_user)
        db.commit()
        # Reset the non-admin viewer's org_id back to NULL on its
        # own session so other tests don't inherit the stale value.
        reset_db = SessionLocal()
        try:
            u = reset_db.get(User, non_admin_user.id)
            assert u is not None
            u.org_id = None
            reset_db.commit()
        finally:
            reset_db.close()
        non_admin_user.org_id = None  # mirror back


def test_list_templates_org_visibility_null_means_mismatch(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """NULL on either side of the org-match is a cross-tenant
    mismatch. Template has org_id=7 but viewer has org_id=None → no
    match. Mirrors the
    ``_is_template_visible_to_user`` semantics."""
    db, admin = db_setup
    non_admin_user.org_id = None  # explicit reset
    db.commit()
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_ORG,
        org_id=7,
    )
    template.is_template = True
    db.commit()
    _grant_ds_read(non_admin_user, data_source)
    try:
        r = client.get(
            "/reports/templates", headers=_auth_headers(non_admin_user)
        )
        assert r.status_code == 200
        assert template.id not in [t["id"] for t in r.json()], (
            "NULL-on-viewer org_id should not match org_id=7 template"
        )
    finally:
        _cleanup_report(db, template.id)
        db.query(DataSourceAccess).filter(
            DataSourceAccess.user_id == non_admin_user.id,
            DataSourceAccess.data_source_id == data_source.id,
        ).delete()
        db.commit()


def test_list_templates_private_visible_only_to_owner(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """Private templates appear only for the owner (or admin)."""
    db, admin = db_setup
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_PRIVATE,
    )
    template.is_template = True
    db.commit()
    # Grant DS read so the private-template visibility ACL is the
    # only thing being tested (without the grant, the request 404s
    # at Layer 1 regardless of report-level visibility).
    _grant_ds_read(non_admin_user, data_source)
    try:
        r_owner = client.get(
            "/reports/templates", headers=_auth_headers(admin)
        )
        r_other = client.get(
            "/reports/templates", headers=_auth_headers(non_admin_user)
        )
        assert r_owner.status_code == 200
        assert r_other.status_code == 200
        assert template.id in [t["id"] for t in r_owner.json()]
        assert template.id not in [t["id"] for t in r_other.json()]
    finally:
        _cleanup_report(db, template.id)
        db.query(DataSourceAccess).filter(
            DataSourceAccess.user_id == non_admin_user.id,
            DataSourceAccess.data_source_id == data_source.id,
        ).delete()
        db.commit()


def test_list_templates_q_filter_narrows_by_name(
    client: TestClient,
    db_setup: tuple[Session, User],
    data_source: DataSource,
) -> None:
    """``?q=`` passes a case-insensitive substring match on ``name``."""
    db, admin = db_setup
    a = _make_report(
        db,
        owner=admin,
        ds=data_source,
        name="AAA-Sales-by-Region",
        visibility=VISIBILITY_PUBLIC,
    )
    b = _make_report(
        db,
        owner=admin,
        ds=data_source,
        name="BBB-Inventory-Summary",
        visibility=VISIBILITY_PUBLIC,
    )
    a.is_template = True
    b.is_template = True
    db.commit()
    try:
        r = client.get(
            "/reports/templates?q=Sales",
            headers=_auth_headers(admin),
        )
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "AAA-Sales-by-Region" in names
        assert "BBB-Inventory-Summary" not in names
    finally:
        _cleanup_report(db, a.id)
        _cleanup_report(db, b.id)


# ---- fork-from-template ----------------------------------------------------


def test_fork_from_template_read_acl_sufficient(
    client: TestClient,
    db_setup: tuple[Session, User],
    non_admin_user: User,
    data_source: DataSource,
) -> None:
    """Read ACL on the template is enough — any user who can browse
    the gallery (visibility ACL or grant) can fork. The non-admin
    caller here has no report-level grant; we put the template at
    ``public`` so the report-level ACL covers them, plus a DS read
    grant so Layer 1 doesn't 404 us."""
    db, admin = db_setup
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_PUBLIC,
    )
    template.is_template = True
    template.template_category = "示例报表"
    db.commit()
    _grant_ds_read(non_admin_user, data_source)
    try:
        r = client.post(
            f"/reports/{template.id}/from-template",
            headers=_auth_headers(non_admin_user),
            json={"name": "My Forked Report"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["is_template"] is False
        assert body["template_source_id"] == template.id
        assert body["name"] == "My Forked Report"
        # The fork is owned by the caller, not the template owner.
        assert body["owner_user_id"] == non_admin_user.id
    finally:
        _cleanup_report(db, template.id)
        if "body" in locals():
            _cleanup_report(db, body["id"])
        db.query(DataSourceAccess).filter(
            DataSourceAccess.user_id == non_admin_user.id,
            DataSourceAccess.data_source_id == data_source.id,
        ).delete()
        db.commit()


def test_fork_from_template_missing_returns_404(
    client: TestClient,
    db_setup: tuple[Session, User],
) -> None:
    db, admin = db_setup
    r = client.post(
        "/reports/99999999/from-template",
        headers=_auth_headers(admin),
        json={},
    )
    assert r.status_code == 404, r.text


def test_fork_from_template_emits_audit_log_entry(
    client: TestClient,
    db_setup: tuple[Session, User],
    data_source: DataSource,
) -> None:
    from app.services import audit as audit_service

    db, admin = db_setup
    template = _make_report(
        db,
        owner=admin,
        ds=data_source,
        visibility=VISIBILITY_PUBLIC,
    )
    template.is_template = True
    db.commit()
    before = (
        db.query(AuditLog)
        .filter(AuditLog.action == audit_service.ACTION_REPORT_FORK)
        .count()
    )
    try:
        r = client.post(
            f"/reports/{template.id}/from-template",
            headers=_auth_headers(admin),
            json={},
        )
        assert r.status_code == 201, r.text
        new_id = r.json()["id"]
        after = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == audit_service.ACTION_REPORT_FORK,
                AuditLog.target_id == new_id,
            )
            .count()
        )
        assert after == before + 1, "fork should emit exactly one audit row"
    finally:
        _cleanup_report(db, template.id)
        if "new_id" in locals():
            _cleanup_report(db, new_id)
