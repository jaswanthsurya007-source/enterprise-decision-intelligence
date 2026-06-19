"""Transactional-outbox relay -- publish staged events, then mark them published.

The pipeline stages every event (canonical changes, metric points, the lineage
edge, and DLQ quarantines) into the outbox *inside the same transaction* as the
canonical write. The relay closes the loop: it reads ``published = false`` rows,
publishes each through the injected :class:`~edis_platform.bus.base.EventSink`
(``make_sink`` -> Kafka / Redis / in-proc), and only then flips them to
published. The ordering -- publish *before* mark -- is the at-least-once
guarantee: a crash after publish but before mark re-publishes on the next pass,
and idempotent consumers (keyed on the canonical id / event id) absorb the
duplicate. There is no persisted-but-not-published gap.

The relay is a pure async function over the :class:`OutboxReader` port + a sink,
so it is unit-testable over the in-memory outbox + the in-proc bus (subscribe ->
relay -> consume), and runs the identical code path in prod over Postgres +
Redpanda. :class:`OutboxRelay` wraps :func:`relay_once` in a stoppable poll loop
for the long-running service.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from edis_platform.logging import get_logger

if TYPE_CHECKING:
    from edis_platform.bus.base import EventSink

    from edis_integration.outbox.outbox_repo import OutboxReader

_log = get_logger(__name__)


async def relay_once(
    reader: "OutboxReader",
    sink: "EventSink",
    *,
    limit: int = 500,
) -> int:
    """Publish one bounded batch of unpublished events; return how many were sent.

    Publishes each event, then marks the whole batch published. A publish failure
    aborts the batch *before* marking, so the failed (and subsequent) events stay
    unpublished and are retried on the next pass -- never lost, at most duplicated.
    """

    pending = await reader.fetch_unpublished(limit=limit)
    if not pending:
        return 0

    published_ids = []
    for event in pending:
        await sink.publish(event.topic, key=event.key, value=event.payload)
        published_ids.append(event.event_id)

    await reader.mark_published(published_ids)
    _log.info("outbox relay flushed batch", extra={"published": len(published_ids)})
    return len(published_ids)


class OutboxRelay:
    """Stoppable poll loop around :func:`relay_once` for the long-running service."""

    def __init__(
        self,
        reader: "OutboxReader",
        sink: "EventSink",
        *,
        batch_limit: int = 500,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._reader = reader
        self._sink = sink
        self._batch_limit = batch_limit
        self._poll_interval_s = poll_interval_s
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        """Signal the run loop to exit after the in-flight batch."""

        self._stopped.set()

    async def run(self) -> None:
        """Drain-then-poll until :meth:`stop`. Errors are logged and retried."""

        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                flushed = await relay_once(self._reader, self._sink, limit=self._batch_limit)
            except Exception:  # pragma: no cover - defensive; logged + retried
                _log.exception("outbox relay batch failed; retrying")
                flushed = 0
            if flushed == 0:
                # Nothing pending -- sleep, but wake immediately on stop().
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=self._poll_interval_s)
                except asyncio.TimeoutError:
                    pass

    async def drain(self) -> int:
        """Publish all currently-pending events in batches; return the total.

        A bounded, terminating variant used by the batch loader and tests: it
        keeps flushing until a pass publishes nothing.
        """

        total = 0
        while True:
            flushed = await relay_once(self._reader, self._sink, limit=self._batch_limit)
            total += flushed
            if flushed == 0:
                return total


__all__ = ["relay_once", "OutboxRelay"]
