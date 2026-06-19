"""Gateway-local response DTOs that the canonical contracts don't already define.

The gateway returns the canonical contract models verbatim wherever one exists
(``Finding`` for anomalies, ``Recommendation`` for recommendations, ``Forecast``
for forecasts) — so the browser receives exactly the ``edis.*.v1`` payload shapes.

The only thing without a dedicated contract is the **KPI snapshot**: the L2 daily
metric rollup (a continuous aggregate over ``metric_observations``) is not a bus
payload, so a thin read DTO is defined here. It is a *projection* of the rollup,
never a new source of truth — every number is a value already computed in L2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KpiSnapshot(BaseModel):
    """One KPI tile: a metric's latest daily-rollup value plus its WoW delta.

    Sourced from the L2 daily metric rollup (continuous aggregate). ``value`` is
    the most recent day's value for ``metric_key`` (optionally dimension-scoped);
    ``previous_value`` is the same-day-last-week value; ``delta_pct`` is the
    week-over-week percent change. All figures are L2-computed; the gateway only
    reshapes them for the dashboard.
    """

    tenant_id: str
    metric_key: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    day: datetime
    value: float
    unit: str | None = None
    previous_value: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    schema_version: int = 1
