"""Tests for per-user dashboard subscription dispatch (批 14.4).

Coverage:

* :func:`_execute_dashboard_subscription` — incremental dedup:
  - First tick: always sends (no ``last_fingerprint``).
  - Unchanged fingerprint: skips send, only stamps ``last_run_at``.
  - Chart rows change: re-fires and notifies.
  - Text item edit: ignored — fingerprint unchanged.
* :func:`create_subscription` / :func:`update_subscription` —
  standard CRUD mirroring ``test_subscriptions.py`` patterns.
* :func:`sync_dashboard_subscriptions_with_database` — the sidecar
  reconcile loads every active subscription into APScheduler.

We monkeypatch :func:`app.services.dashboard.execute_dashboard_chart`
and :func:`app.services.dashboard_subscription._send_notification` so
the tests don't depend on a live data source or webhook endpoint —
the goal is to assert the **dedup decision** and **dispatch call
sequence**, not the sender's HTTP plumbing (which has its own suite).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.dashboard import Dashboard, DashboardItem
from app.models.dashboard_subscription import DashboardSubscription
from app.models.data_source import DataSource
from app.models.user import User
from app.services.dashboard_subscription import (
    _execute_dashboard_subscription,
    create_subscription,
    delete_subscription,
    get_subscription,
    sync_dashboard_subscriptions_with_database,
    update_subscription,
)
from app.services.scheduler import get_scheduler

# ----------------- helpers -----------------


CRON_HOURLY = "0 * * * * *"  # at minute 0 of every hour — never fires in tests


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db_setup() -> Any:
    """Admin user + a fresh DB session (read-only on dev DB)."""
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


def _safe_remove(job_id: str) -> None:
    """Remove a scheduler job if present, swallow JobLookupError."""
    scheduler = get_scheduler()
    if scheduler.scheduler.get_job(job_id):
        scheduler.scheduler.remove_job(job_id)


@pytest.fixture(autouse=True)
def _purge_dashboard_subscription_jobs() -> Any:
    """Drop any ``dsub_<id>`` jobs left in the scheduler singleton
    before each test. Mirror of the report-subscription fixture — keeps
    sibling tests from seeing each other's stray jobs.
    """
    scheduler = get_scheduler()
    for job in list(scheduler.scheduler.get_jobs()):
        if job.id and job.id.startswith("dsub_"):
            scheduler.scheduler.remove_job(job.id)
    yield


@pytest.fixture
def sqlite_data_source(db_setup: Any) -> Any:
    """A throwaway ``sqlite`` data source for chart-item SQL.

    The dispatcher doesn't actually run the chart SQL in tests (we
    monkeypatch ``execute_dashboard_chart``), but the item needs a
    valid ``data_source_id`` for ``_compute_dashboard_fingerprint``
    not to short-circuit.
    """
    db, _ = db_setup
    ds = DataSource(
        name=_unique("pytest_dsub_ds"),
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        password="placeholder",
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


@pytest.fixture
def dashboard_with_items(
    db_setup: Any,
    sqlite_data_source: Any,
) -> Any:
    """A dashboard with one each of text / report (no linked row) /
    chart items. Items are persisted so the dispatcher can iterate
    ``dashboard.items`` and we can mutate state between ticks.
    """
    db, _ = db_setup
    dash = Dashboard(
        name=_unique("pytest_dsub_dash"),
        description="test dashboard",
        owner_user_id=sqlite_data_source.id and db_setup[1].id,
        visibility="private",
    )
    # ``owner_user_id`` may collide — admin owns it.
    dash.owner_user_id = db_setup[1].id
    db.add(dash)
    db.flush()

    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="text",
            title="banner",
            order_index=0,
            x=0,
            y=0,
            w=12,
            h=1,
            text_content="welcome",
        )
    )
    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="report",
            title="placeholder report item",
            order_index=1,
            x=0,
            y=1,
            w=6,
            h=2,
            report_id=None,
        )
    )
    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="chart",
            title="placeholder chart",
            order_index=2,
            x=6,
            y=1,
            w=6,
            h=4,
            data_source_id=sqlite_data_source.id,
            table_name="t",
            fields=["a", "b"],
            limit=10,
        )
    )
    db.commit()
    db.refresh(dash)
    try:
        yield dash
    finally:
        db.delete(dash)
        db.commit()
        db.close()


@pytest.fixture
def subscription(
    db_setup: Any,
    dashboard_with_items: Any,
) -> Any:
    """Active dashboard subscription on the test dashboard.

    ``notification_config`` is set to a stub ``webhook`` so the
    dispatcher's send branch is exercised in tests; ``_send_notification``
    is monkeypatched out so no real HTTP leaves the box.
    """
    db, user = db_setup
    from pydantic import HttpUrl

    from app.schemas.notification import WebhookConfig

    sub = create_subscription(
        db=db,
        owner_user_id=int(user.id),
        dashboard_id=int(dashboard_with_items.id),
        cron_expression=CRON_HOURLY,
        parameters={},
        notification_config=WebhookConfig(
            type="webhook",
            url=HttpUrl("https://example.invalid/dashboard-hook"),
        ),
    )
    sid = int(sub.id) if sub.id is not None else 0
    try:
        yield sub
    finally:
        _safe_remove(f"dsub_{sid}")
        # Re-fetch the row before deleting — the test may have already
        # hard-deleted it via ``delete_subscription``, in which case
        # SQLAlchemy warns about a no-op DELETE.
        existing = db.get(DashboardSubscription, sid)
        if existing is not None:
            db.delete(existing)
            db.commit()
        db.close()


@pytest.fixture
def stub_chart_executor(monkeypatch: Any) -> Any:
    """Stub ``execute_dashboard_chart`` with deterministic rows.

    Returns a mutable list the test can mutate to simulate "rows
    changed" without needing a real data source.
    """
    rows_state: list[list[Any]] = [["x", 1]]

    def _fake(db: Session, item: DashboardItem, user: User) -> dict[str, Any]:
        return {
            "columns": ["label", "value"],
            "rows": rows_state,
            "row_count": len(rows_state),
        }

    monkeypatch.setattr(
        "app.services.dashboard.execute_dashboard_chart",
        _fake,
    )
    return rows_state


@pytest.fixture
def stub_sender(monkeypatch: Any) -> list[dict[str, Any]]:
    """Stub the notification sender — records every call instead of
    hitting the network.
    """
    calls: list[dict[str, Any]] = []

    def _fake_send(
        notification_config: Any,
        report_like: Any,
        file_paths: list[str],
    ) -> None:
        calls.append(
            {
                "config": notification_config,
                "report_like_id": getattr(report_like, "id", None),
                "report_like_name": getattr(report_like, "name", None),
                "file_paths": list(file_paths),
            }
        )

    monkeypatch.setattr(
        "app.services.dashboard_subscription._send_notification",
        _fake_send,
    )
    return calls


# ----------------- dispatcher tests -----------------


def test_first_run_always_sends(
    db_setup: Any,
    subscription: Any,
    stub_chart_executor: Any,
    stub_sender: list[dict[str, Any]],
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """First tick: ``last_fingerprint`` is NULL → must render + send."""
    monkeypatch.setattr(
        "app.config.settings.generated_reports_dir", tmp_path
    )
    _execute_dashboard_subscription(int(subscription.id))
    db, _ = db_setup
    db.refresh(subscription)
    assert subscription.last_run_at is not None
    assert subscription.last_fingerprint is not None
    assert len(stub_sender) == 1
    assert stub_sender[0]["file_paths"], "file should be written before send"
    p = stub_sender[0]["file_paths"][0]
    assert p.startswith(str(tmp_path))


def test_incremental_dedup_skips_unchanged(
    db_setup: Any,
    subscription: Any,
    stub_chart_executor: Any,
    stub_sender: list[dict[str, Any]],
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Two ticks back-to-back with no state change: second tick
    must NOT call the sender, only stamp ``last_run_at``."""
    monkeypatch.setattr(
        "app.config.settings.generated_reports_dir", tmp_path
    )
    _execute_dashboard_subscription(int(subscription.id))
    db, _ = db_setup
    db.refresh(subscription)
    first_fp = subscription.last_fingerprint
    first_run = subscription.last_run_at

    _execute_dashboard_subscription(int(subscription.id))
    db.refresh(subscription)
    assert subscription.last_fingerprint == first_fp
    assert subscription.last_run_at is not None
    assert subscription.last_run_at >= first_run
    # First tick sent; second did not.
    assert len(stub_sender) == 1


def test_chart_hash_change_triggers_send(
    db_setup: Any,
    subscription: Any,
    stub_chart_executor: list[list[Any]],
    stub_sender: list[dict[str, Any]],
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """When chart rows change between ticks, the second tick must
    re-fire and the fingerprint must differ."""
    monkeypatch.setattr(
        "app.config.settings.generated_reports_dir", tmp_path
    )
    _execute_dashboard_subscription(int(subscription.id))
    db, _ = db_setup
    db.refresh(subscription)
    first_fp = subscription.last_fingerprint
    assert len(stub_sender) == 1

    # Mutate the chart row payload and run again.
    stub_chart_executor.clear()
    stub_chart_executor.append(["x", 2])

    _execute_dashboard_subscription(int(subscription.id))
    db.refresh(subscription)
    assert subscription.last_fingerprint != first_fp
    assert len(stub_sender) == 2


def test_text_item_does_not_participate_in_dedup(
    db_setup: Any,
    subscription: Any,
    stub_chart_executor: Any,
    stub_sender: list[dict[str, Any]],
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Editing a text item between ticks must NOT trigger a send —
    text is static and explicitly excluded from the fingerprint."""
    monkeypatch.setattr(
        "app.config.settings.generated_reports_dir", tmp_path
    )
    _execute_dashboard_subscription(int(subscription.id))
    db, _ = db_setup
    db.refresh(subscription)
    first_fp = subscription.last_fingerprint

    # Mutate the text content of the first item.
    text_item = next(
        it for it in subscription.dashboard.items if it.item_type == "text"
    )
    text_item.text_content = "goodbye"
    db.commit()

    _execute_dashboard_subscription(int(subscription.id))
    db.refresh(subscription)
    assert subscription.last_fingerprint == first_fp
    assert len(stub_sender) == 1  # second tick was a no-op


# ----------------- CRUD tests -----------------


def test_create_subscription_persists_row(
    db_setup: Any,
    dashboard_with_items: Any,
) -> None:
    db, user = db_setup
    sub = create_subscription(
        db=db,
        owner_user_id=int(user.id),
        dashboard_id=int(dashboard_with_items.id),
        cron_expression=CRON_HOURLY,
        parameters={"x": 1},
        notification_config=None,
    )
    try:
        assert sub.id is not None
        assert sub.owner_user_id == int(user.id)
        assert sub.dashboard_id == int(dashboard_with_items.id)
        assert sub.cron_expression == CRON_HOURLY
        assert sub.parameters == {"x": 1}
        assert sub.is_active is True
        assert sub.last_fingerprint is None  # first run baseline
    finally:
        sid2 = int(sub.id) if sub.id is not None else 0
        _safe_remove(f"dsub_{sid2}")
        existing = db.get(DashboardSubscription, sid2)
        if existing is not None:
            db.delete(existing)
            db.commit()
        db.close()


def test_get_subscription_filters_by_owner(
    db_setup: Any,
    subscription: Any,
) -> None:
    db, user = db_setup
    found = get_subscription(db, int(subscription.id), int(user.id))
    assert found is not None
    # Wrong owner id → None (mirrors the report-subscription contract).
    assert get_subscription(db, int(subscription.id), int(user.id) + 999) is None
    # Bogus id → None.
    assert get_subscription(db, 99_999_999, int(user.id)) is None


def test_update_subscription_pause_unschedules(
    db_setup: Any,
    subscription: Any,
) -> None:
    db, _ = db_setup
    sid = int(subscription.id)
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is not None

    update_subscription(db, subscription, is_active=False)
    assert subscription.is_active is False
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is None

    update_subscription(db, subscription, is_active=True)
    assert subscription.is_active is True
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is not None


def test_delete_subscription_drops_job(
    db_setup: Any,
    subscription: Any,
) -> None:
    db, _ = db_setup
    sid = int(subscription.id)
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is not None
    delete_subscription(db, subscription)
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is None
    # ``delete_subscription`` already committed the row delete; nothing
    # else to clean here.


# ----------------- reconcile test -----------------


def test_sync_reconcile_loads_active_jobs(
    db_setup: Any,
    subscription: Any,
) -> None:
    """The sidecar calls ``sync_dashboard_subscriptions_with_database``
    on a periodic tick — the function must reinstall the APScheduler
    job for every active row, and prune orphan jobs whose rows have
    gone away."""
    db, _ = db_setup
    sid = int(subscription.id)
    # Sanity: the create path already added the job.
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is not None

    # Drop the job out-of-band to simulate a scheduler restart; the
    # reconciler must put it back.
    _safe_remove(f"dsub_{sid}")
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is None

    sync_dashboard_subscriptions_with_database(db)
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is not None

    # Pause the subscription and re-sync — the orphan job should be
    # pruned on the next reconcile.
    update_subscription(db, subscription, is_active=False)
    # Simulate a desynced scheduler that still has the job in memory.
    sync_dashboard_subscriptions_with_database(db)
    assert get_scheduler().scheduler.get_job(f"dsub_{sid}") is None
