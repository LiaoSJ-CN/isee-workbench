# iSee Workbench — top-level command surface.
# Run `make help` for the available targets.

BACKEND_DIR := backend
FRONTEND_DIR := frontend
PYTHON := cd $(BACKEND_DIR) && source .venv/bin/activate &&

.PHONY: help dev-backend dev-frontend dev-scheduler test test-fast lint \
        typecheck build format clean docker-up docker-down

help: ## Show this help (default target)
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------
# Dev workflow
# ----------------------------------------------------------------------

dev-backend: ## Run FastAPI dev server on :8000
	$(PYTHON) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run Vite dev server on :5173
	cd $(FRONTEND_DIR) && npm run dev

dev-scheduler: ## Run scheduler sidecar (when SCHEDULER_DISABLED=true on web)
	$(PYTHON) python -m app.scheduler_runner

# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------

test: ## Run backend pytest suite (verbose)
	$(PYTHON) pytest

test-fast: ## Run backend pytest in quiet mode (CI-style)
	$(PYTHON) pytest -q

test-cov: ## Run pytest with coverage report (>=70% required)
	$(PYTHON) pytest --cov=app --cov-report=term-missing --cov-fail-under=70

lint: ## Run ruff (backend) + eslint (frontend)
	$(PYTHON) ruff check .
	cd $(FRONTEND_DIR) && npm run lint

typecheck: ## Run mypy (backend) + tsc (frontend)
	$(PYTHON) mypy app
	cd $(FRONTEND_DIR) && npx tsc --noEmit

build: ## Build frontend production bundle
	cd $(FRONTEND_DIR) && npm run build

# ----------------------------------------------------------------------
# Maintenance
# ----------------------------------------------------------------------

format: ## Auto-format backend with ruff
	$(PYTHON) ruff check . --fix

clean: ## Remove build artifacts and Python caches
	rm -rf $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/node_modules/.vite
	find $(BACKEND_DIR) -type d -name __pycache__ -exec rm -rf {} +
	find $(BACKEND_DIR) -type d -name .pytest_cache -exec rm -rf {} +
	find $(BACKEND_DIR) -type d -name .ruff_cache -exec rm -rf {} +
	find $(BACKEND_DIR) -type d -name .mypy_cache -exec rm -rf {} +

# ----------------------------------------------------------------------
# Docker
# ----------------------------------------------------------------------

docker-up: ## Bring up the full stack via docker-compose
	docker compose up -d

docker-up-scheduler: ## Bring up the stack including the scheduler sidecar
	docker compose --profile scheduler up -d

docker-down: ## Tear down the docker-compose stack
	docker compose down
