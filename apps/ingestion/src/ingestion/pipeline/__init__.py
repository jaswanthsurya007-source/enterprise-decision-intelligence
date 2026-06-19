"""The ingestion pipeline: the per-record normalization core.

Per record (one code path, two ingress modes — no logic drift):

    coerce/normalize source quirks
      -> validate with the per-domain model (extra="forbid")
      -> idempotency guard
      -> build IngestEnvelope
      -> land in raw_events (outbox)
      -> publish to the bus (+ AuditEvent DATA_WRITE)

Bad/invalid records become a :class:`~edis_contracts.ingest.DLQRecord` published
to :data:`edis_contracts.topics.DLQ_INGEST` (and persisted) with full error
context; they NEVER block the partition.

:func:`ingestion.pipeline.engine.ingest_record` is the single public function I2
and I3 reuse.
"""

from __future__ import annotations

from ingestion.pipeline.engine import IngestOutcome, IngestResult, ingest_record
from ingestion.pipeline.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    make_idempotency_store,
)

__all__ = [
    "ingest_record",
    "IngestResult",
    "IngestOutcome",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "RedisIdempotencyStore",
    "make_idempotency_store",
]
