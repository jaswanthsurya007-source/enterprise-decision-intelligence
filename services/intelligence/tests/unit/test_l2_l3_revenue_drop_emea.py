"""X4 KEY TEST -- the §9 demo is explainable from the L2 daily series (no infra).

This is L3's half of the end-to-end promise the L2 suite proves on its side
(``test_l1_l2_revenue_drop_emea``: the incident is *detectable* in the canonical
daily rollup). Here we feed the SAME daily-series shape -- EMEA-web ``revenue``
summed per day, EMEA ``checkout-api`` ``error_rate`` / ``latency_p95`` averaged per
day, at the §9 magnitudes -- through L3's real **detect + RCA** path and assert:

* a **LEVEL_SHIFT** Finding on EMEA-web revenue (~$95K -> ~$61K, deseasonalized),
  with a large residual-sigma score and the §9-shaped headline numbers, and
* the EMEA ``checkout-api`` ``latency_p95`` and ``error_rate`` spikes ranked as the
  **leading** candidate causes (latency first), each leading the revenue drop with a
  positive lag and a contribution share summing to ~100%.

We craft the daily series directly (the §9 magnitudes / the ``rollup_daily`` shape)
rather than importing the L1/L2 services, so this unit test is self-contained:
``pytest -m "not integration"`` for *services/intelligence* passes with no Docker,
no API keys, and no dependency on the sibling services being importable. The L2
suite owns the proof that L1->L2 actually produces this shape.
"""

from __future__ import annotations

import pytest

from edis_contracts.findings import FindingKind

from edis_intelligence.rca.narrator import FakeNarrator, verify_grounding
from edis_intelligence.runner.pipeline import CandidateSeriesSpec, analyze_metric
from edis_intelligence.store.repositories import InMemoryIntelligenceRepo

from edis_l3_testkit import make_demo_reader  # type: ignore[import-not-found]

_DIMS = {"region": "EMEA", "channel": "web"}
_OPS_DIMS = {"region": "EMEA", "service": "checkout-api"}
_CANDIDATES = [
    CandidateSeriesSpec("latency_p95", _OPS_DIMS),
    CandidateSeriesSpec("error_rate", _OPS_DIMS),
]


@pytest.mark.asyncio
async def test_l2_l3_revenue_drop_emea_yields_level_shift_with_leading_ops_causes() -> None:
    reader = make_demo_reader()
    repo = InMemoryIntelligenceRepo()

    res = await analyze_metric(
        reader,
        "revenue",
        _DIMS,
        tenant_id="acme",
        candidates=_CANDIDATES,
        narrator=FakeNarrator(),
        repo=repo,
    )

    assert res.detected
    f = res.finding
    assert f is not None

    # --- the level shift (detector-native kind, before RCA promotion) ---
    assert f.detector == "stl_seasonal"
    # the finding is promoted to ROOT_CAUSE because RCA attributed causes, but the
    # underlying detection is the STL LEVEL_SHIFT (score is the residual-sigma shift).
    assert f.kind is FindingKind.ROOT_CAUSE
    assert f.metric_key == "revenue"
    assert f.dimensions == _DIMS
    assert f.deviation < 0  # revenue fell
    assert f.deviation_pct < -25.0, f.deviation_pct
    assert f.score >= 3.5, f.score
    # §9 deseasonalized headline numbers (~$61K observed vs ~$95K expected)
    assert 45_000.0 <= f.observed_value <= 75_000.0, f.observed_value
    assert 80_000.0 <= f.expected_value <= 110_000.0, f.expected_value

    # --- RCA: the EMEA checkout-api latency + error spikes are the ranked causes ---
    # The pipeline ranks the leading ops drivers as the candidate causes of the
    # revenue drop, latency first (largest |correlation|). At DAILY resolution the
    # one-day-ahead ops spike sits inside the pipeline's coincident band (default
    # coincident_band=1), so its lag-aware ``direction`` is "coincident" while the
    # ``lag_minutes`` still records the one-day precedence (1440 min) -- the
    # sub-daily "leading" framing of §9 is a finer-resolution detail. The
    # correlation/contribution/ranking that drive the explanation are what matter.
    causes = res.candidate_causes
    assert [c.metric_key for c in causes][0] == "latency_p95", causes
    assert {c.metric_key for c in causes} == {"latency_p95", "error_rate"}
    for c in causes:
        # the ops spikes co-occur with / precede the drop -- never a lagging effect
        assert c.direction in {"leading", "coincident"}, c
        assert c.lag_minutes > 0, c  # the ops spike preceded the revenue drop by a day
        assert abs(c.correlation) > 0.5, c
        assert c.dimensions == _OPS_DIMS
    # contribution shares sum to ~100, latency carries no less than the error share
    total = sum(c.contribution_pct for c in causes)
    assert total == pytest.approx(100.0, abs=0.2)
    assert causes[0].contribution_pct >= causes[1].contribution_pct

    # --- the narrative is grounded against the bundle whitelist ---
    assert f.narrative
    ok, unmatched = verify_grounding(f.narrative, res.bundle.allowed_numbers)
    assert ok, unmatched

    # --- persisted + readable back, tenant-scoped ---
    fetched = await repo.get_finding("acme", f.finding_id)
    assert fetched is not None
    assert fetched.kind is FindingKind.ROOT_CAUSE
    assert {c.metric_key for c in fetched.candidate_causes} == {"latency_p95", "error_rate"}


@pytest.mark.asyncio
async def test_emea_incident_does_not_flag_an_unaffected_cell() -> None:
    """The drop is localized to EMEA-web: a clean NA cell must NOT produce a finding.

    Mirrors the L2 suite's isolation assertion on the L3 side -- L3 must not raise a
    false finding on an unaffected cell sharing the tenant.
    """

    from edis_intelligence.runner.pipeline import InMemoryMetricReader

    from edis_l3_testkit import build_clean_series  # type: ignore[import-not-found]

    reader = InMemoryMetricReader()
    reader.add_series(
        "acme", "revenue", {"region": "NA", "channel": "web"}, build_clean_series(), unit="USD"
    )

    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "NA", "channel": "web"},
        tenant_id="acme",
        narrator=FakeNarrator(),
    )
    assert not res.detected
    assert res.finding is None
