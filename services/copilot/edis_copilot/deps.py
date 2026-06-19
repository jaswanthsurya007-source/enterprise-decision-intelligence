"""FastAPI dependencies + dependency wiring for the copilot service.

Everything the request path needs is resolved off ``app.state`` (the factory stashes it)
or the verified principal:

* :func:`get_registry` — the frozen :class:`~app.tools.ToolRegistry`.
* :func:`get_llm` — the lazy, key-guarded ``AsyncAnthropic`` client or ``None`` (offline).
* :func:`get_budget` — the per-tenant :class:`~app.budget.accounting.BudgetAccountant`.
* :func:`get_loop_limits` — the :class:`~app.agent.limits.LoopLimits` caps.
* :func:`get_answer_repository` — the :class:`CopilotAnswerRepository` (or in-memory fake).
* :func:`get_audit_emitter` — the :class:`~edis_gov_sdk.audit.AuditEmitter` (or ``None``).
* :func:`get_principal` — the VERIFIED principal. The gateway injects the verified
  identity as trusted server-side headers (``X-EDIS-Tenant`` / ``X-EDIS-User`` /
  ``X-EDIS-Roles``); when present those are authoritative (the gateway already validated
  the JWT and enforced RBAC at the edge). When absent, fall back to validating an
  ``Authorization: Bearer`` JWT directly (non-gateway callers / tests). Tenant comes ONLY
  from the verified principal — never the request body or model output.

Building the default (in-memory) wiring needs no infra and no keys; FastAPI/Starlette are
imported lazily so importing this module needs no running web app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_contracts.security import SecurityContext
from edis_platform.authz.deps import get_security_context
from edis_platform.errors import NotFoundError
from starlette.requests import Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.agent.limits import LoopLimits
    from edis_copilot.budget.accounting import BudgetAccountant
    from edis_copilot.tools.registry import ToolRegistry


def build_default_wiring(settings=None):
    """Build the in-memory tool registry + searcher with no infra and no keys.

    Returns a ``Wiring`` dict with the registry, data port, embedder, answer repository,
    budget accountant, loop limits, the (key-guarded, possibly None) LLM client, and the
    (possibly None) audit emitter. Uses the :class:`InMemoryDataPort` /
    :class:`InMemoryAnswerRepository` fakes and the stub embedder, so the service imports
    and runs offline; a deployment with a DB/keys swaps in the SQLAlchemy port +
    voyage-3 embedder + a real audit sink via the same shapes.
    """

    from edis_copilot.agent.limits import LoopLimits
    from edis_copilot.budget.accounting import BudgetAccountant
    from edis_copilot.llm.client import make_anthropic_client
    from edis_copilot.persistence.repository import make_answer_repository
    from edis_copilot.retrieval.embedder import make_embedder
    from edis_copilot.retrieval.search import HybridSearcher
    from edis_copilot.settings import get_copilot_settings, get_settings
    from edis_copilot.tools.base import InMemoryDataPort
    from edis_copilot.tools.registry import default_registry

    platform_settings = settings or get_settings()
    copilot_settings = get_copilot_settings()

    data = InMemoryDataPort()
    embedder = make_embedder(platform_settings, dim=copilot_settings.embedding_dim)
    searcher = HybridSearcher(data, embedder)
    registry = default_registry(
        data=data,
        searcher=searcher,
        max_tool_rows=copilot_settings.max_tool_rows,
        semantic_k=copilot_settings.semantic_search_k,
    )
    return {
        "registry": registry,
        "data_port": data,
        "embedder": embedder,
        "answers": make_answer_repository(None),
        "budget": BudgetAccountant(cap_usd=copilot_settings.daily_cost_cap_usd),
        "limits": LoopLimits(
            max_iterations=copilot_settings.max_iterations,
            max_output_tokens=copilot_settings.max_output_tokens,
            tool_timeout_s=copilot_settings.tool_timeout_s,
            daily_cost_cap_usd=copilot_settings.daily_cost_cap_usd,
        ),
        "llm": make_anthropic_client(platform_settings),
        "audit": None,  # the bare app has no bus sink; a deployment wires an AuditEmitter
    }


def get_registry(request: Request) -> "ToolRegistry":
    """Return the tool registry from ``app.state`` (404 if unwired)."""

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise NotFoundError("Copilot tool registry is not configured.")
    return registry


def get_llm(request: Request):
    """Return the LLM client from ``app.state`` (``None`` when offline)."""

    return getattr(request.app.state, "llm", None)


def get_budget(request: Request) -> "BudgetAccountant":
    """Return the per-tenant budget accountant from ``app.state``."""

    from edis_copilot.budget.accounting import BudgetAccountant

    budget = getattr(request.app.state, "budget", None)
    return budget if budget is not None else BudgetAccountant(cap_usd=0.0)


def get_loop_limits(request: Request) -> "LoopLimits":
    """Return the loop caps from ``app.state``."""

    from edis_copilot.agent.limits import LoopLimits

    limits = getattr(request.app.state, "limits", None)
    return limits if limits is not None else LoopLimits()


def get_answer_repository(request: Request):
    """Return the copilot answer repository from ``app.state`` (may be ``None``)."""

    return getattr(request.app.state, "answers", None)


def get_audit_emitter(request: Request):
    """Return the audit emitter from ``app.state`` (``None`` when no bus is wired)."""

    return getattr(request.app.state, "audit", None)


async def get_principal(request: Request) -> SecurityContext:
    """Resolve the verified principal: gateway-injected headers first, else a bearer JWT.

    The gateway validates the JWT and enforces RBAC at the edge, then forwards the
    verified identity as ``X-EDIS-Tenant`` / ``X-EDIS-User`` / ``X-EDIS-Roles``. When
    those are present they are authoritative. Otherwise a direct ``Authorization: Bearer``
    JWT is validated by the platform dep. Tenant always comes from the verified principal.
    """

    tenant = request.headers.get("X-EDIS-Tenant")
    if tenant:
        roles_hdr = request.headers.get("X-EDIS-Roles", "")
        roles = [r.strip() for r in roles_hdr.split(",") if r.strip()]
        return SecurityContext(
            tenant_id=tenant,
            user_id=request.headers.get("X-EDIS-User", "copilot"),
            roles=roles or ["analyst"],
        )
    return await get_security_context(request)
