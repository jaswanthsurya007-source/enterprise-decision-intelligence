"""Dead-letter handling for records that fail at the ingestion edge.

A bad record (uncoercible/invalid/unknown-domain) becomes a
:class:`~edis_contracts.ingest.DLQRecord` with full error context, is **persisted**
(durable, replayable) and **published** to :data:`edis_contracts.topics.DLQ_INGEST`.
It is never silently dropped and it never blocks the partition — the engine
continues with the next record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from edis_contracts import topics
from edis_contracts.ingest import DLQRecord, Domain

if TYPE_CHECKING:
    from edis_platform.bus.base import EventSink

    from ingestion.storage.raw_writer import RawWriter


def build_dlq_record(
    *,
    raw: Any,
    error_type: str,
    error_detail: str,
    tenant_id: str | None = None,
    domain: Domain | None = None,
    source_system: str | None = None,
    trace_id: str | None = None,
) -> DLQRecord:
    """Construct a :class:`DLQRecord` capturing the original record + error."""

    return DLQRecord(
        dlq_id=uuid4(),
        tenant_id=tenant_id,
        stage="ingest",
        domain=domain,
        source_system=source_system,
        raw=raw,
        error_type=error_type,
        error_detail=error_detail,
        occurred_at=datetime.now(timezone.utc),
        trace_id=trace_id,
    )


async def route_to_dlq(
    record: DLQRecord,
    *,
    sink: "EventSink",
    writer: "RawWriter | None" = None,
) -> DLQRecord:
    """Persist (if a writer is given) and publish a DLQ record.

    Persist-before-publish mirrors the happy-path outbox: the row is the durable
    record even if the broker is unavailable. Keyed by ``tenant_id`` on the bus
    (per the topic contract); a tenant-less parse failure keys by ``None``.
    """

    if writer is not None:
        await writer.write_dlq(record)
    await sink.publish(topics.DLQ_INGEST, key=record.tenant_id, value=record)
    return record
