"""The Detector protocol and the shared, contract-shaped detection result.

A detector consumes a single metric series (one ``metric_key`` + ``dimensions``
cell, e.g. EMEA-web ``revenue``) and emits zero or more :class:`DetectorResult`
objects. A result is *detector-native* + computed — it carries the observed and
expected values, the deviation, and a detector-native ``score`` (robust z / STL
residual sigma). The scoring layer (``scoring/normalize.py``) maps a result to the
normalized ``severity`` / ``confidence`` / ``business_impact_input`` fields, and a
later unit folds a result into a full :class:`edis_contracts.findings.Finding`.

Everything here is pure data + a structural :class:`typing.Protocol`. Importing
this module pulls in numpy/pandas only (no DB, broker, or LLM), and the detectors
themselves require no infrastructure.

Series shape
------------
Detectors accept either a list of ``(ts, value)`` tuples (tz-aware UTC timestamps,
the natural shape of the L2 daily rollup rows) or a ``pandas.Series`` indexed by a
``DatetimeIndex``. :func:`as_series` normalizes both into a sorted, tz-aware
``pandas.Series`` so each detector body works against one representation. This is
the same daily series the L1->L2 detectability test produces via ``rollup_daily``
(``sum_value`` for revenue; ``avg_value`` for error_rate/latency_p95).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol, Sequence, Union, runtime_checkable

import numpy as np
import pandas as pd

from edis_contracts.findings import FindingKind

#: A metric series as accepted by every detector: either ``(ts, value)`` pairs or
#: an already-built pandas Series indexed by a tz-aware DatetimeIndex.
SeriesLike = Union[Sequence[tuple[datetime, float]], "pd.Series"]


@dataclass(frozen=True)
class DetectionContext:
    """Identity + tuning context handed to a detector for one series.

    ``metric_key`` / ``dimensions`` identify the cell (used to stamp the resulting
    Finding); ``tenant_id`` keeps detection tenant-scoped end to end. ``direction``
    expresses which way a deviation is *bad* for this metric — for ``revenue`` a
    drop is bad (``"down"``); for ``error_rate`` / ``latency_p95`` a rise is bad
    (``"up"``); ``"both"`` flags either direction. The detectors flag anomalies in
    either direction regardless; ``direction`` only feeds severity/impact scoring.
    """

    tenant_id: str
    metric_key: str
    dimensions: dict[str, str] = field(default_factory=dict)
    unit: str | None = None
    #: Which deviation direction is adverse for this metric.
    direction: str = "both"  # "up" | "down" | "both"
    #: Optional override knobs (else the detector's own defaults are used).
    z_threshold: float | None = None
    stl_period: int | None = None
    level_shift_k: float | None = None
    min_shift_run: int | None = None


@dataclass(frozen=True)
class DetectorResult:
    """One computed detection over a series window — the LLM never overrides these.

    Maps directly onto the computed fields of :class:`edis_contracts.findings.Finding`.
    ``score`` is detector-native (robust z-score or STL residual sigma); the
    normalized 0..1 ``severity`` / ``confidence`` / ``business_impact_input`` are
    filled by ``scoring/normalize.py`` (left at ``0.0`` here so the detector core
    has no scoring policy baked in).
    """

    detector: str
    detector_version: str
    kind: FindingKind
    metric_key: str
    dimensions: dict[str, str]
    window_start: datetime
    window_end: datetime
    observed_value: float
    expected_value: float
    deviation: float
    deviation_pct: float
    score: float
    #: Filled by the scoring layer; detectors leave these at 0.0.
    severity: float = 0.0
    confidence: float = 0.0
    business_impact_input: float = 0.0
    #: Free-form computed diagnostics (e.g. mad, residual_sigma, run_length). All
    #: values are floats so they can join the EvidenceBundle.allowed_numbers set.
    diagnostics: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class Detector(Protocol):
    """Structural protocol every detector satisfies.

    Stateless per window; ``detect`` is pure (no I/O). Returns the (possibly empty)
    list of computed detections for the given series under ``ctx``.
    """

    #: Stable registry name (e.g. ``"robust_zscore"``, ``"stl_seasonal"``).
    name: str
    #: Detector version string stamped onto the Finding (``detector_version``).
    version: str

    def detect(self, series: SeriesLike, ctx: DetectionContext) -> list[DetectorResult]: ...


# ---------------------------------------------------------------------------
# Series normalization + shared numeric helpers (pure)
# ---------------------------------------------------------------------------
def _utc(ts: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC (naive is assumed already-UTC)."""

    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def as_series(series: SeriesLike) -> "pd.Series":
    """Normalize a :data:`SeriesLike` into a sorted, tz-aware float pandas Series.

    Accepts ``(ts, value)`` pairs or an existing Series; returns a Series indexed
    by a tz-aware UTC :class:`pandas.DatetimeIndex`, sorted ascending, with float
    values. Duplicated timestamps keep the last value (deterministic). Raises
    :class:`ValueError` on an empty input.
    """

    if isinstance(series, pd.Series):
        s = series.copy()
        if not isinstance(s.index, pd.DatetimeIndex):
            s.index = pd.to_datetime(s.index, utc=True)
        elif s.index.tz is None:
            s.index = s.index.tz_localize("UTC")
        else:
            s.index = s.index.tz_convert("UTC")
        s = s.astype(float)
    else:
        pairs = list(series)
        if not pairs:
            raise ValueError("cannot build a series from an empty input")
        idx = pd.DatetimeIndex([_utc(ts) for ts, _ in pairs])
        vals = np.asarray([float(v) for _, v in pairs], dtype=float)
        s = pd.Series(vals, index=idx)

    if s.empty:
        raise ValueError("cannot build a series from an empty input")
    s = s[~s.index.duplicated(keep="last")]
    s = s.sort_index()
    s.name = getattr(series, "name", None) if isinstance(series, pd.Series) else None
    return s


def median_abs_deviation(values: "np.ndarray | Iterable[float]") -> tuple[float, float]:
    """Return ``(median, MAD)`` for ``values`` (raw MAD, *not* sigma-scaled).

    MAD = median(|x - median(x)|). Robust to outliers (50% breakdown). The caller
    scales by ``1/0.6745`` to approximate sigma when needed.
    """

    arr = np.asarray(list(values), dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return med, mad


#: Iglewicz/Hoaglin constant: 0.6745 = norm.ppf(0.75); MAD/0.6745 ≈ sigma.
MAD_TO_SIGMA = 1.0 / 0.6745
