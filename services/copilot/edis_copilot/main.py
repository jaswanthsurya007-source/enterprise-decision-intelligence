"""FastAPI application factory for the L5 Copilot service.

:func:`create_app` builds an app that is importable and runnable with NO infrastructure
and NO API keys: it configures JSON logging, installs the RFC 9457 error handlers, wires
the default in-memory tool registry + answer repository + budget + (key-guarded) LLM
client onto ``app.state``, mounts the P2 chat/conversations API, and exposes a liveness
probe plus the read-only tool-schema introspection route (the frozen Anthropic ``tools``
array — the exact list the agent loop sends).

The factory never opens a DB/broker connection or builds an SDK client at import time;
the key-guarded LLM client returns ``None`` with no key (the loop then runs the offline
deterministic agent). A deployment with infra/keys swaps the in-memory wiring for the
SQLAlchemy DataPort + voyage-3 embedder + a real :class:`AuditEmitter` on ``app.state``
using the same ``Wiring`` shape (see :mod:`edis_copilot.deps`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI


def create_app(*, wiring=None) -> "FastAPI":
    """Create and configure the copilot FastAPI app.

    ``wiring`` is an optional dict (the :func:`edis_copilot.deps.build_default_wiring` shape: keys
    ``registry``, ``data_port``, ``embedder``, ``answers``, ``budget``, ``limits``,
    ``llm``, ``audit``) — e.g. the SQLAlchemy port for a real deployment, or a pre-seeded
    in-memory port for a test. When omitted, the default in-memory wiring is built — no
    infra, no keys.
    """

    from fastapi import Depends, FastAPI

    from edis_platform.errors import install_exception_handlers
    from edis_platform.logging import configure_logging

    from edis_copilot import __version__
    from edis_copilot.api.chat import router as chat_router
    from edis_copilot.api.conversations import router as conversations_router
    from edis_copilot.deps import build_default_wiring, get_principal, get_registry
    from edis_copilot.settings import get_copilot_settings, get_settings

    platform_settings = get_settings()
    copilot_settings = get_copilot_settings()
    configure_logging(copilot_settings.service_name, level=platform_settings.log_level)

    app = FastAPI(
        title="EDIS Copilot",
        version=__version__,
        summary="L5 grounded, tool-using AI copilot — manual Opus loop + grounding + SSE chat.",
    )
    install_exception_handlers(app)

    w = wiring or build_default_wiring(platform_settings)
    app.state.registry = w["registry"]
    app.state.data_port = w["data_port"]
    app.state.embedder = w["embedder"]
    app.state.answers = w.get("answers")
    app.state.budget = w.get("budget")
    app.state.limits = w.get("limits")
    app.state.llm = w.get("llm")
    app.state.audit = w.get("audit")
    app.state.copilot_settings = copilot_settings

    app.include_router(chat_router)
    app.include_router(conversations_router)

    @app.get("/v1/health", summary="Liveness probe", tags=["copilot"])
    async def health() -> dict[str, str]:
        """Always-ok liveness signal (unauthenticated; probes carry no JWT)."""

        return {"status": "ok", "service": copilot_settings.service_name}

    @app.get("/v1/tools", summary="Frozen Anthropic tool schemas", tags=["copilot"])
    async def tools(registry=Depends(get_registry)) -> dict[str, object]:
        """Return the FROZEN, deterministic Anthropic ``tools`` array + names.

        This is the exact ordered tool list the agent loop sends to Opus; exposing it
        lets the dashboard / contract tests inspect the cache-prefix shape. Requires no
        auth (it is static schema, not tenant data).
        """

        return {"order": registry.names, "tools": registry.anthropic_tools()}

    @app.get("/v1/whoami", summary="Resolved principal (tenant from JWT/gateway)", tags=["copilot"])
    async def whoami(principal=Depends(get_principal)) -> dict[str, object]:
        """Echo the verified principal so callers can confirm tenant scoping.

        The tenant here is exactly what the tools inject server-side — proving the
        copilot's tenant comes only from the verified token / gateway header.
        """

        return {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "roles": list(principal.roles),
        }

    return app
