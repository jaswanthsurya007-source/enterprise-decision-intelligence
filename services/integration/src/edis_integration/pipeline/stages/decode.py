"""Stage 1 -- decode: pull the inner payload dict off the envelope.

The envelope itself is already a validated :class:`IngestEnvelope` (L1 produced
it), so decoding is just extracting ``payload`` and confirming it is a mapping. A
non-dict payload is a structural fault and raises
:class:`~edis_integration.pipeline.engine.NormalizationError` (DLQ), not a DQ
quarantine.
"""

from __future__ import annotations

from edis_integration.pipeline.stages import StageContext


class DecodeStage:
    name = "decode"

    def __call__(self, ctx: StageContext) -> StageContext:
        from edis_integration.pipeline.engine import NormalizationError

        payload = ctx.envelope.payload
        if not isinstance(payload, dict):
            raise NormalizationError(
                stage=self.name,
                error_type="decode_error",
                detail=f"envelope payload is {type(payload).__name__}, expected object",
            )
        ctx.raw_payload = payload
        return ctx


decode = DecodeStage()
