"""Outbox reconcile relay.

The MVP publishes-after-land (see :func:`ingestion.pipeline.engine.ingest_record`),
so the steady-state path keeps ``raw_events.published`` in sync. The relay is the
*recovery* path: if the process died, or the broker was down, between the durable
land and the publish, some rows are left ``published=false``. :func:`reconcile`
re-reads those rows, rebuilds the :class:`~edis_contracts.ingest.IngestEnvelope`
from the stored columns, republishes through the same publisher, and flips the
flag — guaranteeing no "persisted-but-not-published" gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_contracts.ingest import IngestEnvelope

if TYPE_CHECKING:
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.storage.models import RawEvent
    from ingestion.storage.raw_writer import RawWriter


def _envelope_from_row(row: "RawEvent") -> IngestEnvelope:
    """Reconstruct the envelope contract from a persisted ``raw_events`` row."""

    return IngestEnvelope(
        event_id=row.event_id,
        idempotency_key=row.idempotency_key,
        schema_ref=f"{row.domain}.v1",
        domain=row.domain,  # type: ignore[arg-type]
        tenant_id=row.tenant_id,
        source_system=row.source_system,
        ingest_ts=row.ingest_ts,
        event_ts=row.event_ts,
        trace_context={"trace_id": row.trace_id} if row.trace_id else {},
        anomaly_label=row.anomaly_label,
        payload=row.payload,
    )


async def reconcile(
    writer: "RawWriter",
    publisher: "IngestPublisher",
    *,
    limit: int = 500,
) -> int:
    """Republish landed-but-unpublished rows; return how many were reconciled."""

    rows = await writer.fetch_unpublished(limit=limit)
    count = 0
    for row in rows:
        env = _envelope_from_row(row)
        await publisher.publish_envelope(env)
        await writer.mark_published(row.event_id)
        count += 1
    return count
