"""Read/mark side of the transactional outbox.

Staging happens inside the canonical-write transaction (see
:class:`~edis_integration.persistence.repositories.SqlAlchemyUnitOfWork.stage_outbox`):
one ``integration_outbox`` row per event, committed atomically with the canonical
rows. This module is the relay's companion -- it reads ``published = false`` rows
(oldest first) and flips them to published after a successful bus publish, so
there is never a persisted-but-not-published gap and replay is idempotent.

A :class:`PendingEvent` is the relay's view of one staged row: the topic/key and
the already-JSON-serialized ``payload`` (the relay publishes the dict directly --
the bus backends serialize a dict exactly as they would the original model).

Two backends, one shape:

* :class:`SqlAlchemyOutboxRepo` -- over the ``integration_outbox`` table
  (``@pytest.mark.integration``).
* :class:`InMemoryOutboxRepo`   -- adapts an
  :class:`~edis_integration.pipeline.engine.InMemoryIntegrationRepo`'s in-memory
  ``outbox`` list so the relay loop is unit-testable with no Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select, update

from edis_integration.persistence.models import IntegrationOutboxRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from edis_integration.pipeline.engine import InMemoryIntegrationRepo


@dataclass
class PendingEvent:
    """One unpublished outbox row, ready for the relay to publish."""

    event_id: UUID
    topic: str
    key: str | None
    payload: dict


@runtime_checkable
class OutboxReader(Protocol):
    """Relay-facing port: fetch unpublished events, mark them published."""

    async def fetch_unpublished(self, *, limit: int = 500) -> list[PendingEvent]: ...

    async def mark_published(self, event_ids: list[UUID]) -> None: ...


class SqlAlchemyOutboxRepo:
    """Outbox reader/marker over the ``integration_outbox`` table."""

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def fetch_unpublished(self, *, limit: int = 500) -> list[PendingEvent]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.published.is_(False))
                .order_by(IntegrationOutboxRow.created_at)
                .limit(limit)
            )
            rows = result.scalars().all()
        return [
            PendingEvent(
                event_id=r.event_id,
                topic=r.topic,
                key=r.key,
                payload=r.payload,
            )
            for r in rows
        ]

    async def mark_published(self, event_ids: list[UUID]) -> None:
        if not event_ids:
            return
        now = datetime.now(timezone.utc)
        async with self._sessionmaker() as session:
            await session.execute(
                update(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.event_id.in_(event_ids))
                .values(published=True, published_at=now)
            )
            await session.commit()

    async def pending_count(self) -> int:
        """Number of staged-but-unpublished events (ops lag signal)."""

        from sqlalchemy import func

        async with self._sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.published.is_(False))
            )
            return int(result.scalar_one())


class InMemoryOutboxRepo:
    """Adapts an :class:`InMemoryIntegrationRepo`'s ``outbox`` list to the port.

    The in-memory repo stores staged :class:`~edis_integration.pipeline.engine.OutboxEvent`s
    (pydantic value still attached); this reader serializes them to dicts (exactly
    as the bus would on the wire) and tracks which ``event_id``s have been
    published, so the relay loop is exercisable with no DB.
    """

    def __init__(self, repo: "InMemoryIntegrationRepo") -> None:
        self._repo = repo
        self._published: set[UUID] = set()

    async def fetch_unpublished(self, *, limit: int = 500) -> list[PendingEvent]:
        out: list[PendingEvent] = []
        for ev in self._repo.outbox:
            if ev.event_id in self._published:
                continue
            out.append(
                PendingEvent(
                    event_id=ev.event_id,
                    topic=ev.topic,
                    key=ev.key,
                    payload=ev.value.model_dump(mode="json"),
                )
            )
            if len(out) >= limit:
                break
        return out

    async def mark_published(self, event_ids: list[UUID]) -> None:
        self._published.update(event_ids)

    async def pending_count(self) -> int:
        return sum(1 for ev in self._repo.outbox if ev.event_id not in self._published)


__all__ = [
    "PendingEvent",
    "OutboxReader",
    "SqlAlchemyOutboxRepo",
    "InMemoryOutboxRepo",
]
