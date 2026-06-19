#!/usr/bin/env python
"""EDIS seed + demo orchestration (Z1) — the one-command live-demo driver.

This is the script behind ``make seed`` and ``make demo``. It targets the
**running** docker-compose stack (``make up`` first): it talks to the L1 ingest
control API to load history / inject the scenario, and polls the API Gateway / BFF
for the resulting anomaly + recommendation, then prints the end-to-end story and
the grounded copilot answer (arch §9).

Two subcommands::

    python scripts/seed_demo.py seed   # tenant acme + roles + calibration prior + ~90d history
    python scripts/seed_demo.py demo   # inject revenue_drop_emea (7d ago), poll, tell the story

Design split (the load-bearing convention for Z1):

* **Pure, side-effect-free, importable functions** carry all the logic that can
  be unit-tested with no infra — scenario construction (:func:`build_scenario`,
  :func:`scenario_inject_body`, :func:`seed_request_body`) and story / copilot-answer
  formatting (:func:`format_story`, :func:`format_copilot_answer`, :func:`summarize_*`).
  These never open a socket, read the clock destructively, or import infra.
* **Thin async I/O shell** (:class:`EdisClient`, :func:`run_seed`, :func:`run_demo`)
  wires those pure functions to ``httpx`` against the live stack. URLs + the dev JWT
  come from ``EDIS_*`` env (see :class:`DemoConfig`).

Nothing here requires an API key: the copilot answer printed by ``demo`` is formatted
from the *computed* facts the gateway returns (the finding + recommendation), so it
reproduces the §9 answer shape grounded in real polled numbers even when the copilot
is running offline.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ``scripts`` is importable as a package (scripts/__init__.py) so the unit test can
# ``from scripts.seed_demo import ...``; these imports resolve once apps/* are
# installed (``make install``), but the pure functions below never touch them, so
# the test suite can import this module with only the simulator package present.
from ingestion.simulator import (
    REVENUE_DROP_EMEA,
    Scenario,
    get_scenario,
)

# ---------------------------------------------------------------------------
# Constants — the §9 demo shape (tenant, scenario, baselines used for narration).
# ---------------------------------------------------------------------------
DEMO_TENANT_ID = "acme"
DEMO_USER_ID = "demo-operator"
#: Control actions + ingest writes require the operator role; copilot needs analyst.
DEMO_ROLES: tuple[str, ...] = ("operator", "analyst", "admin")
DEMO_SCOPES: tuple[str, ...] = ("metrics:read", "findings:read", "recommendations:read")

DEFAULT_SEED = 42
DEFAULT_HISTORY_DAYS = 90
DEFAULT_SCENARIO = REVENUE_DROP_EMEA.name  # "revenue_drop_emea"
#: The scenario begins this many days before "now" (arch §9: "starting 7 days ago").
DEFAULT_SCENARIO_START_DAYS_AGO = 7
DEFAULT_SCENARIO_DURATION_DAYS = 5


# ---------------------------------------------------------------------------
# Configuration (env-driven; matches the EDIS_* convention)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DemoConfig:
    """Where the live stack is and how to authenticate to it.

    Read from ``EDIS_*`` env with laptop-friendly compose defaults: the ingestion
    container publishes ``8001:8000`` and the gateway ``8000:8000`` (see
    ``docker-compose.yml``). The JWT is minted locally (HS256) with the same secret
    the services validate against, so no live IdP is needed.
    """

    ingest_base_url: str = "http://localhost:8001"
    gateway_base_url: str = "http://localhost:8000"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    tenant_id: str = DEMO_TENANT_ID
    request_timeout_s: float = 30.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DemoConfig:
        e = env if env is not None else dict(os.environ)
        return cls(
            ingest_base_url=e.get("EDIS_INGEST_BASE_URL", cls.ingest_base_url),
            gateway_base_url=e.get("EDIS_GATEWAY_BASE_URL", cls.gateway_base_url),
            jwt_secret=e.get("EDIS_JWT_SECRET", cls.jwt_secret),
            jwt_algorithm=e.get("EDIS_JWT_ALGORITHM", cls.jwt_algorithm),
            tenant_id=e.get("EDIS_DEMO_TENANT", cls.tenant_id),
            request_timeout_s=float(e.get("EDIS_DEMO_TIMEOUT_S", str(cls.request_timeout_s))),
        )


# ===========================================================================
# PURE FUNCTIONS — scenario construction (side-effect-free, unit-testable)
# ===========================================================================
def scenario_anchor_day(
    now: datetime, *, start_days_ago: int = DEFAULT_SCENARIO_START_DAYS_AGO
) -> date:
    """The UTC calendar day the scenario incident begins (``now`` - N days).

    Pure: ``now`` is injected so the test can pin it. Arch §9 starts the incident
    "7 days ago".
    """

    return (now.astimezone(UTC) - timedelta(days=start_days_ago)).date()


def build_scenario(
    name: str = DEFAULT_SCENARIO,
    *,
    now: datetime | None = None,
    start_days_ago: int = DEFAULT_SCENARIO_START_DAYS_AGO,
    duration_days: int = DEFAULT_SCENARIO_DURATION_DAYS,
) -> tuple[Scenario, date, int]:
    """Resolve a named scenario + its anchor day + duration (pure).

    Returns ``(scenario, anchor_day, duration_days)``. Raises ``KeyError`` (with the
    known names) for an unknown scenario — the same contract as
    :func:`ingestion.simulator.get_scenario`.
    """

    scenario = get_scenario(name)
    anchor = scenario_anchor_day(now or datetime.now(UTC), start_days_ago=start_days_ago)
    return scenario, anchor, duration_days


def seed_request_body(
    *,
    days: int = DEFAULT_HISTORY_DAYS,
    seed: int = DEFAULT_SEED,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Body for ``POST /v1/control/seed`` (matches ``SeedRequest``) — pure.

    History is seeded *without* the scenario by default (``scenario=None``); the
    incident is injected separately by ``demo`` so it lands "7 days ago" regardless
    of when the 90-day baseline was loaded.
    """

    return {"days": int(days), "seed": int(seed), "scenario": scenario}


def scenario_inject_body(
    *,
    scenario: str = DEFAULT_SCENARIO,
    anchor_day: date,
    duration_days: int = DEFAULT_SCENARIO_DURATION_DAYS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Body for ``POST /v1/control/simulator/inject`` (matches ``InjectRequest``) — pure.

    Sends the named scenario with its timing params; the control API stamps
    ``anomaly_label`` ground truth downstream (I3 owns that). Exactly one of
    ``profile``/``scenario`` may be set — we always send ``scenario``.
    """

    return {
        "scenario": scenario,
        "params": {
            "anchor_day": anchor_day.isoformat(),
            "duration_days": int(duration_days),
            "seed": int(seed),
        },
    }


# ===========================================================================
# PURE FUNCTIONS — fact summarization + story / copilot-answer formatting
# ===========================================================================
def _pct(numer: float, denom: float) -> float:
    return (numer / denom * 100.0) if denom else 0.0


def pick_revenue_finding(findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the demo's headline finding: a ``revenue`` level-shift, else first revenue.

    Pure selector over the gateway ``/v1/anomalies`` payloads (``Finding`` shape).
    Prefers a ``level_shift`` on ``metric_key=="revenue"`` (the §9 anomaly); falls
    back to any revenue finding, then to the first finding, then ``None``.
    """

    revenue = [f for f in findings if f.get("metric_key") == "revenue"]
    for f in revenue:
        if f.get("kind") == "level_shift":
            return f
    if revenue:
        return revenue[0]
    return findings[0] if findings else None


def pick_top_recommendation(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the rank-1 recommendation (gateway already sorts by priority)."""

    if not recs:
        return None
    ranked = [r for r in recs if r.get("priority_rank") == 1]
    return ranked[0] if ranked else recs[0]


def summarize_finding(finding: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce a raw ``Finding`` dict to the narration facts (pure).

    Every number here is copied straight from the computed finding — the formatter
    never invents figures (the §9 grounding guarantee).
    """

    if not finding:
        return {"present": False}
    dims = finding.get("dimensions") or {}
    causes = finding.get("candidate_causes") or []
    return {
        "present": True,
        "finding_id": finding.get("finding_id"),
        "kind": finding.get("kind"),
        "metric_key": finding.get("metric_key"),
        "region": dims.get("region"),
        "channel": dims.get("channel"),
        "observed_value": finding.get("observed_value"),
        "expected_value": finding.get("expected_value"),
        "deviation": finding.get("deviation"),
        "deviation_pct": finding.get("deviation_pct"),
        "score": finding.get("score"),
        "confidence": finding.get("confidence"),
        "window_start": finding.get("window_start"),
        "window_end": finding.get("window_end"),
        "candidate_causes": [
            {
                "metric_key": c.get("metric_key"),
                "correlation": c.get("correlation"),
                "lag_minutes": c.get("lag_minutes"),
                "contribution_pct": c.get("contribution_pct"),
                "observed_delta": c.get("observed_delta"),
            }
            for c in causes
        ],
    }


def summarize_recommendation(rec: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce a raw ``Recommendation`` dict to the narration facts (pure)."""

    if not rec:
        return {"present": False}
    impact = rec.get("impact") or {}
    confidence = rec.get("confidence") or {}
    return {
        "present": True,
        "recommendation_id": rec.get("recommendation_id"),
        "title": rec.get("title"),
        "action_type": rec.get("action_type"),
        "priority_rank": rec.get("priority_rank"),
        "priority_score": rec.get("priority_score"),
        "impact_value": impact.get("value"),
        "impact_low": impact.get("value_low"),
        "impact_high": impact.get("value_high"),
        "impact_unit": impact.get("unit"),
        "horizon_days": impact.get("horizon_days"),
        "confidence_value": confidence.get("value"),
        "confidence_components": confidence.get("components") or {},
    }


def format_copilot_answer(
    finding: dict[str, Any] | None,
    recommendation: dict[str, Any] | None,
    *,
    wow_total_before: float | None = None,
    wow_total_after: float | None = None,
) -> str:
    """Render the grounded copilot answer (arch §9 "exact copilot answer shape").

    Pure: every figure is read from the computed finding / recommendation (or the
    optional weekly totals), with provenance citations — so the printed answer is
    grounded exactly as the live copilot's would be. If the facts are missing it
    degrades to a clearly-marked placeholder rather than inventing numbers.
    """

    f = summarize_finding(finding)
    r = summarize_recommendation(recommendation)
    lines: list[str] = []

    if wow_total_before and wow_total_after:
        wow_pct = _pct(wow_total_after - wow_total_before, wow_total_before)
        lines.append(
            f"Revenue fell {abs(wow_pct):.1f}% week-over-week "
            f"(${wow_total_before/1000:,.0f}K -> ${wow_total_after/1000:,.0f}K daily average). [1]"
        )
    else:
        lines.append("Revenue fell week-over-week (weekly total via metric_lookup). [1]")

    if f["present"]:
        region = f["region"] or "the affected region"
        channel = f["channel"] or ""
        scope = f"{region} {channel}".strip() + " revenue"
        obs = f["observed_value"]
        exp = f["expected_value"]
        dev_pct = f["deviation_pct"]
        score = f["score"]
        scope_line = f"The drop is concentrated in **{scope}**"
        if obs is not None and exp is not None and dev_pct is not None:
            scope_line += (
                f", which fell {abs(dev_pct):.1f}% " f"(${exp/1000:,.0f}K -> ${obs/1000:,.0f}K/day)"
            )
        scope_line += ". [2]"
        lines.append(scope_line)
        if score is not None:
            lines.append(
                f"This is a {score:.1f}sigma deviation from the seasonal expectation "
                "-- not normal weekly variation."
            )
        leading = next(
            (c for c in f["candidate_causes"] if c["metric_key"] in ("latency_p95", "error_rate")),
            None,
        )
        if leading:
            contrib = leading.get("contribution_pct")
            lag = leading.get("lag_minutes")
            contrib_txt = (
                f", accounting for ~{contrib:.0f}% of the attributed impact" if contrib else ""
            )
            lag_txt = f" about {int(lag/60)}h before the revenue decline" if lag else ""
            lines.append(
                "**Root cause (high confidence):** an availability regression in "
                "`checkout-api`. Latency p95 and error rate spiked"
                f"{lag_txt}{contrib_txt}. [3]"
            )
    else:
        lines.append("No revenue anomaly was returned by find_anomalies yet. [2]")

    if r["present"]:
        impact = r["impact_value"]
        horizon = r["horizon_days"]
        conf = r["confidence_value"]
        impact_txt = ""
        if impact is not None:
            impact_txt = f" Estimated recovery: ~${impact/1000:,.0f}K"
            if horizon:
                impact_txt += f" over the next {int(horizon)} days"
        conf_txt = f", confidence {conf:.2f}" if conf is not None else ""
        lines.append(
            f"**Recommended action (priority #{r['priority_rank'] or 1}):** "
            f"{r['title'] or 'Mitigate the failing service'}."
            f"{impact_txt}{conf_txt}. [4]"
        )
    else:
        lines.append("No recommendation has been produced yet. [4]")

    lines.append(
        "Citations: [1] metric `revenue` weekly (metric_lookup) "
        "[2] finding (find_anomalies) [3] candidate causes (finding) "
        "[4] recommendation (semantic_search)."
    )
    return "\n\n".join(lines)


def format_story(
    finding: dict[str, Any] | None,
    recommendation: dict[str, Any] | None,
    *,
    tenant_id: str = DEMO_TENANT_ID,
    scenario: str = DEFAULT_SCENARIO,
    anchor_day: date | None = None,
) -> str:
    """Render the full layer-by-layer end-to-end story (arch §9), grounded in facts.

    Pure: takes the polled finding + recommendation (or ``None``) and returns the
    printable narrative. The closing copilot answer is delegated to
    :func:`format_copilot_answer`. No I/O, no clock — fully unit-testable.
    """

    f = summarize_finding(finding)
    r = summarize_recommendation(recommendation)
    when = anchor_day.isoformat() if anchor_day else "~7 days ago"

    out: list[str] = []
    out.append("=" * 72)
    out.append(f"EDIS end-to-end demo — scenario '{scenario}' (tenant '{tenant_id}')")
    out.append("=" * 72)
    out.append(
        f"L1 Ingestion: simulated sales+ops records for the EMEA checkout-api outage "
        f"injected starting {when}; the outage records carry anomaly_label='outage'."
    )
    out.append(
        "L2 Integration: records normalized to CanonicalOrder/OpsEvent facts and "
        "MetricObservation rows (revenue, error_rate, latency_p95); daily rollups."
    )

    if f["present"]:
        scope = " x ".join(str(v) for v in (f["region"], f["channel"]) if v) or "(all)"
        obs, exp, dev_pct, score = (
            f["observed_value"],
            f["expected_value"],
            f["deviation_pct"],
            f["score"],
        )
        detail = f"metric={f['metric_key']} dims={scope}"
        if obs is not None and exp is not None:
            detail += f" observed=${obs:,.0f} expected=${exp:,.0f}"
        if dev_pct is not None:
            detail += f" ({dev_pct:+.1f}%)"
        if score is not None:
            detail += f" score={score:.1f}sigma"
        out.append(
            f"L3 Intelligence: detected a {f['kind']} finding [{f['finding_id']}] — {detail}."
        )
        if f["candidate_causes"]:
            for c in f["candidate_causes"]:
                out.append(
                    f"   RCA cause: {c['metric_key']} corr={c['correlation']} "
                    f"lag={c['lag_minutes']}min contribution={c['contribution_pct']}%."
                )
    else:
        out.append("L3 Intelligence: no finding surfaced yet (the sweep may still be running).")

    if r["present"]:
        impact = r["impact_value"]
        unit = r["impact_unit"] or "USD"
        conf = r["confidence_value"]
        out.append(
            f"L4 Decision: rank-{r['priority_rank']} recommendation [{r['recommendation_id']}] "
            f"'{r['title']}' (action={r['action_type']}, "
            f"impact~{impact:,.0f} {unit} over {r['horizon_days']}d, confidence={conf})."
        )
        comps = r["confidence_components"]
        if comps:
            comp_txt = ", ".join(f"{k}={v}" for k, v in comps.items())
            out.append(f"   Confidence breakdown: {comp_txt} (calibration prior, calibration_n=0).")
    else:
        out.append("L4 Decision: no recommendation produced yet.")

    out.append(
        "L6 Dashboard: the EMEA revenue KPI tile turns red, the anomaly feed shows the "
        "level-shift, and the rank-1 recommendation card surfaces with its confidence gauge."
    )
    out.append("-" * 72)
    out.append('L5 Copilot — "Why did revenue drop last week?"')
    out.append("-" * 72)
    out.append(format_copilot_answer(finding, recommendation))
    out.append("=" * 72)
    return "\n".join(out)


# ===========================================================================
# I/O SHELL — the live httpx driver against the running compose stack
# ===========================================================================
@dataclass
class EdisClient:
    """Thin async client over the ingest control API + the gateway snapshots.

    Holds one ``httpx.AsyncClient`` and the dev JWT (minted once). Every call is
    tenant-scoped via the verified token, never the body. Constructed via
    :meth:`open`/:meth:`aclose` (or ``async with``) so the connection pool is
    lifecycle-managed.
    """

    config: DemoConfig
    _client: Any = field(default=None, repr=False)
    _token: str = field(default="", repr=False)

    async def open(self) -> EdisClient:
        import httpx

        self._token = self._mint_token()
        self._client = httpx.AsyncClient(
            timeout=self.config.request_timeout_s,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        return self

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> EdisClient:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _mint_token(self) -> str:
        """Mint the dev HS256 JWT the services validate (same secret as the stack)."""

        from datetime import timedelta

        import jwt  # PyJWT — a transitive dep of edis-platform

        now = datetime.now(UTC)
        payload = {
            "tenant_id": self.config.tenant_id,
            "sub": DEMO_USER_ID,
            "roles": list(DEMO_ROLES),
            "scopes": list(DEMO_SCOPES),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        }
        return jwt.encode(payload, self.config.jwt_secret, algorithm=self.config.jwt_algorithm)

    # -- ingest control API (L1) --
    async def control_seed(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(self.config.ingest_base_url, "/v1/control/seed", body)

    async def control_inject(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(self.config.ingest_base_url, "/v1/control/simulator/inject", body)

    # -- gateway snapshots (BFF) --
    async def list_anomalies(self) -> list[dict[str, Any]]:
        return await self._get(self.config.gateway_base_url, "/v1/anomalies", {"limit": 50})

    async def list_recommendations(self) -> list[dict[str, Any]]:
        return await self._get(self.config.gateway_base_url, "/v1/recommendations", {"limit": 50})

    async def _post(self, base: str, path: str, body: dict[str, Any]) -> Any:
        resp = await self._client.post(f"{base}{path}", json=body)
        resp.raise_for_status()
        return resp.json()

    async def _get(self, base: str, path: str, params: dict[str, Any]) -> Any:
        resp = await self._client.get(f"{base}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def poll_for_demo_facts(
    client: EdisClient,
    *,
    attempts: int = 30,
    interval_s: float = 2.0,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Poll the gateway until both the revenue finding and a recommendation appear.

    Returns ``(finding, recommendation)`` (either may be ``None`` if the chain did
    not complete within the budget). Bounded so the demo never hangs.
    """

    finding: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    for i in range(attempts):
        if finding is None:
            finding = pick_revenue_finding(await client.list_anomalies())
        if recommendation is None:
            recommendation = pick_top_recommendation(await client.list_recommendations())
        if finding is not None and recommendation is not None:
            break
        print(
            f"  ... waiting for the chain to produce facts "
            f"(attempt {i + 1}/{attempts}; finding={'yes' if finding else 'no'}, "
            f"recommendation={'yes' if recommendation else 'no'})",
            file=sys.stderr,
        )
        await asyncio.sleep(interval_s)
    return finding, recommendation


async def run_seed(config: DemoConfig, *, days: int, seed: int) -> int:
    """Seed ~90 days of correlated history via the ingest control API.

    The governance control-plane (tenant ``acme`` + roles + calibration prior) is
    seeded by ``services/governance/app/seed/seed.py`` (``python -m app.seed.seed``);
    ``make seed`` runs that first, then this loads the history. We surface that here
    so the message is honest about the two halves.
    """

    body = seed_request_body(days=days, seed=seed, scenario=None)
    async with await EdisClient(config).open() as client:
        print(f"Seeding {days} days of history for tenant '{config.tenant_id}' (seed={seed}) ...")
        result = await client.control_seed(body)
    print(f"Seed accepted: {result}")
    print(
        "Note: tenant/roles/calibration prior are seeded by the governance seeder "
        "(`python -m app.seed.seed`), which `make seed` runs before this loader."
    )
    return 0


async def run_demo(
    config: DemoConfig,
    *,
    scenario: str,
    seed: int,
    now: datetime | None = None,
) -> int:
    """Inject ``revenue_drop_emea`` 7 days ago, poll the gateway, print the story."""

    _scenario, anchor, duration = build_scenario(scenario, now=now)
    inject_body = scenario_inject_body(
        scenario=scenario, anchor_day=anchor, duration_days=duration, seed=seed
    )
    async with await EdisClient(config).open() as client:
        print(f"Injecting scenario '{scenario}' starting {anchor.isoformat()} ...")
        inject_result = await client.control_inject(inject_body)
        print(f"Inject accepted: {inject_result}")
        finding, recommendation = await poll_for_demo_facts(client)

    print()
    print(
        format_story(
            finding,
            recommendation,
            tenant_id=config.tenant_id,
            scenario=scenario,
            anchor_day=anchor,
        )
    )
    # Non-zero only if the chain never produced the headline finding, so CI/scripts
    # can detect a broken pipeline.
    return 0 if finding is not None else 2


# ===========================================================================
# CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_demo",
        description="EDIS seed + demo orchestration (Z1) — drives the running compose stack.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Load ~90 days of correlated history via the ingest API.")
    p_seed.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS)
    p_seed.add_argument("--seed", type=int, default=DEFAULT_SEED)

    p_demo = sub.add_parser(
        "demo", help="Inject revenue_drop_emea (7d ago), poll the gateway, tell the story."
    )
    p_demo.add_argument("--scenario", default=DEFAULT_SCENARIO)
    p_demo.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = DemoConfig.from_env()

    if args.command == "seed":
        return asyncio.run(run_seed(config, days=args.days, seed=args.seed))
    if args.command == "demo":
        return asyncio.run(run_demo(config, scenario=args.scenario, seed=args.seed))
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
