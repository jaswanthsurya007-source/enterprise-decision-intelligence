"""The L3 analysis pipeline: detect -> score -> RCA -> evidence -> narrate -> forecast
-> persist -> publish, over a :class:`MetricReader`.

The public entrypoint is :func:`analyze_metric` — a single ``async def`` that runs the
whole chain for one metric cell and (optionally) persists + publishes the result.
Everything I/O-shaped is injected (reader, narrator, repo, publisher, embedder) so X4
can unit-test the full chain with an :class:`InMemoryMetricReader` + a
``FakeNarrator`` + an ``InMemoryIntelligenceRepo`` and **no infrastructure and no API
keys**. With nothing injected, ``analyze_metric`` is pure analysis (returns the result
without persisting or publishing).

Data shape: the reader returns the same **daily series** L2 produces via
``rollup_daily`` — a sorted list of ``(ts, value)`` points (``sum_value`` for revenue;
``avg_value`` for error_rate / latency_p95) — so detection reads exactly the series the
L1->L2 detectability test asserts on.

THE GROUNDING GUARANTEE flows through here untouched: detection + scoring + RCA +
forecast are deterministic and never call the LLM; the narrator is asked for prose
*after* the evidence bundle is built, and it self-guards (LLM text only if it passes
the number check, else the deterministic template). A finding always carries *a*
narrative or ``None`` — narration never blocks detection or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from edis_contracts.findings import (
    CandidateCause,
    EvidenceBundle,
    Finding,
    FindingKind,
    Forecast,
)
from edis_platform.logging import get_logger

from edis_intelligence.detectors.base import (
    DetectionContext,
    DetectorResult,
    SeriesLike,
)
from edis_intelligence.detectors.registry import DEFAULT_REGISTRY, DetectorRegistry
from edis_intelligence.forecast.ets_model import forecast_series
from edis_intelligence.grounding.embeddings import Embedder, embed_text
from edis_intelligence.rca.correlation import Candidate, rank_candidate_causes
from edis_intelligence.rca.decomposition import contribution_pct_from_causes
from edis_intelligence.rca.evidence import build_evidence_bundle
from edis_intelligence.rca.narrator import Narrator, NarrationResult, TemplateNarrator
from edis_intelligence.scoring.normalize import score_result

_log = get_logger(__name__)

#: Default direction of an adverse move per metric (feeds severity/impact scoring).
_DEFAULT_DIRECTION = {
    "revenue": "down",
    "orders": "down",
    "page_views": "down",
    "error_rate": "up",
    "latency_p95": "up",
}


@dataclass(frozen=True)
class MetricSeries:
    """A daily metric series for one cell, as the reader returns it.

    ``points`` is a sorted list of ``(ts, value)`` (tz-aware UTC), the same shape the
    L2 ``rollup_daily`` produces. ``unit`` is the metric unit if known.
    """

    tenant_id: str
    metric_key: str
    dimensions: dict[str, str]
    points: list[tuple[datetime, float]]
    unit: str | None = None

    @property
    def series(self) -> SeriesLike:
        return self.points


@dataclass(frozen=True)
class CandidateSeriesSpec:
    """A candidate driver series to consider for RCA against the target."""

    metric_key: str
    dimensions: dict[str, str] = field(default_factory=dict)


class MetricReader(Protocol):
    """Port: read the daily metric series L3 analyzes.

    The pipeline reads the target cell and any candidate driver cells through this one
    seam, so the same chain runs against the in-memory fake (tests), the L2 store
    (prod), or a stream-buffered window.
    """

    async def read_series(
        self,
        tenant_id: str,
        metric_key: str,
        dimensions: dict[str, str],
        *,
        lookback_days: int | None = None,
        end: datetime | None = None,
    ) -> MetricSeries: ...

    async def list_cells(self, tenant_id: str) -> list[tuple[str, dict[str, str]]]:
        """List the ``(metric_key, dimensions)`` cells available for a tenant."""
        ...


# ---------------------------------------------------------------------------
# In-memory fake reader
# ---------------------------------------------------------------------------
class InMemoryMetricReader:
    """Infra-free :class:`MetricReader` backed by pre-loaded series (tests + bare app)."""

    def __init__(self) -> None:
        self._cells: dict[tuple[str, str, str], MetricSeries] = {}

    @staticmethod
    def _key(tenant_id: str, metric_key: str, dimensions: dict[str, str]) -> tuple[str, str, str]:
        dim = "&".join(f"{k}={v}" for k, v in sorted(dimensions.items()))
        return (tenant_id, metric_key, dim)

    def add_series(
        self,
        tenant_id: str,
        metric_key: str,
        dimensions: dict[str, str],
        points: Sequence[tuple[datetime, float]],
        *,
        unit: str | None = None,
    ) -> None:
        """Register a daily series for a cell (deterministic; sorts by ts)."""

        pts = sorted(((t, float(v)) for t, v in points), key=lambda p: p[0])
        self._cells[self._key(tenant_id, metric_key, dimensions)] = MetricSeries(
            tenant_id=tenant_id,
            metric_key=metric_key,
            dimensions=dict(dimensions),
            points=pts,
            unit=unit,
        )

    async def read_series(
        self,
        tenant_id: str,
        metric_key: str,
        dimensions: dict[str, str],
        *,
        lookback_days: int | None = None,
        end: datetime | None = None,
    ) -> MetricSeries:
        cell = self._cells.get(self._key(tenant_id, metric_key, dimensions))
        if cell is None:
            return MetricSeries(tenant_id, metric_key, dict(dimensions), [])
        pts = cell.points
        if end is not None:
            pts = [(t, v) for t, v in pts if t <= end]
        if lookback_days is not None and pts:
            cutoff = end or pts[-1][0]
            from datetime import timedelta

            start = cutoff - timedelta(days=lookback_days)
            pts = [(t, v) for t, v in pts if t >= start]
        return MetricSeries(tenant_id, metric_key, dict(dimensions), pts, unit=cell.unit)

    async def list_cells(self, tenant_id: str) -> list[tuple[str, dict[str, str]]]:
        out: list[tuple[str, dict[str, str]]] = []
        for (t, mk, _dim), cell in self._cells.items():
            if t == tenant_id:
                out.append((mk, dict(cell.dimensions)))
        out.sort(key=lambda c: (c[0], sorted(c[1].items())))
        return out


# ---------------------------------------------------------------------------
# AnalysisResult
# ---------------------------------------------------------------------------
@dataclass
class AnalysisResult:
    """The full output of one ``analyze_metric`` run.

    ``finding`` is ``None`` when no anomaly was detected (the common case). When a
    finding exists, ``bundle`` / ``narration`` / ``forecast`` accompany it, and
    ``embedding`` / ``embedding_model`` are the vector + provenance persisted for
    copilot retrieval. ``persisted`` / ``published`` record whether the IO side-effects
    ran (they only run when a repo / publisher were injected).
    """

    tenant_id: str
    metric_key: str
    dimensions: dict[str, str]
    finding: Finding | None = None
    bundle: EvidenceBundle | None = None
    narration: NarrationResult | None = None
    forecast: Forecast | None = None
    candidate_causes: list[CandidateCause] = field(default_factory=list)
    embedding: list[float] | None = None
    embedding_model: str | None = None
    detected: bool = False
    persisted: bool = False
    published: bool = False
    run_id: UUID | None = None


def _pick_detector(metric_key: str, registry: DetectorRegistry):
    """Choose the detector for a metric: STL level-shift for revenue-like series, else z.

    Revenue / orders / page_views are seasonal level series where a sustained shift is
    the signal of interest (STL). error_rate / latency_p95 are spike-prone -> robust
    z-score point anomalies. Either may be overridden by the caller.
    """

    if metric_key in {"revenue", "orders", "page_views"}:
        return registry.get("stl_seasonal")
    return registry.get("robust_zscore")


def _finding_from_result(
    result: DetectorResult,
    *,
    candidate_causes: Sequence[CandidateCause],
    finding_id: UUID,
    created_at: datetime,
) -> Finding:
    """Fold a scored :class:`DetectorResult` (+ causes) into a :class:`Finding`.

    ``kind`` becomes ROOT_CAUSE when causes were attributed (the finding now carries an
    explanation), else the detector's native kind. ``narrative`` is left ``None`` here
    — narration fills it after the evidence bundle exists.
    """

    kind = FindingKind.ROOT_CAUSE if candidate_causes else result.kind
    return Finding(
        finding_id=finding_id,
        tenant_id="",  # set by caller via model_copy below
        kind=kind,
        metric_key=result.metric_key,
        dimensions=dict(result.dimensions),
        window_start=result.window_start,
        window_end=result.window_end,
        detector=result.detector,
        detector_version=result.detector_version,
        observed_value=result.observed_value,
        expected_value=result.expected_value,
        deviation=result.deviation,
        deviation_pct=result.deviation_pct,
        score=result.score,
        severity=result.severity,
        confidence=result.confidence,
        business_impact_input=result.business_impact_input,
        candidate_causes=list(candidate_causes),
        created_at=created_at,
    )


async def analyze_metric(
    reader: MetricReader,
    metric_key: str,
    dimensions: dict[str, str],
    *,
    tenant_id: str,
    candidates: Sequence[CandidateSeriesSpec] = (),
    narrator: Narrator | None = None,
    repo=None,
    publisher=None,
    embedder: Embedder | None = None,
    registry: DetectorRegistry = DEFAULT_REGISTRY,
    detector_name: str | None = None,
    direction: str | None = None,
    z_threshold: float | None = None,
    stl_period: int | None = None,
    level_shift_k: float | None = None,
    min_shift_run: int | None = None,
    lookback_days: int | None = None,
    forecast_horizon_days: int = 7,
    forecast_interval: float = 0.95,
    end: datetime | None = None,
    now: datetime | None = None,
) -> AnalysisResult:
    """Run the full L3 chain for one metric cell. The pure entrypoint X4 unit-tests.

    Steps: read the target series -> detect -> (no detection -> return early) -> score
    -> read candidate series + rank lag-aware RCA causes -> build the EvidenceBundle ->
    narrate (grounded; template fallback) -> forecast band -> assemble the Finding ->
    persist (if ``repo``) -> publish (if ``publisher``). Returns an
    :class:`AnalysisResult`.

    When ``repo`` / ``publisher`` are ``None`` the side effects are skipped and the
    result is pure analysis. ``narrator`` defaults to a :class:`TemplateNarrator` so the
    chain produces deterministic prose with no LLM and no key.
    """

    now = now or datetime.now(timezone.utc)
    narrator = narrator or TemplateNarrator()

    target = await reader.read_series(
        tenant_id, metric_key, dimensions, lookback_days=lookback_days, end=end
    )
    if len(target.points) < 2:
        return AnalysisResult(tenant_id, metric_key, dict(dimensions))

    direction = direction or _DEFAULT_DIRECTION.get(metric_key, "both")
    ctx = DetectionContext(
        tenant_id=tenant_id,
        metric_key=metric_key,
        dimensions=dict(dimensions),
        unit=target.unit,
        direction=direction,
        z_threshold=z_threshold,
        stl_period=stl_period,
        level_shift_k=level_shift_k,
        min_shift_run=min_shift_run,
    )

    detector = (
        registry.get(detector_name) if detector_name else _pick_detector(metric_key, registry)
    )
    results = detector.detect(target.series, ctx)
    if not results:
        return AnalysisResult(tenant_id, metric_key, dict(dimensions))

    # Most severe detection wins (largest |score|).
    raw = max(results, key=lambda r: abs(r.score))
    scored = score_result(raw, ctx)

    # --- RCA: rank candidate driver series (lag-aware), attribute contribution ---
    causes: list[CandidateCause] = []
    cand_inputs: list[dict] = []
    cand_series: list[Candidate] = []
    for spec in candidates:
        cs = await reader.read_series(
            tenant_id, spec.metric_key, spec.dimensions, lookback_days=lookback_days, end=end
        )
        if len(cs.points) >= 2:
            cand_series.append(Candidate(spec.metric_key, dict(spec.dimensions), cs.series))
            cand_inputs.append(
                {
                    "type": "metric_series",
                    "metric_key": spec.metric_key,
                    "dimensions": spec.dimensions,
                }
            )
    if cand_series:
        ranked = rank_candidate_causes(target.series, cand_series, coincident_band=1)
        causes = contribution_pct_from_causes(ranked)

    finding_id = uuid4()
    # --- forecast band (counterfactual healthy projection) ---
    try:
        forecast: Forecast | None = forecast_series(
            target.series,
            tenant_id=tenant_id,
            metric_key=metric_key,
            dimensions=dict(dimensions),
            horizon_days=forecast_horizon_days,
            interval=forecast_interval,
        )
    except Exception as exc:  # noqa: BLE001 - forecast must never block a finding
        _log.warning("forecast failed; finding emitted without a band", extra={"error": str(exc)})
        forecast = None

    # --- evidence bundle (the ONLY thing the narrator sees) ---
    bundle = build_evidence_bundle(
        tenant_id=tenant_id,
        finding_id=finding_id,
        target=scored,
        candidate_causes=causes,
        forecast=forecast,
        created_at=now,
    )

    # --- grounded narration (template fallback baked into the narrator) ---
    narration = await narrator.narrate(bundle)

    # --- assemble the Finding ---
    finding = _finding_from_result(
        scored, candidate_causes=causes, finding_id=finding_id, created_at=now
    ).model_copy(
        update={
            "tenant_id": tenant_id,
            "narrative": narration.narrative,
            "narrative_model": narration.narrative_model,
            "evidence_ref": bundle.bundle_id,
        }
    )

    # --- embedding for copilot retrieval (voyage-3 or deterministic stub) ---
    embedding: list[float] | None = None
    embedding_model: str | None = None
    if embedder is not None:
        text = _embedding_text(finding, bundle)
        embedding, embedding_model = embed_text(embedder, text, input_type="document")

    result = AnalysisResult(
        tenant_id=tenant_id,
        metric_key=metric_key,
        dimensions=dict(dimensions),
        finding=finding,
        bundle=bundle,
        narration=narration,
        forecast=forecast,
        candidate_causes=causes,
        embedding=embedding,
        embedding_model=embedding_model,
        detected=True,
    )

    # --- persist (evidence bundle FIRST via the repo, then finding + forecast) ---
    if repo is not None:
        await repo.save_finding(
            finding, bundle, embedding=embedding, embedding_model=embedding_model
        )
        if forecast is not None:
            await repo.save_forecast(forecast)
        result.persisted = True

    # --- publish findings + forecasts + lineage ---
    if publisher is not None:
        inputs = [
            {"type": "metric_series", "metric_key": metric_key, "dimensions": dimensions},
            *cand_inputs,
        ]
        result.run_id = await publisher.publish_analysis(
            tenant_id=tenant_id,
            finding=finding,
            forecast=forecast,
            bundle=bundle,
            inputs=inputs,
        )
        result.published = True

    return result


def _embedding_text(finding: Finding, bundle: EvidenceBundle) -> str:
    """Compose the text embedded for copilot retrieval (narrative + key facts).

    Uses the (grounded) narrative plus the metric/dimension identity and the evidence
    item summaries, so semantically-similar findings cluster regardless of which
    narrator (LLM or template) produced the prose.
    """

    dims = " ".join(f"{k}={v}" for k, v in sorted(finding.dimensions.items()))
    parts = [
        f"{finding.metric_key} {dims} {finding.kind.value}",
        finding.narrative or "",
        *[i.summary for i in bundle.items],
    ]
    return "\n".join(p for p in parts if p)
