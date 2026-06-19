"""Transactional outbox: stage-in-txn, then relay-publish-then-mark.

Events are staged into ``integration_outbox`` inside the canonical-write
transaction (in ``persistence.repositories``); the relay here publishes them and
marks them published, so there is no persisted-but-not-published gap.

Public surface:

* :class:`SqlAlchemyOutboxRepo` / :class:`InMemoryOutboxRepo` -- the relay-facing
  reader/marker port over Postgres / in-memory.
* :func:`relay_once` -- publish one bounded batch (pure, unit-testable).
* :class:`OutboxRelay` -- stoppable poll loop (``run``) + bounded ``drain``.
"""

from __future__ import annotations

from edis_integration.outbox.outbox_repo import (
    InMemoryOutboxRepo,
    OutboxReader,
    PendingEvent,
    SqlAlchemyOutboxRepo,
)
from edis_integration.outbox.relay import OutboxRelay, relay_once

__all__ = [
    "PendingEvent",
    "OutboxReader",
    "SqlAlchemyOutboxRepo",
    "InMemoryOutboxRepo",
    "relay_once",
    "OutboxRelay",
]
