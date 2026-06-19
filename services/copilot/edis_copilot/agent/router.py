"""Question router — Haiku structured-output routing with a rule-based offline fallback.

Per the verified agentic-loop rules, routing uses ``claude-haiku-4-5`` via
``client.messages.parse(model="claude-haiku-4-5", max_tokens=512, messages=[...],
output_format=RouteModel)`` — Haiku does NOT accept the ``effort`` parameter, so the
router call carries no ``effort`` / ``thinking`` / sampling params. The parse result is a
:class:`RouteModel`: ``{intent, time_range, scope_ok}``.

With NO key (or on any error), :func:`rule_route` provides a deterministic, dependency-
free classifier over the question text so the whole copilot routes offline. The router
never raises into the turn — a failed LLM route degrades to the rule route.

The route is advisory: it shapes the prompt / the offline plan but never bypasses
grounding (every number still traces to a tool result this turn) and never carries a
tenant (tenant comes only from the verified principal, server-side).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from edis_platform.logging import get_logger

from edis_copilot.llm.models import MODEL_HAIKU

_log = get_logger(__name__)

Intent = Literal["rca", "metric", "recommendation", "anomaly", "general"]

#: Coarse named time windows the router may emit; resolved to concrete UTC bounds by
#: :func:`resolve_time_range` for the offline planner.
TimeRange = Literal["last_24h", "last_week", "last_month", "last_quarter", "all"]


def _route_model_cls() -> type:
    """Build the pydantic ``RouteModel`` lazily (pydantic is a declared dep, import-safe).

    Defined as a function so the module imports even in the (hypothetical) absence of
    pydantic; the SDK ``messages.parse`` path needs the class only when a key is present.
    """

    from pydantic import BaseModel, Field

    class RouteModel(BaseModel):
        """Structured routing output Haiku fills via ``messages.parse``."""

        intent: Intent = Field(description="The kind of question being asked.")
        time_range: TimeRange = Field(
            default="last_week", description="The time window the question concerns."
        )
        scope_ok: bool = Field(
            default=True,
            description="False only if the question is clearly outside this tenant's "
            "business analytics scope (e.g. asks to take an action, change config, or "
            "access another tenant).",
        )

    return RouteModel


# Build once at import (pydantic is always available in this service).
RouteModel: type = _route_model_cls()


@dataclass(frozen=True)
class Route:
    """The resolved route: intent, named window, scope flag, and how it was produced."""

    intent: Intent
    time_range: TimeRange
    scope_ok: bool
    source: str  # "haiku" | "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "time_range": self.time_range,
            "scope_ok": self.scope_ok,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Deterministic rule-based router (offline; also the LLM fallback)
# ---------------------------------------------------------------------------
_RCA_TERMS = ("why", "cause", "root cause", "reason", "explain", "because", "driver")
_REC_TERMS = ("what should", "recommend", "should we", "action", "do about", "fix", "mitigate")
_ANOMALY_TERMS = ("anomal", "spike", "unusual", "went wrong", "alert", "outage", "incident")
_METRIC_TERMS = (
    "how much",
    "total",
    "trend",
    "revenue",
    "orders",
    "error rate",
    "latency",
    "how many",
)

_OUT_OF_SCOPE = (
    "delete",
    "drop table",
    "update ",
    "insert ",
    "shut down",
    "restart",
    "other tenant",
    "all tenants",
    "change config",
    "send email",
    "execute",
)

_TIME_TERMS: tuple[tuple[tuple[str, ...], TimeRange], ...] = (
    (("last 24", "yesterday", "today", "past day", "24 hour"), "last_24h"),
    (("last week", "past week", "this week", "wow", "week over week"), "last_week"),
    (("last month", "past month", "this month"), "last_month"),
    (("last quarter", "past quarter", "this quarter", "qoq"), "last_quarter"),
)


def rule_route(question: str) -> Route:
    """Classify ``question`` deterministically (the offline router + LLM fallback).

    Pure: maps keyword presence to an intent + a named time window, and flags an
    obviously out-of-scope ask (an action request, a cross-tenant ask) as
    ``scope_ok=False``. Order of precedence: recommendation > rca > anomaly > metric >
    general (a "why should we…" reads as a recommendation ask).
    """

    q = question.lower()
    scope_ok = not any(term in q for term in _OUT_OF_SCOPE)

    if any(t in q for t in _REC_TERMS):
        intent: Intent = "recommendation"
    elif any(t in q for t in _RCA_TERMS):
        intent = "rca"
    elif any(t in q for t in _ANOMALY_TERMS):
        intent = "anomaly"
    elif any(t in q for t in _METRIC_TERMS):
        intent = "metric"
    else:
        intent = "general"

    time_range: TimeRange = "last_week"
    for terms, tr in _TIME_TERMS:
        if any(t in q for t in terms):
            time_range = tr
            break

    return Route(intent=intent, time_range=time_range, scope_ok=scope_ok, source="rules")


async def route_question(question: str, *, client: Any | None) -> Route:
    """Route ``question`` via Haiku ``messages.parse`` when a key is present, else rules.

    Never raises: any SDK / parse / API error degrades to :func:`rule_route`. The Haiku
    call uses the verified shape — model=claude-haiku-4-5, max_tokens=512,
    output_format=RouteModel, NO effort/thinking/sampling params.
    """

    if client is None:
        return rule_route(question)

    try:
        resp = await client.messages.parse(
            model=MODEL_HAIKU,
            max_tokens=512,
            messages=[{"role": "user", "content": _route_prompt(question)}],
            output_format=RouteModel,
        )
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            return rule_route(question)
        return Route(
            intent=parsed.intent,
            time_range=parsed.time_range,
            scope_ok=bool(parsed.scope_ok),
            source="haiku",
        )
    except Exception as exc:  # noqa: BLE001 - routing must never break a turn
        _log.warning(
            "haiku route failed; using rule-based router",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return rule_route(question)


def _route_prompt(question: str) -> str:
    """Build the Haiku routing user turn (classification only; no tenant, no data)."""

    return (
        "Classify this analytics question for routing. Decide the intent, the time "
        "window it concerns, and whether it is in scope (in scope = a read-only "
        "question about this tenant's business metrics/anomalies/recommendations; out "
        "of scope = a request to take an action, change configuration, or access "
        f"another tenant).\n\nQuestion: {question}"
    )


def resolve_time_range(
    tr: TimeRange, *, now: datetime | None = None
) -> tuple[datetime | None, datetime | None]:
    """Resolve a named window to concrete ``(start, end)`` UTC bounds (``all`` -> None,None).

    Used by the offline planner to scope its tool calls. ``now`` defaults to the current
    UTC time; pass it explicitly for deterministic tests.
    """

    end = now or datetime.now(timezone.utc)
    if tr == "all":
        return None, None
    spans = {
        "last_24h": timedelta(hours=24),
        "last_week": timedelta(days=7),
        "last_month": timedelta(days=31),
        "last_quarter": timedelta(days=92),
    }
    delta = spans.get(tr, timedelta(days=7))
    return end - delta, end
