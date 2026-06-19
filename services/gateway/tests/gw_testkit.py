"""Importable unit-test helpers for the gateway suite — no Docker/broker/keys.

These live in a uniquely-named module (not ``conftest``) so they can be
imported by test modules under pytest's ``importlib`` import mode without
colliding with another service's ``tests.conftest`` plugin name in a
whole-repo run. The tests dir is placed on ``pythonpath`` (see the root
``pyproject.toml``) so ``from gw_testkit import ...`` resolves from both
``tests/`` and ``tests/unit/``.

Holds the tenant constants and the canonical contract-model builders the
suite seeds with. The conftest fixtures import the builders from here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from edis_contracts.decisions import (
    ConfidenceScore,
    ImpactEstimate,
    Recommendation,
)
from edis_contracts.findings import Finding, FindingKind, Forecast

from edis_gateway.models import KpiSnapshot

TENANT = "acme"
OTHER_TENANT = "globex"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_finding(tenant: str = TENANT, *, metric_key: str = "revenue", **over) -> Finding:
    defaults = dict(
        finding_id=uuid4(),
        tenant_id=tenant,
        kind=FindingKind.LEVEL_SHIFT,
        metric_key=metric_key,
        dimensions={"region": "EMEA", "channel": "web"},
        window_start=_now() - timedelta(days=7),
        window_end=_now(),
        detector="stl_seasonal",
        detector_version="1.0",
        observed_value=61000.0,
        expected_value=95000.0,
        deviation=-34000.0,
        deviation_pct=-35.8,
        score=5.8,
        severity=0.86,
        confidence=0.91,
        business_impact_input=0.78,
        created_at=_now(),
    )
    defaults.update(over)
    return Finding(**defaults)


def make_recommendation(tenant: str = TENANT, *, priority_rank: int = 1, **over) -> Recommendation:
    defaults = dict(
        recommendation_id=uuid4(),
        tenant_id=tenant,
        source_finding_id=uuid4(),
        playbook_id="operational_fix",
        playbook_version="1.0",
        title="Mitigate checkout-api latency in EMEA",
        action_type="operational_fix",
        action_params={"service": "checkout-api", "region": "EMEA"},
        impact=ImpactEstimate(
            value=170000.0,
            value_low=120000.0,
            value_high=200000.0,
            unit="USD",
            direction="increase",
            horizon_days=5,
            inputs={"daily_loss": 34000.0, "affected_days_remaining": 5.0},
            method="recovery_flat",
        ),
        effort_tier="s",
        confidence=ConfidenceScore(
            value=0.84,
            components={"insight": 0.91, "evidence": 0.88, "historical_calibration": 0.74},
            calibration_n=0,
        ),
        priority_score=0.93,
        priority_rank=priority_rank,
        explanation_summary="Roll back the failing deploy to recover EMEA revenue.",
        expires_at=_now() + timedelta(days=5),
        created_at=_now(),
    )
    defaults.update(over)
    return Recommendation(**defaults)


def make_forecast(tenant: str = TENANT, *, metric_key: str = "revenue", **over) -> Forecast:
    defaults = dict(
        forecast_id=uuid4(),
        tenant_id=tenant,
        metric_key=metric_key,
        dimensions={"region": "EMEA", "channel": "web"},
        model="statsforecast.AutoETS",
        horizon_days=7,
        points=[
            {
                "ts": _now().isoformat(),
                "yhat": 95000.0,
                "yhat_lower": 90000.0,
                "yhat_upper": 100000.0,
            }
        ],
        generated_at=_now(),
    )
    defaults.update(over)
    return Forecast(**defaults)


def make_kpi(tenant: str = TENANT, *, metric_key: str = "revenue", **over) -> KpiSnapshot:
    defaults = dict(
        tenant_id=tenant,
        metric_key=metric_key,
        dimensions={"region": "EMEA"},
        day=_now(),
        value=385000.0,
        unit="USD",
        previous_value=420000.0,
        delta_abs=-35000.0,
        delta_pct=-8.3,
    )
    defaults.update(over)
    return KpiSnapshot(**defaults)
