"""Real deterministic-upsert idempotency against Postgres+Timescale.

``@pytest.mark.integration`` -- requires Docker. Drives the same
:func:`process_envelope` the unit suite uses, but through the **real**
:class:`SqlAlchemyIntegrationRepo` (``ON CONFLICT`` upserts + the Timescale
hypertable), proving:

* a sales envelope persists exactly one canonical order (+ line) + one customer,
  with the deterministic ``uuid5`` id, and writes the additive metric rows;
* replaying the SAME envelope is a DUPLICATE -- no second canonical row, no
  double-counted metric (the idempotency mark short-circuits it); and
* a DISTINCT order for the same customer upserts onto the same customer row
  (deterministic customer identity) while inserting a new order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from edis_contracts.ingest import IngestEnvelope
from sqlalchemy import func, select

from edis_integration.mappers.identity import (
    canonical_customer_id,
    canonical_order_id,
)
from edis_integration.persistence.models import (
    CanonicalCustomerRow,
    CanonicalOrderLineRow,
    CanonicalOrderRow,
    MetricObservationRow,
)
from edis_integration.persistence.repositories import SqlAlchemyIntegrationRepo
from edis_integration.pipeline.engine import IntegrationOutcome, process_envelope

pytestmark = pytest.mark.integration

_TENANT = "acme"
_SOURCE = "simulator"


def _sales_envelope(order_id: str, *, customer_id: str = "C1") -> IngestEnvelope:
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
            "customer_id": customer_id,
            "sku": "SKU-A",
            "qty": 2,
            "unit_price": 129.0,
            "currency": "USD",
            "region": "EMEA",
            "channel": "web",
            "order_ts": "2026-06-12T10:00:00Z",
        },
    )


async def _count(session, model) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_sales_envelope_persists_canonical_rows(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    res = await process_envelope(_sales_envelope("SO-1"), repo=repo)
    assert res.outcome is IntegrationOutcome.PERSISTED

    async with pg_sessionmaker() as session:
        order = (
            await session.execute(
                select(CanonicalOrderRow).where(
                    CanonicalOrderRow.canonical_order_id
                    == canonical_order_id(_TENANT, _SOURCE, "SO-1")
                )
            )
        ).scalar_one()
        assert order.amount_base == 258
        assert order.region == "EMEA"
        assert order.channel == "web"
        assert order.canonical_customer_id == canonical_customer_id(_TENANT, "C1")

        assert await _count(session, CanonicalOrderRow) == 1
        assert await _count(session, CanonicalOrderLineRow) == 1
        assert await _count(session, CanonicalCustomerRow) == 1
        # revenue + orders metric rows.
        assert await _count(session, MetricObservationRow) == 2


async def test_replayed_envelope_is_idempotent(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    env = _sales_envelope("SO-DUP")

    first = await process_envelope(env, repo=repo)
    second = await process_envelope(env, repo=repo)
    assert first.outcome is IntegrationOutcome.PERSISTED
    assert second.outcome is IntegrationOutcome.DUPLICATE

    async with pg_sessionmaker() as session:
        assert await _count(session, CanonicalOrderRow) == 1
        assert await _count(session, CanonicalOrderLineRow) == 1
        # no double-counted revenue: still exactly the first write's two rows.
        assert await _count(session, MetricObservationRow) == 2
        rev = (
            await session.execute(
                select(MetricObservationRow.value).where(
                    MetricObservationRow.metric_key == "revenue"
                )
            )
        ).scalar_one()
        assert rev == 258.0  # not 516 -- the replay added nothing


async def test_distinct_orders_share_one_customer_row(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntegrationRepo(pg_sessionmaker)
    # Two different orders for the same customer.
    await process_envelope(_sales_envelope("SO-A", customer_id="CUST-1"), repo=repo)
    await process_envelope(_sales_envelope("SO-B", customer_id="CUST-1"), repo=repo)

    async with pg_sessionmaker() as session:
        assert await _count(session, CanonicalOrderRow) == 2
        # deterministic customer identity -> a single upserted customer row.
        assert await _count(session, CanonicalCustomerRow) == 1
        cust = (await session.execute(select(CanonicalCustomerRow))).scalar_one()
        assert cust.canonical_customer_id == canonical_customer_id(_TENANT, "CUST-1")
