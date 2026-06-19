"""Engine / sessionmaker accessors for the integration (L2) persistence layer.

A thin, import-safe wrapper over :mod:`edis_platform.db.session`: it reuses the
platform's lazily-built async engine + sessionmaker (no connection is opened at
import, so the service imports cleanly in CI with no Postgres). A dedicated
accessor here gives the repositories one import surface and a single place to
build an isolated sessionmaker for ``@pytest.mark.integration`` tests against a
testcontainer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_platform.db.session import (
    get_sessionmaker as _platform_get_sessionmaker,
    make_engine,
    make_sessionmaker,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from edis_platform.settings import Settings


def get_sessionmaker() -> "async_sessionmaker[AsyncSession]":
    """Return the process-wide async sessionmaker (lazily built; no connection)."""

    return _platform_get_sessionmaker()


def build_sessionmaker(settings: "Settings") -> "async_sessionmaker[AsyncSession]":
    """Build a fresh, isolated sessionmaker from ``settings`` (test/integration hook).

    Unlike :func:`get_sessionmaker` this does not touch the process-global
    singleton, so an integration test can point it at a throwaway testcontainer
    DSN without disturbing the rest of the process.
    """

    return make_sessionmaker(make_engine(settings))


__all__ = ["get_sessionmaker", "build_sessionmaker"]
