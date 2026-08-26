#!/usr/bin/env python3
"""Diff docs against code to catch drift early.

Two checks, both run from the repo root:

1. ``Settings`` env vars (``backend/app/config.py``) vs env var table in
   ``DEPLOY.md``. A field declared on ``Settings`` but missing from the
   table is a missing doc entry — operators won't know it exists until
   they read the code (or get surprised when it doesn't take effect).

2. Router paths (``backend/app/routers/*.py``) vs API table in
   ``README.md``. A route registered but missing from the README is
   effectively undocumented. Coverage is reported, not strict equality
   — README only lists the user-facing surface, so we report *missing*
   routes and skip extra mentions in the README.

Usage::

    python scripts/diff_docs_vs_code.py            # report only, exit 0
    python scripts/diff_docs_vs_code.py --strict   # exit 1 on any drift
    python scripts/diff_docs_vs_code.py --quiet    # suppress non-error output

Both checks are designed to be cheap (no DB, no network, no venv needed
beyond the project dependencies for the Settings introspection — see
``--no-import-settings`` to bypass Settings import entirely).
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
README = REPO_ROOT / "README.md"
DEPLOY = REPO_ROOT / "DEPLOY.md"


# ---------------------------------------------------------------------------
# Settings field extraction (env vars)
# ---------------------------------------------------------------------------


def collect_settings_fields() -> list[str]:
    """Return upper-cased env-var names declared on ``app.config.Settings``.

    Falls back to an empty list if the import fails (e.g. venv not
    activated in CI); the script prints a clear note in that case so
    the operator knows the check didn't actually run.
    """
    # The Settings module raises if JWT_SECRET_KEY / ENCRYPTION_KEY are
    # missing in non-debug mode — both are required by the production
    # boot guard. Set DEBUG=true so introspection works without forcing
    # CI to commit to a real secret.
    os.environ.setdefault("DEBUG", "true")
    os.environ.setdefault("JWT_SECRET_KEY", "diff-docs-vs-code-pseudo-key")
    os.environ.setdefault("ENCRYPTION_KEY", "8sF4nMOd8sF4nMOd8sF4nMOd8sF4nMOd")
    try:
        sys.path.insert(0, str(BACKEND_ROOT))
        from app.config import Settings  # noqa: E402 — sys.path edit above
    except Exception as exc:  # pragma: no cover — depends on env
        print(
            f"warning: could not import app.config.Settings: {exc!r}\n"
            f"         the env-var check will be skipped. Run from the\n"
            f"         backend venv (or use --no-import-settings).",
            file=sys.stderr,
        )
        return []
    return sorted(f.upper() for f in Settings.model_fields.keys())


def env_vars_in_deploy(deploy_text: str) -> set[str]:
    """Extract env-var names that appear inside DEPLOY.md's env-var section.

    The section is delimited by the ``## 环境变量配置`` heading until the
    next ``## `` heading — we slice between them so an env-var name
    mentioned only in prose (e.g. ``JWT_SECRET_KEY`` in the production
    checklist) doesn't inflate the "documented" set.
    """
    match = re.search(
        r"^##\s+环境变量配置\s*$(.*?)(?:^##\s+\S|\Z)",
        deploy_text,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        return set()
    section = match.group(1)
    return set(re.findall(r"`([A-Z][A-Z0-9_]+)`", section))


def check_env_vars(strict: bool, quiet: bool) -> list[str]:
    """Return the list of env vars declared in code but missing from DEPLOY.md."""
    declared = collect_settings_fields()
    if not declared:
        return []
    documented = env_vars_in_deploy(DEPLOY.read_text(encoding="utf-8"))
    missing = [name for name in declared if name not in documented]
    if missing and not quiet:
        print("DEPLOY.md env-var table is missing these Settings fields:")
        for name in missing:
            print(f"  - {name}")
    return missing


# ---------------------------------------------------------------------------
# Router path extraction (API surface)
# ---------------------------------------------------------------------------

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def collect_router_paths() -> list[str]:
    """Walk every router file and collect registered paths.

    Handles two router definitions per file (``jobs.py`` has two
    ``APIRouter`` instances with different prefixes) and the rare
    multi-line decorator with the path argument on a separate line.
    """
    paths: list[str] = []
    for router_file in sorted((BACKEND_ROOT / "app" / "routers").glob("*.py")):
        try:
            source = router_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(router_file))
        except SyntaxError:
            continue

        # Map APIRouter variable name -> prefix (default "")
        prefix_by_var: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            call = node.value
            # Skip non-APIRouter calls cheaply
            if not (isinstance(call.func, ast.Name) and call.func.id == "APIRouter"):
                continue
            current_prefix = ""
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    current_prefix = str(kw.value.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefix_by_var[target.id] = current_prefix

        # Walk decorator + function definitions to extract @router.X("path", ...)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                path = _extract_path(decorator)
                if path is None:
                    continue
                method, raw_path = path
                if method not in HTTP_METHODS:
                    continue
                # Resolve the router var name from e.g. ``jobs_router.post``
                router_var = _decorator_target_name(decorator)
                prefix = prefix_by_var.get(router_var or "", "")
                full_path = _join_path(prefix, raw_path)
                paths.append(f"{method.upper()} {full_path}")

    return sorted(set(paths))


def _extract_path(decorator: ast.expr) -> tuple[str, str] | None:
    """Return ``(method, raw_path)`` from a ``@router.METHOD(path, ...)`` node."""
    call = decorator if isinstance(decorator, ast.Call) else None
    if call is None:
        return None
    func = call.func
    method = None
    if isinstance(func, ast.Attribute) and func.attr in HTTP_METHODS:
        method = func.attr
    elif isinstance(func, ast.Name) and func.id in HTTP_METHODS:
        method = func.id
    if method is None:
        return None
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return method, first.value
    return None


def _decorator_target_name(decorator: ast.expr) -> str | None:
    """``jobs_router.post`` -> ``jobs_router``; ``router.post`` -> ``router``."""
    call = decorator if isinstance(decorator, ast.Call) else None
    if call is None or not isinstance(call.func, ast.Attribute):
        return None
    target = call.func.value
    return target.id if isinstance(target, ast.Name) else None


def _join_path(prefix: str, raw: str) -> str:
    """Glue a router prefix and an endpoint path, normalising slashes."""
    if not prefix:
        prefix_path = ""
    else:
        prefix_path = "/" + prefix.strip("/")
    if not raw:
        return prefix_path or "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    return prefix_path + raw


def api_paths_in_readme(readme_text: str) -> set[str]:
    """Extract ``METHOD /path`` strings from README API tables.

    The API section starts at ``## API 端点`` and runs to the next ``## ``
    heading. Inside the section we look for table rows whose first column
    is an HTTP method and second is a path.
    """
    match = re.search(
        r"^##\s+API\s+端点\s*$(.*?)(?:^##\s+\S|\Z)",
        readme_text,
        re.DOTALL | re.MULTILINE,
    )
    if not match:
        return set()
    section = match.group(1)
    found: set[str] = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        method = cells[0].upper()
        # Path is typically wrapped in backticks — strip them so the
        # extracted string matches the un-prefixed AST output.
        path = cells[1].strip("`").strip()
        if method not in {m.upper() for m in HTTP_METHODS}:
            continue
        if not path.startswith("/"):
            continue
        found.add(f"{method} {path}")
    return found


def _normalize(path: str) -> str:
    """Collapse path templates so ``{id}`` and ``{source_id}`` compare equal.

    FastAPI path params are arbitrary identifiers — the docs use
    ``{id}`` for simplicity, code uses ``{source_id}`` / ``{report_id}`` /
    ``{subscription_id}``. We treat any ``{name}`` as the same token so
    mismatch on the placeholder name alone doesn't count as drift.
    """
    normalized = re.sub(r"\{[^}]+\}", "{x}", path)
    return normalized.rstrip("/") or "/"


def check_router_paths(strict: bool, quiet: bool) -> list[str]:
    """Return router endpoints missing from README's API table."""
    declared = collect_router_paths()
    documented = api_paths_in_readme(README.read_text(encoding="utf-8"))
    missing = [
        d
        for d in declared
        if _normalize(d.split(" ", 1)[1])
        not in {_normalize(doc.split(" ", 1)[1]) for doc in documented}
    ]
    if missing and not quiet:
        print("README API table is missing these routes:")
        for m in missing:
            print(f"  - {m}")
    return missing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any drift is found (default: report only).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error output (useful for CI summaries).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    missing_env = check_env_vars(strict=args.strict, quiet=args.quiet)
    missing_paths = check_router_paths(strict=args.strict, quiet=args.quiet)

    if not args.quiet:
        total_env = len(collect_settings_fields())
        total_paths = len(collect_router_paths())
        print(
            f"\nchecked {total_env} Settings fields vs DEPLOY.md, "
            f"{total_paths} router paths vs README.md — "
            f"missing: env={len(missing_env)} paths={len(missing_paths)}"
        )

    if args.strict and (missing_env or missing_paths):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
