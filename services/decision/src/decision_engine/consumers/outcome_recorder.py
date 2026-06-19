"""The NO-OP outcome recorder -- the feedback seam (C2), demonstrably wired, inert.

Consumes ``edis.feedback.outcomes.v1`` (:class:`~edis_contracts.decisions.OutcomeReport`)
and does exactly ONE thing: **persist the report**. It computes NOTHING -- no
realized-vs-predicted error, no recalibration, no confidence update. Those are the
deferred feedback loop (§5.4 / §6 "feedback loop"); the static calibration prior gives
the demo a believable confidence breakdown without a live loop, and ``calibration_n``
stays 0.

The recorder takes a repository-shaped collaborator (anything with an
``async save_outcome(outcome)`` method -- the SQLAlchemy
:class:`~decision_engine.persistence.repository.RecommendationRepository` in production,
an in-memory fake in tests), so it is unit-testable with no DB and no broker. Building it
connects to nothing. One bad outcome never kills the loop -- persist errors are caught,
logged, and the loop continues.
"""

from __future__ import annotations

from typing import Protocol

from edis_contracts import topics
from edis_contracts.decisions import OutcomeReport
from edis_platform.bus.base import MessageSource, parse_message
from edis_platform.logging import get_logger

_log = get_logger(__name__)


class OutcomeRepository(Protocol):
    """Port: persist one :class:`OutcomeReport` (the recorder's only side effect)."""

    async def save_outcome(self, outcome: OutcomeReport) -> None:  # pragma: no cover - protocol
        ...


class OutcomeRecorder:
    """No-op recorder: persists each :class:`OutcomeReport`; computes nothing.

    Construct with an :class:`OutcomeRepository`-shaped collaborator and a
    :class:`~edis_platform.bus.base.MessageSource`. :meth:`record` is the pure unit of
    work (persist one report) tests drive directly; :meth:`run` is the consumer loop that
    subscribes to ``edis.feedback.outcomes.v1`` and records each report until :meth:`stop`.
    """

    def __init__(
        self,
        repo: OutcomeRepository,
        source: MessageSource | None = None,
        *,
        group: str = "edis-decision-outcomes",
    ) -> None:
        self._repo = repo
        self._source = source
        self._group = group
        self._running = False

    async def record(self, outcome: OutcomeReport) -> OutcomeReport:
        """Persist one outcome and return it. NOTHING is computed over it.

        This is the entire job of the feedback seam in the MVP: the row lands, and no
        learning happens. Returning the report lets tests assert the recorder is a pure
        pass-through to persistence.
        """

        await self._repo.save_outcome(outcome)
        _log.info(
            "outcome recorded (no-op; nothing computed)",
            extra={
                "tenant_id": outcome.tenant_id,
                "outcome_id": str(outcome.outcome_id),
                "recommendation_id": str(outcome.recommendation_id),
                "source": outcome.source,
                "accepted": outcome.accepted,
            },
        )
        return outcome

    async def run(self) -> None:
        """Subscribe to ``edis.feedback.outcomes.v1`` and record each report reactively."""

        if self._source is None:
            raise RuntimeError("OutcomeRecorder.run() requires a MessageSource")
        self._running = True
        await self._source.start()
        _log.info("outcome recorder started", extra={"group": self._group})
        try:
            async for msg in self._source.subscribe([topics.FEEDBACK_OUTCOMES], group=self._group):
                if not self._running:
                    break
                parsed = parse_message(msg)
                if not isinstance(parsed, OutcomeReport):
                    continue
                try:
                    await self.record(parsed)
                except Exception as exc:  # noqa: BLE001 - one bad outcome must not kill the loop
                    _log.warning(
                        "outcome record failed",
                        extra={"outcome_id": str(parsed.outcome_id), "error": str(exc)},
                    )
        finally:
            await self._source.stop()
            _log.info("outcome recorder stopped")

    async def stop(self) -> None:
        """Signal the run loop to exit and stop the source."""

        self._running = False
        if self._source is not None:
            await self._source.stop()
