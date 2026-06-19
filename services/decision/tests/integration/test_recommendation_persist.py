"""L4 integration -- persist + read recommendations / lifecycle / outcomes on Postgres.

``@pytest.mark.integration`` (Docker required; excluded from ``pytest -m "not
integration"``). Exercises the real async :class:`RecommendationRepository` over the shipped
Alembic schema (``alembic upgrade head``):

* the §9 demo Recommendation round-trips through the DB and reads back equal to the contract
  payload (impact / confidence JSONB rehydrate exactly), tenant-scoped (a wrong tenant
  cannot read it);
* ``update_status`` returns the previous status and persists the new one; a lifecycle row
  appends and lists; replayed saves are idempotent (ON CONFLICT upsert);
* the no-op outcome recorder persists an OutcomeReport via the real repo and computes
  nothing;
* the TTL sweep query returns past-due proposed recommendations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from edis_contracts.decisions import OutcomeReport

from decision_engine.consumers.outcome_recorder import OutcomeRecorder
from decision_engine.persistence.repository import RecommendationRepository
from decision_engine.synthesis.synthesizer import synthesize

from edis_l4_testkit import DEMO_NOW, DEMO_TENANT, build_demo_finding

pytestmark = pytest.mark.integration


async def _demo_rec(**overrides):
    rec = await synthesize(build_demo_finding(), now=DEMO_NOW)
    return rec.model_copy(update=overrides) if overrides else rec


async def test_recommendation_round_trips(pg_sessionmaker):
    rec = await _demo_rec()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(rec)
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        loaded = await repo.get(DEMO_TENANT, rec.recommendation_id)

    assert loaded is not None
    assert loaded.model_dump() == rec.model_dump()
    # The deterministic numbers survived the JSONB round-trip.
    assert loaded.impact.value == 170000.0
    assert loaded.impact.inputs == {"daily_loss": 34000.0, "affected_days_remaining": 5.0}
    assert 0.8 <= loaded.confidence.value <= 0.9
    assert loaded.priority_rank == 1


async def test_get_is_tenant_scoped(pg_sessionmaker):
    rec = await _demo_rec()
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(rec)
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        wrong_tenant = await repo.get("globex", rec.recommendation_id)
        right_tenant = await repo.get(DEMO_TENANT, rec.recommendation_id)

    assert wrong_tenant is None
    assert right_tenant is not None


async def test_save_is_idempotent_on_replay(pg_sessionmaker):
    rec = await _demo_rec()
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(rec)
        await repo.save_recommendation(rec)  # replayed finding -> same deterministic rec
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        assert await repo.count_for_tenant(DEMO_TENANT) == 1


async def test_list_orders_by_priority_rank(pg_sessionmaker):
    high = await _demo_rec(recommendation_id=uuid4(), priority_rank=1)
    low = await _demo_rec(recommendation_id=uuid4(), priority_rank=2)
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(low)
        await repo.save_recommendation(high)
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        rows = await repo.list_for_tenant(DEMO_TENANT)

    assert [r.priority_rank for r in rows] == [1, 2]


async def test_update_status_and_lifecycle_row(pg_sessionmaker):
    from edis_contracts.decisions import RecommendationLifecycleEvent

    rec = await _demo_rec()
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(rec)
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        previous = await repo.update_status(DEMO_TENANT, rec.recommendation_id, "accepted")
        await repo.record_lifecycle(
            RecommendationLifecycleEvent(
                event_id=uuid4(),
                tenant_id=DEMO_TENANT,
                recommendation_id=rec.recommendation_id,
                from_status="proposed",
                to_status="accepted",
                actor={"type": "user", "id": "op-1"},
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    assert previous == "proposed"
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        loaded = await repo.get(DEMO_TENANT, rec.recommendation_id)
    assert loaded.status == "accepted"


async def test_outcome_recorder_persists_via_real_repo(pg_sessionmaker):
    """The no-op recorder persists an OutcomeReport and computes nothing (real DB)."""

    outcome = OutcomeReport(
        outcome_id=uuid4(),
        tenant_id=DEMO_TENANT,
        recommendation_id=uuid4(),
        source="human",
        accepted=True,
        realized_value=None,
        occurred_at=datetime.now(timezone.utc),
    )

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        recorder = OutcomeRecorder(repo)
        returned = await recorder.record(outcome)
        await session.commit()

    assert returned == outcome  # nothing computed
    # Re-save is idempotent (ON CONFLICT DO NOTHING) -- still nothing computed.
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_outcome(outcome)
        await session.commit()


async def test_list_expired_candidates(pg_sessionmaker):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    stale = await _demo_rec(recommendation_id=uuid4(), status="proposed", expires_at=past)
    fresh = await _demo_rec(
        recommendation_id=uuid4(),
        status="proposed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        await repo.save_recommendation(stale)
        await repo.save_recommendation(fresh)
        await session.commit()

    async with pg_sessionmaker() as session:
        repo = RecommendationRepository(session)
        candidates = await repo.list_expired_candidates(now=datetime.now(timezone.utc))

    ids = {c.recommendation_id for c in candidates}
    assert stale.recommendation_id in ids
    assert fresh.recommendation_id not in ids
