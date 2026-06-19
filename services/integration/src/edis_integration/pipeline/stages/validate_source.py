"""Stage 2 -- validate_source: re-validate the inner payload per domain.

L1 already validated at the edge, but L2 *re-validates* against the strict
per-domain model (``SalesPayloadV1`` / ``OpsPayloadV1``, both ``extra="forbid"``)
so a malformed or drifted payload that somehow reached the raw topic is caught
before it can become a canonical fact. A validation failure is structural and
raises :class:`~edis_integration.pipeline.engine.NormalizationError` (routed to
the integration DLQ as a ``QuarantinedRecord``).
"""

from __future__ import annotations

from edis_contracts.ingest import OpsPayloadV1, SalesPayloadV1
from pydantic import BaseModel, ValidationError

from edis_integration.pipeline.stages import StageContext

#: domain -> strict per-domain payload model
_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "sales": SalesPayloadV1,
    "ops": OpsPayloadV1,
}


class ValidateSourceStage:
    name = "validate_source"

    def __call__(self, ctx: StageContext) -> StageContext:
        from edis_integration.pipeline.engine import NormalizationError

        model = _PAYLOAD_MODELS.get(ctx.domain)
        if model is None:
            raise NormalizationError(
                stage=self.name,
                error_type="unknown_domain",
                detail=f"no source model for domain={ctx.domain!r}",
            )
        try:
            ctx.payload = model.model_validate(ctx.raw_payload)
        except ValidationError as exc:
            raise NormalizationError(
                stage=self.name,
                error_type="validation_error",
                detail=_format_validation_error(exc),
            ) from exc
        return ctx


def _format_validation_error(exc: ValidationError) -> str:
    """Render a Pydantic validation error compactly for the DLQ record."""

    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts) or str(exc)


validate_source = ValidateSourceStage()
