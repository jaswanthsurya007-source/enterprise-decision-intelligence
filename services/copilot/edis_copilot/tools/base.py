"""Tool base, tool context, the DataPort seam, and an infra-free InMemory fake.

This is the load-bearing security + testability seam of the copilot data layer:

* :class:`ToolContext` carries the **server-side** :class:`SecurityContext` (derived
  from the verified JWT). Every tool reads ``ctx.tenant_id`` from here — never from a
  tool argument and never from LLM output. The tenant cannot be set or changed by the
  model. ``ctx`` is constructed by the (P2) agent loop from the request principal.
* :class:`Tool` is the abstract base every tool implements: a stable ``name``, a JSON
  ``input_schema`` (the Anthropic tool schema), and an async ``run(ctx, **kwargs)``.
  ``input_schema`` MUST NOT contain a ``tenant_id`` field — the registry asserts this,
  so the tenant can structurally never be a model-supplied argument.
* :class:`DataPort` is a ``Protocol`` describing every read the tools need from the
  canonical store / pgvector. :class:`InMemoryDataPort` is a deterministic, fully
  tenant-scoped fake implementing it, so the whole tool layer is unit-testable with
  NO database, NO broker, and NO API keys. The SQLAlchemy implementation lives in
  :mod:`edis_copilot.persistence.repository` and satisfies the same Protocol.

All tools are READ-ONLY: the DataPort exposes only reads. There is no write path, so
prompt injection in retrieved content can at most mislead a citation — never take an
action or cross a tenant boundary.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from edis_contracts.security import SecurityContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class ToolError(Exception):
    """A tool failed deterministically (bad args, unknown metric, etc.).

    Tools raise this for caller-correctable conditions; the (P2) dispatcher turns it
    into a tool_result with ``is_error=True`` so the model can adapt, rather than
    aborting the turn. It never carries cross-tenant data.
    """


# ---------------------------------------------------------------------------
# Tool context (tenant comes ONLY from here)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolContext:
    """Server-side execution context for a tool call.

    Built by the agent loop from the verified principal. ``tenant_id`` and ``roles``
    originate from the JWT-derived :class:`SecurityContext`; tools must read the tenant
    from :attr:`tenant_id` (never from their kwargs). ``trace_id`` is threaded for
    audit/observability correlation.
    """

    security: SecurityContext
    trace_id: str | None = None

    @property
    def tenant_id(self) -> str:
        """The tenant scope for every read this turn — from the verified token."""

        return self.security.tenant_id

    @classmethod
    def for_tenant(
        cls,
        tenant_id: str,
        *,
        user_id: str = "copilot",
        roles: list[str] | None = None,
        trace_id: str | None = None,
    ) -> "ToolContext":
        """Construct a context for ``tenant_id`` (test/helper convenience).

        Mirrors how the agent loop builds a context from the request principal, but
        lets unit tests create one without minting a JWT.
        """

        return cls(
            security=SecurityContext(
                tenant_id=tenant_id,
                user_id=user_id,
                roles=roles or ["analyst"],
            ),
            trace_id=trace_id,
        )


# ---------------------------------------------------------------------------
# Tool result (citable, packable)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolResult:
    """The structured result of one tool call.

    ``rows`` is the list of plain-JSON records the tool retrieved/computed this turn.
    ``numbers`` is the flat list of every numeric value the result contains — the
    grounding verifier (P2) treats this as the per-turn whitelist of figures the model
    may cite. ``citation`` is a short, deterministic provenance string the answer can
    reference (e.g. ``"tool metric_lookup: revenue weekly"``). All fields are derived
    from real retrieved data — never from the model.
    """

    tool: str
    rows: list[dict[str, Any]]
    numbers: list[float] = field(default_factory=list)
    citation: str = ""
    summary: str = ""

    def to_tool_content(self) -> dict[str, Any]:
        """Render as the JSON the agent loop feeds back as a ``tool_result`` body."""

        return {
            "tool": self.tool,
            "rows": self.rows,
            "summary": self.summary,
            "citation": self.citation,
        }


# ---------------------------------------------------------------------------
# Tool base
# ---------------------------------------------------------------------------
class Tool(abc.ABC):
    """Abstract read-only tool: a name, a JSON input schema, and async ``run``.

    Subclasses set :attr:`name`, :attr:`description`, and :attr:`input_schema` (the
    Anthropic tool schema — a JSON-Schema ``object``) and implement :meth:`run`. The
    schema MUST NOT declare a ``tenant_id`` property; the tenant is injected from
    :class:`ToolContext` server-side. The registry enforces this invariant.
    """

    #: Stable tool name (also the Anthropic tool ``name``); part of the frozen order.
    name: str = ""
    #: Human/model-facing description (prescriptive: states WHEN to call the tool).
    description: str = ""
    #: Anthropic tool ``input_schema`` — a JSON-Schema object. No ``tenant_id``.
    input_schema: dict[str, Any] = {}

    def anthropic_schema(self) -> dict[str, Any]:
        """Return this tool as one entry of the Anthropic ``tools`` array.

        Deterministic (no timestamps/ids) so the rendered tools list is byte-stable
        across requests and forms a cacheable prompt prefix.
        """

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abc.abstractmethod
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """Execute the read for ``ctx.tenant_id`` and return a :class:`ToolResult`.

        Implementations read the tenant from ``ctx`` only, validate/clamp their kwargs,
        and never mutate state. Raise :class:`ToolError` for caller-correctable issues.
        """


# ---------------------------------------------------------------------------
# DataPort — the read seam the tools depend on
# ---------------------------------------------------------------------------
@runtime_checkable
class DataPort(Protocol):
    """Read-only port the tools query, scoped to ``tenant_id`` by the caller.

    Every method takes ``tenant_id`` explicitly (the tool passes ``ctx.tenant_id``),
    and every implementation MUST filter by it. There is deliberately no write method.
    The :class:`InMemoryDataPort` fake and the SQLAlchemy repository both satisfy this.
    """

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
        """Return metric points (or daily rollups) for one series, newest-last."""
        ...

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
        """Return safe parameterized aggregates (no raw SQL) grouped by dimensions."""
        ...

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
        """Return findings for a metric/window (the find_anomalies read)."""
        ...

    async def vector_search(
        self,
        tenant_id: str,
        query_embedding: list[float],
        *,
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Hybrid pgvector search over findings/recommendations for one tenant."""
        ...


# ---------------------------------------------------------------------------
# InMemory fake (deterministic, tenant-scoped, no infra)
# ---------------------------------------------------------------------------
def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on degenerate input)."""

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _dims_match(row_dims: dict[str, str], want: dict[str, str] | None) -> bool:
    """True if ``row_dims`` contains every key/value in ``want`` (subset match)."""

    if not want:
        return True
    return all(row_dims.get(k) == v for k, v in want.items())


def _in_window(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


@dataclass
class _VectorDoc:
    """One indexed retrievable doc (finding or recommendation) for the fake."""

    tenant_id: str
    kind: str  # "finding" | "recommendation"
    doc_id: str
    embedding: list[float]
    payload: dict[str, Any]
    numbers: list[float] = field(default_factory=list)
    text: str = ""


class InMemoryDataPort:
    """Infra-free :class:`DataPort` for unit tests + the bare (no-DB) app.

    Stores metric points, findings, recommendations, and vector docs in plain lists,
    all keyed/filtered by ``tenant_id``. Deterministic: identical seeds yield identical
    reads. There is no write path exposed to tools — the ``add_*`` helpers are for test
    setup / the demo seeder only, never reachable from a tool.
    """

    def __init__(self) -> None:
        self._metrics: list[dict[str, Any]] = []
        self._findings: list[dict[str, Any]] = []
        self._docs: list[_VectorDoc] = []

    # -- seeding helpers (NOT part of DataPort; test/demo setup only) --
    def add_metric_point(
        self,
        tenant_id: str,
        metric_key: str,
        ts: datetime,
        value: float,
        *,
        dimensions: dict[str, str] | None = None,
        unit: str | None = None,
    ) -> None:
        self._metrics.append(
            {
                "tenant_id": tenant_id,
                "metric_key": metric_key,
                "ts": ts,
                "value": float(value),
                "dimensions": dict(dimensions or {}),
                "unit": unit,
            }
        )

    def add_finding(self, finding: dict[str, Any]) -> None:
        """Add a finding row (must carry ``tenant_id``); used for find_anomalies tests."""

        self._findings.append(dict(finding))

    def add_vector_doc(
        self,
        tenant_id: str,
        kind: str,
        doc_id: str,
        embedding: list[float],
        payload: dict[str, Any],
        *,
        numbers: list[float] | None = None,
        text: str = "",
    ) -> None:
        self._docs.append(
            _VectorDoc(
                tenant_id=tenant_id,
                kind=kind,
                doc_id=doc_id,
                embedding=list(embedding),
                payload=dict(payload),
                numbers=list(numbers or []),
                text=text,
            )
        )

    # -- DataPort reads (all tenant-scoped) --
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
        rows = [
            r
            for r in self._metrics
            if r["tenant_id"] == tenant_id
            and r["metric_key"] == metric_key
            and _dims_match(r["dimensions"], dimensions)
            and _in_window(r["ts"], start, end)
        ]
        rows.sort(key=lambda r: r["ts"])
        if rollup in ("day", "daily"):
            rows = self._roll_daily(rows)
        out = [
            {
                "metric_key": metric_key,
                "ts": r["ts"].isoformat() if isinstance(r["ts"], datetime) else r["ts"],
                "value": r["value"],
                "dimensions": r["dimensions"],
                "unit": r.get("unit"),
            }
            for r in rows
        ]
        return out[-limit:] if limit and len(out) > limit else out

    @staticmethod
    def _roll_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse points to one per (UTC day) with the daily sum (matches the L2 cagg)."""

        by_day: dict[str, dict[str, Any]] = {}
        for r in rows:
            ts: datetime = r["ts"]
            day = ts.date().isoformat()
            bucket = by_day.setdefault(
                day,
                {
                    "ts": ts.replace(hour=0, minute=0, second=0, microsecond=0),
                    "value": 0.0,
                    "dimensions": r["dimensions"],
                    "unit": r.get("unit"),
                },
            )
            bucket["value"] += r["value"]
        return [by_day[d] for d in sorted(by_day)]

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
        rows = [
            r
            for r in self._metrics
            if r["tenant_id"] == tenant_id
            and r["metric_key"] == metric_key
            and _dims_match(r["dimensions"], dimensions)
            and _in_window(r["ts"], start, end)
        ]
        groups: dict[tuple, list[float]] = {}
        gb = group_by or []
        for r in rows:
            key = tuple(r["dimensions"].get(g, "") for g in gb)
            groups.setdefault(key, []).append(r["value"])
        out: list[dict[str, Any]] = []
        for key, values in sorted(groups.items()):
            out.append(
                {
                    "group": dict(zip(gb, key)),
                    "agg": agg,
                    "value": _apply_agg(agg, values),
                    "count": len(values),
                }
            )
        return out

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
        rows = []
        for f in self._findings:
            if f.get("tenant_id") != tenant_id:
                continue
            if metric_key is not None and f.get("metric_key") != metric_key:
                continue
            if status is not None and f.get("status") != status:
                continue
            if start is not None or end is not None:
                ws = _parse_dt(f.get("window_end") or f.get("window_start"))
                if ws is not None and not _in_window(ws, start, end):
                    continue
            rows.append(dict(f))
        rows.sort(key=lambda f: str(f.get("created_at", "")), reverse=True)
        return rows[:limit]

    async def vector_search(
        self,
        tenant_id: str,
        query_embedding: list[float],
        *,
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        scored: list[tuple[float, _VectorDoc]] = []
        for d in self._docs:
            if d.tenant_id != tenant_id:
                continue
            if kinds is not None and d.kind not in kinds:
                continue
            scored.append((_cosine(query_embedding, d.embedding), d))
        scored.sort(key=lambda sd: sd[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, d in scored[:limit]:
            out.append(
                {
                    "kind": d.kind,
                    "id": d.doc_id,
                    "score": round(score, 6),
                    "text": d.text,
                    "numbers": list(d.numbers),
                    "payload": dict(d.payload),
                }
            )
        return out


def _apply_agg(agg: str, values: list[float]) -> float:
    if not values:
        return 0.0
    if agg == "sum":
        return sum(values)
    if agg == "avg":
        return sum(values) / len(values)
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    if agg == "count":
        return float(len(values))
    raise ToolError(f"unsupported aggregate: {agg!r}")


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
