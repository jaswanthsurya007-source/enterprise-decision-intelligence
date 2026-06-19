# EDIS — developer entrypoint.
#
# Targets: install up down logs seed demo test lint fmt migrate (+ help, ts).
#
# Cross-platform notes:
#   * Primary target is bash (Linux/macOS, and Git Bash / WSL on Windows).
#   * On Windows use Git Bash or WSL to run `make`. In plain PowerShell/cmd there
#     is no `make`; the equivalent one-liners are documented under each target and
#     in the README. `docker compose` and `pip` invocations are identical there.
#   * PYTHON / PIP can be overridden:  make install PYTHON=python
#
# Local-package install order is LOAD-BEARING: contracts -> platform -> gov-sdk
# -> services. Sibling packages are NEVER PyPI deps; they are wired here with
# `pip install -e` so cross-package imports resolve.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Use the repo virtualenv if present, else fall back to system python.
PYTHON ?= python
PIP    ?= $(PYTHON) -m pip
COMPOSE ?= docker compose

# Dependency-ordered local packages. Services are appended only if present
# (they are built by later work units), so `make install` works from day one.
LIBS := libs/edis-contracts libs/edis-platform libs/edis-governance-sdk
SERVICES := apps/ingestion services/integration services/intelligence \
            services/decision services/copilot services/governance services/gateway

# Pure-python (no Docker) test selection.
PYTEST_ARGS ?= -m "not integration"

.PHONY: help
help: ## Show this help.
	@echo "EDIS make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Editable-install all libs (in dependency order) + any present services, with dev extras.
	$(PIP) install --upgrade pip
	@for pkg in $(LIBS); do \
		echo ">>> pip install -e $$pkg[dev]"; \
		$(PIP) install -e "$$pkg[dev]" || $(PIP) install -e "$$pkg" || exit 1; \
	done
	@for svc in $(SERVICES); do \
		if [ -f "$$svc/pyproject.toml" ]; then \
			echo ">>> pip install -e $$svc"; \
			$(PIP) install -e "$$svc[dev]" || $(PIP) install -e "$$svc" || exit 1; \
		else \
			echo "--- skip $$svc (not built yet)"; \
		fi; \
	done
	@echo "install complete."

.PHONY: up
up: ## Bring up the dev infra (postgres, redis, redpanda, otel, prometheus, grafana).
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d
	@echo "infra up. console=http://localhost:8080  grafana=http://localhost:3000  prometheus=http://localhost:9090"

.PHONY: up-apps
up-apps: ## Bring up infra + EDIS application services (requires their images/Dockerfiles to exist).
	@test -f .env || cp .env.example .env
	$(COMPOSE) --profile apps up -d --build

.PHONY: down
down: ## Stop and remove containers (keeps named volumes).
	$(COMPOSE) down

.PHONY: down-v
down-v: ## Stop and remove containers AND volumes (wipes data).
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all compose services (SVC=postgres to scope).
	$(COMPOSE) logs -f --tail=100 $(SVC)

.PHONY: migrate
migrate: ## Run Alembic migrations for every service that defines them (D1+). No-op until they exist.
	@echo "Running migrations in dependency order..."
	@for svc in services/governance services/integration apps/ingestion services/intelligence services/decision; do \
		if [ -f "$$svc/alembic.ini" ]; then \
			echo ">>> alembic upgrade head ($$svc)"; \
			( cd "$$svc" && $(PYTHON) -m alembic upgrade head ) || exit 1; \
		else \
			echo "--- skip $$svc (no alembic.ini yet)"; \
		fi; \
	done

.PHONY: seed
seed: ## Seed tenants, roles, calibration prior, and 90-day history (scripts/seed_demo.py — Z1).
	@if [ -f scripts/seed_demo.py ]; then \
		$(PYTHON) scripts/seed_demo.py; \
	else \
		echo "scripts/seed_demo.py not built yet (work unit Z1)."; \
	fi

.PHONY: demo
demo: ## Run the revenue_drop_emea scenario and drive the full chain (Z1).
	@if [ -f scripts/seed_demo.py ]; then \
		$(PYTHON) scripts/seed_demo.py --demo revenue_drop_emea; \
	else \
		echo "demo orchestration not built yet (work unit Z1)."; \
	fi

.PHONY: test
test: ## Run pure-python tests (no Docker; integration-marked tests are skipped).
	$(PYTHON) -m pytest $(PYTEST_ARGS)

.PHONY: test-integration
test-integration: ## Run the Docker-backed integration tests (requires `make up`).
	$(PYTHON) -m pytest -m integration

.PHONY: lint
lint: ## Ruff lint + black --check + mypy on the libs.
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .
	$(PYTHON) -m mypy libs/edis-contracts/edis_contracts libs/edis-platform/edis_platform libs/edis-governance-sdk/edis_gov_sdk

.PHONY: fmt
fmt: ## Auto-format: ruff --fix (imports) + black.
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m black .

.PHONY: ts
ts: ## Build the TypeScript Zod contracts + run the drift check (F6).
	@if [ -f libs/edis-ts-contracts/package.json ]; then \
		( cd libs/edis-ts-contracts && npm ci && npm run build && npm run check:drift ); \
	else \
		echo "libs/edis-ts-contracts not built yet (work unit F6)."; \
	fi

.PHONY: precommit
precommit: ## Install and run pre-commit on all files.
	$(PYTHON) -m pre_commit install
	$(PYTHON) -m pre_commit run --all-files

.PHONY: clean
clean: ## Remove caches and build artifacts.
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
