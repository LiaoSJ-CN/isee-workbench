"""Smoke test for the module-level engine cache in report_generator.

Verifies:
- Repeated `_get_or_create_engine` calls for the same DataSource return the
  same Engine instance (no per-call ``create_engine`` churn).
- A second DataSource gets its own Engine (no cross-pollination).
- The cached engine actually works: a SELECT round-trips successfully.
- `evict_engine` removes the entry and the next call rebuilds.
- `ReportGenerator.__enter__` / `__exit__` reuse the cached engine instead
  of building + disposing on every call (the behaviour change that motivated
  this work).

Run: cd backend && source .venv/bin/activate && python scripts/smoke_engine_cache.py
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.services.report_generator import (
    ReportGenerator,
    _engine_cache,
    _get_or_create_engine,
    evict_engine,
)


def _fake_sqlite_source(source_id: int, db_path: str) -> SimpleNamespace:
    """Minimal DataSource stand-in: only the fields _get_or_create_engine
    and build_connection_url actually read (db_type, database, host, port,
    username, password)."""
    return SimpleNamespace(
        id=source_id,
        db_type="sqlite",
        host="",
        port=0,
        database=db_path,
        username="",
        password="",
    )


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        line = f"  {status}: {name}"
        if detail:
            line += f" — {detail}"
        print(line)
        if not condition:
            failures.append(name)

    # Use high ids so we don't collide with seeded data sources in app.db.
    ds_a_id = 90001
    ds_b_id = 90002

    with tempfile.TemporaryDirectory() as tmp:
        db_a = str(Path(tmp) / "a.db")
        db_b = str(Path(tmp) / "b.db")
        ds_a = _fake_sqlite_source(ds_a_id, db_a)
        ds_b = _fake_sqlite_source(ds_b_id, db_b)

        # Make sure no leftover entry from a prior failed run.
        evict_engine(ds_a_id)
        evict_engine(ds_b_id)

        # --- 1. Repeated lookups for the same DataSource share one engine ---
        eng_a_1 = _get_or_create_engine(ds_a)
        eng_a_2 = _get_or_create_engine(ds_a)
        eng_a_3 = _get_or_create_engine(ds_a)
        check(
            "same DataSource → same Engine instance across 3 calls",
            eng_a_1 is eng_a_2 is eng_a_3,
            detail=f"id(1)={id(eng_a_1)} id(2)={id(eng_a_2)} id(3)={id(eng_a_3)}",
        )
        check(
            "cache has exactly 1 entry for ds_a",
            ds_a_id in _engine_cache and len(_engine_cache) == 1,
        )

        # --- 2. Different DataSource gets its own Engine ---
        eng_b = _get_or_create_engine(ds_b)
        check(
            "different DataSource → different Engine instance",
            eng_b is not eng_a_1,
        )
        check(
            "cache has exactly 2 entries (one per source)",
            len(_engine_cache) == 2 and ds_a_id in _engine_cache and ds_b_id in _engine_cache,
        )

        # --- 3. Cached engine actually works (round-trip a SELECT) ---
        with eng_a_1.connect() as conn:
            conn.execute(text("CREATE TABLE t (x INTEGER)"))
            conn.execute(text("INSERT INTO t VALUES (1), (2), (3)"))
            conn.commit()
            rows = conn.execute(text("SELECT SUM(x) FROM t")).scalar()
        check(
            "cached engine runs queries correctly",
            rows == 6,
            detail=f"sum={rows}",
        )

        # --- 4. ReportGenerator reuses the cached engine, no dispose on exit ---
        gen = ReportGenerator(ds_a)
        gen.__enter__()
        engine_from_gen = gen.engine
        gen.__exit__(None, None, None)
        check(
            "ReportGenerator.__enter__ returns the cached engine",
            engine_from_gen is eng_a_1,
        )
        # Open + close the generator several more times — the engine must
        # remain the same object (the old behaviour disposed it on exit).
        ids_seen = {id(engine_from_gen)}
        for _ in range(4):
            g = ReportGenerator(ds_a).__enter__()
            ids_seen.add(id(g.engine))
            g.__exit__(None, None, None)
        check(
            "5 ReportGenerator open/close cycles reuse one engine",
            len(ids_seen) == 1,
            detail=f"saw {len(ids_seen)} distinct engine ids",
        )

        # --- 5. evict_engine drops the entry and triggers a rebuild ---
        evict_engine(ds_a_id)
        check("evict_engine removes ds_a from cache", ds_a_id not in _engine_cache)
        eng_a_new = _get_or_create_engine(ds_a)
        check(
            "after eviction, next lookup builds a fresh engine",
            eng_a_new is not eng_a_1,
            detail=f"new id={id(eng_a_new)} old id={id(eng_a_1)}",
        )
        check("cache contains the new engine for ds_a", _engine_cache[ds_a_id] is eng_a_new)

        # --- 6. evict_engine on an unknown id is a no-op ---
        before = set(_engine_cache.keys())
        evict_engine(999999)
        check(
            "evict_engine on unknown id is a no-op",
            set(_engine_cache.keys()) == before,
        )

        # Cleanup so this script is repeatable.
        evict_engine(ds_a_id)
        evict_engine(ds_b_id)

    if failures:
        print(f"\nFAIL — {len(failures)} check(s) failed: {failures}")
        return 1
    print("\nPASS — engine cache reuses engines, evicts correctly, and works end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
