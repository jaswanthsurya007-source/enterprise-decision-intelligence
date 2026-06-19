# EDIS Governance (L7) — Database Migrations (D1)

This package owns the **Alembic migrations** for the governance spine. The
governance *service code* (G1 audit/lineage consumers, explainability store, RBAC
admin API + seed) is added later; this unit (D1) ships only the schema.

## What it creates (`0001_governance`)

| Object | Shape | Notes |
|---|---|---|
| `audit_log` | **append-only TimescaleDB hypertable** on `occurred_at` | one column per `AuditEvent` + a `raw` jsonb copy; **idempotent on `audit_id`** (consumer dedupes via `ON CONFLICT DO NOTHING`) |
| `lineage_edge` | materialized lineage graph | `(run_id, tenant_id, src_type, src_id, dst_type, dst_id, stage, occurred_at)`; consumer fans a `LineageEvent`'s inputs × outputs into edges |
| `decision` | explainability record (`Decision` contract) | + `embedding vector(1024)` (voyage-3) for "similar past decisions" |
| `evidence` | immutable value snapshot (`Evidence` contract) | FK → `decision` (CASCADE) |
| `tenant` | control-plane | `(id, name)` |
| `app_role` | control-plane | RBAC role registry (viewer/analyst/operator/auditor/admin) |
| `permission` | control-plane | `(role, action, resource_type)`; mirrors `edis_platform.authz.rbac.ROLE_PERMISSIONS` |
| `calibration_prior` | control-plane | `(tenant_id, playbook_id, value, n)`; pre-seeded static prior, `n=0` in MVP |

Every table carries `tenant_id` (MVP isolation is application-level filtering;
**no RLS FORCE**). All timestamps are `TIMESTAMP WITH TIME ZONE` (UTC). Columns
match the contracts in `libs/edis-contracts/edis_contracts/{governance,security}.py`.

### audit_log idempotency note

A TimescaleDB hypertable's unique/primary-key constraint **must include the
partitioning column**. So under TimescaleDB the uniqueness is `(audit_id,
occurred_at)`; on a plain Postgres the PK is `audit_id` alone. Either way
`audit_id` is the logical idempotency key the consumer dedupes on.

## Running the migrations

Requires PostgreSQL with **TimescaleDB** and **pgvector** (the compose image
`timescale/timescaledb-ha:pg16.4-ts2.16.1-all` bundles both). The migration is
**guarded**: on a plain Postgres without those extensions the core tables still
apply — `audit_log` stays an ordinary table and `decision.embedding` degrades to
`jsonb`.

```bash
# from this directory (services/governance/)
export EDIS_DATABASE_URL="postgresql+asyncpg://edis:edis@localhost:5432/edis"
alembic upgrade head
```

Or from the repo root (runs every service's migrations in dependency order —
governance runs first):

```bash
make migrate
```

`env.py` (under `app/migrations/`) reads `EDIS_DATABASE_URL` and drives
migrations through an **async** asyncpg engine; no connection is opened at import
time.

> **Docker note:** TimescaleDB/pgvector require a live Postgres, not available in
> every environment. The migration is correct-by-construction; integration tests
> that actually apply it are marked `@pytest.mark.integration` and skip without
> Docker.
