"""Pure metric derivation -- additive sales metrics + deterministic ops buckets.

Two kinds of metrics, both produced by **pure functions** over canonical facts
(no I/O, no DB) so the demo math is directly unit-testable:

* **Additive sales metrics** (per :class:`CanonicalOrder`): ``revenue``
  (``amount_base``, USD) and ``orders`` (``1``, count), stamped at ``order_ts``
  with dims ``{region, channel}``. Summing these per day reproduces the daily
  revenue series L3 reads.

* **Ratio / percentile ops metrics** (over a list of :class:`OpsEvent`): the
  events are TIME-BUCKETED per ``(service, region)`` into fixed windows (default
  HOURLY, configurable) and each bucket emits ``error_rate``
  (``errors / total``, a 0..1 fraction, unit ``pct``; an *error* is
  ``level == "error"`` OR ``status_code >= 500``) and ``latency_p95`` (the 95th
  percentile of ``latency_ms`` in the bucket, unit ``ms``) at the bucket start.

The bucket aggregator is deterministic: events are grouped by floored timestamp
and the output is sorted by ``(metric_key, service, region, ts)`` so the same
input list always yields byte-identical output. Daily rollups (revenue summed,
error_rate/latency_p95 averaged/maxed) are L3's job in prod via the Timescale
continuous aggregate; :func:`rollup_daily` computes the same shape from
observation rows when Timescale is absent (so it stays testable).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal

from edis_contracts.canonical import CanonicalOrder, MetricObservation, OpsEvent, SourceRef

BucketGranularity = Literal["minute", "hour", "day"]

_GRANULARITY_DELTA: dict[BucketGranularity, timedelta] = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc(ts: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC (naive is assumed already-UTC)."""

    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def floor_bucket(ts: datetime, granularity: BucketGranularity) -> datetime:
    """Floor ``ts`` to the start of its bucket (tz-aware UTC)."""

    u = _utc(ts)
    if granularity == "minute":
        return u.replace(second=0, microsecond=0)
    if granularity == "hour":
        return u.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return u.replace(hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unknown granularity: {granularity!r}")


def _is_error(ev: OpsEvent) -> bool:
    """An ops event counts as an error if level==error OR status_code>=500."""

    if ev.level == "error":
        return True
    return ev.status_code is not None and ev.status_code >= 500


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy-compatible), pure-Python.

    ``pct`` is in 0..100. Deterministic; matches ``numpy.percentile`` with the
    default ('linear') interpolation so the test math agrees with prod.
    """

    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


def _dims(
    *, region: str | None, channel: str | None = None, service: str | None = None
) -> dict[str, str]:
    """Build a dimension map dropping ``None`` values (stable key order)."""

    out: dict[str, str] = {}
    if service is not None:
        out["service"] = service
    if region is not None:
        out["region"] = region
    if channel is not None:
        out["channel"] = channel
    return out


# ---------------------------------------------------------------------------
# Additive sales metrics
# ---------------------------------------------------------------------------
def derive_order_metrics(order: CanonicalOrder) -> list[MetricObservation]:
    """Derive the additive ``revenue`` + ``orders`` points for one order.

    Both are stamped at ``order.order_ts`` with dims ``{region, channel}`` and
    carry the order's ``source_refs`` for lineage. Summing ``revenue`` per day
    over a region/channel reproduces the daily series the demo's drop appears in.
    """

    dims = _dims(region=order.region, channel=order.channel)
    refs: list[SourceRef] = list(order.source_refs)
    ts = _utc(order.order_ts)
    return [
        MetricObservation(
            tenant_id=order.tenant_id,
            metric_key="revenue",
            ts=ts,
            dimensions=dims,
            value=float(order.amount_base),
            unit="USD",
            source_refs=refs,
        ),
        MetricObservation(
            tenant_id=order.tenant_id,
            metric_key="orders",
            ts=ts,
            dimensions=dims,
            value=1.0,
            unit="count",
            source_refs=refs,
        ),
    ]


# ---------------------------------------------------------------------------
# Ratio / percentile ops metrics (deterministic time-bucket aggregator)
# ---------------------------------------------------------------------------
def derive_ops_metrics(
    events: Iterable[OpsEvent],
    *,
    granularity: BucketGranularity = "hour",
) -> list[MetricObservation]:
    """Aggregate ops events into ``error_rate`` + ``latency_p95`` per bucket.

    Pure function over a list of :class:`OpsEvent`. Events are grouped by
    ``(tenant_id, service, region, floored-ts)``; each group emits:

    * ``error_rate`` = errors / total  (0..1 fraction, unit ``pct``)
    * ``latency_p95`` = p95 of non-null ``latency_ms``  (unit ``ms``) -- omitted
      for a bucket with no latency samples.

    Output is sorted by ``(metric_key, service, region, ts)`` so identical input
    always yields identical output (deterministic). ``source_refs`` for a bucket
    is the concatenation of the contributing events' refs (lineage preserved).
    """

    events = list(events)
    # key -> (tenant, service, region, bucket_ts)
    groups: dict[tuple[str, str, str | None, datetime], list[OpsEvent]] = defaultdict(list)
    for ev in events:
        bucket = floor_bucket(ev.event_ts, granularity)
        groups[(ev.tenant_id, ev.service, ev.region, bucket)].append(ev)

    out: list[MetricObservation] = []
    for (tenant_id, service, region, bucket), bucket_events in groups.items():
        dims = _dims(region=region, service=service)
        refs: list[SourceRef] = [r for ev in bucket_events for r in ev.source_refs]

        total = len(bucket_events)
        errors = sum(1 for ev in bucket_events if _is_error(ev))
        out.append(
            MetricObservation(
                tenant_id=tenant_id,
                metric_key="error_rate",
                ts=bucket,
                dimensions=dims,
                value=(errors / total) if total else 0.0,
                unit="pct",
                source_refs=refs,
            )
        )

        latencies = [ev.latency_ms for ev in bucket_events if ev.latency_ms is not None]
        if latencies:
            out.append(
                MetricObservation(
                    tenant_id=tenant_id,
                    metric_key="latency_p95",
                    ts=bucket,
                    dimensions=dims,
                    value=percentile(latencies, 95.0),
                    unit="ms",
                    source_refs=refs,
                )
            )

    out.sort(
        key=lambda m: (
            m.metric_key,
            m.dimensions.get("service", ""),
            m.dimensions.get("region", ""),
            m.ts,
        )
    )
    return out


# ---------------------------------------------------------------------------
# Daily rollup (Timescale continuous-aggregate parity, computed in-process)
# ---------------------------------------------------------------------------
def rollup_daily(
    observations: Iterable[MetricObservation],
) -> list[dict]:
    """Compute the daily rollup L3 reads, from raw observation rows.

    Mirrors the ``metric_observations_daily`` continuous aggregate shape so a
    plain-Postgres (or in-memory) deployment is testable without TimescaleDB:
    per ``(tenant_id, metric_key, dimensions, day)`` it returns
    ``{sum_value, avg_value, min_value, max_value, sample_count}``. The caller
    chooses which aggregate is meaningful per metric (revenue -> sum;
    error_rate/latency_p95 -> avg/max).

    Returned rows are sorted for deterministic output.
    """

    buckets: dict[tuple[str, str, str, datetime], list[float]] = defaultdict(list)
    for obs in observations:
        day = floor_bucket(obs.ts, "day")
        dim_key = _dim_hash(obs.dimensions)
        buckets[(obs.tenant_id, obs.metric_key, dim_key, day)].append(obs.value)

    rows: list[dict] = []
    for (tenant_id, metric_key, dim_key, day), values in buckets.items():
        n = len(values)
        rows.append(
            {
                "tenant_id": tenant_id,
                "metric_key": metric_key,
                "dimensions": _unhash_dim(dim_key),
                "bucket": day,
                "sum_value": sum(values),
                "avg_value": sum(values) / n if n else 0.0,
                "min_value": min(values) if values else 0.0,
                "max_value": max(values) if values else 0.0,
                "sample_count": n,
            }
        )
    rows.sort(key=lambda r: (r["metric_key"], r["dimensions"], r["bucket"]))
    return rows


def _dim_hash(dimensions: dict[str, str]) -> str:
    """Stable string key for a dimension map (sorted, reversible)."""

    return "&".join(f"{k}={v}" for k, v in sorted(dimensions.items()))


def _unhash_dim(dim_key: str) -> str:
    """Round-trip the dim key back to a comparable string (kept as-is)."""

    return dim_key
