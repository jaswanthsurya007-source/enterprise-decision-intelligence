"""Async database access: shared declarative base, engine, session dependency."""

from __future__ import annotations

from edis_platform.db.session import (
    Base,
    get_engine,
    get_session,
    get_sessionmaker,
    make_engine,
    make_sessionmaker,
    set_tenant,
)

__all__ = [
    "Base",
    "make_engine",
    "make_sessionmaker",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "set_tenant",
]
