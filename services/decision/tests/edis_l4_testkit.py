"""Deterministic builders + infra-free fakes shared across the L4 test suite.

Importable by name (the suite puts ``tests/`` on ``sys.path`` in ``conftest.py``) because
under ``--import-mode=importlib`` ``conftest.py`` itself cannot be imported. Everything
here is pure / in-memory: no Postgres, no broker, no Anthropic key.

Contents
--------
* :func:`build_demo_finding` -- the canonical §9 ``revenue_drop_emea`` Finding: an EMEA-web
  ``revenue`` level shift ($95K -> $61K/day) with leading EMEA ``checkout-api``
  ``latency_p95`` / ``error_rate`` candidate causes, at the exact magnitudes in arch §9.
* :class:`FakeSink` -- an :class:`~edis_platform.bus.base.EventSink` that records every
  ``(topic, key, value)`` publish in memory (the value is the round-tripped JSON dict, as
  the real backends deliver it), so tests assert what was published with no broker.
* :class:`InMemoryRecommendationRepo` -- mirrors the
  :class:`~decision_engine.persistence.repository.RecommendationRepository` method surface
  (save_recommendation / get / list_for_tenant / count_for_tenant / update_status /
  record_lifecycle / list_expired_candidates / save_outcome) over plain dicts, tenant
  scoped, with NO database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from edis_contracts.decisions import (
    OutcomeReport,
    Recommendation,
    RecommendationLifecycleEvent,
)
from edis_contracts.findings import CandidateCause, Finding, FindingKind

DEMO_TENANT = "acme"
#: Stable finding id so the demo recommendation is fully reproducible across tests.
DEMO_FINDING_ID = UUID("00000000-0000-0000-0000-0000000000f1")
#: A fixed `now` inside the demo window (mirrors the ``fixed_now`` fixture).
DEMO_NOW = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)


def build_demo_finding(
    *,
    finding_id: UUID = DEMO_FINDING_ID,
    tenant_id: str = DEMO_TENANT,
) -> Finding:
    """Build the §9 ``revenue_drop_emea`` EMEA-web revenue level-shift Finding.

    Numbers match arch §9 exactly: observed $61K vs expected $95K (deviation -$34K,
    -35.8%, 5.8sigma), severity 0.86, confidence 0.91, business_impact_input 0.78, with the
    leading EMEA ``checkout-api`` latency_p95 (corr 0.94, 71% contribution) and error_rate
    (corr 0.89, 22%) causes. These drive the deterministic estimator to ~$170K impact,
    ~0.84 confidence, priority rank 1.
    """

    return Finding(
        finding_id=finding_id,
        tenant_id=tenant_id,
        kind=FindingKind.LEVEL_SHIFT,
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        window_start=datetime(2026, 6, 12, 0, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 18, 23, 59, 59, tzinfo=timezone.utc),
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
        candidate_causes=[
            CandidateCause(
                metric_key="latency_p95",
                dimensions={"region": "EMEA", "service": "checkout-api"},
                correlation=0.94,
                lag_minutes=120,
                contribution_pct=71.0,
                direction="leading",
                observed_delta=1220.0,
            ),
            CandidateCause(
                metric_key="error_rate",
                dimensions={"region": "EMEA", "service": "checkout-api"},
                correlation=0.89,
                lag_minutes=120,
                contribution_pct=22.0,
                direction="leading",
                observed_delta=0.086,
            ),
        ],
        created_at=datetime(2026, 6, 19, 0, 0, 0, tzinfo=timezone.utc),
    )


class FakeSink:
    """In-memory :class:`~edis_platform.bus.base.EventSink` that records publishes.

    Each publish is stored as ``(topic, key, value_dict)`` where ``value_dict`` is the
    JSON round-trip of the published model -- exactly the plain dict the Kafka / Redis /
    inproc backends deliver -- so tests can ``parse_message`` it back or assert on raw
    fields. ``start``/``stop`` are no-ops; building it connects to nothing.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str | None, dict]] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def publish(self, topic: str, key: str | None, value) -> None:
        if hasattr(value, "model_dump_json"):
            decoded = json.loads(value.model_dump_json())
        else:
            decoded = json.loads(json.dumps(value, default=str))
        self.published.append((topic, key, decoded))

    # -- convenience accessors used by tests --------------------------------
    def topics_published(self) -> list[str]:
        return [t for (t, _k, _v) in self.published]

    def values_for(self, topic: str) -> list[dict]:
        return [v for (t, _k, v) in self.published if t == topic]

    def keys_for(self, topic: str) -> list[str | None]:
        return [k for (t, k, _v) in self.published if t == topic]


class InMemoryRecommendationRepo:
    """Infra-free stand-in for :class:`RecommendationRepository` (plain dicts, no DB).

    Mirrors the method surface the lifecycle manager, API, finding consumer, and no-op
    outcome recorder call. Tenant-scoped reads/writes; lifecycle rows and outcomes are
    captured in lists so tests can assert they landed (and that the outcome recorder
    computes nothing beyond persistence).
    """

    def __init__(self) -> None:
        # (tenant_id, recommendation_id) -> Recommendation
        self._recs: dict[tuple[str, UUID], Recommendation] = {}
        self.lifecycle_rows: list[RecommendationLifecycleEvent] = []
        self.outcomes: list[OutcomeReport] = []

    # -- recommendation -----------------------------------------------------
    async def save_recommendation(self, rec: Recommendation) -> None:
        self._recs[(rec.tenant_id, rec.recommendation_id)] = rec

    async def get(self, tenant_id: str, recommendation_id: UUID) -> Recommendation | None:
        return self._recs.get((tenant_id, recommendation_id))

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Recommendation]:
        rows = [r for (t, _id), r in self._recs.items() if t == tenant_id]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        # rank 1 first; ties broken by created_at descending (mirrors the SQL repo).
        rows.sort(key=lambda r: (r.priority_rank, -r.created_at.timestamp()))
        return rows[offset : offset + limit]

    async def count_for_tenant(self, tenant_id: str, *, status: str | None = None) -> int:
        rows = [r for (t, _id), r in self._recs.items() if t == tenant_id]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        return len(rows)

    async def update_status(
        self, tenant_id: str, recommendation_id: UUID, to_status: str
    ) -> str | None:
        rec = self._recs.get((tenant_id, recommendation_id))
        if rec is None:
            return None
        previous = rec.status
        self._recs[(tenant_id, recommendation_id)] = rec.model_copy(update={"status": to_status})
        return previous

    # -- lifecycle ----------------------------------------------------------
    async def record_lifecycle(self, event: RecommendationLifecycleEvent) -> None:
        self.lifecycle_rows.append(event)

    async def list_expired_candidates(
        self, *, now: datetime | None = None, limit: int = 500
    ) -> list[Recommendation]:
        now = now or datetime.now(timezone.utc)
        candidates = [
            r for r in self._recs.values() if r.status == "proposed" and r.expires_at < now
        ]
        candidates.sort(key=lambda r: r.expires_at)
        return candidates[:limit]

    # -- outcomes (no-op recorder target) -----------------------------------
    async def save_outcome(self, outcome: OutcomeReport) -> None:
        self.outcomes.append(outcome)
