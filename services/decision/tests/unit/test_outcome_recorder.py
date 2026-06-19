"""Unit tests for the NO-OP :class:`OutcomeRecorder` (the inert feedback seam).

Asserts the recorder persists an :class:`OutcomeReport` via the in-memory repo and computes
NOTHING -- no realized-vs-predicted error, no recalibration, no mutation of the report. It
is a pure pass-through to persistence. No infra, no key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from edis_contracts.decisions import OutcomeReport

from decision_engine.consumers.outcome_recorder import OutcomeRecorder

from edis_l4_testkit import DEMO_TENANT


def _outcome(*, accepted: bool = True, realized_value=None) -> OutcomeReport:
    return OutcomeReport(
        outcome_id=uuid4(),
        tenant_id=DEMO_TENANT,
        recommendation_id=uuid4(),
        source="human",
        accepted=accepted,
        realized_value=realized_value,
        realized_unit="USD" if realized_value is not None else None,
        notes="acted on it",
        occurred_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )


async def test_recorder_persists_outcome(in_memory_repo):
    recorder = OutcomeRecorder(in_memory_repo)
    outcome = _outcome()

    returned = await recorder.record(outcome)

    assert in_memory_repo.outcomes == [outcome]
    assert returned == outcome  # pure pass-through


async def test_recorder_computes_nothing(in_memory_repo):
    """The persisted report is byte-identical to the input -- no derived fields set."""

    recorder = OutcomeRecorder(in_memory_repo)
    # A report with NO realized_value: the no-op recorder must NOT fill it in.
    outcome = _outcome(realized_value=None)

    await recorder.record(outcome)

    persisted = in_memory_repo.outcomes[0]
    assert persisted.model_dump() == outcome.model_dump()
    assert persisted.realized_value is None  # no computation populated it
    assert persisted.realized_unit is None


async def test_recorder_does_not_touch_recommendations(in_memory_repo):
    """Recording an outcome must not create / mutate any recommendation or lifecycle row."""

    recorder = OutcomeRecorder(in_memory_repo)
    await recorder.record(_outcome())

    assert in_memory_repo.lifecycle_rows == []
    assert await in_memory_repo.count_for_tenant(DEMO_TENANT) == 0


async def test_recorder_persists_each_of_multiple_outcomes(in_memory_repo):
    recorder = OutcomeRecorder(in_memory_repo)
    o1, o2 = _outcome(), _outcome(accepted=False)

    await recorder.record(o1)
    await recorder.record(o2)

    assert in_memory_repo.outcomes == [o1, o2]


async def test_run_requires_a_source(in_memory_repo):
    import pytest

    recorder = OutcomeRecorder(in_memory_repo)  # no source supplied
    with pytest.raises(RuntimeError):
        await recorder.run()
