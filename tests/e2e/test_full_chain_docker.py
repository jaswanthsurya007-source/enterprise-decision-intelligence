"""Z3 — the LIVE-STACK full-chain e2e (``@pytest.mark.integration``; needs Docker).

This is the Docker-backed sibling of :mod:`test_full_chain`. Where that test wires the
pure entrypoints in process (no infra, the suite that MUST always pass), this one drives
the SAME demo through the **running compose topology** — real Redpanda, Postgres +
TimescaleDB + pgvector, and the EDIS services — over the network, exactly as the
``make seed`` / ``make demo`` operator flow does (arch §9).

It deliberately reuses the Z1 orchestration in :mod:`scripts.seed_demo` (the one-command
demo driver) rather than re-implementing the HTTP calls:

* :class:`~scripts.seed_demo.EdisClient` — the tenant-scoped async client over the L1
  ingest control API + the gateway snapshots (mints the dev JWT locally).
* :func:`~scripts.seed_demo.run_seed` — load ~90 days of correlated history.
* :func:`~scripts.seed_demo.scenario_inject_body` / ``control_inject`` — inject
  ``revenue_drop_emea`` starting 7 days ago.
* :func:`~scripts.seed_demo.poll_for_demo_facts` + the pure selectors
  (``pick_revenue_finding`` / ``pick_top_recommendation``) — wait for the chain to
  surface the finding + recommendation, then assert the §9 shape.

Skipping rules (so a laptop without the stack up never sees a hard failure):

* The whole module is marked ``@pytest.mark.integration`` -> excluded from the default
  ``pytest -m "not integration"`` selection (and from CI).
* Even under ``-m integration`` it **skips** (not fails) if the gateway / ingest control
  API is not reachable, so ``make test-integration`` degrades gracefully when only the
  infra (not the app services) is up. Bring the full topology up with
  ``make up-apps && make migrate && make seed`` first to exercise it.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _stack_reachable(config) -> bool:
    """True iff both the gateway and the ingest control API answer (else we skip)."""

    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a platform dep
        return False

    async with httpx.AsyncClient(timeout=3.0) as client:
        for base in (config.gateway_base_url, config.ingest_base_url):
            try:
                resp = await client.get(f"{base}/health")
                if resp.status_code >= 500:
                    return False
            except Exception:
                return False
    return True


async def test_live_stack_revenue_drop_emea_full_chain() -> None:
    """Seed + inject ``revenue_drop_emea`` on the live stack; assert the §9 chain output.

    Mirrors ``make seed && make demo``: load history, inject the incident 7 days ago,
    poll the gateway until the EMEA-web revenue finding and its rank-1 recommendation
    appear, and assert the §9 magnitudes + the operational_fix action — the canonical
    demo, proven over real Redpanda + Postgres.
    """

    from scripts.seed_demo import (
        DEFAULT_SCENARIO,
        DemoConfig,
        EdisClient,
        build_scenario,
        pick_revenue_finding,
        pick_top_recommendation,
        poll_for_demo_facts,
        run_seed,
        scenario_inject_body,
    )

    config = DemoConfig.from_env()
    if not await _stack_reachable(config):
        pytest.skip(
            "EDIS live stack not reachable at "
            f"{config.gateway_base_url} / {config.ingest_base_url}; "
            "run `make up-apps && make migrate` first."
        )

    seed = int(os.environ.get("EDIS_DEMO_SEED", "42"))

    # 1. Load ~90 days of correlated history via the L1 control API.
    await run_seed(config, days=90, seed=seed)

    # 2. Inject the revenue_drop_emea incident starting 7 days ago.
    _scenario, anchor, duration = build_scenario(DEFAULT_SCENARIO)
    inject_body = scenario_inject_body(
        scenario=DEFAULT_SCENARIO, anchor_day=anchor, duration_days=duration, seed=seed
    )
    async with await EdisClient(config).open() as client:
        await client.control_inject(inject_body)
        finding, recommendation = await poll_for_demo_facts(client)

    # 3. The chain produced the EMEA-web revenue finding (the demo headline).
    assert finding is not None, "the live chain never surfaced a revenue finding"
    assert finding is pick_revenue_finding([finding])  # the demo's chosen headline finding
    assert finding["metric_key"] == "revenue"
    dims = finding.get("dimensions") or {}
    assert dims.get("region") == "EMEA"
    assert float(finding["deviation_pct"]) < -25.0  # ~-36% drop
    assert float(finding["observed_value"]) < float(finding["expected_value"])

    # 4. And the rank-1 operational_fix recommendation.
    assert recommendation is not None, "the live chain never surfaced a recommendation"
    assert recommendation is pick_top_recommendation([recommendation])
    assert recommendation.get("priority_rank") == 1
    assert recommendation.get("action_type") == "operational_fix"
    impact = recommendation.get("impact") or {}
    assert float(impact.get("value", 0.0)) > 0.0
