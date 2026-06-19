"""Stage 3 -- map: dispatch to the versioned :class:`SourceMapper`.

Looks up the mapper for ``(domain, schema_ref)`` and applies it to the validated
payload, producing the canonical entities (``MapperResult``). A missing mapper is
structural and raises :class:`~edis_integration.pipeline.engine.NormalizationError`
(DLQ). The mapping itself is pure.
"""

from __future__ import annotations

from edis_integration.mappers.registry import UnknownMapperError, get_mapper
from edis_integration.pipeline.stages import StageContext


class MapStage:
    name = "map"

    def __call__(self, ctx: StageContext) -> StageContext:
        from edis_integration.pipeline.engine import NormalizationError

        try:
            mapper = get_mapper(ctx.domain, ctx.schema_ref)
        except UnknownMapperError as exc:
            raise NormalizationError(
                stage=self.name, error_type="unknown_mapper", detail=str(exc)
            ) from exc

        assert ctx.payload is not None  # validate_source ran first
        ctx.mapped = mapper.map(
            ctx.payload,
            tenant_id=ctx.tenant_id,
            source_system=ctx.source_system,
            idempotency_key=ctx.envelope.idempotency_key,
            occurred_at=ctx.occurred_at,
        )
        return ctx


map_stage = MapStage()
