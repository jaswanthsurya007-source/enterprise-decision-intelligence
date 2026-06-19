"""Named, reproducible anomaly scenarios — the demo's scripted incidents.

A :class:`Scenario` is a factory that, given an *anchor day* (the day the
incident begins) and an optional duration, returns the list of
:class:`~ingestion.simulator.anomalies.AnomalyState`\\ s the generator applies.

The headline scenario is :data:`REVENUE_DROP_EMEA` (arch §9): an EMEA
``checkout-api`` outage that, for ~5 days,

* drives ``latency_p95`` ~180ms -> ~1,400ms (x7.78) and ``error_rate``
  ~0.4% -> ~9% (x22.5) on ``service=checkout-api`` in EMEA, and
* depresses EMEA *web* revenue ~$95K/day -> ~$61K/day (-36%),

which drags total daily revenue from ~$420K to ~$385K (about -8.3% WoW). The
ops failure leads the revenue drop in the same window so L3's lag-correlation
RCA can attribute it. Both the ops and the affected sales records carry
``anomaly_label="outage"`` ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from ingestion.simulator.anomalies import AnomalyState, make_anomaly

#: The revenue-loss fraction for EMEA-web during the outage (-36%).
_EMEA_WEB_DROP = 0.36
#: Latency/error peak multipliers: 180ms*7.78~=1400ms; 0.4%*22.5~=9%.
_OUTAGE_LATENCY_MULT = 7.78
_OUTAGE_ERROR_MULT = 22.5
_DEFAULT_DURATION_DAYS = 5


def _as_date(anchor: date | datetime) -> date:
    return anchor.date() if isinstance(anchor, datetime) else anchor


@dataclass(frozen=True)
class Scenario:
    """A named incident: ``build(anchor_day, duration_days)`` -> anomalies."""

    name: str
    description: str
    build: Callable[[date, int], list[AnomalyState]]

    def __call__(
        self, anchor: date | datetime, duration_days: int | None = None
    ) -> list[AnomalyState]:
        days = duration_days or _DEFAULT_DURATION_DAYS
        return self.build(_as_date(anchor), days)


def _build_revenue_drop_emea(anchor: date, duration_days: int) -> list[AnomalyState]:
    """EMEA checkout-api outage + the consequent EMEA-web revenue drop."""

    outage = make_anomaly(
        "outage",
        start_day=anchor,
        duration_days=duration_days,
        region="EMEA",
        channel="web",
        service="checkout-api",
        magnitude=_EMEA_WEB_DROP,
        latency_peak_mult=_OUTAGE_LATENCY_MULT,
        error_peak_mult=_OUTAGE_ERROR_MULT,
        label="outage",
    )
    return [outage]


REVENUE_DROP_EMEA = Scenario(
    name="revenue_drop_emea",
    description=(
        "EMEA checkout-api outage: latency_p95 ~180ms->~1400ms, error_rate "
        "~0.4%->~9% for ~5 days; EMEA-web revenue ~$95K->~$61K/day (-36%), "
        "total ~-8.3% WoW."
    ),
    build=_build_revenue_drop_emea,
)


#: Registry of named scenarios resolvable by the CLI / control API.
SCENARIOS: dict[str, Scenario] = {
    REVENUE_DROP_EMEA.name: REVENUE_DROP_EMEA,
}


def get_scenario(name: str) -> Scenario:
    """Return the named scenario or raise ``KeyError`` with the known names."""

    try:
        return SCENARIOS[name]
    except KeyError as exc:
        known = ", ".join(sorted(SCENARIOS)) or "<none>"
        raise KeyError(f"unknown scenario {name!r}; known: {known}") from exc
