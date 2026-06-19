"""Async repository for the raw-landing outbox.

The :class:`RawWriter` is the durable-write side of the outbox pattern. The MVP
uses **publish-after-land**: the engine lands a ``raw_events`` row (the durable
record) and then publishes; a reconcile path (:meth:`fetch_unpublished` +
:meth:`mark_published`) republishes any row still ``published=false`` after a
broker outage, so there is no "persisted-but-not-published" gap.

The writer is constructed with a sessionmaker (never a live connection) so it is
import-safe; each write opens, commits and closes its own short transaction.
``write_raw`` is idempotent at the DB level via the unique ``idempotency_key`` —
a concurrent duplicate that slipped past the Redis guard is absorbed here and
reported as "not newly written" rather than raising.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from edis_contracts.ingest import DLQRecord, IngestEnvelope
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ingestion.storage.models import IngestCheckpoint, IngestDLQ, RawEvent

if TYPE_CHECKING:
    pass


class RawWriter:
    """Outbox repository over an async sessionmaker."""

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def write_raw(self, env: IngestEnvelope, *, trace_id: str | None = None) -> bool:
        """Land an envelope as a ``raw_events`` row (the outbox write).

        Uses ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` so a
        duplicate that slipped past the in-flight idempotency guard is silently
        absorbed at the DB. Returns ``True`` when a new row was inserted, ``False``
        when it was a duplicate (the caller should then *not* re-publish).
        """

        values = {
            "id": env.event_id,
            "tenant_id": env.tenant_id,
            "domain": env.domain,
            "source_system": env.source_system,
            "idempotency_key": env.idempotency_key,
            "event_id": env.event_id,
            "payload": env.payload,
            "anomaly_label": env.anomaly_label,
            "ingest_ts": env.ingest_ts,
            "event_ts": env.event_ts,
            "published": False,
            "trace_id": trace_id,
        }
        stmt = (
            pg_insert(RawEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(RawEvent.id)
        )
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            inserted = result.scalar_one_or_none() is not None
            await session.commit()
            return inserted

    async def mark_published(self, event_id: UUID) -> None:
        """Flip ``published=true`` after a successful bus publish."""

        async with self._sessionmaker() as session:
            await session.execute(
                update(RawEvent).where(RawEvent.id == event_id).values(published=True)
            )
            await session.commit()

    async def fetch_unpublished(self, limit: int = 500) -> list[RawEvent]:
        """Return landed-but-unpublished rows for the reconcile relay."""

        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RawEvent)
                .where(RawEvent.published.is_(False))
                .order_by(RawEvent.ingest_ts)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def write_dlq(self, record: DLQRecord) -> None:
        """Persist a dead-letter record (durable, replayable)."""

        raw = record.raw
        # JSONB column wants a dict/list/scalar; coerce odd raw types to a wrapper.
        if raw is not None and not isinstance(raw, (dict, list, str, int, float, bool)):
            raw = {"value": str(raw)}
        row = IngestDLQ(
            dlq_id=record.dlq_id,
            tenant_id=record.tenant_id,
            stage=record.stage,
            domain=record.domain,
            source_system=record.source_system,
            raw=(
                raw
                if isinstance(raw, (dict, list))
                else ({"value": raw} if raw is not None else None)
            ),
            error_type=record.error_type,
            error_detail=record.error_detail,
            occurred_at=record.occurred_at,
            trace_id=record.trace_id,
        )
        async with self._sessionmaker() as session:
            session.add(row)
            await session.commit()

    async def fetch_dlq(
        self, limit: int = 500, *, include_replayed: bool = False
    ) -> list[IngestDLQ]:
        """Return persisted DLQ rows (for ``dil replay-dlq``)."""

        async with self._sessionmaker() as session:
            stmt = select(IngestDLQ).order_by(IngestDLQ.occurred_at).limit(limit)
            if not include_replayed:
                stmt = stmt.where(IngestDLQ.replayed.is_(False))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def mark_dlq_replayed(self, dlq_id: UUID) -> None:
        """Flag a DLQ row as replayed so it is not re-processed."""

        async with self._sessionmaker() as session:
            await session.execute(
                update(IngestDLQ).where(IngestDLQ.dlq_id == dlq_id).values(replayed=True)
            )
            await session.commit()

    async def get_checkpoint(self, tenant_id: str, source_key: str) -> int:
        """Return the last processed offset for a batch source (0 if none)."""

        async with self._sessionmaker() as session:
            result = await session.execute(
                select(IngestCheckpoint.offset).where(
                    IngestCheckpoint.tenant_id == tenant_id,
                    IngestCheckpoint.source_key == source_key,
                )
            )
            offset = result.scalar_one_or_none()
            return int(offset) if offset is not None else 0

    async def set_checkpoint(
        self, tenant_id: str, source_key: str, offset: int, *, rows_ingested: int = 0
    ) -> None:
        """Upsert the batch-loader checkpoint for a source."""

        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(IngestCheckpoint)
            .values(
                tenant_id=tenant_id,
                source_key=source_key,
                offset=offset,
                rows_ingested=rows_ingested,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "source_key"],
                set_={"offset": offset, "rows_ingested": rows_ingested, "updated_at": now},
            )
        )
        async with self._sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()
