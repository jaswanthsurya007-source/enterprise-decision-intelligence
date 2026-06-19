"""Stream consumer: subscribe edis.raw.* -> process_envelope -> relay-publish.

The reactive ingress mode. It subscribes to ``edis.raw.sales.v1`` /
``edis.raw.ops.v1`` through :func:`~edis_platform.bus.base.make_source` (Kafka /
Redis / in-proc -- identical downstream), deserializes each :class:`Message` into
an :class:`IngestEnvelope`, runs the shared :func:`process_envelope` orchestration
(decode -> ... -> upsert -> derive-metrics -> stage outbox, transactionally), then
runs the outbox relay so the staged canonical/metric/lineage/DLQ events are
published. Processing and publishing are deliberately separated by the outbox: the
canonical write commits first, the relay publishes second -- no
persisted-but-not-published gap.

This wraps exactly one normalization implementation (the same
:func:`process_envelope` the batch loader uses), so the two ingress modes cannot
drift. It is stoppable (``stop()``) and unit-testable over the in-proc bus + the
in-memory repo + the in-memory outbox: subscribe, publish a raw envelope, run a
bounded number of iterations, assert the canonical event came out the other side.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from edis_contracts.ingest import IngestEnvelope
from edis_contracts import topics
from edis_platform.logging import get_logger

from edis_integration.outbox.relay import relay_once
from edis_integration.pipeline.engine import IntegrationOutcome, process_envelope

if TYPE_CHECKING:
    from edis_platform.bus.base import EventSink, MessageSource

    from edis_integration.outbox.outbox_repo import OutboxReader
    from edis_integration.pipeline.engine import IntegrationRepo

_log = get_logger(__name__)

#: The raw topics the integration layer consumes (sales + ops in the MVP).
RAW_TOPICS = [topics.RAW_SALES, topics.RAW_OPS]


class StreamConsumer:
    """Reactive raw-topic consumer wrapping :func:`process_envelope` + the relay."""

    def __init__(
        self,
        source: "MessageSource",
        sink: "EventSink",
        repo: "IntegrationRepo",
        outbox_reader: "OutboxReader",
        *,
        group: str = "edis-integration",
        metric_bucket: str = "hour",
        dq_min_score: float = 0.5,
        topics_: list[str] | None = None,
        relay_batch_limit: int = 500,
    ) -> None:
        self._source = source
        self._sink = sink
        self._repo = repo
        self._outbox_reader = outbox_reader
        self._group = group
        self._metric_bucket = metric_bucket
        self._dq_min_score = dq_min_score
        self._topics = topics_ or RAW_TOPICS
        self._relay_batch_limit = relay_batch_limit
        self._stopped = asyncio.Event()
        self._stream = None
        # Lightweight counters for the ops/admin lag + health endpoints.
        self.processed = 0
        self.persisted = 0
        self.quarantined = 0
        self.duplicates = 0

    def subscribe(self) -> None:
        """Register the raw-topic subscription **eagerly** (before any publish).

        The in-proc bus registers a group's queue the instant ``subscribe`` is
        called, so calling this before producers publish guarantees delivery (the
        canonical ``subscribe -> publish -> consume`` pattern). :meth:`run` calls
        it lazily if not already done; tests call it explicitly to publish first.
        """

        if self._stream is None:
            self._stream = self._source.subscribe(self._topics, group=self._group)

    def stop(self) -> None:
        """Signal the consume loop to exit after the in-flight message."""

        self._stopped.set()

    async def process_one(self, envelope: IngestEnvelope) -> IntegrationOutcome:
        """Run the pipeline for one envelope, then relay-publish the staged events."""

        result = await process_envelope(
            envelope,
            repo=self._repo,
            metric_bucket=self._metric_bucket,
            dq_min_score=self._dq_min_score,
        )
        self.processed += 1
        if result.outcome is IntegrationOutcome.PERSISTED:
            self.persisted += 1
        elif result.outcome is IntegrationOutcome.QUARANTINED:
            self.quarantined += 1
            # A quarantine has no canonical write txn: publish its DLQ event(s)
            # directly so the record terminates in exactly one of {store, DLQ}.
            for ev in result.outbox:
                await self._sink.publish(ev.topic, key=ev.key, value=ev.value)
        else:
            self.duplicates += 1
        # Drain whatever the canonical-write txn staged (PERSISTED path).
        await relay_once(self._outbox_reader, self._sink, limit=self._relay_batch_limit)
        return result.outcome

    async def run(self, *, max_messages: int | None = None) -> int:
        """Consume until :meth:`stop` (or ``max_messages``); return count processed.

        ``max_messages`` bounds the loop for tests; ``None`` runs until stopped.
        """

        self._stopped.clear()
        self.subscribe()
        stream = self._stream
        assert stream is not None
        count = 0
        stop_task = asyncio.create_task(self._stopped.wait())
        next_task: asyncio.Task | None = None
        try:
            while not self._stopped.is_set():
                if next_task is None:
                    next_task = asyncio.create_task(anext(stream))  # type: ignore[arg-type]
                done, _ = await asyncio.wait(
                    {next_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_task not in done:
                    # stop() fired while waiting -- leave next_task to be cancelled.
                    break
                resolved, next_task = next_task, None
                try:
                    msg = resolved.result()
                except StopAsyncIteration:
                    break
                try:
                    envelope = IngestEnvelope.model_validate(msg.value)
                except Exception:
                    _log.exception("undecodable raw message dropped", extra={"topic": msg.topic})
                    continue
                await self.process_one(envelope)
                count += 1
                if max_messages is not None and count >= max_messages:
                    break
        finally:
            stop_task.cancel()
            if next_task is not None:
                next_task.cancel()
        return count


__all__ = ["StreamConsumer", "RAW_TOPICS"]
