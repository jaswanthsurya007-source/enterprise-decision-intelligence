"""Batch loader: drain a bounded set of raw envelopes through one core.

The bulk ingress mode. Where :class:`StreamConsumer` reacts to a live topic, the
batch loader processes a finite, in-hand collection of :class:`IngestEnvelope`s
(e.g. a replayed topic range, a seed file already enveloped by L1, or a test
fixture) through the **identical** :func:`process_envelope` orchestration, then
drains the outbox once at the end. Same normalization core, two ingress modes --
no logic drift -- so a record is processed identically regardless of how it
arrived (the convergence guarantee in the architecture's data-flow section).

It is bounded and terminating (returns a :class:`BatchResult` summary), so it is
trivially unit-testable over the in-memory repo + in-proc bus, and is the engine
behind the ops/admin ``POST /v1/integration/reprocess`` route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from edis_contracts.canonical import OpsEvent
from edis_contracts.ingest import IngestEnvelope
from edis_platform.logging import get_logger

from edis_integration.mappers.metrics import derive_ops_metrics
from edis_integration.outbox.relay import OutboxRelay
from edis_integration.pipeline.engine import (
    IntegrationOutcome,
    process_envelope,
)

if TYPE_CHECKING:
    from edis_platform.bus.base import EventSink

    from edis_integration.outbox.outbox_repo import OutboxReader
    from edis_integration.pipeline.engine import IntegrationRepo

_log = get_logger(__name__)


@dataclass
class BatchResult:
    """Summary of one batch drain."""

    processed: int = 0
    persisted: int = 0
    quarantined: int = 0
    duplicates: int = 0
    published: int = 0
    quarantine_ids: list[str] = field(default_factory=list)


class BatchLoader:
    """Drain a bounded set of envelopes through :func:`process_envelope` + relay."""

    def __init__(
        self,
        repo: "IntegrationRepo",
        sink: "EventSink",
        outbox_reader: "OutboxReader",
        *,
        metric_bucket: str = "hour",
        dq_min_score: float = 0.5,
        max_records: int = 500,
        relay_batch_limit: int = 500,
    ) -> None:
        self._repo = repo
        self._sink = sink
        self._outbox_reader = outbox_reader
        self._metric_bucket = metric_bucket
        self._dq_min_score = dq_min_score
        self._max_records = max_records
        self._relay_batch_limit = relay_batch_limit

    async def load(self, envelopes: Iterable[IngestEnvelope]) -> BatchResult:
        """Process up to ``max_records`` envelopes, then drain the outbox once.

        Ops events are persisted as facts *without* per-event ratio/percentile
        metrics, then the correct ``error_rate`` / ``latency_p95`` are derived in
        ONE pass over the whole bucketed batch (the architecture's pure-function
        contract) and written as proper bucket metrics. Order (additive) metrics
        are derived inline per envelope as usual.
        """

        result = BatchResult()
        collected_ops: list[OpsEvent] = []
        for envelope in envelopes:
            if result.processed >= self._max_records:
                break
            await self._process(envelope, result, collected_ops)
        # True bucket aggregate over the full batch of ops events.
        await self._aggregate_ops(collected_ops, result)
        # One bounded, terminating drain of everything the batch staged.
        relay = OutboxRelay(self._outbox_reader, self._sink, batch_limit=self._relay_batch_limit)
        result.published = await relay.drain()
        _log.info(
            "batch drained",
            extra={
                "processed": result.processed,
                "persisted": result.persisted,
                "quarantined": result.quarantined,
                "duplicates": result.duplicates,
                "published": result.published,
            },
        )
        return result

    async def _process(
        self,
        envelope: IngestEnvelope,
        result: BatchResult,
        collected_ops: list[OpsEvent],
    ) -> IntegrationOutcome:
        res = await process_envelope(
            envelope,
            repo=self._repo,
            metric_bucket=self._metric_bucket,
            dq_min_score=self._dq_min_score,
            # Persist ops facts here; aggregate the bucket metrics once, below.
            derive_ops_metrics_inline=False,
        )
        result.processed += 1
        if res.outcome is IntegrationOutcome.PERSISTED:
            result.persisted += 1
            collected_ops.extend(res.ops_events)
        elif res.outcome is IntegrationOutcome.QUARANTINED:
            result.quarantined += 1
            if res.quarantine is not None:
                result.quarantine_ids.append(str(res.quarantine.quarantine_id))
                # Quarantines bypass the canonical txn; publish their DLQ events.
                for ev in res.outbox:
                    await self._sink.publish(ev.topic, key=ev.key, value=ev.value)
        else:
            result.duplicates += 1
        return res.outcome

    async def _aggregate_ops(self, ops_events: list[OpsEvent], result: BatchResult) -> None:
        """Derive + persist true bucket ``error_rate`` / ``latency_p95`` metrics.

        One pure-function pass over the whole batch of ops events (bucketed per
        ``(service, region)`` at ``metric_bucket`` granularity). Each resulting
        :class:`MetricObservation` is written + staged for publication in its own
        unit of work (no canonical row to co-commit with -- the ops facts already
        committed during ``_process``). Idempotent on the metric's natural key.
        """

        if not ops_events:
            return
        from edis_integration.pipeline.engine import _metric_point

        metrics = derive_ops_metrics(ops_events, granularity=self._metric_bucket)
        if not metrics:
            return
        async with self._repo.unit_of_work() as uow:
            for obs in metrics:
                await uow.insert_metric(obs)
                await uow.stage_outbox(_metric_point(obs, source="integration-batch"))


__all__ = ["BatchLoader", "BatchResult"]
