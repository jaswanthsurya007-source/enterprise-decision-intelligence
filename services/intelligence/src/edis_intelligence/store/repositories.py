"""Async repositories that persist + read Finding / EvidenceBundle / Forecast.

The :class:`IntelligenceRepo` *port* (a ``Protocol``) is what the pipeline and the
read API depend on. Two implementations:

* :class:`InMemoryIntelligenceRepo` — the infra-free fake the unit suite and the
  read-API tests run against (no DB, deterministic, tenant-scoped).
* :class:`SqlAlchemyIntelligenceRepo` — the real async-SQLAlchemy implementation
  exercised under ``@pytest.mark.integration`` against a Postgres testcontainer.

Persistence ordering matters: the EvidenceBundle is written **before** the Finding so
``findings.evidence_ref`` can FK to ``evidence_bundle.bundle_id``. Upserts are keyed
on the primary id (``INSERT ... ON CONFLICT DO UPDATE``) so a replayed analysis is
idempotent. The pgvector ``embedding`` column is written via explicit SQL with a
JSON-encoded vector literal — pgvector parses ``[..]`` as a vector, and a plain
Postgres stores it as jsonb — so the repo never imports the optional ``pgvector``
package and works on both.

Reads are always tenant-scoped and paginated (newest-first by ``created_at`` /
``generated_at``), matching how the gateway/copilot query findings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from edis_contracts.findings import EvidenceBundle, Finding, Forecast

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StoredFinding:
    """A persisted finding plus its read-side embedding provenance.

    The :class:`~edis_contracts.findings.Finding` is the contract payload; ``embedding``
    / ``embedding_model`` are L3-owned columns the contract doesn't carry but the
    copilot retrieves over.
    """

    finding: Finding
    embedding: list[float] | None = None
    embedding_model: str | None = None


@dataclass
class StoredForecast:
    """A persisted forecast (thin wrapper for symmetry / future read-side fields)."""

    forecast: Forecast


class IntelligenceRepo(Protocol):
    """Port: persist + read findings, evidence bundles, and forecasts."""

    async def save_finding(
        self,
        finding: Finding,
        bundle: EvidenceBundle | None = None,
        *,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None: ...

    async def save_forecast(self, forecast: Forecast) -> None: ...

    async def get_finding(self, tenant_id: str, finding_id: UUID) -> Finding | None: ...

    async def list_findings(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]: ...

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]: ...

    async def get_evidence_bundle(
        self, tenant_id: str, bundle_id: UUID
    ) -> EvidenceBundle | None: ...


# ---------------------------------------------------------------------------
# In-memory fake
# ---------------------------------------------------------------------------
class InMemoryIntelligenceRepo:
    """Infra-free :class:`IntelligenceRepo` for unit tests + the bare app.

    Stores deep copies (model round-trips) so callers can't mutate persisted state.
    Tenant-scoped and deterministic; lists are newest-first and paginated.
    """

    def __init__(self) -> None:
        self._findings: dict[UUID, StoredFinding] = {}
        self._forecasts: dict[UUID, Forecast] = {}
        self._bundles: dict[UUID, EvidenceBundle] = {}

    async def save_finding(
        self,
        finding: Finding,
        bundle: EvidenceBundle | None = None,
        *,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        if bundle is not None:
            self._bundles[bundle.bundle_id] = bundle.model_copy(deep=True)
        self._findings[finding.finding_id] = StoredFinding(
            finding=finding.model_copy(deep=True),
            embedding=list(embedding) if embedding is not None else None,
            embedding_model=embedding_model,
        )

    async def save_forecast(self, forecast: Forecast) -> None:
        self._forecasts[forecast.forecast_id] = forecast.model_copy(deep=True)

    async def get_finding(self, tenant_id: str, finding_id: UUID) -> Finding | None:
        stored = self._findings.get(finding_id)
        if stored is None or stored.finding.tenant_id != tenant_id:
            return None
        return stored.finding.model_copy(deep=True)

    async def list_findings(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]:
        rows = [
            s.finding
            for s in self._findings.values()
            if s.finding.tenant_id == tenant_id
            and (status is None or s.finding.status == status)
            and (metric_key is None or s.finding.metric_key == metric_key)
        ]
        rows.sort(key=lambda f: (f.created_at, str(f.finding_id)), reverse=True)
        return [f.model_copy(deep=True) for f in rows[offset : offset + limit]]

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]:
        rows = [
            f
            for f in self._forecasts.values()
            if f.tenant_id == tenant_id and (metric_key is None or f.metric_key == metric_key)
        ]
        rows.sort(key=lambda f: (f.generated_at, str(f.forecast_id)), reverse=True)
        return [f.model_copy(deep=True) for f in rows[offset : offset + limit]]

    async def get_evidence_bundle(self, tenant_id: str, bundle_id: UUID) -> EvidenceBundle | None:
        b = self._bundles.get(bundle_id)
        if b is None or b.tenant_id != tenant_id:
            return None
        return b.model_copy(deep=True)

    # Test convenience (not part of the port).
    def stored_finding(self, finding_id: UUID) -> StoredFinding | None:
        return self._findings.get(finding_id)


# ---------------------------------------------------------------------------
# SQLAlchemy implementation
# ---------------------------------------------------------------------------
class SqlAlchemyIntelligenceRepo:
    """Real async-SQLAlchemy :class:`IntelligenceRepo` (integration-tested)."""

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def save_finding(
        self,
        finding: Finding,
        bundle: EvidenceBundle | None = None,
        *,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        from sqlalchemy import text as sql_text
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from edis_intelligence.store.models import EvidenceBundleRow, FindingRow

        async with self._sessionmaker() as session:
            async with session.begin():
                # Evidence bundle FIRST (findings.evidence_ref FKs it).
                if bundle is not None:
                    b_vals = {
                        "bundle_id": bundle.bundle_id,
                        "tenant_id": bundle.tenant_id,
                        "finding_id": bundle.finding_id,
                        "created_at": bundle.created_at,
                        "items": [i.model_dump(mode="json") for i in bundle.items],
                        "allowed_numbers": list(bundle.allowed_numbers),
                        "schema_version": bundle.schema_version,
                    }
                    b_stmt = pg_insert(EvidenceBundleRow).values(**b_vals)
                    b_stmt = b_stmt.on_conflict_do_update(
                        index_elements=[EvidenceBundleRow.bundle_id],
                        set_={
                            k: b_stmt.excluded[k]
                            for k in ("items", "allowed_numbers", "created_at")
                        },
                    )
                    await session.execute(b_stmt)

                f_vals = {
                    "finding_id": finding.finding_id,
                    "tenant_id": finding.tenant_id,
                    "kind": finding.kind.value,
                    "metric_key": finding.metric_key,
                    "dimensions": dict(finding.dimensions),
                    "window_start": finding.window_start,
                    "window_end": finding.window_end,
                    "detector": finding.detector,
                    "detector_version": finding.detector_version,
                    "observed_value": finding.observed_value,
                    "expected_value": finding.expected_value,
                    "deviation": finding.deviation,
                    "deviation_pct": finding.deviation_pct,
                    "score": finding.score,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "business_impact_input": finding.business_impact_input,
                    "candidate_causes": [
                        c.model_dump(mode="json") for c in finding.candidate_causes
                    ],
                    "narrative": finding.narrative,
                    "narrative_model": finding.narrative_model,
                    "evidence_ref": finding.evidence_ref,
                    "status": finding.status,
                    "created_at": finding.created_at,
                    "schema_version": finding.schema_version,
                }
                f_stmt = pg_insert(FindingRow).values(**f_vals)
                f_update = {
                    k: f_stmt.excluded[k]
                    for k in (
                        "narrative",
                        "narrative_model",
                        "evidence_ref",
                        "status",
                        "severity",
                        "confidence",
                        "business_impact_input",
                        "candidate_causes",
                    )
                }
                f_stmt = f_stmt.on_conflict_do_update(
                    index_elements=[FindingRow.finding_id], set_=f_update
                )
                await session.execute(f_stmt)

                # Embedding via explicit SQL: JSON list literal is valid both as a
                # pgvector vector and as jsonb, so this works with or without pgvector.
                if embedding is not None:
                    await session.execute(
                        sql_text(
                            "UPDATE findings SET embedding = CAST(:emb AS text)::"
                            "{coltype} WHERE finding_id = :fid".format(
                                coltype=await self._embedding_coltype(session)
                            )
                        ),
                        {"emb": json.dumps(embedding), "fid": str(finding.finding_id)},
                    )

    @staticmethod
    async def _embedding_coltype(session: "AsyncSession") -> str:
        """Detect whether the ``findings.embedding`` column is vector or jsonb."""

        from sqlalchemy import text as sql_text

        row = (
            await session.execute(
                sql_text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'findings' AND column_name = 'embedding'"
                )
            )
        ).first()
        udt = (row[0] if row else "") or ""
        return "vector" if udt == "vector" else "jsonb"

    async def save_forecast(self, forecast: Forecast) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from edis_intelligence.store.models import ForecastRow

        async with self._sessionmaker() as session:
            async with session.begin():
                vals = {
                    "forecast_id": forecast.forecast_id,
                    "tenant_id": forecast.tenant_id,
                    "metric_key": forecast.metric_key,
                    "dimensions": dict(forecast.dimensions),
                    "model": forecast.model,
                    "horizon_days": forecast.horizon_days,
                    "points": list(forecast.points),
                    "generated_at": forecast.generated_at,
                    "schema_version": forecast.schema_version,
                }
                stmt = pg_insert(ForecastRow).values(**vals)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ForecastRow.forecast_id],
                    set_={k: stmt.excluded[k] for k in ("points", "generated_at", "model")},
                )
                await session.execute(stmt)

    async def get_finding(self, tenant_id: str, finding_id: UUID) -> Finding | None:
        from sqlalchemy import select

        from edis_intelligence.store.models import FindingRow

        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(FindingRow).where(
                        FindingRow.finding_id == finding_id,
                        FindingRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            return _finding_from_row(row) if row is not None else None

    async def list_findings(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        metric_key: str | None = None,
    ) -> list[Finding]:
        from sqlalchemy import select

        from edis_intelligence.store.models import FindingRow

        stmt = select(FindingRow).where(FindingRow.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(FindingRow.status == status)
        if metric_key is not None:
            stmt = stmt.where(FindingRow.metric_key == metric_key)
        stmt = (
            stmt.order_by(FindingRow.created_at.desc(), FindingRow.finding_id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_finding_from_row(r) for r in rows]

    async def list_forecasts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        metric_key: str | None = None,
    ) -> list[Forecast]:
        from sqlalchemy import select

        from edis_intelligence.store.models import ForecastRow

        stmt = select(ForecastRow).where(ForecastRow.tenant_id == tenant_id)
        if metric_key is not None:
            stmt = stmt.where(ForecastRow.metric_key == metric_key)
        stmt = (
            stmt.order_by(ForecastRow.generated_at.desc(), ForecastRow.forecast_id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_forecast_from_row(r) for r in rows]

    async def get_evidence_bundle(self, tenant_id: str, bundle_id: UUID) -> EvidenceBundle | None:
        from sqlalchemy import select

        from edis_intelligence.store.models import EvidenceBundleRow

        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(EvidenceBundleRow).where(
                        EvidenceBundleRow.bundle_id == bundle_id,
                        EvidenceBundleRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return EvidenceBundle.model_validate(
                {
                    "bundle_id": row.bundle_id,
                    "tenant_id": row.tenant_id,
                    "finding_id": row.finding_id,
                    "created_at": row.created_at,
                    "items": row.items,
                    "allowed_numbers": row.allowed_numbers,
                    "schema_version": row.schema_version,
                }
            )


def _finding_from_row(row) -> Finding:
    return Finding.model_validate(
        {
            "finding_id": row.finding_id,
            "tenant_id": row.tenant_id,
            "kind": row.kind,
            "metric_key": row.metric_key,
            "dimensions": row.dimensions,
            "window_start": row.window_start,
            "window_end": row.window_end,
            "detector": row.detector,
            "detector_version": row.detector_version,
            "observed_value": row.observed_value,
            "expected_value": row.expected_value,
            "deviation": row.deviation,
            "deviation_pct": row.deviation_pct,
            "score": row.score,
            "severity": row.severity,
            "confidence": row.confidence,
            "business_impact_input": row.business_impact_input,
            "candidate_causes": row.candidate_causes,
            "narrative": row.narrative,
            "narrative_model": row.narrative_model,
            "evidence_ref": row.evidence_ref,
            "status": row.status,
            "created_at": row.created_at,
            "schema_version": row.schema_version,
        }
    )


def _forecast_from_row(row) -> Forecast:
    return Forecast.model_validate(
        {
            "forecast_id": row.forecast_id,
            "tenant_id": row.tenant_id,
            "metric_key": row.metric_key,
            "dimensions": row.dimensions,
            "model": row.model,
            "horizon_days": row.horizon_days,
            "points": row.points,
            "generated_at": row.generated_at,
            "schema_version": row.schema_version,
        }
    )


def make_repo(settings, sessionmaker: "async_sessionmaker[AsyncSession] | None" = None):
    """Select a repo: in-memory when no DB is wired, else the SQLAlchemy repo.

    Mirrors the L2 ``make_repo`` selector. The pipeline/app pass a sessionmaker built
    from ``edis_platform.db.session`` when a database is configured; with none (CI /
    the bare app) the in-memory fake keeps the whole chain runnable.
    """

    if sessionmaker is None:
        return InMemoryIntelligenceRepo()
    return SqlAlchemyIntelligenceRepo(sessionmaker)
