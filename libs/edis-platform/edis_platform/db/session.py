"""Async SQLAlchemy engine, sessionmaker, and the FastAPI session dependency.

Connections are strictly lazy: no engine is created at import time, so a service
imports cleanly in CI with no database. The engine/sessionmaker singletons are
built on first use from :func:`get_settings`. ``set_tenant`` issues
``SET LOCAL app.tenant_id`` -- a harmless no-op today (app-level tenant filtering
in the MVP) that becomes the binding for Postgres RLS ``FORCE`` once that is built.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from edis_platform.settings import Settings


class Base(DeclarativeBase):
    """Shared declarative base for every service's ORM models."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(settings: "Settings") -> AsyncEngine:
    """Create a new async engine from settings (no connection is opened yet)."""

    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an :class:`async_sessionmaker` bound to ``engine``."""

    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )


def get_engine() -> AsyncEngine:
    """Return the lazily-created process-wide engine singleton."""

    global _engine
    if _engine is None:
        from edis_platform.settings import get_settings

        _engine = make_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the lazily-created process-wide sessionmaker singleton."""

    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = make_sessionmaker(get_engine())
    return _sessionmaker


async def set_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Bind the tenant on the session via ``SET LOCAL app.tenant_id``.

    A harmless no-op before RLS exists; making it the seam means turning on RLS
    ``FORCE`` later requires no call-site changes. Bound parameters cannot be
    used with ``SET LOCAL``, so the value is quoted defensively.
    """

    safe = str(tenant_id).replace("'", "''")
    await session.execute(text(f"SET LOCAL app.tenant_id = '{safe}'"))


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, rolling back on error."""

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def reset_engine() -> None:
    """Drop the cached engine/sessionmaker (test hook; rarely needed)."""

    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
