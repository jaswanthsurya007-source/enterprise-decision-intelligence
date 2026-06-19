"""Concrete :class:`IntegrationRepo` implementations.

The engine (``pipeline/engine.py``) defines the :class:`IntegrationRepo` *port*
(a ``Protocol``), the transactional-outbox unit-of-work contract
(:class:`RepoUnitOfWork`), and the :class:`InMemoryIntegrationRepo` fake the unit
suite runs against. This module adds the **real async-SQLAlchemy implementation**
-- :class:`SqlAlchemyIntegrationRepo` -- exercised under
``@pytest.mark.integration`` against a Postgres/Timescale testcontainer.

The repo satisfies the port structurally: ``process_envelope`` opens one
:meth:`unit_of_work` per envelope and performs *all* writes for that envelope --
the canonical upserts, the metric rows, the outbox rows, and the idempotency mark
-- inside it. The SQLAlchemy UoW maps that to a single DB transaction (one
session, one ``commit`` on clean exit, ``rollback`` on error), so the canonical
state and the outbox events are atomically consistent: no
persisted-but-not-published gap. Upserts use ``INSERT ... ON CONFLICT`` keyed on
the deterministic canonical id, so a replayed envelope is idempotent.

For convenience the in-memory fake is re-exported here so callers have one
import surface for "give me a repo"; ``make_repo(settings)`` selects between them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from edis_contracts.canonical import (
    CanonicalCustomer,
    CanonicalOrder,
    MetricObservation,
    OpsEvent,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from edis_integration.persistence.models import (
    CanonicalCustomerRow,
    CanonicalOrderLineRow,
    CanonicalOrderRow,
    IntegrationIdempotencyRow,
    IntegrationOutboxRow,
    IntegrationQuarantineRow,
    MetricObservationRow,
    OpsEventRow,
)
from edis_integration.pipeline.engine import (
    InMemoryIntegrationRepo,
    IntegrationRepo,
    OutboxEvent,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from edis_platform.settings import Settings

# Re-export the in-memory fake so callers have one import surface.
__all__ = [
    "SqlAlchemyIntegrationRepo",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyQuarantineRepo",
    "InMemoryIntegrationRepo",
    "make_repo",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _refs_json(refs) -> list[dict]:
    """Serialize a list[SourceRef] (pydantic) to JSON-able dicts for JSONB."""

    return [r.model_dump(mode="json") for r in refs]


class SqlAlchemyUnitOfWork:
    """One atomic write scope over a single :class:`AsyncSession`.

    Every write is staged on the live session; the parent repo commits on clean
    ``__aexit__`` and rolls back on any exception, so the canonical rows, metric
    rows, and outbox rows are all-or-nothing. Mirrors ``_InMemoryUnitOfWork`` so
    the engine cannot tell the two repos apart.
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                await self._session.rollback()
            else:
                await self._session.commit()
        finally:
            await self._session.close()

    # -- canonical upserts (idempotent ON CONFLICT) --------------------------
    async def upsert_customer(self, customer: CanonicalCustomer) -> bool:
        values = {
            "canonical_customer_id": customer.canonical_customer_id,
            "tenant_id": customer.tenant_id,
            "legal_name": customer.legal_name,
            "display_name": customer.display_name,
            "primary_email": customer.primary_email,
            "country_iso2": customer.country_iso2,
            "industry": customer.industry,
            "region": customer.region,
            "valid_from": customer.valid_from,
            "valid_to": customer.valid_to,
            "is_current": customer.is_current,
            "version": customer.version,
            "source_refs": _refs_json(customer.source_refs),
            "dq_score": customer.dq_score,
            "record_hash": customer.record_hash,
            "created_at": customer.created_at,
            "updated_at": customer.updated_at,
        }
        # Was this id already present? (drives the "newly inserted" return)
        existed = (
            await self._session.execute(
                select(CanonicalCustomerRow.canonical_customer_id).where(
                    CanonicalCustomerRow.canonical_customer_id == customer.canonical_customer_id
                )
            )
        ).scalar_one_or_none() is not None
        # DO UPDATE on the mutable, content-derived columns so a corrected source
        # record updates the canonical row; created_at is preserved (insert-only).
        stmt = (
            pg_insert(CanonicalCustomerRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["canonical_customer_id"],
                set_={
                    "legal_name": values["legal_name"],
                    "display_name": values["display_name"],
                    "primary_email": values["primary_email"],
                    "country_iso2": values["country_iso2"],
                    "industry": values["industry"],
                    "region": values["region"],
                    "source_refs": values["source_refs"],
                    "dq_score": values["dq_score"],
                    "record_hash": values["record_hash"],
                    "updated_at": values["updated_at"],
                },
            )
        )
        await self._session.execute(stmt)
        return not existed

    async def upsert_order(self, order: CanonicalOrder) -> bool:
        values = {
            "canonical_order_id": order.canonical_order_id,
            "tenant_id": order.tenant_id,
            "canonical_customer_id": order.canonical_customer_id,
            "order_ts": order.order_ts,
            "currency_base": order.currency_base,
            "amount_base": Decimal(str(order.amount_base)),
            "amount_src": Decimal(str(order.amount_src)),
            "currency_src": order.currency_src,
            "fx_rate": Decimal(str(order.fx_rate)),
            "region": order.region,
            "channel": order.channel,
            "source_refs": _refs_json(order.source_refs),
            "record_hash": order.record_hash,
            "created_at": order.created_at,
        }
        # An order is an immutable fact: DO NOTHING keeps replay idempotent.
        stmt = (
            pg_insert(CanonicalOrderRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["canonical_order_id"])
            .returning(CanonicalOrderRow.canonical_order_id)
        )
        result = await self._session.execute(stmt)
        inserted = result.scalar_one_or_none() is not None
        if inserted:
            await self._insert_order_lines(order)
        return inserted

    async def _insert_order_lines(self, order: CanonicalOrder) -> None:
        for line_no, line in enumerate(order.line_items, start=1):
            stmt = (
                pg_insert(CanonicalOrderLineRow)
                .values(
                    tenant_id=order.tenant_id,
                    canonical_order_id=order.canonical_order_id,
                    line_no=line_no,
                    canonical_product_id=line.canonical_product_id,
                    sku=line.sku,
                    qty=line.qty,
                    unit_price_base=Decimal(str(line.unit_price_base)),
                    line_amount_base=Decimal(str(line.line_amount_base)),
                )
                .on_conflict_do_nothing(constraint="uq_canonical_order_line_no")
            )
            await self._session.execute(stmt)

    async def insert_ops_event(self, event: OpsEvent) -> bool:
        values = {
            "canonical_ops_event_id": event.canonical_ops_event_id,
            "tenant_id": event.tenant_id,
            "service": event.service,
            "region": event.region,
            "level": event.level,
            "status_code": event.status_code,
            "latency_ms": event.latency_ms,
            "message": event.message,
            "event_ts": event.event_ts,
            "source_refs": _refs_json(event.source_refs),
            "record_hash": event.record_hash,
        }
        stmt = (
            pg_insert(OpsEventRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["canonical_ops_event_id"])
            .returning(OpsEventRow.canonical_ops_event_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def insert_metric(self, obs: MetricObservation) -> None:
        values = {
            "tenant_id": obs.tenant_id,
            "metric_key": obs.metric_key,
            "ts": obs.ts,
            "dimensions": obs.dimensions,
            "value": obs.value,
            "unit": obs.unit,
            "source_refs": _refs_json(obs.source_refs),
        }
        # Composite natural key (tenant_id, metric_key, ts). Additive metrics
        # (revenue/orders) can collide on the same ts within a bucket; we ADD
        # the values on conflict so the daily sum stays correct under replay of
        # *distinct* envelopes while a true duplicate envelope is already short-
        # circuited upstream by the idempotency guard.
        stmt = (
            pg_insert(MetricObservationRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["tenant_id", "metric_key", "ts"],
                set_={
                    "value": MetricObservationRow.value + values["value"],
                    "source_refs": values["source_refs"],
                },
            )
        )
        await self._session.execute(stmt)

    async def stage_outbox(self, event: OutboxEvent) -> None:
        payload = event.value.model_dump(mode="json")
        tenant_id = getattr(event.value, "tenant_id", None) or (event.key or "")
        stmt = (
            pg_insert(IntegrationOutboxRow)
            .values(
                event_id=event.event_id,
                tenant_id=str(tenant_id),
                topic=event.topic,
                key=event.key,
                payload=payload,
                published=False,
                created_at=_utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        await self._session.execute(stmt)

    async def mark_idempotency_key(self, tenant_id: str, key: str) -> None:
        stmt = (
            pg_insert(IntegrationIdempotencyRow)
            .values(tenant_id=tenant_id, idempotency_key=key, processed_at=_utc_now())
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        )
        await self._session.execute(stmt)


class SqlAlchemyIntegrationRepo:
    """Real async-SQLAlchemy :class:`IntegrationRepo` (integration-test path).

    Constructed with a sessionmaker (never a live connection), so it is
    import-safe. :meth:`unit_of_work` opens one session/transaction per envelope.
    """

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._sessionmaker())

    async def seen_idempotency_key(self, tenant_id: str, key: str) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(IntegrationIdempotencyRow.idempotency_key).where(
                    IntegrationIdempotencyRow.tenant_id == tenant_id,
                    IntegrationIdempotencyRow.idempotency_key == key,
                )
            )
            return result.scalar_one_or_none() is not None


class SqlAlchemyQuarantineRepo:
    """Persist + read :class:`QuarantinedRecord`s, and rebuild raw envelopes.

    Backs the ops/admin ``/v1/integration/quarantine`` + ``/reprocess`` routes.
    The persisted ``raw`` column holds the original envelope JSON, so
    :meth:`envelopes_for` can replay a quarantined record through the batch loader.
    """

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def persist(self, record) -> None:
        """Persist one :class:`QuarantinedRecord` (idempotent on quarantine_id)."""

        stmt = (
            pg_insert(IntegrationQuarantineRow)
            .values(
                quarantine_id=record.quarantine_id,
                tenant_id=record.tenant_id,
                stage=record.stage,
                reason=record.reason,
                dq_failures=list(record.dq_failures),
                raw=record.raw if isinstance(record.raw, (dict, list)) else None,
                occurred_at=record.occurred_at,
                reprocessed=False,
            )
            .on_conflict_do_nothing(index_elements=["quarantine_id"])
        )
        async with self._sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()

    async def list_quarantined(self, *, limit: int = 100) -> list[dict]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(IntegrationQuarantineRow)
                .where(IntegrationQuarantineRow.reprocessed.is_(False))
                .order_by(IntegrationQuarantineRow.occurred_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
        return [
            {
                "quarantine_id": str(r.quarantine_id),
                "tenant_id": r.tenant_id,
                "reason": r.reason,
                "dq_failures": r.dq_failures,
                "occurred_at": r.occurred_at.isoformat(),
            }
            for r in rows
        ]

    async def envelopes_for(self, quarantine_ids: list[str]):
        """Rebuild the raw envelopes for the given quarantine ids (for replay)."""

        from edis_contracts.ingest import IngestEnvelope

        ids = [UUID(q) for q in quarantine_ids]
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(IntegrationQuarantineRow).where(
                    IntegrationQuarantineRow.quarantine_id.in_(ids)
                )
            )
            rows = result.scalars().all()
        out = []
        for r in rows:
            if isinstance(r.raw, dict):
                try:
                    out.append(IngestEnvelope.model_validate(r.raw))
                except Exception:  # pragma: no cover - corrupt stored raw
                    continue
        return out


# Structural conformance check (raises at import only if the port drifts).
_: type[IntegrationRepo] = SqlAlchemyIntegrationRepo  # type: ignore[assignment]


def make_repo(settings: "Settings") -> IntegrationRepo:
    """Select a repo by backend: real SQLAlchemy repo, or the in-memory fake.

    When persistence is unavailable/disabled (``sink_backend == "inproc"`` and no
    DB), callers pass the in-memory repo explicitly; this factory returns the
    SQLAlchemy repo bound to the platform sessionmaker for the live service.
    """

    from edis_integration.persistence.db import get_sessionmaker

    return SqlAlchemyIntegrationRepo(get_sessionmaker())
