"""Classical, pluggable anomaly detectors (L3 detection core).

Every detector implements the :class:`~edis_intelligence.detectors.base.Detector`
protocol: it takes a metric series (a list of ``(ts, value)`` or a pandas Series)
plus a :class:`~edis_intelligence.detectors.base.DetectionContext` and returns
:class:`~edis_intelligence.detectors.base.DetectorResult` objects shaped to build a
:class:`edis_contracts.findings.Finding`.

The cores are **pure, deterministic, and unit-testable** with in-memory series —
no DB, no broker, no LLM, no API keys. Detectors are stateless per window, so the
same code runs reactively (stream tap) and on a scheduled batch sweep.
"""

from __future__ import annotations

from edis_intelligence.detectors.base import (
    DetectionContext,
    Detector,
    DetectorResult,
    as_series,
)
from edis_intelligence.detectors.registry import (
    DEFAULT_REGISTRY,
    DetectorRegistry,
    get_detector,
    list_detectors,
    register,
)
from edis_intelligence.detectors.robust_zscore import RobustZScoreDetector
from edis_intelligence.detectors.stl_seasonal import StlSeasonalDetector

__all__ = [
    "DetectionContext",
    "Detector",
    "DetectorResult",
    "as_series",
    "RobustZScoreDetector",
    "StlSeasonalDetector",
    "DetectorRegistry",
    "DEFAULT_REGISTRY",
    "register",
    "get_detector",
    "list_detectors",
]
