# Contributing to iSee Workbench

This project uses a mix of manual commands (Makefile) and automated hooks
(pre-commit) to keep quality high. CI is the final gate.

## Quick reference

```bash
# Run dev servers
make dev-backend        # FastAPI on :8000
make dev-frontend       # Vite on :5173
make dev-scheduler      # Sidecar when SCHEDULER_DISABLED=true on web

# Verify before pushing
make test-fast          # pytest
make lint               # ruff + eslint
make typecheck          # mypy + tsc
make build              # vite production build
```

## Pre-commit hooks (optional but recommended)

The repo ships `.pre-commit-config.yaml`. To enable:

```bash
pip install pre-commit
pre-commit install
```

After install, every `git commit` will:

- Strip trailing whitespace and fix EOF newlines
- Run `ruff --fix` and `ruff format` on staged backend files
- Run `eslint --fix` on staged frontend files

Heavy checks (mypy, tsc, full pytest) live in CI and are **not** run on
commit. Run them locally with `make typecheck && make test-fast` before
pushing.

Skip a hook in an emergency:

```bash
git commit --no-verify
```

## Project layout

See `docs/IMPROVEMENT_PLAN.md` for the high-level roadmap and
`docs/ARCHITECTURE.md` for the design notes. Configuration is documented
in `backend/.env.example`.

## Coding conventions

The codebase is enforced by `ruff` and `eslint`; you mostly just need to
follow what they tell you. Beyond that:

- Write code that reads like the surrounding code; if you're about to
  add a "smart" abstraction for a one-off, don't.
- Comments only when the logic is non-obvious; the type signature is
  usually enough.
- Tests travel with the code they cover; new router/service code should
  come with a test that exercises the happy path and at least one sad path.
- Use the existing `formatError`, `RateLimiter`, `validate_select_only`,
  and other helpers — see the import list of any sibling file for the
  local vocabulary.
