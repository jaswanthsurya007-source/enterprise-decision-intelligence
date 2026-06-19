# EDIS L7 Governance service image.
#
# Build context is the REPO ROOT (docker-compose sets
# `context: .  dockerfile: services/governance/Dockerfile`) so the sibling local
# packages are available to `pip install -e` in dependency order:
#   edis-contracts -> edis-platform -> edis-governance-sdk -> this service.
# Those siblings are NEVER PyPI deps (the service pyproject lists third-party only);
# editable installs are how cross-package imports resolve at runtime.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Minimal build deps (asyncpg/greenlet wheels are prebuilt, but keep gcc for safety).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip

# --- shared libs first (dependency order is load-bearing) ---
COPY libs/edis-contracts /app/libs/edis-contracts
COPY libs/edis-platform /app/libs/edis-platform
COPY libs/edis-governance-sdk /app/libs/edis-governance-sdk
RUN pip install -e /app/libs/edis-contracts \
    && pip install -e /app/libs/edis-platform \
    && pip install -e /app/libs/edis-governance-sdk

# --- the governance service ---
COPY services/governance /app/services/governance
RUN pip install -e /app/services/governance

WORKDIR /app/services/governance

EXPOSE 8000

# Serve the FastAPI app. Consumers (audit/lineage) launch in the app lifespan.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
