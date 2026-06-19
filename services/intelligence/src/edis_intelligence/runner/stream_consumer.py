"""Reactive tap on ``edis.metrics.points.v1`` — the real-time L3 trigger.

The real-time path (architecture §3): L3 subscribes to the metric stream via
``make_source`` and reacts as points land. Because detection needs a *window* (a
single point isn't an anomaly), this consumer buffers incoming :class:`MetricPoint`s
per cell into a rolling daily series, and when a cell accumulates enough history it
runs :func:`~edis_intelligence.runner.pipeline.analyze_metric` for that cell against an
adapter that reads from the buffer.

The buffer is the :class:`MetricReader` the pipeline uses, so the reactive path runs the
*identical* analysis chain as the scheduled sweep — downstream can't tell which trigger
produced a finding. Per-cell debouncing (only re-analyze after ``min_points_between``
new points) keeps a busy stream from re-running on every single tick.

Collaborators (source, narrator, repo, publisher, embedder, candidate map) are
injected; :meth:`run` loops until :meth:`stop`. Building it connects to nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Mapping, Sequence

from edis_contracts import topics
from edis_contracts.events import MetricPoint
from edis_platform.bus.base import MessageSource, parse_message
from edis_platform.logging import get_logger

from edis_intelligence.grounding.embeddings import Embedder
from edis_intelligence.rca.narrator import Narrator
from edis_intelligence.runner.pipeline import (
    CandidateSeriesSpec,
    InMemoryMetricReader,
    analyze_metric,
)

_log = get_logger(__name__)


def _dim_key(dimensions: Mapping[str, str]) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted(dimensions.items()))


class MetricStreamConsumer:
    """Buffers ``edis.metrics.points.v1`` into rolling series and analyzes reactively."""

    def __init__(
        self,
        source: MessageSource,
        *,
        narrator: Narrator | None = None,
        repo=None,
        publisher=None,
        embedder: Embedder | None = None,
        candidates: Mapping[str, Sequence[CandidateSeriesSpec]] | None = None,
        group: str = "edis-intelligence",
        min_points: int = 14,
        min_points_between: int = 1,
        max_buffer: int = 400,
        forecast_horizon_days: int = 7,
        forecast_interval: float = 0.95,
    ) -> None:
        self._source = source
        self._narrator = narrator
        self._repo = repo
        self._publisher = publisher
        self._embedder = embedder
        self._candidates = dict(candidates or {})
        self._group = group
        self._min_points = int(min_points)
        self._min_between = int(min_points_between)
        self._max_buffer = int(max_buffer)
        self._horizon = int(forecast_horizon_days)
        self._fc_interval = float(forecast_interval)

        # The buffer doubles as the MetricReader the pipeline reads from.
        self._reader = InMemoryMetricReader()
        # Raw rolling points + identity, keyed by (tenant, metric, dim_key).
        self._buf: dict[tuple[str, str, str], list[tuple[datetime, float]]] = defaultdict(list)
        self._dims: dict[tuple[str, str, str], dict[str, str]] = {}
        self._since_analyzed: dict[tuple[str, str, str], int] = defaultdict(int)
        self._running = False

    @property
    def reader(self) -> InMemoryMetricReader:
        """The rolling-window reader (also usable by the scheduler / tests)."""

        return self._reader

    def ingest(self, point: MetricPoint) -> tuple[str, str, str]:
        """Add one point to its cell's rolling buffer; return the cell key.

        Pure-ish bookkeeping (no analysis) so tests can drive the buffer directly.
        Caps the buffer at ``max_buffer`` (drops oldest) and re-registers the series on
        the in-memory reader so the pipeline sees the current window.
        """

        key = (point.tenant_id, point.metric_key, _dim_key(point.dimensions))
        self._dims[key] = dict(point.dimensions)
        pts = self._buf[key]
        pts.append((point.ts, float(point.value)))
        pts.sort(key=lambda p: p[0])
        if len(pts) > self._max_buffer:
            del pts[: len(pts) - self._max_buffer]
        self._reader.add_series(
            point.tenant_id, point.metric_key, point.dimensions, pts, unit=point.unit
        )
        self._since_analyzed[key] += 1
        return key

    def _should_analyze(self, key: tuple[str, str, str]) -> bool:
        return (
            len(self._buf[key]) >= self._min_points
            and self._since_analyzed[key] >= self._min_between
        )

    async def _maybe_analyze(self, point: MetricPoint, key: tuple[str, str, str]) -> None:
        if not self._should_analyze(key):
            return
        self._since_analyzed[key] = 0
        candidates = tuple(self._candidates.get(point.metric_key, ()))
        try:
            res = await analyze_metric(
                self._reader,
                point.metric_key,
                self._dims[key],
                tenant_id=point.tenant_id,
                candidates=candidates,
                narrator=self._narrator,
                repo=self._repo,
                publisher=self._publisher,
                embedder=self._embedder,
                forecast_horizon_days=self._horizon,
                forecast_interval=self._fc_interval,
            )
            if res.detected:
                _log.info(
                    "stream finding",
                    extra={
                        "tenant_id": point.tenant_id,
                        "metric_key": point.metric_key,
                        "kind": res.finding.kind.value if res.finding else None,
                        "narrative_source": res.narration.source if res.narration else None,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - one bad cell must not kill the tap
            _log.warning(
                "stream analysis failed",
                extra={"metric_key": point.metric_key, "error": str(exc)},
            )

    async def run(self) -> None:
        """Subscribe to the metric stream and analyze reactively until :meth:`stop`."""

        self._running = True
        await self._source.start()
        _log.info("metric stream tap started", extra={"group": self._group})
        try:
            async for msg in self._source.subscribe([topics.METRICS_POINTS], group=self._group):
                if not self._running:
                    break
                parsed = parse_message(msg)
                if not isinstance(parsed, MetricPoint):
                    continue
                key = self.ingest(parsed)
                await self._maybe_analyze(parsed, key)
        finally:
            await self._source.stop()
            _log.info("metric stream tap stopped")

    async def stop(self) -> None:
        """Signal the run loop to exit and stop the source."""

        self._running = False
        await self._source.stop()
