"""The per-record pipeline orchestrator — the one code path I2/I3 reuse.

:func:`ingest_record` runs the full edge-of-trust flow for a single record:

    coerce  ->  validate (per-domain, extra="forbid")  ->  derive idempotency key
      ->  idempotency guard  ->  build IngestEnvelope  ->  land in raw_events (outbox)
      ->  publish to the bus  ->  emit AuditEvent(DATA_WRITE)

Outcomes (never raises for *data* problems — bad input is normal at the edge):

* ``LANDED``     — accepted, landed, published.
* ``DUPLICATE``  — idempotency key already seen (guard or DB unique); skipped.
* ``DLQ``        — coercion/validation/unknown-domain failure; routed to the DLQ.

The function takes its collaborators (sink, idempotency store, writer) as
explicit parameters so it is unit-testable with the in-memory store and the
in-proc sink and **no infra** — and so I2 (REST/control) and I3 (simulator/batch)
share exactly one implementation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from edis_contracts.ingest import Domain, IngestEnvelope
from pydantic import ValidationError

from ingestion.pipeline import coerce as coerce_mod
from ingestion.pipeline import dlq as dlq_mod
from ingestion.pipeline import validator as validator_mod
from ingestion.pipeline.envelope_builder import build_envelope
from ingestion.pipeline.idempotency import derive_idempotency_key

if TYPE_CHECKING:
    from ingestion.pipeline.idempotency import IdempotencyStore
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.storage.raw_writer import RawWriter


class IngestOutcome(str, enum.Enum):
    """Terminal state of one record through the pipeline."""

    LANDED = "landed"
    DUPLICATE = "duplicate"
    DLQ = "dlq"


@dataclass
class IngestResult:
    """The auditable result of processing one record.

    Exactly one of ``envelope`` (LANDED/DUPLICATE) or ``dlq_id``/``error`` (DLQ)
    is populated. ``published`` is ``True`` only when the record was newly landed
    and pushed to the bus.
    """

    outcome: IngestOutcome
    idempotency_key: str | None = None
    envelope: IngestEnvelope | None = None
    dlq_id: str | None = None
    error: str | None = None
    published: bool = False

    @property
    def ok(self) -> bool:
        """True if the record did not dead-letter (landed or deduped)."""

        return self.outcome is not IngestOutcome.DLQ


async def ingest_record(
    domain: Domain,
    raw: dict[str, Any],
    *,
    tenant_id: str,
    source_system: str,
    ctx_sink: "IngestPublisher",
    idem: "IdempotencyStore",
    writer: "RawWriter | None" = None,
    trace_context: dict[str, str] | None = None,
    is_synthetic: bool = True,
    anomaly_label: str | None = None,
    publish_after_land: bool = True,
) -> IngestResult:
    """Process one untrusted record end-to-end. Never raises for bad *data*.

    Parameters
    ----------
    domain:
        ``"sales"`` | ``"ops"`` | ``"customer"``.
    raw:
        The untrusted source record (messy types allowed).
    tenant_id, source_system:
        Provenance stamped on the envelope (``tenant_id`` is mandatory and carried
        through every downstream event/row).
    ctx_sink:
        An :class:`~ingestion.publish.publisher.IngestPublisher` wrapping the
        process EventSink (named ``ctx_sink`` so callers think "the publishing
        context"). It also emits the ``DATA_WRITE`` audit event.
    idem:
        The :class:`~ingestion.pipeline.idempotency.IdempotencyStore` guard
        (in-memory in tests, Redis ``SETNX`` in production).
    writer:
        The outbox :class:`~ingestion.storage.raw_writer.RawWriter`. Optional so
        the pipeline runs with **no database** (unit tests / pure stream demo); a
        DLQ on a tenant-less parse failure still publishes without persisting.
    publish_after_land:
        MVP outbox mode — land the durable row, then publish; the reconcile relay
        republishes any row left ``published=false``.
    """

    trace_id = None
    if trace_context:
        trace_id = trace_context.get("trace_id") or trace_context.get("traceparent")

    # 1. coerce source quirks, then 2. validate against the strict per-domain model.
    try:
        coerced = coerce_mod.coerce(domain, raw)
        validated = validator_mod.validate(domain, coerced)
    except validator_mod.UnknownDomainError as exc:
        return await _to_dlq(
            raw=raw,
            error_type="unknown_domain",
            error_detail=str(exc),
            tenant_id=tenant_id,
            domain=None,
            source_system=source_system,
            trace_id=trace_id,
            ctx_sink=ctx_sink,
            writer=writer,
        )
    except ValidationError as exc:
        return await _to_dlq(
            raw=raw,
            error_type="validation_error",
            error_detail=validator_mod.format_validation_error(exc),
            tenant_id=tenant_id,
            domain=domain,
            source_system=source_system,
            trace_id=trace_id,
            ctx_sink=ctx_sink,
            writer=writer,
        )

    payload = validated.model_dump()

    # 3. derive the deterministic, replay-safe idempotency key (arch §4.1).
    idempotency_key = derive_idempotency_key(
        domain, tenant_id, source_system, payload, trace_id=trace_id
    )

    # 4. idempotency guard — first sighting only.
    if not await idem.seen(idempotency_key):
        return IngestResult(outcome=IngestOutcome.DUPLICATE, idempotency_key=idempotency_key)

    # 5. build the envelope (frozen boundary).
    env = build_envelope(
        domain,
        validated,
        tenant_id=tenant_id,
        source_system=source_system,
        idempotency_key=idempotency_key,
        trace_context=trace_context,
        is_synthetic=is_synthetic,
        anomaly_label=anomaly_label,
    )

    # 6. land in raw_events (outbox). DB unique constraint is the final dedupe
    #    backstop: a False return means a duplicate raced past the guard.
    if writer is not None:
        newly_landed = await writer.write_raw(env, trace_id=trace_id)
        if not newly_landed:
            return IngestResult(
                outcome=IngestOutcome.DUPLICATE,
                idempotency_key=idempotency_key,
                envelope=env,
            )

    # 7. publish (publish-after-land) + audit, then mark the outbox row published.
    published = False
    if publish_after_land:
        await ctx_sink.publish_envelope(env)
        published = True
        if writer is not None:
            await writer.mark_published(env.event_id)

    return IngestResult(
        outcome=IngestOutcome.LANDED,
        idempotency_key=idempotency_key,
        envelope=env,
        published=published,
    )


async def _to_dlq(
    *,
    raw: Any,
    error_type: str,
    error_detail: str,
    tenant_id: str | None,
    domain: Domain | None,
    source_system: str | None,
    trace_id: str | None,
    ctx_sink: "IngestPublisher",
    writer: "RawWriter | None",
) -> IngestResult:
    """Persist + publish a DLQ record and return a DLQ result (never raises)."""

    record = dlq_mod.build_dlq_record(
        raw=raw,
        error_type=error_type,
        error_detail=error_detail,
        tenant_id=tenant_id,
        domain=domain,
        source_system=source_system,
        trace_id=trace_id,
    )
    if writer is not None:
        await writer.write_dlq(record)
    await ctx_sink.publish_dlq(record)
    return IngestResult(outcome=IngestOutcome.DLQ, dlq_id=str(record.dlq_id), error=error_detail)
