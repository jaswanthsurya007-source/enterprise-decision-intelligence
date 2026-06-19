"""Unit tests for the recommendation lifecycle FSM + manager.

Covers the pure :class:`LifecycleStateMachine` (legal transitions ok; illegal -> 409
:class:`ConflictError`; terminal states) and the :class:`LifecycleManager` over the
in-memory repo + fake bus: a legal transition persists the new status, appends a lifecycle
row, publishes the lifecycle event, and emits an audit event; an illegal transition raises
409 BEFORE any side effect. No infra, no key.
"""

from __future__ import annotations

import pytest

from edis_contracts import topics
from edis_contracts.decisions import RecommendationLifecycleEvent
from edis_platform.errors import ConflictError, NotFoundError

from decision_engine.events.producer import DecisionEventProducer
from decision_engine.lifecycle.manager import LifecycleManager
from decision_engine.lifecycle.state_machine import (
    ACTION_TO_STATUS,
    LifecycleStateMachine,
    can_transition,
    is_terminal,
    legal_transitions,
)
from decision_engine.synthesis.synthesizer import synthesize

from edis_l4_testkit import build_demo_finding


# ---------------------------------------------------------------------------
# Pure FSM
# ---------------------------------------------------------------------------
def test_legal_transitions_from_proposed():
    assert legal_transitions("proposed") == frozenset({"accepted", "rejected", "expired"})
    assert can_transition("proposed", "accepted")
    assert can_transition("proposed", "rejected")
    assert can_transition("proposed", "expired")


def test_terminal_states_have_no_outgoing_transition():
    for terminal in ("accepted", "rejected", "expired"):
        assert legal_transitions(terminal) == frozenset()
        assert is_terminal(terminal)
    assert not is_terminal("proposed")


def test_illegal_transition_accepted_to_proposed_raises_conflict():
    fsm = LifecycleStateMachine()
    with pytest.raises(ConflictError):
        fsm.validate("accepted", "proposed")
    assert not fsm.can_transition("accepted", "proposed")


def test_illegal_transition_accept_after_accept_raises_conflict():
    fsm = LifecycleStateMachine()
    with pytest.raises(ConflictError):
        fsm.validate("accepted", "accepted")


def test_validate_passes_for_legal_move():
    fsm = LifecycleStateMachine()
    fsm.validate("proposed", "accepted")  # no raise


def test_action_to_status_map():
    assert ACTION_TO_STATUS == {
        "accept": "accepted",
        "reject": "rejected",
        "expire": "expired",
    }
    assert LifecycleStateMachine().resolve_action("accept") == "accepted"


def test_resolve_unknown_action_raises():
    with pytest.raises(ConflictError):
        LifecycleStateMachine().resolve_action("bogus")


# ---------------------------------------------------------------------------
# Manager over in-memory repo + fake bus
# ---------------------------------------------------------------------------
async def _seed_proposed(repo, fixed_now):
    rec = await synthesize(build_demo_finding(), now=fixed_now)
    await repo.save_recommendation(rec)
    return rec


def _manager(repo, sink):
    return LifecycleManager(repo, DecisionEventProducer(sink), sink)


async def test_transition_accept_persists_publishes_and_audits(
    in_memory_repo, fake_sink, operator_ctx, fixed_now
):
    rec = await _seed_proposed(in_memory_repo, fixed_now)
    manager = _manager(in_memory_repo, fake_sink)

    updated = await manager.transition(
        rec.tenant_id, rec.recommendation_id, "accepted", ctx=operator_ctx
    )

    # Returned + persisted status is accepted.
    assert updated.status == "accepted"
    stored = await in_memory_repo.get(rec.tenant_id, rec.recommendation_id)
    assert stored.status == "accepted"

    # A lifecycle row was appended (proposed -> accepted).
    assert len(in_memory_repo.lifecycle_rows) == 1
    row = in_memory_repo.lifecycle_rows[0]
    assert row.from_status == "proposed"
    assert row.to_status == "accepted"
    assert row.actor["id"] == operator_ctx.user_id

    # The lifecycle event was published (keyed by recommendation_id) + an audit emitted.
    lifecycle_values = fake_sink.values_for(topics.DECISIONS_LIFECYCLE)
    assert len(lifecycle_values) == 1
    evt = RecommendationLifecycleEvent.model_validate(lifecycle_values[0])
    assert (evt.from_status, evt.to_status) == ("proposed", "accepted")
    assert fake_sink.keys_for(topics.DECISIONS_LIFECYCLE) == [str(rec.recommendation_id)]
    assert topics.AUDIT in fake_sink.topics_published()


async def test_illegal_transition_raises_409_before_side_effects(
    in_memory_repo, fake_sink, operator_ctx, fixed_now
):
    rec = await _seed_proposed(in_memory_repo, fixed_now)
    manager = _manager(in_memory_repo, fake_sink)

    # First accept succeeds.
    await manager.transition(rec.tenant_id, rec.recommendation_id, "accepted", ctx=operator_ctx)
    published_after_first = len(fake_sink.published)

    # accepted -> proposed is illegal -> 409, with NO further side effects.
    with pytest.raises(ConflictError):
        await manager.transition(rec.tenant_id, rec.recommendation_id, "proposed", ctx=operator_ctx)

    # accept-after-accept is also illegal -> 409.
    with pytest.raises(ConflictError):
        await manager.transition(rec.tenant_id, rec.recommendation_id, "accepted", ctx=operator_ctx)

    # No new publishes and only the one lifecycle row from the successful accept.
    assert len(fake_sink.published) == published_after_first
    assert len(in_memory_repo.lifecycle_rows) == 1
    stored = await in_memory_repo.get(rec.tenant_id, rec.recommendation_id)
    assert stored.status == "accepted"


async def test_transition_unknown_recommendation_raises_404(in_memory_repo, fake_sink):
    import uuid

    manager = _manager(in_memory_repo, fake_sink)
    with pytest.raises(NotFoundError):
        await manager.transition("acme", uuid.uuid4(), "accepted")


async def test_sweep_expires_stale_proposed(in_memory_repo, fake_sink, fixed_now):
    """The TTL sweeper expires past-due proposed recommendations with a system actor."""

    from datetime import timedelta

    rec = await synthesize(build_demo_finding(), now=fixed_now, ttl_hours=72)
    await in_memory_repo.save_recommendation(rec)
    manager = _manager(in_memory_repo, fake_sink)

    # Sweep at a time AFTER expiry.
    after_expiry = rec.expires_at + timedelta(hours=1)
    n = await manager.sweep_expired(now=after_expiry)

    assert n == 1
    stored = await in_memory_repo.get(rec.tenant_id, rec.recommendation_id)
    assert stored.status == "expired"
    evt = RecommendationLifecycleEvent.model_validate(
        fake_sink.values_for(topics.DECISIONS_LIFECYCLE)[0]
    )
    assert (evt.from_status, evt.to_status) == ("proposed", "expired")
    assert evt.actor["type"] == "system"
