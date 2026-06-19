"""The EvidenceBundle builder — the *only* thing the narrator may reason over.

THE GROUNDING GUARANTEE (the contract X3's narrator + grounding guard depend on):
the LLM narrator is handed an :class:`~edis_contracts.findings.EvidenceBundle` and
*nothing else*. Every number it is permitted to cite must appear in
``EvidenceBundle.allowed_numbers``. After generation, the grounding verifier extracts
every numeric token from the narrative and asserts each matches an allowed number
within a small relative tolerance; on any unmatched number the narrative is discarded
and a deterministic template (built from these same items) is used instead.

So this builder has one job and must do it exactly: assemble the computed facts as
:class:`~edis_contracts.findings.EvidenceItem`\\s **and** collect the matching
``allowed_numbers`` whitelist. If a figure is summarized in an item's text or values,
it MUST also be in ``allowed_numbers`` — otherwise a faithful narrative would be
wrongly rejected. The builder is the single place that guarantees that invariant.

What goes in (all computed, all citable):

* **metric_window** — the anomalous target: observed, expected, deviation,
  deviation_pct, detector score, severity/confidence.
* **baseline** — the pre-incident expectation level (so "down from $95K" is grounded).
* **candidate_cause** — one per ranked RCA cause: correlation, lag_minutes,
  contribution_pct, observed_delta.
* **dimension_contribution** (optional) — which cell drove an aggregate change.
* **forecast** (optional) — the next point's yhat + band, if a forecast was attached.

Numbers added to ``allowed_numbers`` are also offered in *rounded* variants (the
human-facing forms a narrator naturally writes — ``-35.8`` for percent, ``61000`` for
revenue) so the guard's tolerance check matches both the raw and the rounded token.
Everything is pure: no DB, no LLM, no API key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from edis_contracts.findings import (
    CandidateCause,
    EvidenceBundle,
    EvidenceItem,
    Finding,
    Forecast,
)

from edis_intelligence.detectors.base import DetectorResult
from edis_intelligence.rca.decomposition import DimensionContribution


# ---------------------------------------------------------------------------
# allowed_numbers accumulation
# ---------------------------------------------------------------------------
class _AllowedNumbers:
    """Dedupe-accumulator for the grounding whitelist (raw + human-rounded forms)."""

    def __init__(self) -> None:
        self._seen: dict[float, None] = {}

    def add(self, *values: float | None) -> None:
        for v in values:
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            self._add_one(f)
            # Human-facing rounded variants the narrator may actually type.
            self._add_one(round(f, 1))
            self._add_one(round(f, 2))
            self._add_one(float(round(f)))
            # Magnitude (a narrator may write "$34K drop" not "-$34K").
            self._add_one(abs(f))
            self._add_one(round(abs(f), 1))

    def _add_one(self, f: float) -> None:
        # Normalize -0.0 to 0.0 so the whitelist is stable.
        if f == 0.0:
            f = 0.0
        self._seen.setdefault(f, None)

    def as_list(self) -> list[float]:
        return sorted(self._seen.keys())


# ---------------------------------------------------------------------------
# Item builders
# ---------------------------------------------------------------------------
def _metric_window_item(result: DetectorResult) -> EvidenceItem:
    pct = result.deviation_pct
    return EvidenceItem(
        kind="metric_window",
        metric_key=result.metric_key,
        dimensions=dict(result.dimensions),
        summary=(
            f"{result.metric_key} {_dims_phrase(result.dimensions)} was "
            f"{result.observed_value:,.0f} vs an expected {result.expected_value:,.0f} "
            f"({pct:+.1f}%), a {result.detector} deviation of {abs(result.score):.1f}."
        ),
        values={
            "observed_value": float(result.observed_value),
            "expected_value": float(result.expected_value),
            "deviation": float(result.deviation),
            "deviation_pct": float(result.deviation_pct),
            "score": float(result.score),
            "severity": float(result.severity),
            "confidence": float(result.confidence),
            "business_impact_input": float(result.business_impact_input),
        },
        ref={
            "detector": result.detector,
            "detector_version": result.detector_version,
            "window_start": result.window_start.isoformat(),
            "window_end": result.window_end.isoformat(),
        },
    )


def _baseline_item(result: DetectorResult) -> EvidenceItem:
    return EvidenceItem(
        kind="baseline",
        metric_key=result.metric_key,
        dimensions=dict(result.dimensions),
        summary=(
            f"The pre-incident expectation for {result.metric_key} "
            f"{_dims_phrase(result.dimensions)} was {result.expected_value:,.0f}."
        ),
        values={"expected_value": float(result.expected_value)},
        ref=None,
    )


def _candidate_cause_item(cause: CandidateCause) -> EvidenceItem:
    contrib = (
        "" if cause.contribution_pct is None else f", ~{cause.contribution_pct:.0f}% of impact"
    )
    return EvidenceItem(
        kind="candidate_cause",
        metric_key=cause.metric_key,
        dimensions=dict(cause.dimensions),
        summary=(
            f"{cause.metric_key} {_dims_phrase(cause.dimensions)} moved by "
            f"{cause.observed_delta:+.4g} and is {cause.direction} the target "
            f"(correlation {cause.correlation:+.2f}, lag {cause.lag_minutes} min{contrib})."
        ),
        values={
            "correlation": float(cause.correlation),
            "lag_minutes": float(cause.lag_minutes),
            "observed_delta": float(cause.observed_delta),
            **(
                {"contribution_pct": float(cause.contribution_pct)}
                if cause.contribution_pct is not None
                else {}
            ),
        },
        ref={"direction": cause.direction},
    )


def _dimension_contribution_item(dc: DimensionContribution, metric_key: str) -> EvidenceItem:
    return EvidenceItem(
        kind="dimension_contribution",
        metric_key=metric_key,
        dimensions={},
        summary=(
            f"{dc.dimension_value} accounts for ~{dc.contribution_pct:.0f}% of the "
            f"{metric_key} change ({dc.baseline:,.0f} -> {dc.observed:,.0f}, "
            f"{dc.delta:+,.0f})."
        ),
        values={
            "baseline": float(dc.baseline),
            "observed": float(dc.observed),
            "delta": float(dc.delta),
            "contribution_pct": float(dc.contribution_pct),
        },
        ref={"dimension_value": dc.dimension_value},
    )


def _forecast_item(forecast: Forecast) -> EvidenceItem | None:
    if not forecast.points:
        return None
    p = forecast.points[0]
    yhat = float(p.get("yhat", 0.0))
    lo = float(p.get("yhat_lower", yhat))
    hi = float(p.get("yhat_upper", yhat))
    return EvidenceItem(
        kind="forecast",
        metric_key=forecast.metric_key,
        dimensions=dict(forecast.dimensions),
        summary=(
            f"The {forecast.model} forecast for {forecast.metric_key} projects "
            f"{yhat:,.0f} next (band {lo:,.0f}-{hi:,.0f})."
        ),
        values={"yhat": yhat, "yhat_lower": lo, "yhat_upper": hi},
        ref={"model": forecast.model, "horizon_days": float(forecast.horizon_days)},
    )


def _dims_phrase(dimensions: Mapping[str, str]) -> str:
    if not dimensions:
        return "(overall)"
    return "(" + ", ".join(f"{k}={v}" for k, v in sorted(dimensions.items())) + ")"


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------
def build_evidence_bundle(
    *,
    tenant_id: str,
    finding_id: UUID,
    target: DetectorResult,
    candidate_causes: Sequence[CandidateCause] = (),
    dimension_contributions: Iterable[tuple[str, DimensionContribution]] = (),
    forecast: Forecast | None = None,
    bundle_id: UUID | None = None,
    created_at: datetime | None = None,
) -> EvidenceBundle:
    """Assemble the :class:`EvidenceBundle` for one finding.

    ``target`` is the (scored) :class:`DetectorResult` that produced the finding;
    ``candidate_causes`` are the ranked RCA causes (ideally already
    ``contribution_pct``-weighted); ``dimension_contributions`` is an optional
    ``(metric_key, DimensionContribution)`` iterable; ``forecast`` is the optional
    attached forecast band.

    Returns a bundle whose ``items`` are the computed facts and whose
    ``allowed_numbers`` whitelist contains *every figure cited in those items* (raw
    plus human-rounded variants), satisfying the grounding guarantee: a faithful
    narrative cites only numbers in this set, and the guard rejects any number that
    is not. Pure / deterministic.
    """

    items: list[EvidenceItem] = []
    allowed = _AllowedNumbers()

    # --- target metric window + baseline ---
    items.append(_metric_window_item(target))
    allowed.add(
        target.observed_value,
        target.expected_value,
        target.deviation,
        target.deviation_pct,
        target.score,
        target.severity,
        target.confidence,
        target.business_impact_input,
    )
    # detector diagnostics are computed facts too (level/sigmas/runs).
    for v in target.diagnostics.values():
        allowed.add(v)

    items.append(_baseline_item(target))
    allowed.add(target.expected_value)

    # --- candidate causes ---
    for cause in candidate_causes:
        items.append(_candidate_cause_item(cause))
        allowed.add(
            cause.correlation,
            float(cause.lag_minutes),
            cause.observed_delta,
            cause.contribution_pct,
        )

    # --- dimensional contributions ---
    for metric_key, dc in dimension_contributions:
        items.append(_dimension_contribution_item(dc, metric_key))
        allowed.add(dc.baseline, dc.observed, dc.delta, dc.contribution_pct)

    # --- forecast band ---
    if forecast is not None:
        fitem = _forecast_item(forecast)
        if fitem is not None:
            items.append(fitem)
            allowed.add(*fitem.values.values())

    return EvidenceBundle(
        bundle_id=bundle_id or uuid4(),
        tenant_id=tenant_id,
        finding_id=finding_id,
        created_at=created_at or datetime.now(timezone.utc),
        items=items,
        allowed_numbers=allowed.as_list(),
    )


def bundle_for_finding(
    finding: Finding,
    target: DetectorResult,
    **kwargs,
) -> EvidenceBundle:
    """Convenience: build a bundle from a :class:`Finding` (pulls tenant/finding id).

    ``candidate_causes`` defaults to the finding's own ``candidate_causes`` when not
    supplied in ``kwargs``.
    """

    kwargs.setdefault("candidate_causes", finding.candidate_causes)
    return build_evidence_bundle(
        tenant_id=finding.tenant_id,
        finding_id=finding.finding_id,
        target=target,
        **kwargs,
    )
