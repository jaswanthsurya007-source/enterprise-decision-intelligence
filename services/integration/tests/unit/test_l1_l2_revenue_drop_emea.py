"""THE KEY TEST -- the demo anomaly is detectable from L2 output (no infra).

This proves the end-to-end promise of architecture Sections 3 + 9: feeding the
Phase-2 simulator's ``revenue_drop_emea`` scenario through **L1 ingestion** and
**L2 integration** yields canonical metric series in which the incident is plainly
visible:

* daily **EMEA x web ``revenue``** drops ~30-40% across the incident window vs the
  pre-incident baseline (~$91K/day -> ~$55K/day), and
* daily **``error_rate`` / ``latency_p95`` for ``checkout-api`` in EMEA** spike
  far above baseline (~0.2% -> ~8%, ~178ms -> ~1400ms).

Wiring (one real code path per layer, no Docker):

    simulator.generate_day  (raw source dicts, with ground-truth anomaly_label)
      -> L1 ingestion.pipeline.engine.ingest_record  (coerce/validate/key/envelope)
      -> L2 BatchLoader / process_envelope  (map/clean/coerce/DQ/upsert + metrics)
      -> in-memory repo -> rollup_daily  (the daily series L3 reads)

The BatchLoader is used because the ops ratio/percentile metrics
(``error_rate``/``latency_p95``) are a *bucket* aggregate (a pure function over the
whole window of ops events), exactly as the architecture specifies; the additive
``revenue`` points are derived per order. We bucket ops at DAY granularity so each
incident day yields one ``error_rate``/``latency_p95`` point per (service, region).

Assertions are on window means/maxes (not single days) and are deliberately broad
bands around the §9 magnitudes, so the test proves *detectability* without being
brittle to the simulator's small day-to-day noise.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from edis_contracts.ingest import IngestEnvelope

# L1 ingestion (Phase 2) -- pushed onto sys.path by conftest.
from ingestion.pipeline.engine import IngestOutcome, ingest_record
from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
from ingestion.simulator.generator import SimConfig, generate_day
from ingestion.simulator.scenarios import REVENUE_DROP_EMEA

from edis_integration.consumers.batch_loader import BatchLoader
from edis_integration.mappers.metrics import rollup_daily
from edis_integration.outbox.outbox_repo import InMemoryOutboxRepo
from edis_integration.pipeline.engine import InMemoryIntegrationRepo

_TENANT = "acme"
_SOURCE = "simulator"
_ANCHOR = date(2026, 6, 12)  # incident begins (arch §9: "starting 7 days ago")
_DURATION = 5
_BASELINE_DAYS = 7  # the week before the incident


class _CollectPublisher:
    """A minimal :class:`IngestPublisher`-shaped sink that captures envelopes.

    The L1 ``ingest_record`` only needs ``publish_envelope`` / ``publish_dlq`` on
    its ``ctx_sink``; we capture the envelopes in memory rather than going over a
    bus, since this test drives L2 directly from the captured envelopes.
    """

    def __init__(self) -> None:
        self.envelopes: list[IngestEnvelope] = []
        self.dlq: list = []

    async def publish_envelope(self, env: IngestEnvelope) -> None:
        self.envelopes.append(env)

    async def publish_dlq(self, record) -> None:  # pragma: no cover - no DLQ expected
        self.dlq.append(record)


async def _ingest_day(
    day: date,
    *,
    anomalies,
    cfg: SimConfig,
    publisher: _CollectPublisher,
    idem: InMemoryIdempotencyStore,
) -> None:
    """Generate one simulator day and push every raw record through L1."""

    data = generate_day(datetime(day.year, day.month, day.day, tzinfo=timezone.utc), cfg, anomalies)
    for domain, rows in (("sales", data.sales), ("ops", data.ops)):
        for raw in rows:
            raw = dict(raw)
            # The simulator stamps the ground-truth label on the record; L1 takes
            # it as a separate kwarg (the strict payload model forbids extras).
            anomaly_label = raw.pop("anomaly_label", None)
            res = await ingest_record(
                domain,  # type: ignore[arg-type]
                raw,
                tenant_id=_TENANT,
                source_system=_SOURCE,
                ctx_sink=publisher,
                idem=idem,
                writer=None,
                anomaly_label=anomaly_label,
            )
            # Every simulated record is well-formed -> lands (never DLQ).
            assert res.outcome is IngestOutcome.LANDED, res.error


async def _run_l1_l2(days_with_anomalies: list[tuple[date, list]]) -> list[dict]:
    """Run L1 + L2 over the given days; return the L2 daily metric rollup rows."""

    cfg = SimConfig()
    publisher = _CollectPublisher()
    idem = InMemoryIdempotencyStore()
    for day, anomalies in days_with_anomalies:
        await _ingest_day(day, anomalies=anomalies, cfg=cfg, publisher=publisher, idem=idem)

    repo = InMemoryIntegrationRepo()
    reader = InMemoryOutboxRepo(repo)

    class _NullSink:
        async def publish(self, *a, **k):  # relay publishes here; we ignore it
            return None

    # DAY-bucket ops so each day yields one error_rate / latency_p95 per cell.
    loader = BatchLoader(
        repo,
        _NullSink(),
        reader,
        metric_bucket="day",
        max_records=10_000_000,
    )
    result = await loader.load(publisher.envelopes)
    assert result.quarantined == 0, result.quarantine_ids
    assert result.persisted > 0

    return rollup_daily(repo.metrics)


def _daily_series(rows: list[dict], metric_key: str, dim_key: str) -> dict[date, dict]:
    """Index the rollup rows for one (metric, dimensions) by calendar day."""

    return {
        r["bucket"].date(): r
        for r in rows
        if r["metric_key"] == metric_key and r["dimensions"] == dim_key
    }


# Stable dim-key strings (rollup_daily sorts the dims: "k=v&k=v").
_EMEA_WEB = "channel=web&region=EMEA"
_CHECKOUT_EMEA = "region=EMEA&service=checkout-api"


@pytest.mark.asyncio
async def test_l1_l2_revenue_drop_emea_is_detectable() -> None:
    anomalies = REVENUE_DROP_EMEA(_ANCHOR, _DURATION)

    baseline_days = [
        (_ANCHOR - timedelta(days=_BASELINE_DAYS - i), []) for i in range(_BASELINE_DAYS)
    ]
    incident_days = [(_ANCHOR + timedelta(days=i), anomalies) for i in range(_DURATION)]

    rows = await _run_l1_l2(baseline_days + incident_days)

    # ---------------------------------------------------------------- revenue
    revenue = _daily_series(rows, "revenue", _EMEA_WEB)
    base_rev = [
        revenue[(_ANCHOR - timedelta(days=_BASELINE_DAYS - i))]["sum_value"]
        for i in range(_BASELINE_DAYS)
    ]
    inc_rev = [revenue[(_ANCHOR + timedelta(days=i))]["sum_value"] for i in range(_DURATION)]
    mean_base = sum(base_rev) / len(base_rev)
    mean_inc = sum(inc_rev) / len(inc_rev)
    drop_pct = 100.0 * (1.0 - mean_inc / mean_base)

    # §9 baseline magnitude sanity (~$95K/day; allow a generous band).
    assert 70_000 <= mean_base <= 120_000, mean_base
    # The headline: EMEA-web revenue drops ~30-40% across the incident (demo target
    # is -36%; the band absorbs the simulator's small day-to-day RNG noise around it).
    assert 28.0 <= drop_pct <= 44.0, drop_pct
    # Every incident day is clearly below every baseline day's central tendency.
    assert max(inc_rev) < mean_base

    # ------------------------------------------------------------- error_rate
    err = _daily_series(rows, "error_rate", _CHECKOUT_EMEA)
    base_err = [
        err[(_ANCHOR - timedelta(days=_BASELINE_DAYS - i))]["avg_value"]
        for i in range(_BASELINE_DAYS)
    ]
    inc_err = [err[(_ANCHOR + timedelta(days=i))]["avg_value"] for i in range(_DURATION)]
    mean_base_err = sum(base_err) / len(base_err)
    mean_inc_err = sum(inc_err) / len(inc_err)

    # baseline ~0.4% (noisy, can dip to 0 on a quiet day) -> incident ~9%.
    assert mean_base_err < 0.02, mean_base_err
    assert mean_inc_err > 0.05, mean_inc_err  # well into "percent" territory
    # the incident error rate is an order of magnitude above baseline.
    assert mean_inc_err > 10 * max(mean_base_err, 1e-4)
    # every incident day spikes above every baseline day.
    assert min(inc_err) > max(base_err)

    # ------------------------------------------------------------ latency_p95
    lat = _daily_series(rows, "latency_p95", _CHECKOUT_EMEA)
    base_lat = [
        lat[(_ANCHOR - timedelta(days=_BASELINE_DAYS - i))]["avg_value"]
        for i in range(_BASELINE_DAYS)
    ]
    inc_lat = [lat[(_ANCHOR + timedelta(days=i))]["avg_value"] for i in range(_DURATION)]
    mean_base_lat = sum(base_lat) / len(base_lat)
    mean_inc_lat = sum(inc_lat) / len(inc_lat)

    # baseline ~180ms -> incident ~1400ms.
    assert 120.0 <= mean_base_lat <= 300.0, mean_base_lat
    assert mean_inc_lat > 1_000.0, mean_inc_lat
    assert mean_inc_lat > 5 * mean_base_lat
    # every incident day's p95 spikes above every baseline day's.
    assert min(inc_lat) > max(base_lat)


@pytest.mark.asyncio
async def test_incident_is_isolated_to_emea_web_and_checkout() -> None:
    """The drop is concentrated in EMEA-web; other cells are unaffected.

    Confirms the anomaly is *localized* (so L3's dimensional RCA can attribute it),
    by checking an untouched revenue cell (NA-web) and an untouched ops cell
    (catalog-api/EMEA) stay flat across the same windows.
    """

    anomalies = REVENUE_DROP_EMEA(_ANCHOR, _DURATION)
    baseline_days = [
        (_ANCHOR - timedelta(days=_BASELINE_DAYS - i), []) for i in range(_BASELINE_DAYS)
    ]
    incident_days = [(_ANCHOR + timedelta(days=i), anomalies) for i in range(_DURATION)]
    rows = await _run_l1_l2(baseline_days + incident_days)

    # NA-web revenue: incident mean within +/-20% of baseline mean (no drop).
    na_web = _daily_series(rows, "revenue", "channel=web&region=NA")
    base = [
        na_web[(_ANCHOR - timedelta(days=_BASELINE_DAYS - i))]["sum_value"]
        for i in range(_BASELINE_DAYS)
    ]
    inc = [na_web[(_ANCHOR + timedelta(days=i))]["sum_value"] for i in range(_DURATION)]
    ratio = (sum(inc) / len(inc)) / (sum(base) / len(base))
    assert 0.8 <= ratio <= 1.2, ratio

    # catalog-api/EMEA error_rate stays near baseline (no ops spike there).
    cat = _daily_series(rows, "error_rate", "region=EMEA&service=catalog-api")
    cat_base = [
        cat[(_ANCHOR - timedelta(days=_BASELINE_DAYS - i))]["avg_value"]
        for i in range(_BASELINE_DAYS)
    ]
    cat_inc = [cat[(_ANCHOR + timedelta(days=i))]["avg_value"] for i in range(_DURATION)]
    assert (sum(cat_inc) / len(cat_inc)) < 0.02
    assert (sum(cat_base) / len(cat_base)) < 0.02
