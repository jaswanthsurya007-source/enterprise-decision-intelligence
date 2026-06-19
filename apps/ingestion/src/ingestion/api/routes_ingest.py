"""Ingest routes — the REST ingress mode of the one pipeline core.

``POST /v1/ingest/sales`` and ``POST /v1/ingest/ops`` accept either a single record
object **or** a batch array of records, and run each through the shared
:func:`ingestion.pipeline.engine.ingest_record` — the *same* code path the simulator
and batch loader use, so a record is processed identically regardless of ingress.

Response shape:

* A single record returns its :class:`IngestRecordResult` (HTTP ``200`` landed/
  deduped, ``422`` only if the *request* itself is malformed JSON — bad *data* in a
  well-formed body still returns ``200`` with ``outcome="dlq"``, because a bad
  record is normal at the edge and must never 5xx).
* A batch returns a 207-style :class:`BatchIngestResult`
  (``{accepted, rejected, dlq, results[]}``) with HTTP ``207`` whenever it is mixed
  or contains any DLQ; pure-success batches return ``200``.

Tenant + provenance: ``tenant_id`` is the verified token's tenant (never the body);
``source_system`` defaults to a per-request header / settings default. Writes
require the ``operator`` role and are audited as ``DATA_WRITE`` inside the pipeline.
"""

from __future__ import annotations

from typing import Any, Literal

from edis_contracts.ingest import Domain
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Header, Response, status
from pydantic import BaseModel, Field

from ingestion.api.deps import (
    get_idempotency,
    get_ingestion_settings_dep,
    get_publisher,
    get_writer,
    require_role,
)
from ingestion.pipeline.engine import IngestOutcome, IngestResult, ingest_record

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])

#: A single untrusted record body is an arbitrary JSON object (messy types allowed;
#: coercion + the strict per-domain model run inside the pipeline).
RecordBody = dict[str, Any]
#: The request body is one record or a batch of records.
IngestBody = RecordBody | list[RecordBody]


class IngestRecordResult(BaseModel):
    """Per-record result mirrored from the pipeline's :class:`IngestResult`."""

    outcome: Literal["landed", "duplicate", "dlq"]
    idempotency_key: str | None = None
    event_id: str | None = None
    dlq_id: str | None = None
    error: str | None = None
    published: bool = False

    @classmethod
    def from_result(cls, result: IngestResult) -> "IngestRecordResult":
        event_id = str(result.envelope.event_id) if result.envelope is not None else None
        return cls(
            outcome=result.outcome.value,  # type: ignore[arg-type]
            idempotency_key=result.idempotency_key,
            event_id=event_id,
            dlq_id=result.dlq_id,
            error=result.error,
            published=result.published,
        )


class BatchIngestResult(BaseModel):
    """207-style partial result for a batch submission."""

    accepted: int  # landed + duplicate (did not dead-letter)
    rejected: int  # == dlq; named per the I2 spec's {accepted, rejected, dlq}
    dlq: int
    landed: int = 0
    duplicate: int = 0
    results: list[IngestRecordResult] = Field(default_factory=list)


async def _run_one(
    domain: Domain,
    raw: RecordBody,
    *,
    tenant_id: str,
    source_system: str,
    publisher,
    idem,
    writer,
    publish_after_land: bool,
) -> IngestResult:
    return await ingest_record(
        domain,
        raw,
        tenant_id=tenant_id,
        source_system=source_system,
        ctx_sink=publisher,
        idem=idem,
        writer=writer,
        publish_after_land=publish_after_land,
    )


async def _ingest(
    domain: Domain,
    body: IngestBody,
    *,
    response: Response,
    ctx: SecurityContext,
    source_system: str,
    publisher,
    idem,
    writer,
    settings,
) -> IngestRecordResult | BatchIngestResult:
    """Dispatch single-vs-batch and assemble the response + status code."""

    publish_after_land = settings.publish_after_land
    common = dict(
        tenant_id=ctx.tenant_id,
        source_system=source_system,
        publisher=publisher,
        idem=idem,
        writer=writer,
        publish_after_land=publish_after_land,
    )

    # Single record.
    if isinstance(body, dict):
        result = await _run_one(domain, body, **common)
        return IngestRecordResult.from_result(result)

    # Batch: process every record; a bad record dead-letters but never blocks
    # the rest of the partition.
    results: list[IngestResult] = []
    for raw in body:
        if not isinstance(raw, dict):
            # Non-object array element: synthesize a DLQ-shaped result rather than
            # 5xx, keeping the batch contract uniform.
            results.append(
                IngestResult(
                    outcome=IngestOutcome.DLQ,
                    error="record must be a JSON object",
                )
            )
            continue
        results.append(await _run_one(domain, raw, **common))

    landed = sum(1 for r in results if r.outcome is IngestOutcome.LANDED)
    duplicate = sum(1 for r in results if r.outcome is IngestOutcome.DUPLICATE)
    dlq = sum(1 for r in results if r.outcome is IngestOutcome.DLQ)
    accepted = landed + duplicate

    batch = BatchIngestResult(
        accepted=accepted,
        rejected=dlq,
        dlq=dlq,
        landed=landed,
        duplicate=duplicate,
        results=[IngestRecordResult.from_result(r) for r in results],
    )
    # 207 Multi-Status whenever any record dead-lettered (partial success);
    # a fully-accepted batch is a plain 200.
    if dlq > 0:
        response.status_code = status.HTTP_207_MULTI_STATUS
    return batch


@router.post("/sales", summary="Ingest a sales record or a batch of sales records")
async def ingest_sales(
    body: IngestBody,
    response: Response,
    ctx: SecurityContext = Depends(require_role("operator")),
    publisher=Depends(get_publisher),
    idem=Depends(get_idempotency),
    writer=Depends(get_writer),
    settings=Depends(get_ingestion_settings_dep),
    x_source_system: str | None = Header(default=None),
) -> IngestRecordResult | BatchIngestResult:
    source_system = x_source_system or settings.default_source_system
    return await _ingest(
        "sales",
        body,
        response=response,
        ctx=ctx,
        source_system=source_system,
        publisher=publisher,
        idem=idem,
        writer=writer,
        settings=settings,
    )


@router.post("/ops", summary="Ingest an ops record or a batch of ops records")
async def ingest_ops(
    body: IngestBody,
    response: Response,
    ctx: SecurityContext = Depends(require_role("operator")),
    publisher=Depends(get_publisher),
    idem=Depends(get_idempotency),
    writer=Depends(get_writer),
    settings=Depends(get_ingestion_settings_dep),
    x_source_system: str | None = Header(default=None),
) -> IngestRecordResult | BatchIngestResult:
    source_system = x_source_system or settings.default_source_system
    return await _ingest(
        "ops",
        body,
        response=response,
        ctx=ctx,
        source_system=source_system,
        publisher=publisher,
        idem=idem,
        writer=writer,
        settings=settings,
    )
