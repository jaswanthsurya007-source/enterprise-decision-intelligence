"""Publish L3 outputs: ``edis.findings.v1`` + ``edis.forecasts.v1`` + a lineage event.

Thin wrapper over an injected :class:`~edis_platform.bus.base.EventSink` (built by
``make_sink`` from the platform settings — Kafka / Redis / in-proc) plus the
governance SDK's :func:`~edis_gov_sdk.lineage.emit_lineage`. Owns no DB.

Topic keying follows the §4.3 contract:

* ``edis.findings.v1``   — key ``tenant_id:finding_id``
* ``edis.forecasts.v1``  — key ``tenant_id:metric_key:dim_hash``
* ``edis.governance.lineage.v1`` — keyed ``tenant_id`` (by the SDK)

The lineage event records the inputs (the metric series the analysis read) and the
outputs (the finding / forecast / evidence bundle produced) under one ``run_id``, so
governance can trace any finding back to the metric rows that produced it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from edis_contracts import topics
from edis_contracts.findings import EvidenceBundle, Finding, Forecast
from edis_gov_sdk.lineage import emit_lineage


def _dim_hash(dimensions: dict[str, str]) -> str:
    """Stable string key for a dimension map (sorted) — matches the L2 keying scheme."""

    return "&".join(f"{k}={v}" for k, v in sorted(dimensions.items()))


class IntelligencePublisher:
    """Publishes findings, forecasts, and a lineage event for one analysis run."""

    def __init__(self, sink) -> None:
        self._sink = sink

    async def publish_finding(self, finding: Finding) -> None:
        """Publish a :class:`Finding` to ``edis.findings.v1`` (key tenant:finding_id)."""

        key = f"{finding.tenant_id}:{finding.finding_id}"
        await self._sink.publish(topics.FINDINGS, key=key, value=finding)

    async def publish_forecast(self, forecast: Forecast) -> None:
        """Publish a :class:`Forecast` to ``edis.forecasts.v1`` (key tenant:metric:dim)."""

        key = f"{forecast.tenant_id}:{forecast.metric_key}:{_dim_hash(forecast.dimensions)}"
        await self._sink.publish(topics.FORECASTS, key=key, value=forecast)

    async def publish_lineage(
        self,
        *,
        tenant_id: str,
        run_id: UUID,
        inputs: list[dict],
        outputs: list[dict],
    ) -> None:
        """Emit an intelligence-stage lineage event via the governance SDK."""

        await emit_lineage(
            self._sink,
            tenant_id=tenant_id,
            run_id=run_id,
            inputs=inputs,
            outputs=outputs,
            stage="intelligence",
        )

    async def publish_analysis(
        self,
        *,
        tenant_id: str,
        finding: Finding | None = None,
        forecast: Forecast | None = None,
        bundle: EvidenceBundle | None = None,
        inputs: list[dict] | None = None,
        run_id: UUID | None = None,
    ) -> UUID:
        """Publish a finding + forecast + lineage for one analysis run; return ``run_id``.

        ``inputs`` is the list of lineage input refs (the metric series read). Outputs
        are derived from whatever was produced (finding / forecast / evidence bundle).
        Any of ``finding`` / ``forecast`` may be ``None`` (publish only what exists).
        A finding with ``narrative=None`` is published unchanged — narration never
        blocks the publish.
        """

        run_id = run_id or uuid4()
        outputs: list[dict] = []

        if finding is not None:
            await self.publish_finding(finding)
            outputs.append({"type": "finding", "id": str(finding.finding_id)})
        if forecast is not None:
            await self.publish_forecast(forecast)
            outputs.append({"type": "forecast", "id": str(forecast.forecast_id)})
        if bundle is not None:
            outputs.append({"type": "evidence_bundle", "id": str(bundle.bundle_id)})

        await self.publish_lineage(
            tenant_id=tenant_id,
            run_id=run_id,
            inputs=list(inputs or []),
            outputs=outputs,
        )
        return run_id
