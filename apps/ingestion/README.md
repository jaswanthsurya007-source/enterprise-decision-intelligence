# EDIS L1 — Data Ingestion (`apps/ingestion`)

The **edge of trust**: the only component permitted to accept untrusted source
data. It guarantees *structural validity, source fidelity, and at-least-once
delivery with idempotency* — but **not** semantic correctness across sources (that
is L2's job).

This package is a hatchling, src-layout app; the import name is `ingestion`. It
imports the shared platform SDK (`edis_platform`), contracts (`edis_contracts`) and
governance SDK (`edis_gov_sdk`) normally — they are installed editable and are
**never** listed as dependencies here.

## The pipeline (one code path, two ingress modes)

Per record, the edge-of-trust flow (`pipeline/engine.py::ingest_record`) is:

```
coerce/normalize source quirks            (pipeline/coerce.py)
  -> validate per-domain, extra="forbid"  (pipeline/validator.py)
  -> derive idempotency key (arch §4.1)   (pipeline/idempotency.py)
  -> idempotency guard (first-seen only)  (pipeline/idempotency.py)
  -> build IngestEnvelope (frozen)        (pipeline/envelope_builder.py)
  -> land in raw_events (outbox)          (storage/raw_writer.py)
  -> publish to edis.raw.<domain>.v1      (publish/publisher.py)
  -> emit AuditEvent(DATA_WRITE)          (publish/publisher.py via edis_gov_sdk)
```

Bad/invalid records become a `DLQRecord`, are **persisted** to `ingest_dlq` and
**published** to `edis.dlq.ingest.v1` with full error context (`pipeline/dlq.py`).
They never block the partition — the engine returns a `DLQ` outcome and moves on.

`ingest_record(domain, raw, *, tenant_id, source_system, ctx_sink, idem, writer)`
is the single public entry point. I2 (REST + control API) and I3 (simulator +
batch loader + CLI) reuse it, so the real-time and batch paths never drift.

## Idempotency

Key derivation follows arch §4.1 **exactly**:

| domain | key |
|---|---|
| sales | `f"sales:{tenant_id}:{source_system}:{order_id}"` |
| ops | `sha256(f"{tenant_id}|{service}|{event_ts}|{message}|{trace_id}")` |
| customer | `f"customer:{tenant_id}:{session_id}:{event}:{event_ts.timestamp()}"` |

A content-hash fallback is used when the natural id is null, so no record is ever
un-keyable. The guard (`IdempotencyStore`) has two backends, selected by
`EDIS_INGEST_IDEMPOTENCY_BACKEND`:

- `memory` (default) — in-process, **no infra**, used in unit tests so dedupe is
  testable without Redis.
- `redis` — atomic `SET key 1 NX EX <ttl>` (`SETNX`), shared across replicas.

`raw_events.idempotency_key` is **UNIQUE** — a DB-level dedupe backstop behind the
guard (a duplicate that races past the guard is absorbed by `ON CONFLICT DO
NOTHING` and reported as `DUPLICATE`, never re-published).

## Outbox

The MVP uses **publish-after-land**: the durable `raw_events` row is written
first, then the record is published and the row flipped `published=true`. If the
process or broker dies in between, `storage/relay.py::reconcile` re-reads rows
still `published=false`, rebuilds the envelope from the stored columns, and
republishes — so there is no "persisted-but-not-published" gap. Writing the row +
flipping `published` in one transaction is the natural upgrade to a relay-only
design; the seam is identical.

## Storage

`storage/models.py` (SQLAlchemy on `edis_platform.db.session.Base`):

- `raw_events` — outbox / durable landing record (`idempotency_key` unique,
  `published` flag for the relay). Carries `tenant_id`, `domain`, `source_system`,
  `event_id`, `payload` (JSONB), `anomaly_label`, `ingest_ts`, `event_ts`,
  `trace_id`.
- `ingest_dlq` — persisted dead-letter records with full error context.
- `ingest_checkpoint` — per-source offset for the chunked, resumable batch loader.

Migrations are Alembic at `apps/ingestion/migrations` (`alembic.ini` at the app
root). The URL comes from `EDIS_DATABASE_URL` via the shared settings, not from
`alembic.ini`.

```bash
cd apps/ingestion
alembic upgrade head        # requires Postgres (integration)
```

## Run with no infra

Everything is importable and unit-testable **without Docker**. With
`EDIS_SINK_BACKEND=inproc` (default) and `EDIS_INGEST_IDEMPOTENCY_BACKEND=memory`
(default), the service and pipeline run with no Postgres/Redpanda/Redis. Pass
`writer=None` to `ingest_record` to run the pipeline with no database at all.

```python
from edis_platform.settings import Settings
from edis_platform.bus.base import make_sink
from ingestion.pipeline.engine import ingest_record
from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
from ingestion.publish.publisher import IngestPublisher

settings = Settings(sink_backend="inproc")
sink = make_sink(settings); await sink.start()
result = await ingest_record(
    "sales",
    {"order_id": "SO-1", "customer_id": "C1", "sku": "X",
     "qty": "2", "unit_price": "129.00", "region": "EMEA",
     "channel": "web", "ts": "06/12/2026"},
    tenant_id="acme", source_system="simulator",
    ctx_sink=IngestPublisher(sink), idem=InMemoryIdempotencyStore(), writer=None,
)
# result.outcome == IngestOutcome.LANDED
# result.idempotency_key == "sales:acme:simulator:SO-1"
```

Anything that needs real infra (Postgres/Redpanda/Redis) is marked
`@pytest.mark.integration` so the unit suite is green without Docker.

## Configuration

- Platform (`EDIS_` prefix): `database_url`, `redis_url`, `kafka_bootstrap_servers`,
  `sink_backend` (`kafka`|`redis`|`inproc`), `log_level`, `otel_*`.
- Ingestion (`EDIS_INGEST_` prefix): `idempotency_backend` (`memory`|`redis`),
  `idempotency_ttl_seconds`, `publish_after_land`, `batch_chunk_size`,
  `default_source_system`, `default_tenant_id`.

## What this unit (I1) builds

`config.py`, `app.py` (FastAPI factory with lazy sink/idempotency lifecycle), the
`pipeline/` core, `storage/` (models + outbox writer + reconcile relay), `publish/`
(publisher + audit), and the Alembic migration. I2 adds the REST/control API onto
this app; I3 adds the simulator, batch loader and `dil` CLI on top of the same
`ingest_record` core.
