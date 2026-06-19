"""Async read repositories behind one :class:`GatewayRepo` port.

The gateway serves REST **snapshots** of facts other layers already computed and
persisted in the shared Postgres. It never computes anything; it reads:

* ``/v1/kpis``            -> the L2 **daily metric rollup** (a continuous aggregate
                             over ``metric_observations``), projected to
                             :class:`~edis_gateway.models.KpiSnapshot`;
* ``/v1/anomalies``       -> the L3 ``findings`` table (:class:`Finding`);
* ``/v1/recommendations`` -> the L4 ``recommendations`` table
                             (:class:`Recommendation`), **sorted by priority**;
* ``/v1/forecasts``       -> the L3 ``forecasts`` table (:class:`Forecast`).

Two implementations sit behind the :class:`GatewayRepo` ``Protocol``:

* :class:`InMemoryGatewayRepo` — the infra-free, deterministic, tenant-scoped fake
  the REST unit tests run against (no Docker, no DB);
* :class:`SqlAlchemyGatewayRepo` — the real async-SQLAlchemy reader exercised under
  ``@pytest.mark.integration`` against a Postgres testcontainer.

**Every** read is tenant-scoped by the caller-supplied ``tenant_id`` (which the
routes take only from the verified JWT). The fake enforces this exactly like the
real repo's ``WHERE tenant_id = :tenant`` filter, so a cross-tenant read returns
nothing in both. The SQL repo issues read-only ``SELECT``s and binds parameters
(no string interpolation of tenant/user input).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from edis_contracts.decisions import Recommendation
from edis_contracts.findings import Finding, Forecast

from edis_gateway.models import KpiSnapshot

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_MAX_PAGE = 200


class GatewayRepo(Protocol):
    """Port: tenant-scoped reads of KPIs, findings, recommendations, forecasts."""

    async def list_kpis(
        self,
        tenant_id: str,
        *,
        metric_key: str | None = None,
        limit: int = 50,
    ) -> list[KpiSnapshot]: ...

    async def list_anomalies(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]: ...

    async def list_recommendations(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[Recommendation]: ...

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]: ...


# ---------------------------------------------------------------------------
# In-memory fake (infra-free; the unit suite runs against this)
# ---------------------------------------------------------------------------
class InMemoryGatewayRepo:
    """Deterministic, tenant-scoped in-memory :class:`GatewayRepo` for tests.

    Seed it with already-computed snapshots/contract models; reads filter by
    ``tenant_id`` (so a cross-tenant read is empty), apply the optional filters,
    sort exactly as the real repo (recommendations by ``priority_rank`` then
    descending ``priority_score``; findings/forecasts newest-first; KPIs by
    ``metric_key``), and paginate. No I/O, no Docker.
    """

    def __init__(
        self,
        *,
        kpis: list[KpiSnapshot] | None = None,
        anomalies: list[Finding] | None = None,
        recommendations: list[Recommendation] | None = None,
        forecasts: list[Forecast] | None = None,
    ) -> None:
        self._kpis = list(kpis or [])
        self._anomalies = list(anomalies or [])
        self._recommendations = list(recommendations or [])
        self._forecasts = list(forecasts or [])

    # --- seed helpers (used by tests / a non-DB demo wiring) ---
    def add_kpi(self, snapshot: KpiSnapshot) -> None:
        self._kpis.append(snapshot)

    def add_anomaly(self, finding: Finding) -> None:
        self._anomalies.append(finding)

    def add_recommendation(self, rec: Recommendation) -> None:
        self._recommendations.append(rec)

    def add_forecast(self, forecast: Forecast) -> None:
        self._forecasts.append(forecast)

    async def list_kpis(
        self,
        tenant_id: str,
        *,
        metric_key: str | None = None,
        limit: int = 50,
    ) -> list[KpiSnapshot]:
        rows = [k for k in self._kpis if k.tenant_id == tenant_id]
        if metric_key is not None:
            rows = [k for k in rows if k.metric_key == metric_key]
        rows.sort(key=lambda k: (k.metric_key, tuple(sorted(k.dimensions.items()))))
        return rows[: _clamp(limit)]

    async def list_anomalies(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]:
        rows = [f for f in self._anomalies if f.tenant_id == tenant_id]
        if status is not None:
            rows = [f for f in rows if f.status == status]
        if metric_key is not None:
            rows = [f for f in rows if f.metric_key == metric_key]
        rows.sort(key=lambda f: f.created_at, reverse=True)
        return _page(rows, limit, offset)

    async def list_recommendations(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[Recommendation]:
        rows = [r for r in self._recommendations if r.tenant_id == tenant_id]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        # Priority ordering: best rank first (rank 1 highest); break ties on the
        # higher priority_score, then newest — identical to the SQL ORDER BY.
        rows.sort(key=lambda r: (r.priority_rank, -r.priority_score, _neg_ts(r.created_at)))
        return _page(rows, limit, offset)

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]:
        rows = [f for f in self._forecasts if f.tenant_id == tenant_id]
        if metric_key is not None:
            rows = [f for f in rows if f.metric_key == metric_key]
        rows.sort(key=lambda f: f.generated_at, reverse=True)
        return _page(rows, limit, offset)


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), _MAX_PAGE))


def _page(rows: list, limit: int, offset: int) -> list:
    off = max(0, int(offset))
    return rows[off : off + _clamp(limit)]


def _neg_ts(dt) -> float:
    return -dt.timestamp()


# ---------------------------------------------------------------------------
# SQLAlchemy read repo (integration path)
# ---------------------------------------------------------------------------
class SqlAlchemyGatewayRepo:
    """Real async-SQLAlchemy reader over the shared Postgres (integration-only).

    Holds an :class:`async_sessionmaker`; opens a short-lived read session per
    call. Tenant isolation is the platform pattern: bind ``app.tenant_id`` via
    :func:`edis_platform.db.session.set_tenant` (a no-op until RLS ``FORCE`` is
    enabled) **and** a ``WHERE tenant_id = :tenant`` filter on every query. All
    values are bound parameters; nothing from the request is interpolated.

    Constructed lazily by the app factory only when a real database is configured;
    importing this module never opens a connection.
    """

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def list_kpis(
        self,
        tenant_id: str,
        *,
        metric_key: str | None = None,
        limit: int = 50,
    ) -> list[KpiSnapshot]:
        from sqlalchemy import text

        # Read the L2 daily metric rollup (continuous aggregate). For each
        # (metric_key, dimensions) take the latest day and the same-day-last-week
        # value, computing the WoW delta. The continuous-aggregate view name and
        # columns match the L2/D1 migration (`metric_daily`).
        sql = text("""
            WITH latest AS (
                SELECT DISTINCT ON (metric_key, dimensions)
                       metric_key, dimensions, day, value, unit
                FROM metric_daily
                WHERE tenant_id = :tenant
                  AND (:metric_key IS NULL OR metric_key = :metric_key)
                ORDER BY metric_key, dimensions, day DESC
            )
            SELECT l.metric_key, l.dimensions, l.day, l.value, l.unit,
                   p.value AS previous_value
            FROM latest l
            LEFT JOIN metric_daily p
                   ON p.tenant_id = :tenant
                  AND p.metric_key = l.metric_key
                  AND p.dimensions = l.dimensions
                  AND p.day = l.day - INTERVAL '7 days'
            ORDER BY l.metric_key
            LIMIT :limit
            """)
        async with self._sessionmaker() as session:
            await _set_tenant(session, tenant_id)
            result = await session.execute(
                sql,
                {"tenant": tenant_id, "metric_key": metric_key, "limit": _clamp(limit)},
            )
            out: list[KpiSnapshot] = []
            for row in result.mappings():
                value = float(row["value"])
                prev = row["previous_value"]
                prev_f = float(prev) if prev is not None else None
                delta_abs = (value - prev_f) if prev_f is not None else None
                delta_pct = (
                    (delta_abs / prev_f * 100.0)
                    if prev_f not in (None, 0.0) and delta_abs is not None
                    else None
                )
                out.append(
                    KpiSnapshot(
                        tenant_id=tenant_id,
                        metric_key=row["metric_key"],
                        dimensions=_as_dimensions(row["dimensions"]),
                        day=row["day"],
                        value=value,
                        unit=row["unit"],
                        previous_value=prev_f,
                        delta_abs=delta_abs,
                        delta_pct=delta_pct,
                    )
                )
            return out

    async def list_anomalies(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]:
        from sqlalchemy import text

        sql = text("""
            SELECT payload FROM findings
            WHERE tenant_id = :tenant
              AND (:status IS NULL OR status = :status)
              AND (:metric_key IS NULL OR metric_key = :metric_key)
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """)
        return await self._fetch_models(
            Finding,
            sql,
            tenant_id,
            {
                "tenant": tenant_id,
                "status": status,
                "metric_key": metric_key,
                "limit": _clamp(limit),
                "offset": max(0, int(offset)),
            },
        )

    async def list_recommendations(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[Recommendation]:
        from sqlalchemy import text

        # Sorted by priority: best rank first, then higher score, then newest.
        sql = text("""
            SELECT payload FROM recommendations
            WHERE tenant_id = :tenant
              AND (:status IS NULL OR status = :status)
            ORDER BY priority_rank ASC, priority_score DESC, created_at DESC
            LIMIT :limit OFFSET :offset
            """)
        return await self._fetch_models(
            Recommendation,
            sql,
            tenant_id,
            {
                "tenant": tenant_id,
                "status": status,
                "limit": _clamp(limit),
                "offset": max(0, int(offset)),
            },
        )

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]:
        from sqlalchemy import text

        sql = text("""
            SELECT payload FROM forecasts
            WHERE tenant_id = :tenant
              AND (:metric_key IS NULL OR metric_key = :metric_key)
            ORDER BY generated_at DESC
            LIMIT :limit OFFSET :offset
            """)
        return await self._fetch_models(
            Forecast,
            sql,
            tenant_id,
            {
                "tenant": tenant_id,
                "metric_key": metric_key,
                "limit": _clamp(limit),
                "offset": max(0, int(offset)),
            },
        )

    async def _fetch_models(self, model, sql, tenant_id: str, params: dict) -> list:
        """Run ``sql`` and validate each ``payload`` jsonb column into ``model``."""

        async with self._sessionmaker() as session:
            await _set_tenant(session, tenant_id)
            result = await session.execute(sql, params)
            return [model.model_validate(_as_dict(row[0])) for row in result.all()]


async def _set_tenant(session: "AsyncSession", tenant_id: str) -> None:
    """Bind ``app.tenant_id`` for the read (no-op until RLS FORCE; never raises)."""

    try:
        from edis_platform.db.session import set_tenant

        await set_tenant(session, tenant_id)
    except Exception:  # noqa: BLE001 - tenant binding is defense-in-depth, not the gate
        pass


def _as_dict(payload) -> dict:
    import json

    return payload if isinstance(payload, dict) else json.loads(payload)


def _as_dimensions(value) -> dict[str, str]:
    import json

    if value is None:
        return {}
    data = value if isinstance(value, dict) else json.loads(value)
    return {str(k): str(v) for k, v in data.items()}


def make_repo(settings, sessionmaker) -> GatewayRepo:
    """Select the repo: in-memory fake unless a real database is configured.

    Mirrors the L3 app factory: the bare app (default localhost URL or no
    sessionmaker) gets the deterministic in-memory fake so it boots with no
    Postgres; a configured database selects the SQLAlchemy reader.
    """

    if sessionmaker is None:
        return InMemoryGatewayRepo()
    return SqlAlchemyGatewayRepo(sessionmaker)
