"""The L3 runner: it ties detect -> score -> RCA -> evidence -> narrate -> forecast
-> persist -> publish together over a metric source.

* :mod:`~edis_intelligence.runner.pipeline` — the :class:`MetricReader` interface (with
  an :class:`InMemoryMetricReader` fake), the pure ``analyze_metric`` entrypoint, and
  :class:`AnalysisResult`. X4 unit-tests the whole chain with the in-memory reader + a
  FakeNarrator and no infrastructure.
* :mod:`~edis_intelligence.runner.scheduler` — an APScheduler periodic sweep (the batch
  trigger): on each tick it analyzes the configured metric cells.
* :mod:`~edis_intelligence.runner.stream_consumer` — the reactive tap on
  ``edis.metrics.points.v1`` via ``make_source`` (the real-time trigger).
"""

from __future__ import annotations

from edis_intelligence.runner.pipeline import (
    AnalysisResult,
    CandidateSeriesSpec,
    InMemoryMetricReader,
    MetricReader,
    MetricSeries,
    analyze_metric,
)
from edis_intelligence.runner.scheduler import IntelligenceScheduler, SweepSpec
from edis_intelligence.runner.stream_consumer import MetricStreamConsumer

__all__ = [
    "AnalysisResult",
    "CandidateSeriesSpec",
    "InMemoryMetricReader",
    "MetricReader",
    "MetricSeries",
    "analyze_metric",
    "IntelligenceScheduler",
    "SweepSpec",
    "MetricStreamConsumer",
]
