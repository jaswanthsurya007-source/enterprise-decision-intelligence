"""Typed pipeline stages + the shared :class:`StageContext` they thread through.

A :class:`Stage` is a pure callable ``(StageContext) -> StageContext`` (no I/O).
The engine composes them in the fixed order ``decode -> validate_source -> map ->
clean -> coerce -> dq_check`` and then performs the side-effecting upsert /
metric-derivation / outbox staging itself. Keeping the stages pure is what makes
the normalization core directly unit-testable.

A stage either advances the context (filling a field) or sets ``context.failure``
to flag the record for quarantine; ``decode`` / ``validate_source`` raise a
:class:`~edis_integration.pipeline.engine.NormalizationError` for structural
problems the engine routes to the DLQ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel

    from edis_contracts.ingest import IngestEnvelope
    from edis_integration.mappers.registry import MapperResult


@dataclass
class StageContext:
    """Mutable state threaded through the normalization stages for one record.

    Fields fill in stage order. ``failure`` (a DQ reason list) being non-empty
    after ``dq_check`` flags the record for quarantine; structural failures in
    ``decode`` / ``validate_source`` raise instead (the engine routes those to the
    DLQ). Everything here is plain data -- no I/O, no DB handles.
    """

    envelope: IngestEnvelope
    tenant_id: str
    source_system: str
    domain: str
    schema_ref: str
    occurred_at: datetime

    # decode -> the raw inner payload dict pulled off the envelope
    raw_payload: dict | None = None
    # validate_source -> the strict per-domain model (SalesPayloadV1 / OpsPayloadV1)
    payload: "BaseModel | None" = None
    # map -> canonical entities
    mapped: "MapperResult | None" = None
    # clean -> normalized canonical entities (currency->base, ISO, trim/case)
    cleaned: "MapperResult | None" = None
    # coerce -> final type-coerced canonical entities ready to persist
    coerced: "MapperResult | None" = None
    # dq_check -> 0..1 score + the list of failed checks (non-empty => quarantine)
    dq_score: float = 1.0
    failure: list[str] = field(default_factory=list)

    @property
    def quarantined(self) -> bool:
        """True once a DQ check has flagged this record for quarantine."""

        return bool(self.failure)


@runtime_checkable
class Stage(Protocol):
    """A pure normalization step: ``(StageContext) -> StageContext``."""

    name: str

    def __call__(self, ctx: StageContext) -> StageContext: ...
