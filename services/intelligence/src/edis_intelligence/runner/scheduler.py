"""APScheduler periodic sweep — the batch trigger for L3 analysis.

The batch path (architecture §3): rather than reacting to each metric point, the
scheduler wakes on a fixed interval and re-analyzes a configured set of metric cells
(each a :class:`SweepSpec`) over the metric store. This is how the 90-day seed and any
backfill get findings: L3 sweeps on a schedule rather than reactively.

Collaborators are injected (reader, narrator, repo, publisher, embedder) and each
sweep just calls :func:`~edis_intelligence.runner.pipeline.analyze_metric` per spec.
Building the scheduler starts nothing; :meth:`start` adds the job and starts the
``AsyncIOScheduler`` (APScheduler), :meth:`stop` shuts it down. :meth:`sweep_once` runs
one full pass synchronously (used by tests and the manual trigger), so the sweep logic
is exercised without waiting on a timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from edis_platform.logging import get_logger

from edis_intelligence.grounding.embeddings import Embedder
from edis_intelligence.rca.narrator import Narrator
from edis_intelligence.runner.pipeline import (
    AnalysisResult,
    CandidateSeriesSpec,
    MetricReader,
    analyze_metric,
)

_log = get_logger(__name__)


@dataclass(frozen=True)
class SweepSpec:
    """One metric cell to analyze each sweep, plus its RCA candidate drivers."""

    tenant_id: str
    metric_key: str
    dimensions: dict[str, str] = field(default_factory=dict)
    candidates: tuple[CandidateSeriesSpec, ...] = ()


class IntelligenceScheduler:
    """Periodic APScheduler sweep over configured metric cells."""

    def __init__(
        self,
        reader: MetricReader,
        specs: Sequence[SweepSpec],
        *,
        narrator: Narrator | None = None,
        repo=None,
        publisher=None,
        embedder: Embedder | None = None,
        interval_seconds: float = 300.0,
        forecast_horizon_days: int = 7,
        forecast_interval: float = 0.95,
    ) -> None:
        self._reader = reader
        self._specs = list(specs)
        self._narrator = narrator
        self._repo = repo
        self._publisher = publisher
        self._embedder = embedder
        self._interval = float(interval_seconds)
        self._horizon = int(forecast_horizon_days)
        self._fc_interval = float(forecast_interval)
        self._scheduler = None

    async def sweep_once(self) -> list[AnalysisResult]:
        """Analyze every configured cell once; return the results (errors logged, not raised)."""

        results: list[AnalysisResult] = []
        for spec in self._specs:
            try:
                res = await analyze_metric(
                    self._reader,
                    spec.metric_key,
                    dict(spec.dimensions),
                    tenant_id=spec.tenant_id,
                    candidates=spec.candidates,
                    narrator=self._narrator,
                    repo=self._repo,
                    publisher=self._publisher,
                    embedder=self._embedder,
                    forecast_horizon_days=self._horizon,
                    forecast_interval=self._fc_interval,
                )
                results.append(res)
                if res.detected:
                    _log.info(
                        "sweep finding",
                        extra={
                            "tenant_id": spec.tenant_id,
                            "metric_key": spec.metric_key,
                            "kind": res.finding.kind.value if res.finding else None,
                            "narrative_source": res.narration.source if res.narration else None,
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - one bad cell must not kill the sweep
                _log.warning(
                    "sweep cell failed",
                    extra={
                        "tenant_id": spec.tenant_id,
                        "metric_key": spec.metric_key,
                        "error": str(exc),
                    },
                )
        return results

    async def start(self) -> None:
        """Start the periodic APScheduler job (no-op if already running)."""

        if self._scheduler is not None:
            return
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.sweep_once,
            "interval",
            seconds=self._interval,
            id="edis-intelligence-sweep",
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        _log.info(
            "intelligence sweep scheduled",
            extra={"interval_seconds": self._interval, "cells": len(self._specs)},
        )

    async def stop(self) -> None:
        """Stop the APScheduler (best-effort)."""

        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
            self._scheduler = None
