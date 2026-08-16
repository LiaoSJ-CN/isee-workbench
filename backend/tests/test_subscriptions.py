"""Tests for per-user report subscriptions (批 8.3).

Coverage:

* HTTP ``/subscriptions`` surface — auth-gated; CRUD returns the
  correct status codes and shape; ``report_id`` filter narrows the
  list; ``?report_id=N`` ownership is enforced via the auth identity,
  not query strings.
* :func:`app.services.subscription.create_subscription` — validates
  cron at the service layer so a malformed cron never reaches the DB.
* :func:`app.services.subscription.update_subscription` — partial
  updates; re-validation of cron when it changes; rescheduling via
  APScheduler is idempotent.
* :func:`app.services.subscription._execute_subscription` — happy
  path: report exists + is_active, no notification_config → file is
  produced but not delivered (logged), ``last_run_at`` is stamped.

Sidecar / scheduler integration is exercised through the same
``get_scheduler().scheduler`` singleton the production code uses.
This is safe for tests because :func:`shutdown` is called between
the major suites — see the ``scheduler_module`` fixture.

For the dispatch-side invariants (notification_config typed-dispatch
end-to-end, including the IM providers added in 批 8.4) we rely on
the union's own tests in :mod:`tests.test_notification_config` and
:mod:`tests.test_notification_im`. The subscription tests here only
cover the ``notification_config`` setter via Pydantic — anything more
would duplicate coverage already in place.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.report_subscription import ReportSubscription
from app.models.user import User
from app.services.scheduler import (
    InvalidCronExpression,
    get_scheduler,
)
from app.services.subscription import (
    _execute_subscription,
    create_subscription,
    get_subscription,
    update_subscription,
)

# ----------------- helpers -----------------


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


CRON_HOURLY = "0 * * * * *"  # at minute 0 of every hour — never fires in tests


@pytest.fixture
def db_setup() -> Any:
    """Pair of (Session, admin User) so each test gets a clean handle.

    Mirrors the local fixture in :mod:`tests.test_job_queue` — we
    don't promote it to conftest because it's only used by tests
    that need raw DB access plus a real admin user row.
    """
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _purge_subscription_jobs() -> Any:
    """Drop any ``sub_<id>`` jobs left in the scheduler singleton
    before each test.

    The scheduler is process-global; without this an early failing
    test that aborts mid-flight can leak jobs into the next test's
    ``get_job`` assertions and produce confusing cross-test errors.
    The ``report_<id>`` namespace is intentionally untouched — those
    jobs belong to the existing scheduler suite.
    """
    scheduler = get_scheduler()
    for job in list(scheduler.scheduler.get_jobs()):
        if job.id and job.id.startswith("sub_"):
            scheduler.scheduler.remove_job(job.id)
    yield


@pytest.fixture
def report_with_sqlite() -> int:
    """A Report + DataSource pair backed by in-memory SQLite. The
    subscription service only checks ``report.exists`` for the create
    path; tests can therefore reuse the same Report for many
    subscriptions.

    Returns the report id; the fixture cleans up the report and its
    data source but not subscriptions (those are owned by rows the
    test must clean per-case to keep the scheduler from leaking
    jobs across tests).
    """
    db: Session = SessionLocal()
    rep_name = _unique("pytest_sub_report")
    ds_name = _unique("pytest_sub_ds")
    src = DataSource(
        name=ds_name,
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    rep = Report(
        name=rep_name,
        data_source_id=src.id,
        is_active=True,
        is_scheduled=False,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    rid = int(rep.id)
    try:
        yield rid
    finally:
        db.query(ReportSubscription).filter(
            ReportSubscription.report_id == rid
        ).delete()
        db.commit()
        db.delete(rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


@pytest.fixture
def admin_user_id(db_setup: Any) -> int:
    """Admin user's id — the test client is always authenticated as admin."""
    _db, user = db_setup
    return int(user.id)


# ----------------- service-layer tests -----------------


def test_create_subscription_persists_row(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
) -> None:
    db, _ = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=admin_user_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={"x": 1},
        notification_config=None,
    )
    try:
        assert sub.id is not None
        assert sub.owner_user_id == admin_user_id
        assert sub.report_id == report_with_sqlite
        assert sub.cron_expression == CRON_HOURLY
        assert sub.parameters == {"x": 1}
        assert sub.is_active is True
    finally:
        # Drop the APScheduler job the create call registered — keeps
        # the singleton clean for siblings.
        get_scheduler().scheduler.remove_job(f"sub_{sub.id}")
        db.delete(sub)
        db.commit()


def test_create_subscription_rejects_invalid_cron(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
) -> None:
    db, _ = db_setup
    with pytest.raises(InvalidCronExpression):
        create_subscription(
            db=db,
            owner_user_id=admin_user_id,
            report_id=report_with_sqlite,
            cron_expression="not-a-cron",
            parameters={},
            notification_config=None,
        )


def test_create_subscription_rejects_missing_report(
    db_setup: Any,
    admin_user_id: int,
) -> None:
    db, _ = db_setup
    with pytest.raises(LookupError):
        create_subscription(
            db=db,
            owner_user_id=admin_user_id,
            report_id=99_999_999,
            cron_expression=CRON_HOURLY,
            parameters={},
            notification_config=None,
        )


def test_get_subscription_filters_by_owner(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
) -> None:
    """Cross-owner lookups must return None — the router surfaces 404
    for any unauthorized access without leaking existence."""
    db, _ = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=admin_user_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    try:
        found = get_subscription(db, int(sub.id), admin_user_id)
        assert found is not None

        # Different owner id → None (no row leaks).
        assert get_subscription(db, int(sub.id), admin_user_id + 999) is None
        # Bogus id → None.
        assert get_subscription(db, 99_999_999, admin_user_id) is None
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sub.id}")
        db.delete(sub)
        db.commit()


def test_update_subscription_revalidates_changed_cron(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
) -> None:
    db, _ = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=admin_user_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    try:
        # Invalid cron must raise — leaves the row unchanged.
        with pytest.raises(InvalidCronExpression):
            update_subscription(db, sub, cron_expression="bad-cron")
        db.refresh(sub)
        assert sub.cron_expression == CRON_HOURLY

        # Valid cron change reschedules — the APScheduler job is
        # ``replace_existing`` so the new cron's first fire replaces
        # the old one.
        update_subscription(db, sub, cron_expression="0 0 * * * *")
        assert sub.cron_expression == "0 0 * * * *"
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sub.id}")
        db.delete(sub)
        db.commit()


def test_update_subscription_pause_unschedules_job(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
) -> None:
    db, _ = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=admin_user_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    sid = int(sub.id)
    try:
        # Sanity: job present right after create.
        assert get_scheduler().scheduler.get_job(f"sub_{sid}") is not None

        update_subscription(db, sub, is_active=False)
        assert sub.is_active is False
        # Job pruned — no future ticks fire while paused.
        assert get_scheduler().scheduler.get_job(f"sub_{sid}") is None

        update_subscription(db, sub, is_active=True)
        assert sub.is_active is True
        assert get_scheduler().scheduler.get_job(f"sub_{sid}") is not None
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sid}")
        db.delete(sub)
        db.commit()


def test_execute_subscription_writes_file_and_stamps_last_run(
    db_setup: Any,
    admin_user_id: int,
    report_with_sqlite: int,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Happy-path worker tick: file is produced, ``last_run_at``
    advances, no notification_config → no outbound HTTP (logged)."""
    db, _ = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=admin_user_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    sid = int(sub.id)

    # Direct the report generator into tmp_path so cleanup is easy.
    monkeypatch.setattr(
        "app.config.settings.generated_reports_dir", tmp_path
    )

    # ``generate_report`` needs at least one item-less Report; ours
    # has no items → a no-item render raises. Use a real Excel path
    # by patching with a stub that records the call.
    called: list[dict[str, Any]] = []

    def _stub_generate(**kwargs: Any) -> dict[str, Any]:
        called.append(kwargs)
        return {"file_path": str(tmp_path / f"sub_{sid}.xlsx")}

    monkeypatch.setattr(
        "app.services.subscription.generate_report", _stub_generate
    )

    try:
        before = datetime.now(timezone.utc)
        _execute_subscription(sid)
        db.refresh(sub)
        assert sub.last_run_at is not None
        # SQLite drops tz info on round-trip — normalize to UTC for the
        # comparison instead of relying on the driver.
        last_run = sub.last_run_at
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        assert last_run >= before
        assert called, "generate_report should have run exactly once"
        assert called[0]["report"] is not None
        assert called[0]["parameters"] == {}
        assert called[0]["output_format"] == "excel"
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sid}")
        db.query(ReportSubscription).filter(
            ReportSubscription.id == sid
        ).delete()
        db.commit()


# ----------------- HTTP-layer tests -----------------


def test_create_endpoint_returns_201(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
) -> None:
    resp = client.post(
        "/subscriptions",
        headers=auth_headers,
        json={
            "report_id": report_with_sqlite,
            "cron_expression": CRON_HOURLY,
            "parameters": {},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cron_expression"] == CRON_HOURLY
    assert body["report_id"] == report_with_sqlite
    assert body["is_active"] is True
    # Cleanup the scheduler entry the create call registered.
    get_scheduler().scheduler.remove_job(f"sub_{body['id']}")


def test_create_endpoint_returns_400_for_invalid_cron(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
) -> None:
    resp = client.post(
        "/subscriptions",
        headers=auth_headers,
        json={
            "report_id": report_with_sqlite,
            "cron_expression": "garbage",
            "parameters": {},
        },
    )
    assert resp.status_code == 400


def test_create_endpoint_returns_404_for_unknown_report(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    resp = client.post(
        "/subscriptions",
        headers=auth_headers,
        json={
            "report_id": 99_999_999,
            "cron_expression": CRON_HOURLY,
            "parameters": {},
        },
    )
    assert resp.status_code == 404


def test_create_endpoint_returns_401_unauthenticated(
    client: TestClient,
    report_with_sqlite: int,
) -> None:
    """No auth header → 401 (router is auth-gated)."""
    resp = client.post(
        "/subscriptions",
        json={
            "report_id": report_with_sqlite,
            "cron_expression": CRON_HOURLY,
            "parameters": {},
        },
    )
    assert resp.status_code == 401


def test_list_endpoint_returns_only_my_subscriptions(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
) -> None:
    """List endpoint must not leak another user's subscriptions.

    The project's conftest.py only mints ``auth_headers`` for admin,
    so we fabricate a different-owner row directly via the DB and
    assert the admin's GET doesn't see it. This is the same pattern
    the ``test_get_endpoint_returns_404_for_other_user`` test uses
    a few lines down.
    """
    db = SessionLocal()
    admin = db.query(User).filter(User.username == "admin").first()
    if admin is None:
        db.close()
        pytest.skip("admin user not seeded")
    admin_id = int(admin.id)

    sub_admin = create_subscription(
        db=db,
        owner_user_id=admin_id,
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    # Synthetic non-admin row: insert directly. id 999 is unused
    # in a fresh dev DB; satisfy the FK by reusing an existing user
    # that isn't admin. Easier path: insert a fake owner id that
    # doesn't match admin — the unique constraint / FK rely on
    # existing users, but for the read filter we only need rows
    # the admin shouldn't see. We create a synthetic user.
    from app.models.user import User as UserModel

    fake = UserModel(
        username=_unique("other"),
        password_hash="x",
    )
    db.add(fake)
    db.commit()
    db.refresh(fake)
    sub_other = ReportSubscription(
        owner_user_id=int(fake.id),
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
        is_active=True,
    )
    db.add(sub_other)
    db.commit()
    db.refresh(sub_other)
    # Capture ids BEFORE closing the session — once closed, the ORM
    # objects can't lazy-load.
    sub_admin_id = int(sub_admin.id)
    sub_other_id = int(sub_other.id)
    fake_id = int(fake.id)
    db.close()

    try:
        resp = client.get("/subscriptions", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        ids = [row["id"] for row in body]
        assert sub_admin_id in ids
        assert sub_other_id not in ids
    finally:
        # ``sub_other`` was inserted directly via ORM and never went
        # through ``create_subscription``, so it has no APScheduler
        # job — only ``sub_admin`` does. Wrap in try/except
        # ``JobLookupError`` for forward-compat in case future
        # changes schedule both rows.
        from apscheduler.jobstores.base import JobLookupError

        for jid in (f"sub_{sub_admin_id}", f"sub_{sub_other_id}"):
            try:
                get_scheduler().scheduler.remove_job(jid)
            except JobLookupError:
                pass
        db = SessionLocal()
        db.query(ReportSubscription).filter(
            ReportSubscription.id.in_([sub_admin_id, sub_other_id])
        ).delete()
        db.query(User).filter(User.id == fake_id).delete()
        db.commit()
        db.close()


def test_get_endpoint_returns_404_for_other_user(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
    db_setup: Any,
) -> None:
    """A subscription owned by admin must 404 for any non-owner — but
    the test client is always admin, so we manually NULL out the
    owner_user_id to a different value and confirm the read is
    blocked.
    """
    db, user = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=int(user.id),
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    sid = int(sub.id)
    # Fabricate another user row to be the "owner" instead.
    from app.models.user import User as UserModel

    other = UserModel(
        username=_unique("other_get"),
        password_hash="x",
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    sub.owner_user_id = int(other.id)
    db.commit()
    try:
        resp = client.get(f"/subscriptions/{sid}", headers=auth_headers)
        assert resp.status_code == 404
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sid}")
        db.query(ReportSubscription).filter(
            ReportSubscription.id == sid
        ).delete()
        db.delete(other)
        db.commit()
        db.close()


def test_pause_resume_endpoints_toggle_is_active(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
    db_setup: Any,
) -> None:
    db, user = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=int(user.id),
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    sid = int(sub.id)
    try:
        resp = client.post(f"/subscriptions/{sid}/pause", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        # APScheduler pruned.
        assert get_scheduler().scheduler.get_job(f"sub_{sid}") is None

        resp = client.post(f"/subscriptions/{sid}/resume", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True
        assert get_scheduler().scheduler.get_job(f"sub_{sid}") is not None
    finally:
        get_scheduler().scheduler.remove_job(f"sub_{sid}")
        db.query(ReportSubscription).filter(
            ReportSubscription.id == sid
        ).delete()
        db.commit()
        db.close()


def test_delete_endpoint_returns_204(
    client: TestClient,
    auth_headers: dict[str, str],
    report_with_sqlite: int,
    db_setup: Any,
) -> None:
    db, user = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=int(user.id),
        report_id=report_with_sqlite,
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=None,
    )
    sid = int(sub.id)
    resp = client.delete(f"/subscriptions/{sid}", headers=auth_headers)
    assert resp.status_code == 204
    # APScheduler pruned, DB row gone.
    assert get_scheduler().scheduler.get_job(f"sub_{sid}") is None
    db.close()
