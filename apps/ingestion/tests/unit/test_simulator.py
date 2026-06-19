"""Simulator unit tests — deterministic, anomaly-correct, baseline-plausible.

These run with no Postgres/Redpanda/Redis: they call the pure
:func:`ingestion.simulator.generator.generate_day` and drive the connector through
the same :func:`ingestion.pipeline.engine.ingest_record` core over the in-proc
sink + in-memory idempotency store. They assert:

* **Determinism** — the same seed yields byte-identical output, run after run.
* **Baselines (arch §9)** — weekly-avg total revenue ~$420K/day and EMEA-web
  ~$95K/day; the weekly factor redistributes, it does not inflate.
* **Anomaly profiles** — each of spike/drop/drift/outage moves its metric in the
  right direction and stamps ``anomaly_label`` ground truth on exactly the
  affected records (untouched cells stay unlabeled).
* **The named ``revenue_drop_emea`` scenario** — EMEA-web revenue ~-36%, total
  WoW ~-8.3%, ops latency_p95 ~1,400ms and error_rate ~9% on checkout-api, all
  labeled ``outage``.
* **Pipeline integration** — generated records (with labels) flow through the
  shared core and reach ``edis.raw.{sales,ops}.v1``.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ingestion.connectors.base import SimulatorConnector
from ingestion.pipeline.engine import ingest_record
from ingestion.simulator.anomalies import make_anomaly
from ingestion.simulator.generator import (
    CHECKOUT_SERVICE,
    SimConfig,
    generate_day,
)
from ingestion.simulator.scenarios import REVENUE_DROP_EMEA, get_scenario

_MONDAY = datetime(2026, 6, 1, tzinfo=timezone.utc)  # a Monday


def _week_totals(cfg, anomalies=None, start=_MONDAY, n=7):
    return [generate_day(start + timedelta(days=i), cfg, anomalies).total_revenue for i in range(n)]


# --- determinism -------------------------------------------------------------


def test_same_seed_identical_output():
    cfg = SimConfig(seed=42)
    a = generate_day(_MONDAY, cfg)
    b = generate_day(_MONDAY, cfg)
    assert a.sales == b.sales
    assert a.ops == b.ops
    assert a.revenue_by_cell == b.revenue_by_cell


def test_same_seed_identical_over_a_week():
    # Determinism holds across a multi-day span, not just a single day.
    cfg = SimConfig(seed=42)
    run1 = [generate_day(_MONDAY + timedelta(days=i), cfg) for i in range(7)]
    run2 = [generate_day(_MONDAY + timedelta(days=i), cfg) for i in range(7)]
    assert [d.sales for d in run1] == [d.sales for d in run2]
    assert [d.ops for d in run1] == [d.ops for d in run2]


def test_different_seed_differs():
    a = generate_day(_MONDAY, SimConfig(seed=1))
    b = generate_day(_MONDAY, SimConfig(seed=2))
    assert a.sales != b.sales


# --- baselines (arch §9) -----------------------------------------------------


def test_weekly_total_revenue_baseline():
    avg = statistics.mean(_week_totals(SimConfig(seed=42)))
    assert 378_000 <= avg <= 462_000  # ~$420K/day +/- 10%


def test_emea_web_baseline():
    cfg = SimConfig(seed=42)
    ew = [
        generate_day(_MONDAY + timedelta(days=i), cfg).revenue_by_cell[("EMEA", "web")]
        for i in range(7)
    ]
    assert 80_000 <= statistics.mean(ew) <= 110_000  # ~$95K/day


def test_weekly_factor_redistributes_not_inflates():
    # A full week's average equals the un-seasonal baseline (factor mean == 1.0).
    cfg = SimConfig(seed=7, revenue_noise_sigma=0.0)
    avg = statistics.mean(_week_totals(cfg))
    assert abs(avg - cfg.daily_revenue) / cfg.daily_revenue < 0.02


# --- anomaly profiles --------------------------------------------------------


def test_drop_profile_labels_and_reduces_revenue():
    cfg = SimConfig(seed=42)
    anomaly = make_anomaly(
        "drop", start_day=_MONDAY.date(), duration_days=1, region="NA", channel="web", magnitude=0.5
    )
    normal = generate_day(_MONDAY, cfg).revenue_by_cell[("NA", "web")]
    dropped = generate_day(_MONDAY, cfg, [anomaly])
    assert dropped.revenue_by_cell[("NA", "web")] < normal * 0.7
    labeled = [s for s in dropped.sales if s.get("anomaly_label") == "drop"]
    assert labeled and all(s["region"] == "NA" and s["channel"] == "web" for s in labeled)
    # untouched cells carry no label
    assert all(s.get("anomaly_label") is None for s in dropped.sales if s["region"] != "NA")


def test_spike_profile_increases_revenue():
    cfg = SimConfig(seed=42)
    anomaly = make_anomaly(
        "spike",
        start_day=_MONDAY.date(),
        duration_days=1,
        region="APAC",
        channel="web",
        magnitude=1.0,
    )
    normal = generate_day(_MONDAY, cfg).revenue_by_cell[("APAC", "web")]
    spiked = generate_day(_MONDAY, cfg, [anomaly]).revenue_by_cell[("APAC", "web")]
    assert spiked > normal * 1.5


def test_drift_profile_ramps_over_window():
    # A drift accumulates: the last day's loss exceeds the first day's.
    cfg = SimConfig(seed=42, revenue_noise_sigma=0.0)
    anomaly = make_anomaly(
        "drift",
        start_day=_MONDAY.date(),
        duration_days=5,
        region="NA",
        channel="web",
        magnitude=0.4,
    )
    day0 = generate_day(_MONDAY, cfg, [anomaly]).revenue_by_cell[("NA", "web")]
    day4 = generate_day(_MONDAY + timedelta(days=4), cfg, [anomaly]).revenue_by_cell[("NA", "web")]
    base0 = generate_day(_MONDAY, cfg).revenue_by_cell[("NA", "web")]
    base4 = generate_day(_MONDAY + timedelta(days=4), cfg).revenue_by_cell[("NA", "web")]
    loss0 = 1 - day0 / base0
    loss4 = 1 - day4 / base4
    assert loss4 > loss0  # ramp deepens
    labeled = [
        s for s in generate_day(_MONDAY, cfg, [anomaly]).sales if s.get("anomaly_label") == "drift"
    ]
    assert labeled


def test_outage_profile_blows_out_ops_metrics():
    cfg = SimConfig(seed=42)
    anomaly = make_anomaly(
        "outage",
        start_day=_MONDAY.date(),
        duration_days=1,
        region="EMEA",
        service=CHECKOUT_SERVICE,
        latency_peak_mult=7.78,
        error_peak_mult=22.5,
    )
    data = generate_day(_MONDAY, cfg, [anomaly])
    recs = [r for r in data.ops if r["region"] == "EMEA" and r["service"] == CHECKOUT_SERVICE]
    p95 = float(np.percentile([r["latency_ms"] for r in recs], 95))
    err_rate = sum(1 for r in recs if r["level"] == "error") / len(recs)
    assert 1_100 <= p95 <= 1_700  # ~180ms * 7.78 ~= 1,400ms
    assert 0.06 <= err_rate <= 0.13  # ~0.4% * 22.5 ~= 9%
    assert all(r.get("anomaly_label") == "outage" for r in recs)
    # a healthy service in the same region is untouched
    healthy = [r for r in data.ops if r["region"] == "EMEA" and r["service"] != CHECKOUT_SERVICE]
    assert all(r.get("anomaly_label") is None for r in healthy)
    # a different region's checkout-api is untouched too (scoped to EMEA)
    other_region = [r for r in data.ops if r["region"] == "NA" and r["service"] == CHECKOUT_SERVICE]
    assert all(r.get("anomaly_label") is None for r in other_region)


# --- named scenario: revenue_drop_emea ---------------------------------------


def test_revenue_drop_emea_scenario_drops_emea_web_revenue():
    cfg = SimConfig(seed=42)
    anomalies = REVENUE_DROP_EMEA(_MONDAY.date())  # 5-day outage
    normal = generate_day(_MONDAY, cfg).revenue_by_cell[("EMEA", "web")]
    incident = generate_day(_MONDAY, cfg, anomalies).revenue_by_cell[("EMEA", "web")]
    pct = incident / normal - 1
    assert -0.45 <= pct <= -0.27  # target ~-36%


def test_revenue_drop_emea_scenario_spikes_ops():
    cfg = SimConfig(seed=42)
    anomalies = REVENUE_DROP_EMEA(_MONDAY.date())
    data = generate_day(_MONDAY, cfg, anomalies)
    recs = [r for r in data.ops if r["region"] == "EMEA" and r["service"] == CHECKOUT_SERVICE]
    p95 = float(np.percentile([r["latency_ms"] for r in recs], 95))
    err_rate = sum(1 for r in recs if r["level"] == "error") / len(recs)
    assert p95 >= 1_100  # ~1,400ms
    assert err_rate >= 0.06  # ~9%
    assert all(r.get("anomaly_label") == "outage" for r in recs)


def test_revenue_drop_emea_total_wow_decline():
    # Total daily revenue during the incident week drops vs a clean baseline week
    # by roughly -8% (arch §9: ~$420K -> ~$385K, about -8.3% WoW). Use a noiseless
    # config and average over the 5 incident days for a stable comparison.
    cfg = SimConfig(seed=42, revenue_noise_sigma=0.0)
    anomalies = REVENUE_DROP_EMEA(_MONDAY.date(), 5)
    baseline = statistics.mean(_week_totals(cfg, None, _MONDAY, 5))
    incident = statistics.mean(_week_totals(cfg, anomalies, _MONDAY, 5))
    wow = incident / baseline - 1
    assert -0.13 <= wow <= -0.05  # ~-8.3%


def test_get_scenario_unknown_raises():
    with pytest.raises(KeyError):
        get_scenario("does_not_exist")


def test_get_scenario_returns_named():
    assert get_scenario("revenue_drop_emea") is REVENUE_DROP_EMEA


# --- pipeline integration (no infra) -----------------------------------------


@pytest.mark.asyncio
async def test_connector_feeds_pipeline_core_with_labels(publisher, idem):
    cfg = SimConfig(seed=42)
    anomaly = make_anomaly(
        "outage", start_day=_MONDAY.date(), duration_days=1, region="EMEA", service=CHECKOUT_SERVICE
    )
    connector = SimulatorConnector(cfg=cfg, start=_MONDAY, n_days=1, anomalies=[anomaly])

    landed = 0
    labeled = 0
    domains_seen: set[str] = set()
    async for rec in connector.iter_records():
        res = await ingest_record(
            rec.domain,
            rec.raw,
            tenant_id="acme",
            source_system="simulator",
            ctx_sink=publisher,
            idem=idem,
            writer=None,
            anomaly_label=rec.anomaly_label,
        )
        if res.envelope is not None:
            landed += 1
            domains_seen.add(res.envelope.domain)
            if res.envelope.anomaly_label:
                labeled += 1

    assert landed > 0
    assert labeled > 0  # outage labels reached the envelope
    assert {"sales", "ops"} <= domains_seen  # both domains landed via one core
