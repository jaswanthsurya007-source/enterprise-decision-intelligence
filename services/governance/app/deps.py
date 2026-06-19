"""FastAPI dependencies for the governance service.

Wires the lazily-built DB session (from :mod:`edis_platform.db.session`) and the
governance repositories into request handlers, and re-exports the platform authz
gates the API uses (``get_security_context`` + ``require_role`` factories). The
process-wide :class:`EventSink` lives on ``app.state`` (started at lifespan) and
is reachable via :func:`get_sink` for handlers that need to emit governance
events themselves.

Nothing here opens a connection at import time -- the engine/sessionmaker are
created on first request by the platform's lazy singletons.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from edis_platform.authz.deps import (  # re-exported for the API modules
    get_security_context,
    require_role,
)
from edis_platform.db.session import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.repo import (
    AuditRepository,
    ExplainRepository,
    LineageRepository,
    RbacRepository,
)

__all__ = [
    "get_security_context",
    "require_role",
    "DbSession",
    "get_audit_repo",
    "get_lineage_repo",
    "get_explain_repo",
    "get_rbac_repo",
    "get_sink",
]


async def DbSession() -> AsyncIterator[AsyncSession]:  # noqa: N802 - dependency alias
    """Yield a tenant-agnostic async session (repositories scope by tenant_id)."""

    async for session in get_session():
        yield session


async def get_audit_repo() -> AsyncIterator[AuditRepository]:
    async for session in get_session():
        yield AuditRepository(session)


async def get_lineage_repo() -> AsyncIterator[LineageRepository]:
    async for session in get_session():
        yield LineageRepository(session)


async def get_explain_repo() -> AsyncIterator[ExplainRepository]:
    async for session in get_session():
        yield ExplainRepository(session)


async def get_rbac_repo() -> AsyncIterator[RbacRepository]:
    async for session in get_session():
        yield RbacRepository(session)


def get_sink(request: Request):
    """Return the process-wide :class:`EventSink` from ``app.state`` (or ``None``)."""

    return getattr(request.app.state, "sink", None)
