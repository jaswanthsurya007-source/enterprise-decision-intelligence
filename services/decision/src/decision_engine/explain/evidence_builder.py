"""Assemble a recommendation's evidence trail + write the explainability record.

Two responsibilities, both pure-then-IO:

1. :func:`build_evidence_trail` -- a deterministic list of provenance links from the
   recommendation back to its source finding, the finding's metric, the root-cause
   candidates, and the bound playbook rule. (C1's synthesizer already builds a trail on
   the Recommendation; this re-derives the same structure from the Finding + Recommendation
   so the governance Decision record is self-contained and the trail can be rebuilt for an
   already-persisted recommendation that carries an empty trail.)
2. :func:`build_decision_record` -- turn that trail into a
   :class:`~edis_contracts.governance.Decision` with immutable
   :class:`~edis_contracts.governance.Evidence` snapshots (the computed numbers frozen at
   decision time, plus a live ``ref``), which :class:`EvidenceBuilder.write` POSTs to the
   governance service via :class:`~edis_gov_sdk.explain.ExplainabilityClient`.

The governance write TOLERATES the service being absent: any HTTP / network error is
logged and swallowed (the recommendation is already published; explainability is
best-effort and must never block or fail the decision flow). No LLM, no numbers invented:
every snapshot value is copied verbatim from the finding / the deterministic
ImpactEstimate.inputs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from edis_contracts.decisions import Recommendation
from edis_contracts.findings import Finding
from edis_contracts.governance import Decision, Evidence
from edis_platform.logging import get_logger

_log = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_evidence_trail(finding: Finding, rec: Recommendation) -> list[dict]:
    """Deterministic provenance links: finding + metric + root_causes + playbook_rule.

    Mirrors the structure C1's synthesizer attaches, re-derivable here so the governance
    Decision record stands alone. Pure -- no numbers are *authored*; figures are copied
    from the finding and the recommendation's deterministic impact.
    """

    trail: list[dict] = [
        {
            "type": "finding",
            "id": str(finding.finding_id),
            "kind": finding.kind.value,
            "metric_key": finding.metric_key,
            "dimensions": dict(finding.dimensions),
        },
        {
            "type": "metric",
            "metric_key": finding.metric_key,
            "dimensions": dict(finding.dimensions),
            "observed_value": finding.observed_value,
            "expected_value": finding.expected_value,
            "deviation": finding.deviation,
            "deviation_pct": finding.deviation_pct,
        },
    ]
    for cause in finding.candidate_causes[:3]:
        trail.append(
            {
                "type": "root_cause",
                "metric_key": cause.metric_key,
                "dimensions": dict(cause.dimensions),
                "correlation": cause.correlation,
                "lag_minutes": cause.lag_minutes,
                "contribution_pct": cause.contribution_pct,
                "direction": cause.direction,
            }
        )
    if finding.evidence_ref is not None:
        trail.append({"type": "evidence_bundle", "id": str(finding.evidence_ref)})
    trail.append(
        {
            "type": "playbook_rule",
            "playbook_id": rec.playbook_id,
            "playbook_version": rec.playbook_version,
            "params": dict(rec.action_params),
            "impact_method": rec.impact.method,
        }
    )
    return trail


def _evidence_items(finding: Finding, rec: Recommendation) -> list[Evidence]:
    """Build immutable :class:`Evidence` snapshots for the Decision record."""

    items: list[Evidence] = [
        Evidence(
            evidence_id=uuid4(),
            kind="finding",
            summary=(
                f"{finding.kind.value} on {finding.metric_key}: observed "
                f"{finding.observed_value:,.0f} vs expected {finding.expected_value:,.0f} "
                f"({finding.deviation_pct:.1f}%)."
            ),
            snapshot={
                "observed_value": finding.observed_value,
                "expected_value": finding.expected_value,
                "deviation": finding.deviation,
                "deviation_pct": finding.deviation_pct,
                "severity": finding.severity,
                "confidence": finding.confidence,
            },
            ref={"type": "finding", "id": str(finding.finding_id)},
        ),
        Evidence(
            evidence_id=uuid4(),
            kind="recommendation",
            summary=(
                f"{rec.title}: estimated {rec.impact.direction} of ~"
                f"{rec.impact.value:,.0f} {rec.impact.unit} over {rec.impact.horizon_days} "
                f"day(s) (method={rec.impact.method}); confidence {rec.confidence.value:.2f}."
            ),
            # The DETERMINISTIC impact inputs are the frozen authoritative numbers.
            snapshot={
                "impact_value": rec.impact.value,
                "impact_low": rec.impact.value_low,
                "impact_high": rec.impact.value_high,
                "impact_inputs": dict(rec.impact.inputs),
                "confidence": rec.confidence.value,
                "confidence_components": dict(rec.confidence.components),
                "priority_score": rec.priority_score,
                "priority_rank": rec.priority_rank,
            },
            ref={"type": "recommendation", "id": str(rec.recommendation_id)},
        ),
    ]
    for cause in finding.candidate_causes[:3]:
        items.append(
            Evidence(
                evidence_id=uuid4(),
                kind="root_cause",
                summary=(
                    f"{cause.metric_key} ({cause.direction}) corr={cause.correlation:+.2f}"
                    + (
                        f", contribution {cause.contribution_pct:.0f}%"
                        if cause.contribution_pct is not None
                        else ""
                    )
                ),
                snapshot={
                    "correlation": cause.correlation,
                    "lag_minutes": cause.lag_minutes,
                    "contribution_pct": cause.contribution_pct,
                    "observed_delta": cause.observed_delta,
                },
                ref={"type": "metric", "metric_key": cause.metric_key},
            )
        )
    return items


def build_decision_record(
    finding: Finding, rec: Recommendation, *, rationale: str | None = None
) -> Decision:
    """Build the :class:`Decision` explainability record for a recommendation.

    ``decision_type="recommendation"``, ``subject_id`` is the recommendation id, and the
    evidence list carries immutable snapshots of every cited number. The ``rationale``
    defaults to the recommendation's grounded ``explanation_summary`` (never LLM prose).
    """

    return Decision(
        decision_id=uuid4(),
        tenant_id=rec.tenant_id,
        decision_type="recommendation",
        subject_id=rec.recommendation_id,
        actor={"type": "system", "id": "decision-engine"},
        rationale=rationale or rec.explanation_summary,
        evidence=_evidence_items(finding, rec),
        created_at=_utc_now(),
    )


class EvidenceBuilder:
    """Builds + writes a recommendation's explainability record to governance (best-effort).

    Construct with an optional :class:`~edis_gov_sdk.explain.ExplainabilityClient`. When it
    is ``None`` (no governance configured), :meth:`write` builds the record and returns it
    without an HTTP call -- so the builder works identically in CI and in the bare app, and
    the trail is always available on the returned :class:`Decision`.
    """

    def __init__(self, client=None) -> None:
        self._client = client

    async def write(
        self, finding: Finding, rec: Recommendation, *, rationale: str | None = None
    ) -> Decision:
        """Build the Decision record and POST it to governance, tolerating its absence.

        Returns the built :class:`Decision` regardless of whether the write succeeded.
        Any error from the governance client (service down, timeout, non-2xx) is logged
        and swallowed -- explainability is best-effort and never blocks the decision flow.
        """

        decision = build_decision_record(finding, rec, rationale=rationale)
        if self._client is None:
            return decision
        try:
            await self._client.write_decision(decision)
        except Exception as exc:  # noqa: BLE001 - governance is best-effort; never raise
            _log.warning(
                "explainability write failed; governance may be absent (tolerated)",
                extra={
                    "tenant_id": rec.tenant_id,
                    "recommendation_id": str(rec.recommendation_id),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        return decision
