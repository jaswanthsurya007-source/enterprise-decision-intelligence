"""Shared fixtures for the governance service tests.

Provides:

* ``settings`` -- a :class:`~edis_platform.settings.Settings` forced onto the
  in-process bus (``sink_backend="inproc"``) so audit/lineage round-trips run with
  no broker. Each test gets its own ``Settings`` instance, which (because the
  in-proc broker registry is keyed by ``id(settings)``) isolates that test's
  broker; ``reset_brokers()`` runs around every test as belt-and-suspenders.
* ``security_context_factory`` / ``security_context`` -- build a verified-looking
  :class:`~edis_contracts.security.SecurityContext` for any role set.
* ``dev_token_factory`` / ``dev_token`` -- mint a real HS256 dev JWT (validated by
  the same ``decode_token`` path the API uses) for the matching context.

Nothing here opens a DB or broker connection at collection time -- importing this
module (and the whole suite) is safe in CI with no infrastructure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from edis_contracts.security import SecurityContext
from edis_platform.authz.jwt import make_dev_token
from edis_platform.bus.inproc import reset_brokers
from edis_platform.settings import Settings

#: Default tenant used across the governance tests.
TENANT_ID = "tenant-a"


@pytest.fixture(autouse=True)
def _isolated_broker() -> Iterator[None]:
    """Give every test a clean in-process broker registry (no cross-test leakage)."""

    reset_brokers()
    yield
    reset_brokers()


@pytest.fixture
def settings() -> Settings:
    """Settings pinned to the in-process bus; a fresh instance per test.

    A distinct ``Settings`` object means a distinct in-proc broker (the registry
    is keyed by ``id(settings)``), so a sink and a source built from the *same*
    fixture value share one broker while different tests never collide.
    """

    return Settings(
        sink_backend="inproc",
        service_name="edis-governance-test",
        jwt_secret="test-secret",
    )


@pytest.fixture
def security_context_factory() -> Callable[..., SecurityContext]:
    """Return a builder for :class:`SecurityContext` with arbitrary roles/scopes."""

    def _make(
        *,
        tenant_id: str = TENANT_ID,
        user_id: str = "user-1",
        roles: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> SecurityContext:
        return SecurityContext(
            tenant_id=tenant_id,
            user_id=user_id,
            roles=list(roles or []),
            scopes=list(scopes or []),
        )

    return _make


@pytest.fixture
def security_context(
    security_context_factory: Callable[..., SecurityContext],
) -> SecurityContext:
    """A convenience analyst-role context on the default tenant."""

    return security_context_factory(roles=["analyst"], scopes=["read:metrics"])


@pytest.fixture
def dev_token_factory(settings: Settings) -> Callable[..., str]:
    """Return a builder that mints a real HS256 dev JWT for a role/scope set.

    The token is signed with the same ``settings.jwt_secret`` the API verifies
    against, so a token from this factory decodes back to the matching context via
    :func:`edis_platform.authz.jwt.decode_token`.
    """

    def _make(
        *,
        tenant_id: str = TENANT_ID,
        user_id: str = "user-1",
        roles: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> str:
        return make_dev_token(
            tenant_id,
            user_id,
            list(roles or []),
            list(scopes or []),
            settings,
        )

    return _make


@pytest.fixture
def dev_token(dev_token_factory: Callable[..., str]) -> str:
    """A signed dev JWT for an admin principal on the default tenant."""

    return dev_token_factory(roles=["admin"])
