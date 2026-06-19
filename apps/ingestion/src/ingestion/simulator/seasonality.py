"""Weekly seasonality + region/channel mix — the deterministic baseline shape.

Pure functions only (no RNG, no IO): given a calendar day they return the
multiplicative weekly-seasonal factor and the static region/channel revenue
splits. The generator multiplies the global daily revenue baseline (~$420K) by
these to land on the architecture's Section 9 numbers — in particular
EMEA x web ~= $95K/day:

    420_000 * region_mix["EMEA"] (0.30) * channel_mix["web"] (0.55)
        * weekly_factor(avg ~= 1.02) ~= 95_000

The weekly factor encodes a realistic business-week rhythm: weekdays run hotter
than weekends, with a mid-week peak. The factors are normalized so a *full week*
averages 1.0, meaning the weekly pattern redistributes revenue within a week
without changing the weekly total — so a 7-day average reproduces the baseline.
"""

from __future__ import annotations

from datetime import datetime

# Region share of total revenue (sums to 1.0). EMEA at 0.34 and web-dominant
# channel mix below put EMEA x web at ~22.6% of total -> ~$95K/day on a ~$420K/day
# baseline (arch §9).
_REGION_MIX: dict[str, float] = {
    "NA": 0.38,
    "EMEA": 0.34,
    "APAC": 0.18,
    "LATAM": 0.10,
}

# Channel share within a region (sums to 1.0). web dominant: 0.34 * 0.66 ~= 0.224.
_CHANNEL_MIX: dict[str, float] = {
    "web": 0.66,
    "partner": 0.21,
    "direct": 0.13,
}

# Day-of-week multipliers (Mon=0 .. Sun=6), pre-normalized to average exactly 1.0
# over a 7-day week. Mid-week peak, weekend trough — a believable B2B rhythm.
_WEEKDAY_RAW: dict[int, float] = {
    0: 1.05,  # Mon
    1: 1.12,  # Tue
    2: 1.15,  # Wed (peak)
    3: 1.10,  # Thu
    4: 1.00,  # Fri
    5: 0.80,  # Sat
    6: 0.78,  # Sun (trough)
}
_WEEKDAY_MEAN = sum(_WEEKDAY_RAW.values()) / 7.0
_WEEKLY_FACTOR: dict[int, float] = {dow: raw / _WEEKDAY_MEAN for dow, raw in _WEEKDAY_RAW.items()}


def weekly_factor(day: datetime) -> float:
    """Multiplicative weekly-seasonal factor for ``day`` (averages 1.0 per week)."""

    return _WEEKLY_FACTOR[day.weekday()]


def region_mix() -> dict[str, float]:
    """Return a copy of the region revenue-share map (sums to 1.0)."""

    return dict(_REGION_MIX)


def channel_mix() -> dict[str, float]:
    """Return a copy of the channel revenue-share map (sums to 1.0)."""

    return dict(_CHANNEL_MIX)


def cell_revenue_share(region: str, channel: str) -> float:
    """Fraction of total daily revenue attributable to ``(region, channel)``."""

    return _REGION_MIX[region] * _CHANNEL_MIX[channel]
