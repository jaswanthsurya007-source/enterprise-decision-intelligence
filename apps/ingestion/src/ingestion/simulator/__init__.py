"""Deterministic, seed-driven data simulator (I3).

The showcase generator: it produces *correlated, realistic* sales + operations
data with weekly seasonality across regions ``{NA, EMEA, APAC, LATAM}`` and
channels ``{web, partner, direct}``, baselined to the architecture's Section 9
demo shape (total revenue ~$420K/day; EMEA-web ~$95K/day). Anomaly profiles
(:mod:`~ingestion.simulator.anomalies`) — ``spike``, ``drop``, ``drift``,
``outage`` — and the named :data:`~ingestion.simulator.scenarios.REVENUE_DROP_EMEA`
scenario stamp ``anomaly_label`` ground truth on affected records so detection is
evaluable.

Determinism guarantee: the same ``seed`` produces byte-identical output, because
every random draw goes through one ``numpy.random.default_rng(seed)`` whose
substreams are derived deterministically per (day, region, channel).

The pure, infra-free entry point the unit tests call is
:func:`~ingestion.simulator.generator.generate_day`, which returns the sales +
ops records for a single UTC day (with any active anomaly's ground-truth labels
already applied) so anomaly correctness is testable with no broker or database.
"""

from __future__ import annotations

from ingestion.simulator.anomalies import (
    AnomalyProfile,
    AnomalyState,
    PROFILES,
    make_anomaly,
)
from ingestion.simulator.generator import (
    CHANNELS,
    REGIONS,
    DayData,
    SimConfig,
    Simulator,
    generate_day,
)
from ingestion.simulator.scenarios import (
    REVENUE_DROP_EMEA,
    SCENARIOS,
    Scenario,
    get_scenario,
)
from ingestion.simulator.seasonality import (
    channel_mix,
    region_mix,
    weekly_factor,
)

__all__ = [
    "AnomalyProfile",
    "AnomalyState",
    "PROFILES",
    "make_anomaly",
    "CHANNELS",
    "REGIONS",
    "DayData",
    "SimConfig",
    "Simulator",
    "generate_day",
    "REVENUE_DROP_EMEA",
    "SCENARIOS",
    "Scenario",
    "get_scenario",
    "channel_mix",
    "region_mix",
    "weekly_factor",
]
