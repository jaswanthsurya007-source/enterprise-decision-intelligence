"""SQLAlchemy read-only :class:`DataPort` over the canonical store + pgvector.

:class:`SqlAlchemyDataPort` is the real implementation of the read-only
:class:`~app.tools.base.DataPort`: it reads the L2 ``metric_observations`` hypertable
(metric series + safe parameterized aggregates), the L3 ``findings`` table
(find_anomalies), and runs pgvector similarity over the findings' ``embedding`` column
(semantic_search). It satisfies the same Protocol as the InMemory fake, so swapping it
in is transparent and the tool layer's unit tests stay infra-free.

EVERY query is tenant-scoped with a bound ``:tenant`` parameter and EVERY value the
model can influence is passed as a bound parameter — there is no string-formatted SQL
from tool arguments (the only formatted identifiers are the fixed, validated aggregate
function name and the allow-listed group-by dimension keys, never model free-text).
``embedding`` is queried via the pgvector ``<=>`` operator when the column is a
``vector``, degrading to a no-vector ordering on a plain Postgres (jsonb column) — the
same vector/jsonb-agnostic pattern the L3 repo uses on writes.

Reads are integration-tested (``@pytest.mark.integration``) against a Postgres +
pgvector + Timescale testcontainer; importing this module opens no connection.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from edis_copilot.tools.base import ToolError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from edis_copilot.agent.synthesis import CopilotAnswer

# Aggregates are mapped to SQL functions here (NOT taken from model free-text). The
# tool already validates ``agg`` against this same set; this map is the second gate.
_AGG_SQL = {
    "sum": "sum",
    "avg": "avg",
    "min": "min",
    "max": "max",
    "count": "count",
}
#: Dimension keys allowed in group_by / filters (mirrors StructuredQueryTool).
_ALLOWED_DIMS = ("region", "channel", "service")


class SqlAlchemyDataPort:
    """Real async-SQLAlchemy :class:`~app.tools.base.DataPort` (integration-tested)."""

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    # -- metric series / rollup --
    async def metric_series(
        self,
        tenant_id: str,
        metric_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        dimensions: dict[str, str] | None = None,
        rollup: str = "raw",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sql_text

        params: dict[str, Any] = {"tenant": tenant_id, "metric": metric_key, "lim": int(limit)}
        where = ["tenant_id = :tenant", "metric_key = :metric"]
        _apply_window(where, params, start, end)
        _apply_dim_filter(where, params, dimensions)
        clause = " AND ".join(where)

        if rollup in ("day", "daily"):
            sql = (
                "SELECT date_trunc('day', ts) AS ts, sum(value) AS value, "
                "min(unit) AS unit FROM metric_observations "
                f"WHERE {clause} GROUP BY date_trunc('day', ts) ORDER BY ts"
            )
        elif rollup in ("week", "weekly"):
            sql = (
                "SELECT date_trunc('week', ts) AS ts, sum(value) AS value, "
                "min(unit) AS unit FROM metric_observations "
                f"WHERE {clause} GROUP BY date_trunc('week', ts) ORDER BY ts"
            )
        else:
            sql = (
                "SELECT ts, value, unit, dimensions FROM metric_observations "
                f"WHERE {clause} ORDER BY ts"
            )

        async with self._sessionmaker() as session:
            rows = (await session.execute(sql_text(sql), params)).mappings().all()
        out = [
            {
                "metric_key": metric_key,
                "ts": _iso(r["ts"]),
                "value": float(r["value"]),
                "dimensions": _row_dims(r, dimensions),
                "unit": r.get("unit"),
            }
            for r in rows
        ]
        return out[-limit:] if limit and len(out) > limit else out

    # -- safe parameterized aggregate (no raw SQL from the model) --
    async def metric_aggregate(
        self,
        tenant_id: str,
        metric_key: str,
        *,
        agg: str = "sum",
        start: datetime | None = None,
        end: datetime | None = None,
        dimensions: dict[str, str] | None = None,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sql_text

        fn = _AGG_SQL.get(agg)
        if fn is None:
            raise ToolError(f"unsupported aggregate: {agg!r}")
        gb = _validated_group_by(group_by)

        params: dict[str, Any] = {"tenant": tenant_id, "metric": metric_key}
        where = ["tenant_id = :tenant", "metric_key = :metric"]
        _apply_window(where, params, start, end)
        _apply_dim_filter(where, params, dimensions)
        clause = " AND ".join(where)

        # group_by keys are from the fixed allow-list only (never model free-text),
        # so interpolating them as jsonb path keys is safe; values stay parameterized.
        select_groups = ", ".join(f"dimensions->>'{k}' AS {k}" for k in gb)
        group_clause = ", ".join(f"dimensions->>'{k}'" for k in gb)
        select_prefix = (select_groups + ", ") if select_groups else ""
        agg_expr = "count(*)" if fn == "count" else f"{fn}(value)"
        sql = (
            f"SELECT {select_prefix}{agg_expr} AS value, count(*) AS n "
            f"FROM metric_observations WHERE {clause}"
        )
        if group_clause:
            sql += f" GROUP BY {group_clause} ORDER BY {group_clause}"

        async with self._sessionmaker() as session:
            rows = (await session.execute(sql_text(sql), params)).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "group": {k: r.get(k) for k in gb},
                    "agg": agg,
                    "value": float(r["value"]) if r["value"] is not None else 0.0,
                    "count": int(r["n"]),
                }
            )
        return out

    # -- findings (find_anomalies) --
    async def findings_for_metric(
        self,
        tenant_id: str,
        *,
        metric_key: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sql_text

        params: dict[str, Any] = {"tenant": tenant_id, "lim": int(limit)}
        where = ["tenant_id = :tenant"]
        if metric_key is not None:
            where.append("metric_key = :metric")
            params["metric"] = metric_key
        if status is not None:
            where.append("status = :status")
            params["status"] = status
        if start is not None:
            where.append("window_end >= :start")
            params["start"] = start
        if end is not None:
            where.append("window_start <= :end")
            params["end"] = end
        clause = " AND ".join(where)
        sql = (
            "SELECT finding_id, tenant_id, kind, metric_key, dimensions, window_start, "
            "window_end, detector, detector_version, observed_value, expected_value, "
            "deviation, deviation_pct, score, severity, confidence, business_impact_input, "
            "candidate_causes, narrative, status, created_at "
            f"FROM findings WHERE {clause} ORDER BY created_at DESC LIMIT :lim"
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(sql_text(sql), params)).mappings().all()
        return [_finding_row(r) for r in rows]

    # -- pgvector semantic search over findings --
    async def vector_search(
        self,
        tenant_id: str,
        query_embedding: list[float],
        *,
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sql_text

        # MVP indexes findings; recommendations join is a P2/L4 extension. If the
        # caller restricts to only recommendations, there is nothing to return yet.
        if kinds is not None and "finding" not in kinds:
            return []

        async with self._sessionmaker() as session:
            coltype = await self._embedding_coltype(session)
            params: dict[str, Any] = {"tenant": tenant_id, "lim": int(limit)}
            if coltype == "vector":
                # pgvector cosine distance (<=>) ordering; lower distance = more similar.
                params["emb"] = json.dumps(query_embedding)
                sql = (
                    "SELECT finding_id, metric_key, dimensions, narrative, observed_value, "
                    "expected_value, deviation, deviation_pct, score, severity, confidence, "
                    "candidate_causes, (1 - (embedding <=> CAST(:emb AS vector))) AS score_sim "
                    "FROM findings WHERE tenant_id = :tenant AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:emb AS vector) LIMIT :lim"
                )
            else:
                # Plain Postgres (jsonb embedding): no vector op available; return
                # most-recent findings as a coarse fallback so retrieval still works.
                sql = (
                    "SELECT finding_id, metric_key, dimensions, narrative, observed_value, "
                    "expected_value, deviation, deviation_pct, score, severity, confidence, "
                    "candidate_causes, 0.0 AS score_sim "
                    "FROM findings WHERE tenant_id = :tenant "
                    "ORDER BY created_at DESC LIMIT :lim"
                )
            rows = (await session.execute(sql_text(sql), params)).mappings().all()

        out: list[dict[str, Any]] = []
        for r in rows:
            payload = _finding_row(r)
            out.append(
                {
                    "kind": "finding",
                    "id": str(r["finding_id"]),
                    "score": float(r.get("score_sim") or 0.0),
                    "text": r.get("narrative") or "",
                    "numbers": _finding_payload_numbers(payload),
                    "payload": payload,
                }
            )
        return out

    @staticmethod
    async def _embedding_coltype(session: "AsyncSession") -> str:
        """Detect whether ``findings.embedding`` is a pgvector ``vector`` or ``jsonb``."""

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


# ---------------------------------------------------------------------------
# Row helpers (pure)
# ---------------------------------------------------------------------------
def _apply_window(where: list[str], params: dict[str, Any], start, end) -> None:
    if start is not None:
        where.append("ts >= :start")
        params["start"] = start
    if end is not None:
        where.append("ts <= :end")
        params["end"] = end


def _apply_dim_filter(where: list[str], params: dict[str, Any], dimensions) -> None:
    """Append jsonb equality filters; keys allow-listed, values parameterized."""

    if not dimensions:
        return
    for i, (k, v) in enumerate(sorted(dimensions.items())):
        if k not in _ALLOWED_DIMS:
            raise ToolError(f"dimension key not allowed: {k!r}")
        pname = f"dim{i}"
        where.append(f"dimensions->>'{k}' = :{pname}")
        params[pname] = str(v)


def _validated_group_by(group_by: list[str] | None) -> list[str]:
    gb = group_by or []
    bad = [k for k in gb if k not in _ALLOWED_DIMS]
    if bad:
        raise ToolError(f"group_by keys must be one of {_ALLOWED_DIMS} (got {bad})")
    return gb


def _row_dims(r: Any, requested: dict[str, str] | None) -> dict[str, str]:
    dims = r.get("dimensions")
    if isinstance(dims, dict):
        return {str(k): str(v) for k, v in dims.items()}
    return dict(requested or {})


def _iso(v: Any) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _finding_row(r: Any) -> dict[str, Any]:
    """Normalize a findings row mapping to a plain JSON dict (Finding-shaped)."""

    def num(key: str):
        v = r.get(key)
        return float(v) if isinstance(v, (int, float)) else v

    causes = r.get("candidate_causes")
    if isinstance(causes, str):
        try:
            causes = json.loads(causes)
        except ValueError:
            causes = []
    dims = r.get("dimensions")
    if isinstance(dims, str):
        try:
            dims = json.loads(dims)
        except ValueError:
            dims = {}
    return {
        "finding_id": str(r.get("finding_id")) if r.get("finding_id") is not None else None,
        "tenant_id": r.get("tenant_id"),
        "kind": r.get("kind"),
        "metric_key": r.get("metric_key"),
        "dimensions": dims or {},
        "window_start": _iso(r["window_start"]) if r.get("window_start") is not None else None,
        "window_end": _iso(r["window_end"]) if r.get("window_end") is not None else None,
        "detector": r.get("detector"),
        "detector_version": r.get("detector_version"),
        "observed_value": num("observed_value"),
        "expected_value": num("expected_value"),
        "deviation": num("deviation"),
        "deviation_pct": num("deviation_pct"),
        "score": num("score"),
        "severity": num("severity"),
        "confidence": num("confidence"),
        "business_impact_input": num("business_impact_input"),
        "candidate_causes": causes or [],
        "narrative": r.get("narrative"),
        "status": r.get("status"),
        "created_at": _iso(r["created_at"]) if r.get("created_at") is not None else None,
    }


def _finding_payload_numbers(payload: dict[str, Any]) -> list[float]:
    """Gather computed numbers off a finding payload (for the grounding whitelist)."""

    out: list[float] = []
    for key in (
        "observed_value",
        "expected_value",
        "deviation",
        "deviation_pct",
        "score",
        "severity",
        "confidence",
        "business_impact_input",
    ):
        v = payload.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    for cause in payload.get("candidate_causes", []) or []:
        if not isinstance(cause, dict):
            continue
        for key in ("correlation", "lag_minutes", "contribution_pct", "observed_delta"):
            v = cause.get(key)
            if isinstance(v, (int, float)):
                out.append(float(v))
    return out


# ---------------------------------------------------------------------------
# CopilotAnswer store (the copilot's own conversation/answer history)
# ---------------------------------------------------------------------------
class CopilotAnswerRepository:
    """Tenant-scoped writer/reader for ``copilot_conversation`` + ``copilot_answer``.

    Persists each grounded turn (question, answer, citations, facts_used, tool_trace,
    grounding outcome) and lists a tenant's conversations. EVERY read filters by
    ``tenant_id`` (the verified principal's tenant); a write stamps it from the same
    principal — never a request body. Integration-tested against the testcontainer; the
    in-memory fake (:class:`InMemoryAnswerRepository`) keeps the API layer testable.
    """

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sessionmaker = sessionmaker

    async def save_answer(
        self,
        *,
        tenant_id: str,
        user_id: str,
        question: str,
        answer: "CopilotAnswer",
        conversation_id: UUID | None = None,
    ) -> UUID:
        """Persist one grounded answer; return its ``answer_id``. Tenant-stamped here."""

        from edis_copilot.persistence.models import CopilotAnswerRow

        answer_id = uuid4()
        row = CopilotAnswerRow(
            answer_id=answer_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            question=question,
            answer_text=answer.answer_text,
            answer_model=answer.answer_model,
            citations=answer.citations,
            facts_used=answer.facts_used,
            tool_trace=answer.tool_trace,
            grounding_passed=answer.grounding_passed,
            confidence=answer.confidence,
            created_at=datetime.now(timezone.utc),
        )
        async with self._sessionmaker() as session:
            session.add(row)
            await session.commit()
        return answer_id

    async def list_conversations(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """List a tenant's conversations (newest first). Tenant-scoped."""

        from sqlalchemy import text as sql_text

        sql = (
            "SELECT conversation_id, tenant_id, user_id, title, created_at, updated_at "
            "FROM copilot_conversation WHERE tenant_id = :tenant "
            "ORDER BY updated_at DESC LIMIT :lim"
        )
        async with self._sessionmaker() as session:
            rows = (
                (await session.execute(sql_text(sql), {"tenant": tenant_id, "lim": int(limit)}))
                .mappings()
                .all()
            )
        return [
            {
                "conversation_id": str(r["conversation_id"]),
                "user_id": r["user_id"],
                "title": r["title"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ]


class InMemoryAnswerRepository:
    """Infra-free :class:`CopilotAnswerRepository` stand-in for the bare app + unit tests.

    Stores answers/conversations in plain lists, tenant-scoped on read. Same interface as
    the SQLAlchemy repository so the API layer is testable with no DB.
    """

    def __init__(self) -> None:
        self._answers: list[dict[str, Any]] = []
        self._conversations: list[dict[str, Any]] = []

    async def save_answer(
        self,
        *,
        tenant_id: str,
        user_id: str,
        question: str,
        answer: "CopilotAnswer",
        conversation_id: UUID | None = None,
    ) -> UUID:
        answer_id = uuid4()
        self._answers.append(
            {
                "answer_id": str(answer_id),
                "tenant_id": tenant_id,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "user_id": user_id,
                "question": question,
                "answer_text": answer.answer_text,
                "answer_model": answer.answer_model,
                "citations": answer.citations,
                "facts_used": answer.facts_used,
                "tool_trace": answer.tool_trace,
                "grounding_passed": answer.grounding_passed,
                "confidence": answer.confidence,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return answer_id

    async def list_conversations(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = [c for c in self._conversations if c.get("tenant_id") == tenant_id]
        rows.sort(key=lambda c: str(c.get("updated_at", "")), reverse=True)
        return rows[:limit]

    # Test/demo helper (not part of the repository interface).
    def add_conversation(self, conversation: dict[str, Any]) -> None:
        self._conversations.append(dict(conversation))


def make_answer_repository(sessionmaker=None):
    """Select an answer repository: in-memory when no DB is wired, else SQLAlchemy."""

    if sessionmaker is None:
        return InMemoryAnswerRepository()
    return CopilotAnswerRepository(sessionmaker)


def make_data_port(sessionmaker=None):
    """Select a DataPort: in-memory fake when no DB is wired, else the SQLAlchemy port.

    Mirrors the L3 ``make_repo`` selector. With a sessionmaker (DB configured) the real
    read port is used; with none (CI / the bare app) the in-memory fake keeps the whole
    tool chain runnable and unit-testable.
    """

    if sessionmaker is None:
        from edis_copilot.tools.base import InMemoryDataPort

        return InMemoryDataPort()
    return SqlAlchemyDataPort(sessionmaker)
