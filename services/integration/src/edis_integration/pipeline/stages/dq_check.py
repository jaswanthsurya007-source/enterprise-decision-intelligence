"""Stage 6 -- dq_check: score data quality and flag records for quarantine.

Runs a set of cheap deterministic checks over the *coerced* canonical entities,
accumulating a ``dq_score`` (1.0 minus a penalty per failed check, floored at 0)
and a list of failure reasons. The engine quarantines the record (routes a
:class:`~edis_contracts.ingest.QuarantinedRecord` to ``edis.dlq.integration.v1``)
when the score falls below ``dq_min_score`` -- so every record terminates in
exactly one of {canonical store, quarantine}, never silently dropped, never
double-counted. Pure: it only reads the context and writes ``dq_score`` /
``failure``.
"""

from __future__ import annotations

from decimal import Decimal

from edis_integration.pipeline.stages import StageContext

_VALID_REGIONS = {"NA", "EMEA", "APAC", "LATAM"}
_PENALTY = 0.25  # score deducted per failed check


class DqCheckStage:
    name = "dq_check"

    def __init__(self, *, min_score: float = 0.5) -> None:
        self.min_score = min_score

    def __call__(self, ctx: StageContext) -> StageContext:
        assert ctx.coerced is not None  # coerce ran first
        result = ctx.coerced
        # Hard failures always quarantine (data-integrity violations that would
        # corrupt a metric); soft failures only lower the dq_score (warnings).
        hard: list[str] = []
        soft: list[str] = []

        order = result.order
        if order is not None:
            if order.amount_base <= Decimal("0"):
                hard.append("order.amount_base must be > 0")
            if order.fx_rate <= Decimal("0"):
                hard.append("order.fx_rate must be > 0")
            if order.line_items and any(li.qty <= 0 for li in order.line_items):
                hard.append("order line qty must be > 0")
            if order.region is not None and order.region not in _VALID_REGIONS:
                soft.append(f"order.region unknown: {order.region!r}")

        for ev in result.ops_events:
            if not ev.service:
                hard.append("ops_event.service is empty")
            if ev.latency_ms is not None and ev.latency_ms < 0:
                hard.append("ops_event.latency_ms must be >= 0")
            if ev.region is not None and ev.region not in _VALID_REGIONS:
                soft.append(f"ops_event.region unknown: {ev.region!r}")

        score = max(0.0, 1.0 - _PENALTY * len(soft))
        ctx.dq_score = score
        # Quarantine on any hard failure, or when soft penalties drag the score
        # below the configured floor.
        if hard:
            ctx.failure = hard + soft
        elif score < self.min_score:
            ctx.failure = soft or ["dq_score below threshold"]
        else:
            ctx.failure = []
        return ctx


# Default instance; the engine may construct one with a tenant-tuned threshold.
dq_check = DqCheckStage()
