"""Time-series repository for the ``metric_observations`` hypertable.

L2 writes metric points inside the canonical-write transaction (see
:class:`~edis_integration.persistence.repositories.SqlAlchemyUnitOfWork.insert_metric`);
this repo is the **read side** L3/ops/admin use, plus a standalone bulk-write
helper for tooling. The headline method is :meth:`daily_rollup`, which returns the
per-``(tenant, metric, day)`` rollup L3 reads.

In production the daily rollup is the Timescale ``metric_observations_daily``
continuous aggregate; on a plain Postgres without TimescaleDB the migration
created a regular view of the *same shape*. :meth:`daily_rollup` therefore always
computes ``GROUP BY date_trunc('day', ts)`` directly over ``metric_observations``
-- so it returns the correct rollup whether or not Timescale is present (it does
not depend on the aggregate being refreshed). The pure in-process equivalent for
unit tests is :func:`edis_integration.mappers.metrics.rollup_daily`.

Daily-rollup semantics per the architecture: revenue/orders are *summed* per day;
error_rate/latency_p95 are *averaged/maxed* -- the row carries every aggregate
(``sum``/``avg``/``min``/``max``/``count``) so the caller picks the meaningful one.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from edis_contracts.canonical import MetricObservation
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from edis_integration.persistence.models import MetricObservationRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _refs_json(refs) -> list[dict]:
    return [r.model_dump(mode="json") for r in refs]


class TimeseriesRepo:
    """Read/write access to the metric series (Timescale-agnostic)."""

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def write_metrics(self, observations: list[MetricObservation]) -> int:
        """Bulk-write metric observations (idempotent on the natural key).

        A standalone helper for tooling/backfill; the live pipeline writes metric
        rows transactionally through the unit of work instead. Additive values
        ADD on conflict (matching the UoW), so re-running a backfill of distinct
        rows is safe.
        """

        if not observations:
            return 0
        async with self._sessionmaker() as session:
            for obs in observations:
                values = {
                    "tenant_id": obs.tenant_id,
                    "metric_key": obs.metric_key,
                    "ts": obs.ts,
                    "dimensions": obs.dimensions,
                    "value": obs.value,
                    "unit": obs.unit,
                    "source_refs": _refs_json(obs.source_refs),
                }
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
                await session.execute(stmt)
            await session.commit()
            return len(observations)

    async def read_series(
        self,
        tenant_id: str,
        metric_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[MetricObservation]:
        """Return the raw observation rows for one ``(tenant, metric)`` series."""

        stmt = select(MetricObservationRow).where(
            MetricObservationRow.tenant_id == tenant_id,
            MetricObservationRow.metric_key == metric_key,
        )
        if start is not None:
            stmt = stmt.where(MetricObservationRow.ts >= start)
        if end is not None:
            stmt = stmt.where(MetricObservationRow.ts < end)
        stmt = stmt.order_by(MetricObservationRow.ts)
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            MetricObservation(
                tenant_id=r.tenant_id,
                metric_key=r.metric_key,
                ts=r.ts,
                dimensions=r.dimensions,
                value=r.value,
                unit=r.unit,
                source_refs=r.source_refs,
            )
            for r in rows
        ]

    async def daily_rollup(
        self,
        tenant_id: str,
        metric_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Per-day rollup for a ``(tenant, metric)`` series -- Timescale-agnostic.

        Computes ``date_trunc('day', ts)`` aggregates directly over
        ``metric_observations`` (correct with or without the continuous aggregate)
        and returns one row per day with ``sum``/``avg``/``min``/``max``/``count``.
        The caller chooses the meaningful aggregate per metric (revenue -> sum;
        error_rate/latency_p95 -> avg/max).
        """

        bucket = func.date_trunc("day", MetricObservationRow.ts).label("bucket")
        stmt = (
            select(
                MetricObservationRow.tenant_id,
                MetricObservationRow.metric_key,
                bucket,
                func.sum(MetricObservationRow.value).label("sum_value"),
                func.avg(MetricObservationRow.value).label("avg_value"),
                func.min(MetricObservationRow.value).label("min_value"),
                func.max(MetricObservationRow.value).label("max_value"),
                func.count().label("sample_count"),
            )
            .where(
                MetricObservationRow.tenant_id == tenant_id,
                MetricObservationRow.metric_key == metric_key,
            )
            .group_by(
                MetricObservationRow.tenant_id,
                MetricObservationRow.metric_key,
                bucket,
            )
            .order_by(bucket)
        )
        if start is not None:
            stmt = stmt.where(MetricObservationRow.ts >= start)
        if end is not None:
            stmt = stmt.where(MetricObservationRow.ts < end)

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            rows = result.all()
        return [
            {
                "tenant_id": r.tenant_id,
                "metric_key": r.metric_key,
                "bucket": r.bucket,
                "sum_value": float(r.sum_value) if r.sum_value is not None else 0.0,
                "avg_value": float(r.avg_value) if r.avg_value is not None else 0.0,
                "min_value": float(r.min_value) if r.min_value is not None else 0.0,
                "max_value": float(r.max_value) if r.max_value is not None else 0.0,
                "sample_count": int(r.sample_count),
            }
            for r in rows
        ]


__all__ = ["TimeseriesRepo"]
