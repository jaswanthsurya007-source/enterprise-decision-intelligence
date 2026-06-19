"""Pure metric-derivation tests -- additive points + the deterministic ops buckets.

Covers the two kinds of metric the demo's detectability rests on:

* **Additive sales metrics** (:func:`derive_order_metrics`): one ``revenue`` point
  (``amount_base`` USD) and one ``orders`` point (value 1, count) per order,
  stamped at ``order_ts`` with dims ``{region, channel}``.
* **Ratio / percentile ops metrics** (:func:`derive_ops_metrics`): the hourly
  (configurable) bucket aggregator computes ``error_rate = errors/total`` (an
  error is ``level=="error"`` OR ``status_code>=500``) and ``latency_p95`` as the
  95th percentile of ``latency_ms`` in the bucket, at the bucket start. Asserted
  on a crafted ops list with hand-computed expected values, including the bucket
  boundary split, the (service, region) grouping, and the deterministic ordering
  of the output.

All functions are pure (no infra), so the demo math is directly unit-testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from edis_contracts.canonical import (
    CanonicalOrder,
    CanonicalOrderLine,
    OpsEvent,
    SourceRef,
)

from edis_integration.mappers.metrics import (
    derive_order_metrics,
    derive_ops_metrics,
    floor_bucket,
    percentile,
    rollup_daily,
)

_TENANT = "acme"
_REF = SourceRef(source_system="simulator", source_id="x", schema_version=1, match_confidence=1.0)


def _order(
    *,
    amount: str,
    region: str | None = "EMEA",
    channel: str | None = "web",
    ts: datetime,
) -> CanonicalOrder:
    amt = Decimal(amount)
    return CanonicalOrder(
        canonical_order_id=__import__("uuid").uuid4(),
        tenant_id=_TENANT,
        canonical_customer_id=__import__("uuid").uuid4(),
        order_ts=ts,
        currency_base="USD",
        amount_base=amt,
        amount_src=amt,
        currency_src="USD",
        fx_rate=Decimal("1.0"),
        region=region,
        channel=channel,
        line_items=[
            CanonicalOrderLine(
                canonical_product_id=__import__("uuid").uuid4(),
                sku="SKU-A",
                qty=1,
                unit_price_base=amt,
                line_amount_base=amt,
            )
        ],
        source_refs=[_REF],
        record_hash="h",
        created_at=ts,
    )


def _ops(
    *,
    service: str = "checkout-api",
    region: str | None = "EMEA",
    level: str = "info",
    status_code: int | None = 200,
    latency_ms: float | None = 100.0,
    ts: datetime,
) -> OpsEvent:
    return OpsEvent(
        canonical_ops_event_id=__import__("uuid").uuid4(),
        tenant_id=_TENANT,
        service=service,
        region=region,
        level=level,  # type: ignore[arg-type]
        status_code=status_code,
        latency_ms=latency_ms,
        message=None,
        event_ts=ts,
        source_refs=[_REF],
        record_hash="h",
    )


# ---------------------------------------------------------------------------
# Additive sales metrics
# ---------------------------------------------------------------------------
def test_order_metrics_emit_additive_revenue_and_orders_points() -> None:
    ts = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    metrics = derive_order_metrics(_order(amount="387.00", ts=ts))

    by_key = {m.metric_key: m for m in metrics}
    assert set(by_key) == {"revenue", "orders"}

    rev = by_key["revenue"]
    assert rev.value == 387.0
    assert rev.unit == "USD"
    assert rev.ts == ts
    assert rev.dimensions == {"region": "EMEA", "channel": "web"}

    orders = by_key["orders"]
    assert orders.value == 1.0
    assert orders.unit == "count"
    assert orders.dimensions == {"region": "EMEA", "channel": "web"}
    # lineage carried through.
    assert rev.source_refs and rev.source_refs[0].match_confidence == 1.0


def test_revenue_points_sum_additively_per_day_cell() -> None:
    # Two EMEA-web orders in a day; the daily revenue rollup sums them.
    day = datetime(2026, 6, 12, tzinfo=timezone.utc)
    ms = []
    ms += derive_order_metrics(_order(amount="60000", ts=day.replace(hour=2)))
    ms += derive_order_metrics(_order(amount="35000", ts=day.replace(hour=20)))
    # An NA-web order that must NOT pollute the EMEA-web cell.
    ms += derive_order_metrics(
        _order(amount="99999", region="NA", channel="web", ts=day.replace(hour=5))
    )

    rows = rollup_daily(ms)
    emea = [
        r
        for r in rows
        if r["metric_key"] == "revenue" and r["dimensions"] == "channel=web&region=EMEA"
    ]
    assert len(emea) == 1
    assert emea[0]["sum_value"] == 95000.0
    assert emea[0]["sample_count"] == 2


# ---------------------------------------------------------------------------
# Ratio / percentile ops bucket aggregator
# ---------------------------------------------------------------------------
def test_error_rate_counts_error_level_or_5xx() -> None:
    # One hour bucket, 5 events: 2 errors (one level=error, one status 500), the
    # rest healthy -> error_rate = 2/5 = 0.4.
    h = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    events = [
        _ops(level="error", status_code=503, latency_ms=900.0, ts=h.replace(minute=1)),
        _ops(level="info", status_code=500, latency_ms=800.0, ts=h.replace(minute=2)),
        _ops(level="info", status_code=200, latency_ms=100.0, ts=h.replace(minute=3)),
        _ops(level="warn", status_code=404, latency_ms=120.0, ts=h.replace(minute=4)),
        _ops(level="info", status_code=200, latency_ms=110.0, ts=h.replace(minute=5)),
    ]
    metrics = derive_ops_metrics(events, granularity="hour")

    er = [m for m in metrics if m.metric_key == "error_rate"]
    assert len(er) == 1
    assert er[0].value == pytest.approx(0.4)
    assert er[0].unit == "pct"
    assert er[0].ts == h  # stamped at bucket start
    assert er[0].dimensions == {"service": "checkout-api", "region": "EMEA"}


def test_latency_p95_matches_numpy_linear_interpolation() -> None:
    h = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    latencies = [float(x) for x in range(100, 1100, 100)]  # 100..1000, 10 samples
    events = [
        _ops(level="info", status_code=200, latency_ms=lat, ts=h.replace(minute=i))
        for i, lat in enumerate(latencies)
    ]
    metrics = derive_ops_metrics(events, granularity="hour")
    lp = [m for m in metrics if m.metric_key == "latency_p95"]
    assert len(lp) == 1
    # numpy default ('linear') p95 over 100..1000 step 100.
    np = pytest.importorskip("numpy")
    expected = float(np.percentile(latencies, 95))
    assert lp[0].value == pytest.approx(expected)
    assert lp[0].value == pytest.approx(percentile(latencies, 95.0))
    assert lp[0].unit == "ms"


def test_ops_bucket_splits_on_hour_boundary() -> None:
    # Two events in hour 9, one in hour 10 -> two error_rate buckets.
    nine = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    ten = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    events = [
        _ops(level="error", status_code=500, latency_ms=900.0, ts=nine.replace(minute=10)),
        _ops(level="info", status_code=200, latency_ms=100.0, ts=nine.replace(minute=50)),
        _ops(level="error", status_code=500, latency_ms=900.0, ts=ten.replace(minute=5)),
    ]
    metrics = derive_ops_metrics(events, granularity="hour")
    er = {m.ts: m.value for m in metrics if m.metric_key == "error_rate"}
    assert er[nine] == pytest.approx(0.5)  # 1 of 2
    assert er[ten] == pytest.approx(1.0)  # 1 of 1


def test_ops_groups_by_service_and_region() -> None:
    h = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    events = [
        _ops(service="checkout-api", region="EMEA", level="error", status_code=500, ts=h),
        _ops(service="checkout-api", region="NA", level="info", status_code=200, ts=h),
        _ops(service="catalog-api", region="EMEA", level="info", status_code=200, ts=h),
    ]
    metrics = derive_ops_metrics(events, granularity="hour")
    er = {
        (m.dimensions["service"], m.dimensions["region"]): m.value
        for m in metrics
        if m.metric_key == "error_rate"
    }
    assert er[("checkout-api", "EMEA")] == pytest.approx(1.0)
    assert er[("checkout-api", "NA")] == pytest.approx(0.0)
    assert er[("catalog-api", "EMEA")] == pytest.approx(0.0)


def test_ops_aggregator_is_deterministic_and_sorted() -> None:
    h = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    events = [
        _ops(service="b-svc", region="NA", level="info", latency_ms=50.0, ts=h),
        _ops(
            service="a-svc", region="EMEA", level="error", status_code=500, latency_ms=900.0, ts=h
        ),
    ]
    a = derive_ops_metrics(events, granularity="hour")
    b = derive_ops_metrics(list(reversed(events)), granularity="hour")
    # Same input set (any order) -> byte-identical output (sorted by key/dims/ts).
    a_repr = [(m.metric_key, m.dimensions, m.ts, m.value) for m in a]
    b_repr = [(m.metric_key, m.dimensions, m.ts, m.value) for m in b]
    assert a_repr == b_repr
    # Output sorted by (metric_key, service, region, ts).
    keys = [(m.metric_key, m.dimensions.get("service", "")) for m in a]
    assert keys == sorted(keys)


def test_ops_bucket_omits_latency_when_no_samples() -> None:
    h = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    events = [_ops(level="error", status_code=500, latency_ms=None, ts=h)]
    metrics = derive_ops_metrics(events, granularity="hour")
    keys = {m.metric_key for m in metrics}
    assert keys == {"error_rate"}  # latency_p95 omitted (no samples)


def test_floor_bucket_granularities() -> None:
    ts = datetime(2026, 6, 12, 9, 37, 41, 500, tzinfo=timezone.utc)
    assert floor_bucket(ts, "minute") == datetime(2026, 6, 12, 9, 37, tzinfo=timezone.utc)
    assert floor_bucket(ts, "hour") == datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
    assert floor_bucket(ts, "day") == datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc)
