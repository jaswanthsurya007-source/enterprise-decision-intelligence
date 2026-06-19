"""The deterministic correlated sales + ops generator.

This is the showcase. Given a ``seed`` and a calendar day it emits the raw
*source-shaped* records (the messy dicts L1 ingests) for sales and ops, with:

* **Weekly seasonality** and the region/channel revenue mix from
  :mod:`~ingestion.simulator.seasonality`, baselined so total revenue ~$420K/day
  and EMEA x web ~$95K/day (arch §9).
* **Correlated ops**: each region/service has a baseline latency_p95 (~180ms) and
  error_rate (~0.4%); an injected ``outage`` blows these out *and* depresses the
  dependent revenue cell, so the revenue drop and the ops failure co-occur (the
  signal L3's RCA correlates).
* **Ground-truth labels**: any active anomaly stamps ``anomaly_label`` on exactly
  the records it shaped, so detection precision/recall is measurable.

Determinism: a single ``numpy.random.default_rng(seed)`` seeds the run; per-cell
draws use a child generator seeded from ``(seed, day_ordinal, region, channel)``
so the output is independent of iteration order and **identical across runs** for
the same seed.

:func:`generate_day` is the pure, infra-free function the unit tests call to
assert anomaly correctness and baseline magnitudes without any broker/DB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from ingestion.simulator.anomalies import (
    AnomalyEffect,
    AnomalyState,
    combine_ops,
    combine_revenue,
)
from ingestion.simulator.seasonality import (
    cell_revenue_share,
    weekly_factor,
)

REGIONS: tuple[str, ...] = ("NA", "EMEA", "APAC", "LATAM")
CHANNELS: tuple[str, ...] = ("web", "partner", "direct")

#: Service that fronts each region's checkout path (the outage target).
CHECKOUT_SERVICE = "checkout-api"
#: Operational services emitting ops logs per region.
SERVICES: tuple[str, ...] = (CHECKOUT_SERVICE, "catalog-api", "payments-api")

#: A few SKUs so order line shape is believable; price drawn around these.
_SKUS: tuple[tuple[str, float], ...] = (
    ("SKU-CORE-01", 129.0),
    ("SKU-CORE-02", 89.0),
    ("SKU-PRO-01", 349.0),
    ("SKU-PRO-02", 599.0),
    ("SKU-ADDON-01", 39.0),
)


@dataclass
class SimConfig:
    """Tunable baselines for the generator (defaults reproduce arch §9)."""

    seed: int = 42
    tenant_id: str = "acme"
    source_system: str = "simulator"
    #: Mean total revenue per day across all regions/channels (USD).
    daily_revenue: float = 420_000.0
    #: Baseline ops volume (log records) per service/region/day.
    ops_events_per_day: int = 240
    #: Baseline latency_p95 (ms) and error_rate (fraction) for a healthy service.
    base_latency_p95_ms: float = 180.0
    base_error_rate: float = 0.004
    #: Per-cell revenue noise (lognormal sigma) — small, keeps days believable.
    revenue_noise_sigma: float = 0.05


@dataclass
class DayData:
    """The sales + ops source records produced for one UTC day.

    ``sales`` / ``ops`` are lists of *raw* source dicts (the messy shape L1
    ingests). ``revenue_by_cell`` is the realized daily revenue per
    ``(region, channel)`` (ground-truth totals for tests/eval).
    """

    day: datetime
    sales: list[dict] = field(default_factory=list)
    ops: list[dict] = field(default_factory=list)
    revenue_by_cell: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def total_revenue(self) -> float:
        return float(sum(self.revenue_by_cell.values()))


def _child_rng(seed: int, *parts: object) -> np.random.Generator:
    """Deterministic child RNG seeded from ``seed`` + a label tuple.

    Hashing the parts gives an order-independent, reproducible substream per
    cell, so generating one cell never perturbs another (same seed -> same bytes).
    """

    h = hashlib.sha256(("|".join([str(seed), *(str(p) for p in parts)])).encode("utf-8")).digest()
    sub = int.from_bytes(h[:8], "big")
    return np.random.default_rng(sub)


def _day_start_utc(day: datetime) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _spread_timestamps(rng: np.random.Generator, day_start: datetime, n: int) -> list[datetime]:
    """``n`` tz-aware UTC timestamps spread across the day (sorted)."""

    if n <= 0:
        return []
    secs = np.sort(rng.uniform(0, 86_400, size=n))
    return [day_start + timedelta(seconds=float(s)) for s in secs]


def _gen_sales_cell(
    cfg: SimConfig,
    day_start: datetime,
    region: str,
    channel: str,
    target_revenue: float,
    label: str | None,
) -> tuple[list[dict], float]:
    """Generate sales orders for one cell summing ~ ``target_revenue``.

    Returns the raw source dicts and the realized revenue total. Order count is
    derived from the *expected per-order value* (mean SKU base price x mean qty x
    the mean of the price-jitter lognormal) so the realized total tracks
    ``target_revenue`` closely and the §9 baselines (~$420K/day total, ~$95K/day
    EMEA-web) hold without any post-hoc rescaling.
    """

    if target_revenue <= 0:
        return [], 0.0

    rng = _child_rng(cfg.seed, day_start.date().toordinal(), region, channel, "sales")

    # Expected per-order value, so n_orders * E[order] ~= target_revenue.
    mean_base = sum(p for _, p in _SKUS) / len(_SKUS)
    mean_qty = 2.0  # qty drawn from integers[1,4) -> {1,2,3}, mean 2.0
    jitter_mean = float(np.exp(0.08**2 / 2.0))  # E[lognormal(0, 0.08)]
    expected_order_value = mean_base * mean_qty * jitter_mean
    n_orders = max(1, int(round(target_revenue / expected_order_value)))
    timestamps = _spread_timestamps(rng, day_start, n_orders)

    orders: list[dict] = []
    realized = 0.0
    for i, ts in enumerate(timestamps):
        sku_idx = int(rng.integers(0, len(_SKUS)))
        sku, base_price = _SKUS[sku_idx]
        # Price jitter around the SKU base (lognormal, tight) — currency-as-string
        # on purpose so the L1 coercion path is exercised end to end.
        unit_price = float(base_price * rng.lognormal(mean=0.0, sigma=0.08))
        qty = int(rng.integers(1, 4))
        realized += unit_price * qty
        orders.append(
            {
                "order_id": f"SO-{region}-{channel}-{day_start.date().isoformat()}-{i:05d}",
                "customer_id": f"CUST-{region}-{int(rng.integers(0, 5000)):05d}",
                "sku": sku,
                "qty": qty,
                # currency-as-string to exercise coercion (e.g. "129.00").
                "unit_price": f"{unit_price:.2f}",
                "currency": "USD",
                "region": region,
                "channel": channel,
                "order_ts": ts.isoformat(),
            }
        )
    return orders, realized


def _gen_ops_service(
    cfg: SimConfig,
    day_start: datetime,
    region: str,
    service: str,
    effect: AnomalyEffect,
) -> list[dict]:
    """Generate ops-log records for one region/service for the day.

    Healthy baseline latency ~``base_latency_p95_ms`` and error_rate
    ~``base_error_rate``; an active outage effect multiplies both so latency_p95
    blows out and error logs spike. Each ``error``-level record carries an HTTP
    5xx; latency is stamped on every record so L2 can compute latency_p95.
    """

    rng = _child_rng(cfg.seed, day_start.date().toordinal(), region, service, "ops")
    n = int(cfg.ops_events_per_day * float(rng.uniform(0.9, 1.1)))
    timestamps = _spread_timestamps(rng, day_start, n)

    # Latency p50 sits below p95; we draw per-event latency from a lognormal whose
    # scale tracks the (possibly inflated) p95 target.
    latency_p95 = cfg.base_latency_p95_ms * effect.latency_mult
    error_rate = min(0.95, cfg.base_error_rate * effect.error_rate_mult)
    # lognormal params chosen so the ~95th percentile lands near latency_p95.
    sigma = 0.5
    median = latency_p95 / float(np.exp(1.645 * sigma))

    records: list[dict] = []
    for ts in timestamps:
        latency_ms = float(median * rng.lognormal(mean=0.0, sigma=sigma))
        is_error = bool(rng.uniform() < error_rate)
        if is_error:
            level = "error"
            status_code = int(rng.choice([500, 502, 503]))
            message = f"{service} request failed (upstream timeout)"
        else:
            level = "info"
            status_code = 200
            message = None
        rec = {
            "service": service,
            "region": region,
            "level": level,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "message": message,
            "event_ts": ts.isoformat(),
        }
        records.append(rec)
    return records


def generate_day(
    day: datetime,
    cfg: SimConfig,
    anomalies: list[AnomalyState] | None = None,
) -> DayData:
    """Generate one UTC day of correlated sales + ops source records (pure).

    This is the deterministic, infra-free core the tests call. For ``day``:

    1. Compute each ``(region, channel)`` cell's baseline revenue from the global
       daily baseline, the static region/channel mix, and the weekly factor.
    2. Apply any active anomaly's revenue effect (multiplicative) and label.
    3. Emit sales orders summing to the (possibly perturbed) cell revenue.
    4. Emit ops logs per region/service; apply any active outage effect so
       latency_p95 / error_rate blow out on the failing service.

    The same ``(day, cfg.seed, anomalies)`` always yields identical output.
    """

    anomalies = anomalies or []
    day_start = _day_start_utc(day)
    wf = weekly_factor(day_start)

    data = DayData(day=day_start)

    # --- sales ---------------------------------------------------------------
    for region in REGIONS:
        for channel in CHANNELS:
            baseline = cfg.daily_revenue * cell_revenue_share(region, channel) * wf
            # small deterministic per-cell noise around the baseline.
            noise_rng = _child_rng(cfg.seed, day_start.date().toordinal(), region, channel, "noise")
            baseline *= float(noise_rng.lognormal(mean=0.0, sigma=cfg.revenue_noise_sigma))

            rev_effect = combine_revenue(
                [a.revenue_effect(day_start, region, channel) for a in anomalies]
            )
            target = baseline * rev_effect.revenue_mult

            orders, realized = _gen_sales_cell(
                cfg, day_start, region, channel, target, rev_effect.label
            )
            if rev_effect.active:
                for o in orders:
                    o["anomaly_label"] = rev_effect.label
            data.sales.extend(orders)
            data.revenue_by_cell[(region, channel)] = realized

    # --- ops -----------------------------------------------------------------
    for region in REGIONS:
        for service in SERVICES:
            ops_effect = combine_ops([a.ops_effect(day_start, region, service) for a in anomalies])
            recs = _gen_ops_service(cfg, day_start, region, service, ops_effect)
            if ops_effect.active:
                for r in recs:
                    r["anomaly_label"] = ops_effect.label
            data.ops.extend(recs)

    return data


class Simulator:
    """Stateful, deterministic generator producing a sequence of :class:`DayData`.

    Wraps :func:`generate_day` with a config + an active anomaly list. Used by the
    connectors (live stream) and the batch seeder; the heavy lifting stays in the
    pure functions so the class holds only config + the anomaly schedule.
    """

    def __init__(self, cfg: SimConfig | None = None) -> None:
        self.cfg = cfg or SimConfig()
        self.anomalies: list[AnomalyState] = []

    def add_anomaly(self, anomaly: AnomalyState) -> None:
        """Register an anomaly to apply to days within its window."""

        self.anomalies.append(anomaly)

    def clear_anomalies(self) -> None:
        self.anomalies.clear()

    def day(self, day: datetime) -> DayData:
        """Generate one day applying the registered anomalies."""

        return generate_day(day, self.cfg, self.anomalies)

    def days(self, start: datetime, n_days: int):
        """Yield :class:`DayData` for ``n_days`` consecutive days from ``start``."""

        base = _day_start_utc(start)
        for i in range(n_days):
            yield self.day(base + timedelta(days=i))
