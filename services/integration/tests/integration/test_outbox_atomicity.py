"""Transactional-outbox atomicity + relay publish/mark against real Postgres.

``@pytest.mark.integration`` -- requires Docker. Proves the outbox guarantee on
the real schema:

* the canonical write and the outbox rows are staged in ONE transaction, so after
  a PERSISTED envelope the ``integration_outbox`` holds the canonical + metric +
  lineage events (no persisted-but-not-published gap);
* :func:`relay_once` publishes every unpublished row through the sink and then
  marks them published -- a second relay pass publishes nothing (idempotent);
* a failure inside the unit of work rolls back atomically: neither the canonical
  row nor its outbox rows persist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from edis_contracts import topics
from edis_contracts.ingest import IngestEnvelope
from sqlalchemy import func, select

from edis_integration.outbox.outbox_repo import SqlAlchemyOutboxRepo
from edis_integration.outbox.relay import relay_once
from edis_integration.persistence.models import (
    CanonicalOrderRow,
    IntegrationOutboxRow,
)
from edis_integration.persistence.repositories import SqlAlchemyIntegrationRepo
from edis_integration.pipeline.engine import IntegrationOutcome, process_envelope

pytestmark = pytest.mark.integration

_TENANT = "acme"
_SOURCE = "simulator"


class _RecordingSink:
    """Captures published (topic, key, value) tuples for assertions."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str | None, object]] = []

    async def start(self) -> None:  # pragma: no cover - lifecycle no-op
        return None

    async def stop(self) -> None:  # pragma: no cover - lifecycle no-op
        return None

    async def publish(self, topic, key, value) -> None:
        self.published.append((topic, key, value))


def _sales_envelope(order_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=uuid4(),
        idempotency_key=f"sales:{_TENANT}:{_SOURCE}:{order_id}",
        schema_ref="sales.v1",
        domain="sales",
        tenant_id=_TENANT,
        source_system=_SOURCE,
        ingest_ts=datetime.now(timezone.utc),
        event_ts=datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
        payload={
            "order_id": order_id,
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": 2,
            "unit_price": 129.0,
            "currency": "USD",
            "region": "EMEA",
            "channel": "web",
            "order_ts": "2026-06-12T10:00:00Z",
        },
    )


async def _count(session, model, **where) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in where.items():
        stmt = stmt.where(getattr(model, col) == val)
    return int((await session.execute(stmt)).scalar_one())


async def test_canonical_write_and_outbox_committed_together(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    res = await process_envelope(_sales_envelope("SO-OB-1"), repo=repo)
    assert res.outcome is IntegrationOutcome.PERSISTED

    async with pg_sessionmaker() as session:
        assert await _count(session, CanonicalOrderRow) == 1
        # outbox holds: order + customer canonical events, 2 metric points, lineage.
        rows = (
            (
                await session.execute(
                    select(IntegrationOutboxRow.topic).where(
                        IntegrationOutboxRow.published.is_(False)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert topics.CANONICAL_ORDER in rows
        assert topics.CANONICAL_CUSTOMER in rows
        assert rows.count(topics.METRICS_POINTS) == 2
        assert rows.count(topics.LINEAGE) == 1


async def test_relay_publishes_then_marks_published(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    await process_envelope(_sales_envelope("SO-OB-2"), repo=repo)

    reader = SqlAlchemyOutboxRepo(pg_sessionmaker)
    sink = _RecordingSink()

    n = await relay_once(reader, sink, limit=500)
    assert n == 5  # order + customer + 2 metrics + lineage
    published_topics = [t for (t, _k, _v) in sink.published]
    assert topics.CANONICAL_ORDER in published_topics
    assert published_topics.count(topics.METRICS_POINTS) == 2

    # everything is now marked published; a second pass publishes nothing.
    again = await relay_once(reader, sink, limit=500)
    assert again == 0
    async with pg_sessionmaker() as session:
        unpublished = await _count(session, IntegrationOutboxRow, published=False)
        assert unpublished == 0


async def test_unit_of_work_rolls_back_atomically(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    env = _sales_envelope("SO-OB-ROLLBACK")
    ctx = __import__(
        "edis_integration.pipeline.engine", fromlist=["normalize_envelope"]
    ).normalize_envelope(env)
    order = ctx.coerced.order
    customer = ctx.coerced.customer

    # Open a unit of work, stage writes, then raise inside the `async with` so the
    # transaction rolls back -- neither the canonical row nor outbox rows persist.
    with pytest.raises(RuntimeError):
        async with repo.unit_of_work() as uow:
            await uow.upsert_customer(customer)
            await uow.upsert_order(order)
            raise RuntimeError("boom -- force rollback")

    async with pg_sessionmaker() as session:
        assert await _count(session, CanonicalOrderRow) == 0
        assert await _count(session, IntegrationOutboxRow) == 0
