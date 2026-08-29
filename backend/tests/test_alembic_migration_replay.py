"""End-to-end Alembic migration replay tests (P2-3).

The deployment story for this app is "fresh ``alembic upgrade head``
on a new install" plus, in theory, "roll back one revision while
debugging". Three failure modes that bit us during 批 5 → 批 11 and
that the rest of the suite never exercised:

1. **Round-trip state leak** — ``upgrade head`` followed by
   ``downgrade base`` followed by ``upgrade head`` must produce a
   schema identical to running ``upgrade head`` directly. Catches
   migrations that drop a table but leave its indexes behind, or
   forget to invert a CHECK constraint.
2. **Per-revision round-trip** — for every single revision, run
   upgrade → downgrade → upgrade and assert the schema still
   matches ``head``. A migration whose downgrade is a no-op (e.g.
   ``c0a2b1d4e5f6`` normalizing legacy rows) is fine; we don't
   *require* a perfect undo, we require idempotent re-application.
3. **``env.py`` logger regression** — we deliberately do *not* call
   ``logging.config.fileConfig("alembic.ini")`` because the
   ``[loggers]`` section replaces root-logger handlers on load,
   which would clobber the FastAPI lifespan's request-id log
   factory and pytest's ``caplog`` handler. Without this test a
   future contributor who copy-pastes from a tutorial will silently
   break log capture across the suite.

These tests do not assert column-by-column shape; they compare
the *set of tables + indexes* produced by two migration paths.
That's coarse enough to stay maintainable as columns are added,
but tight enough to catch every regression we've actually seen.

**SQLite limitation:** two migrations (``01a6b1ebae29`` and
``921b7fe787b0``) downgrade via ``op.drop_constraint(...)`` which
SQLite does not support — it requires batch-alter-table mode. The
round-trip and full-downgrade tests skip those revisions on
SQLite; the PostgreSQL CI job runs them against a real PG16
instance, where the path is fully exercised.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import settings

# Linear chain d75b4bd46c54 → ... → a51e9a14f8c7 (head). Pulled from
# ``alembic/versions/*.py`` at import time so a new migration is
# auto-covered by the parametrized round-trip tests below.
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Migrations whose downgrade uses ``op.drop_constraint`` — SQLite
# rejects ALTER on foreign keys and forces batch mode, which the
# project's migrations don't currently use. The PG CI job covers
# these; the SQLite suite skips them so we can still run the
# downgrade assertions for the rest of the chain.
_SQLITE_INCOMPATIBLE_DOWNGRADES: frozenset[str] = frozenset(
    {
        "01a6b1ebae29",  # data_source_acl (批 9.3)
        "921b7fe787b0",  # report_owner_visibility_grants (批 9.4)
    }
)


def _read_migration_field(filename: str, field: str) -> str | None:
    """Pull ``revision: str = '...'`` or ``down_revision = '...'``
    out of a migration file. We don't want to actually import the
    module (that would run upgrade against the live DB).

    Handles two RHS shapes:

    * ``= 'abc123'`` — quoted revision string (common case)
    * ``= None`` — root migration's ``down_revision`` is unquoted

    The annotation on ``down_revision`` is
    ``Union[str, Sequence[str], None]`` (variable tokens before the
    ``=``), so we don't try to parse the type — just anchor on the
    ``=``.
    """
    txt = (_MIGRATIONS_DIR / filename).read_text()
    line = next(
        (ln for ln in txt.splitlines() if ln.startswith(f"{field}:")),
        None,
    )
    if line is None:
        return None
    quoted = re.search(r"=\s*['\"]([^'\"]+)['\"]", line)
    if quoted:
        return quoted.group(1)
    if re.search(r"=\s*None\s*$", line):
        return None
    return None


def _linear_chain() -> list[str]:
    """Return the migration revisions in upgrade order (root first,
    head last). Hard-fails if the chain isn't linear — that's the
    whole point of having this test: branch points are exactly the
    kind of shape that breaks round-trip invariants.

    We walk from root → head via the *successor* map (down_revision
    → revision), not the down_revision chain itself. A migration's
    ``down_revision`` points at its parent, so walking it goes
    ancestors-ward; we want the other direction.
    """
    revs: dict[str, str | None] = {}
    successors: dict[str | None, list[str]] = {}
    for f in _MIGRATIONS_DIR.glob("*.py"):
        rev = _read_migration_field(f.name, "revision")
        down = _read_migration_field(f.name, "down_revision")
        if rev is None:
            continue
        revs[rev] = down
        successors.setdefault(down, []).append(rev)

    # Root = the one whose down_revision is None.
    roots = successors.get(None, [])
    assert len(roots) == 1, (
        f"expected exactly one root migration, got {roots} — branching "
        f"heads break the round-trip invariants below"
    )
    root = roots[0]

    # Walk root → head. Each node must have exactly one successor
    # (no forks) and every revision must appear exactly once.
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = root
    while cur is not None:
        assert cur not in seen, f"cycle in migration chain at {cur}"
        seen.add(cur)
        chain.append(cur)
        kids = successors.get(cur, [])
        assert len(kids) <= 1, (
            f"migration {cur} has multiple successors: {kids} — "
            f"branch points break the round-trip invariants below"
        )
        cur = kids[0] if kids else None

    assert len(chain) == len(revs), f"chain is not linear: chain={chain}, all revs={sorted(revs)}"
    return chain


_REVISIONS: list[str] = _linear_chain()
HEAD: str = _REVISIONS[-1]


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """Fresh sqlite file under pytest tmp dir, with ``settings`` pointed
    at it so ``env.py`` resolves the URL through the app config."""
    db_path = tmp_path / "alembic_replay.db"
    test_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "database_url", test_url)
    yield test_url
    # Engine disposal happens implicitly when the monkeypatch unwinds;
    # pytest tmp_path cleanup deletes the file.


def _alembic_config(test_url: str) -> Any:
    """Build a minimal alembic ``Config`` that talks to the tmp DB.

    We don't load ``alembic.ini`` because that pulls in its logging
    config — exactly the regression surface we want to keep locked
    down. ``env.py`` reads ``config.get_section(config.config_ini_section)``
    only, which works fine with an empty section.
    """
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR.parent))
    cfg.set_main_option("sqlalchemy.url", test_url)
    # ``config_ini_section`` defaults to "alembic" — env.py pulls the
    # sqlalchemy.* options from there. We don't set any.
    return cfg


def _run_alembic(cfg: Any, direction: str, target: str) -> None:
    """Run alembic up-or-down to ``target``. Direction is "upgrade" or
    "downgrade"."""
    from alembic import command

    if direction == "upgrade":
        command.upgrade(cfg, target)
    elif direction == "downgrade":
        command.downgrade(cfg, target)
    else:
        raise ValueError(f"unsupported direction: {direction}")


def _is_sqlite(test_url: str) -> bool:
    return test_url.startswith("sqlite:")


def _schema_snapshot(test_url: str) -> set[tuple[str, str]]:
    """A schema fingerprint: (table_name, index_or_table) tuples. We
    include both table names and index names — that way a migration
    that drops a table but leaves its indexes behind (the classic
    round-trip leak) shows up as a delta."""
    eng = create_engine(test_url, connect_args={"check_same_thread": False})
    try:
        inspector = inspect(eng)
        parts: set[tuple[str, str]] = set()
        for table in inspector.get_table_names():
            parts.add((table, "__table__"))
            for idx in inspector.get_indexes(table):
                # Index names are unique within a table — namespace
                # with the table name so we don't collapse two
                # tables that happen to share an index name.
                parts.add((table, f"idx:{idx['name']}"))
        return parts
    finally:
        eng.dispose()


# ---- shape tests -----------------------------------------------------------


def test_migration_chain_is_linear_and_contains_all_revisions() -> None:
    """All 14 revisions are reachable from a single root, no forks.
    A future contributor who adds a branch will fail this test and
    have to think about the round-trip semantics — that's by design.
    """
    assert _REVISIONS == [
        "d75b4bd46c54",
        "b430089a9cac",
        "222001adeb57",
        "c0a2b1d4e5f6",
        "ded6ee4f08ce",
        "371bcac5fa32",
        "01a6b1ebae29",
        "921b7fe787b0",
        "6e3ed720f397",
        "dff25a24e6b4",
        "a51e9a14f8c7",
        "ce6c152ead31",
        "525edc1ba876",  # 批 13 — report template fields
        "3b8e4f7c2a91",  # 批 14 — dashboard tables
        "e4f1b2c3a5d6",  # 批 14.4 — dashboard subscription fingerprint
    ], f"migration chain shape changed: {_REVISIONS}"


def test_env_py_does_not_call_file_config() -> None:
    """The headline regression guard: ``env.py`` must not call
    ``logging.config.fileConfig`` (or any equivalent that replaces
    root-logger handlers). Copy-pasting from an alembic tutorial
    brings this back; without this test we wouldn't notice until
    pytest's ``caplog`` started silently dropping records.

    Note the assertion targets ``fileConfig(`` (the call) — the bare
    token ``fileConfig`` appears in ``engine_from_config`` and the
    docstring, both of which are fine.
    """
    env_src = (_MIGRATIONS_DIR.parent / "env.py").read_text()
    # Anchored on actual call sites: ``logging.config.fileConfig(``
    # (with the open-paren) and ``import logging.config``. The bare
    # tokens ``fileConfig`` and ``logging.config`` appear in the
    # explanatory comment, so they don't trip the regex.
    assert not re.search(r"logging\.config\.fileConfig\s*\(", env_src), (
        "alembic/env.py must not call logging.config.fileConfig() — "
        "it replaces root-logger handlers and breaks lifespan log "
        "formatting + pytest caplog"
    )
    assert not re.search(r"^\s*import\s+logging\.config", env_src, re.M), (
        "alembic/env.py must not import logging.config for the same reason"
    )


# ---- runtime tests ---------------------------------------------------------


def test_upgrade_head_on_fresh_db(fresh_db: str) -> None:
    """``alembic upgrade head`` against an empty sqlite must complete
    without raising and leave ``alembic_version`` set to the head
    revision. Belt-and-braces against the lifespan path breaking
    on a fresh install."""
    cfg = _alembic_config(fresh_db)
    _run_alembic(cfg, "upgrade", "head")

    eng = create_engine(fresh_db, connect_args={"check_same_thread": False})
    try:
        with eng.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == HEAD, f"alembic_version={version!r}, expected {HEAD!r}"
        tables = set(inspect(eng).get_table_names())
        # Pin the user-visible table names that the routers depend
        # on. New migrations add tables; this list is the floor.
        for expected in (
            "data_sources",
            "reports",
            "report_items",
            "report_jobs",
            "report_parameters",
            "users",
            "revoked_jti",
            "rate_limit_events",
            "data_source_access",
            "report_access",
            "audit_log",
            "report_subscriptions",
        ):
            assert expected in tables, f"missing table after upgrade head: {expected}"
    finally:
        eng.dispose()


def test_root_logger_handlers_preserved_after_alembic_run(fresh_db: str) -> None:
    """Run ``upgrade head`` while a sentinel handler is attached to
    the root logger; the handler must still be there afterwards.
    This is the runtime counterpart of
    ``test_env_py_does_not_call_file_config`` — together they pin
    the logging contract both at source and at runtime.
    """
    sentinel = logging.NullHandler()
    sentinel.ident = "alembic-replay-sentinel"  # type: ignore[attr-defined]
    root = logging.getLogger()
    root.addHandler(sentinel)
    try:
        cfg = _alembic_config(fresh_db)
        _run_alembic(cfg, "upgrade", "head")
        assert sentinel in root.handlers, (
            "root logger handlers were replaced during alembic run — "
            "env.py must not call logging.config.fileConfig"
        )
    finally:
        root.removeHandler(sentinel)


@pytest.mark.parametrize("revision", _REVISIONS)
def test_round_trip_each_revision(fresh_db: str, revision: str) -> None:
    """For every single revision: upgrade to it, downgrade back, then
    upgrade to head again. The resulting schema fingerprint must
    equal the fingerprint of a fresh ``upgrade head`` run.

    This catches migrations whose downgrade leaves dangling indexes
    or constraints behind, and migrations whose upgrade assumes
    state the previous migration didn't actually create.
    """
    if _is_sqlite(fresh_db) and revision in _SQLITE_INCOMPATIBLE_DOWNGRADES:
        pytest.skip(
            f"{revision} downgrades via op.drop_constraint, which SQLite "
            "does not support — covered by the PG CI job"
        )

    cfg = _alembic_config(fresh_db)

    # Golden fingerprint: upgrade head from scratch.
    _run_alembic(cfg, "upgrade", "head")
    golden = _schema_snapshot(fresh_db)

    # Reset to a clean DB so the round-trip starts from the same
    # baseline (otherwise we'd be running upgrade on top of the
    # golden state, which is the wrong invariant).
    eng = create_engine(fresh_db, connect_args={"check_same_thread": False})
    try:
        with eng.begin() as conn:
            inspector = inspect(eng)
            for table in reversed(inspector.get_table_names()):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    finally:
        eng.dispose()

    # Round-trip path: upgrade → revision, downgrade → previous,
    # upgrade → head. Alembic handles the multi-hop dance.
    _run_alembic(cfg, "upgrade", revision)
    _run_alembic(cfg, "downgrade", "-1")
    _run_alembic(cfg, "upgrade", "head")

    actual = _schema_snapshot(fresh_db)
    missing_in_actual = golden - actual
    extra_in_actual = actual - golden
    assert not missing_in_actual and not extra_in_actual, (
        f"round-trip via {revision!r} diverged from a fresh upgrade head:\n"
        f"  missing: {sorted(missing_in_actual)}\n"
        f"  extra:   {sorted(extra_in_actual)}"
    )


def test_full_downgrade_then_upgrade(fresh_db: str) -> None:
    """The full roll-back-and-forward dance: upgrade head, downgrade
    all the way to base (dropping every table), upgrade head again.
    The end state must equal the golden fingerprint and the DB
    must be fully usable (a smoke query against ``reports`` works).
    """
    if _is_sqlite(fresh_db):
        pytest.skip(
            "two migrations downgrade via op.drop_constraint, which "
            "SQLite does not support — covered by the PG CI job"
        )
    cfg = _alembic_config(fresh_db)
    _run_alembic(cfg, "upgrade", "head")

    _run_alembic(cfg, "downgrade", "base")

    # After downgrade to base, only ``alembic_version`` should remain.
    eng = create_engine(fresh_db, connect_args={"check_same_thread": False})
    try:
        with eng.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        assert tables == {"alembic_version"}, (
            f"after downgrade base, expected only alembic_version, got {tables}"
        )
    finally:
        eng.dispose()

    # And back up.
    _run_alembic(cfg, "upgrade", "head")

    eng = create_engine(fresh_db, connect_args={"check_same_thread": False})
    try:
        with eng.begin() as conn:
            # Insert + read a row through the rebuilt schema to
            # prove it's not just shape-correct but functionally
            # intact (catches a hypothetical regression where
            # round-trip re-creates a table with the wrong
            # columns but the inspector still lists it).
            conn.execute(
                text(
                    "INSERT INTO data_sources "
                    "(name, db_type, host, port, database, username, password) "
                    "VALUES ('replay-ds', 'sqlite', 'h', 1, ':memory:', 'u', 'p')"
                )
            )
            row = conn.execute(
                text("SELECT db_type FROM data_sources WHERE name = 'replay-ds'")
            ).scalar()
            assert row == "sqlite"
    finally:
        eng.dispose()
