# EDIS Integration (L2) — Database Migrations (D1)

This package owns the **Alembic migrations** for the integration layer's
system-of-record schema: the canonical unified data model plus the
`metric_observations` TimescaleDB hypertable. The integration *service code* (N1/N3)
is added later; this unit (D1) ships only the schema.

## What it creates (`0001_canonical`)

| Object | Shape | Notes |
|---|---|---|
| `canonical_customer` | SCD-2-shaped dimension | `valid_from/valid_to/is_current/version`; MVP always `is_current=true`, `valid_to=null`, `version=1` |
| `canonical_product` | SCD-2-shaped dimension | per `CanonicalProduct` contract |
| `canonical_order` | immutable sales fact | normalized to base currency (USD); FK → `canonical_customer` |
| `canonical_order_line` | order line | per `CanonicalOrderLine`; FK → `canonical_order` (CASCADE) |
| `ops_event` | immutable ops fact | feeds `error_rate` / `latency_p95` metrics |
| `customer_activity` | immutable activity fact | feeds `page_views` / sessions |
| `metric_observations` | **TimescaleDB hypertable** on `ts` | + `embedding vector(1024)` (voyage-3) pgvector column |
| `metric_observations_daily` | **daily continuous aggregate** | falls back to a plain VIEW without TimescaleDB |

Every table carries `tenant_id` (MVP isolation is application-level filtering;
**no RLS FORCE**). All timestamps are `TIMESTAMP WITH TIME ZONE` (UTC).

The columns match the contracts in
`libs/edis-contracts/edis_contracts/canonical.py` exactly — the integration ORM
models (N3) and any reader must conform to this schema.

## Running the migrations

Requires PostgreSQL with the **TimescaleDB** and **pgvector** extensions (the
compose image `timescale/timescaledb-ha:pg16.4-ts2.16.1-all` bundles both). The
migration is **guarded**: on a plain Postgres without those extensions, the core
tables still apply — `metric_observations` stays an ordinary table, the daily
rollup becomes a plain VIEW, and `embedding` degrades to `jsonb`.

```bash
# from this directory (services/integration/)
export EDIS_DATABASE_URL="postgresql+asyncpg://edis:edis@localhost:5432/edis"
alembic upgrade head
```

Or from the repo root via the Makefile (runs every service's migrations in
dependency order):

```bash
make migrate
```

`env.py` reads `EDIS_DATABASE_URL` (the same var `edis_platform.settings` uses)
and drives migrations through an **async** asyncpg engine; no connection is
opened at import time.

> **Docker note:** TimescaleDB/pgvector require a live Postgres, which is not
> available in every environment. The migration is written correct-by-construction;
> integration tests that actually apply it are marked `@pytest.mark.integration`
> and skip without Docker.
