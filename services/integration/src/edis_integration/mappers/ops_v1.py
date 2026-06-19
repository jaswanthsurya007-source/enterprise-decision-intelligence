"""``ops.v1`` mapper -- :class:`OpsPayloadV1` -> :class:`OpsEvent`.

Pure mapping (no I/O). The ops-event id is derived deterministically from the
envelope ``idempotency_key`` (already a content hash at L1), falling back to a
hash of the ops fields, so replay upserts onto the same row. Metric derivation
(error_rate / latency_p95 by time bucket) is *not* done here -- it is a separate
pure aggregation over a list of :class:`OpsEvent` (see ``mappers.metrics``),
because those metrics are defined over a window, not a single event.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edis_contracts.canonical import OpsEvent, SourceRef
from edis_contracts.ingest import OpsPayloadV1

from edis_integration.mappers.identity import canonical_ops_event_id, record_hash
from edis_integration.mappers.registry import MapperResult, register_mapper


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


class OpsV1Mapper:
    """Maps a validated ``ops.v1`` payload to a single :class:`OpsEvent`."""

    domain = "ops"
    schema_ref = "ops.v1"

    def map(
        self,
        payload: OpsPayloadV1,
        *,
        tenant_id: str,
        source_system: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> MapperResult:
        event_ts = _utc(payload.event_ts)

        ops_id = canonical_ops_event_id(
            tenant_id,
            idempotency_key,
            service=payload.service,
            event_ts_iso=event_ts.isoformat(),
            message=payload.message,
            status_code=payload.status_code,
            latency_ms=payload.latency_ms,
        )

        ref = SourceRef(
            source_system=source_system,
            source_id=idempotency_key,
            schema_version=1,
            match_confidence=1.0,
        )

        ops_event = OpsEvent(
            canonical_ops_event_id=ops_id,
            tenant_id=tenant_id,
            service=payload.service,
            region=payload.region,
            level=payload.level,
            status_code=payload.status_code,
            latency_ms=payload.latency_ms,
            message=payload.message,
            event_ts=event_ts,
            source_refs=[ref],
            record_hash=record_hash(
                tenant_id,
                payload.service,
                payload.region,
                payload.level,
                payload.status_code,
                payload.latency_ms,
                event_ts.isoformat(),
            ),
        )

        return MapperResult(ops_events=[ops_event])


register_mapper(OpsV1Mapper())
