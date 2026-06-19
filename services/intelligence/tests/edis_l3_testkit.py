"""Shared, importable test helpers for the L3 (intelligence) suite.

Lives as a plain module (not ``conftest.py``) so it is importable by name from the
test modules even under pytest's ``--import-mode=importlib`` (where ``conftest.py``
is loaded under a private module name and cannot be ``import``-ed directly). The
service ``conftest.py`` adds this directory to ``sys.path`` so ``import
edis_l3_testkit`` resolves from anywhere in the suite.

Everything here is pure + deterministic: the §9 ``revenue_drop_emea`` demo series at
the architecture's magnitudes, built in the same daily-series shape L2's
``rollup_daily`` produces (revenue summed per day; error_rate / latency_p95 averaged
per day) so L3 reads exactly the series the L1->L2 detectability test asserts on.
No infra, no API keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

#: §9 demo anchor + day-of-week seasonality + window sizes.
DEMO_START = datetime(2026, 5, 15, tzinfo=timezone.utc)
DEMO_WEEKLY = [1.05, 1.0, 0.98, 1.02, 1.1, 0.92, 0.93]
DEMO_BASELINE_DAYS = 28
DEMO_INCIDENT_DAYS = 7


def build_demo_series(
    *,
    seed: int = 42,
    baseline_days: int = DEMO_BASELINE_DAYS,
    incident_days: int = DEMO_INCIDENT_DAYS,
    drop_factor: float = 0.64,
) -> tuple[list, list, list]:
    """Build the §9-shaped daily ``(revenue, latency_p95, error_rate)`` series.

    Deterministic for a fixed ``seed``. revenue ~$95K/day weekly-seasonal dropping
    ~36% during the incident; EMEA ``checkout-api`` latency p95 ~180ms -> ~1,400ms
    and error_rate ~0.4% -> ~9%, spiking one day *before* the revenue drop (so RCA
    sees them lead). Each element is a list of ``(ts, value)`` pairs -- the shape
    L2's ``rollup_daily`` yields and the pipeline reader returns.
    """

    rng = np.random.default_rng(seed)
    n = baseline_days + incident_days
    days = [DEMO_START + timedelta(days=i) for i in range(n)]
    rev: list[tuple[datetime, float]] = []
    lat: list[tuple[datetime, float]] = []
    err: list[tuple[datetime, float]] = []
    for i, d in enumerate(days):
        base = 95_000 * DEMO_WEEKLY[d.weekday()] + rng.normal(0, 1500)
        if i >= baseline_days:
            base *= drop_factor
        rev.append((d, base))
        if i >= baseline_days - 1:  # ops spike leads the revenue drop by one day
            lat.append((d, 1400 + rng.normal(0, 40)))
            err.append((d, 0.09 + rng.normal(0, 0.005)))
        else:
            lat.append((d, 180 + rng.normal(0, 10)))
            err.append((d, 0.004 + rng.normal(0, 0.001)))
    return rev, lat, err


def build_clean_series(
    *,
    seed: int = 7,
    days: int = DEMO_BASELINE_DAYS + DEMO_INCIDENT_DAYS,
    level: float = 95_000.0,
) -> list:
    """A stable weekly-seasonal series with NO injected anomaly (must not flag)."""

    rng = np.random.default_rng(seed)
    out: list[tuple[datetime, float]] = []
    for i in range(days):
        d = DEMO_START + timedelta(days=i)
        out.append((d, level * DEMO_WEEKLY[d.weekday()] + rng.normal(0, 1200)))
    return out


def make_demo_reader(seed: int = 42):
    """An :class:`InMemoryMetricReader` pre-loaded with the §9 demo cells."""

    from edis_intelligence.runner.pipeline import InMemoryMetricReader

    rev, lat, err = build_demo_series(seed=seed)
    reader = InMemoryMetricReader()
    reader.add_series("acme", "revenue", {"region": "EMEA", "channel": "web"}, rev, unit="USD")
    reader.add_series(
        "acme", "latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat, unit="ms"
    )
    reader.add_series(
        "acme", "error_rate", {"region": "EMEA", "service": "checkout-api"}, err, unit="pct"
    )
    return reader
