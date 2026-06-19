"""Shared pytest fixtures for the integration (L2) test suite.

The whole **unit** suite runs with NO Docker -- no Postgres, Redpanda, or Redis.
That is achieved by:

* forcing ``sink_backend="inproc"`` (a *fresh* :class:`Settings` instance per
  test, so the in-proc broker registry -- keyed by ``id(settings)`` -- is isolated
  between tests),
* persisting through the :class:`~edis_integration.pipeline.engine.InMemoryIntegrationRepo`
  fake (the engine's port has an in-memory impl precisely so the pipeline + outbox
  semantics are testable without a database), and
* adapting that repo's in-memory outbox to the relay's
  :class:`~edis_integration.outbox.outbox_repo.OutboxReader` port via
  :class:`InMemoryOutboxRepo`.

Anything that genuinely needs infra is marked ``@pytest.mark.integration`` and
excluded from ``pytest -m "not integration"``.

The repo installs ``edis_platform`` / ``edis_contracts`` editable but NOT
``edis_integration`` or ``ingestion`` (both src layout), so this module prepends
both ``src`` dirs to ``sys.path`` -- making the suite runnable with a bare
``pytest`` and importing the Phase-2 simulator for the key L1->L2 test.

Fixtures provided here:

* ``platform_settings`` -- :class:`edis_platform.settings.Settings` pinned to
  ``sink_backend="inproc"`` (per-test isolated broker).
* ``sink`` / ``source`` -- started in-proc :class:`EventSink` / :class:`MessageSource`
  over the same settings (so a publisher and a consumer find each other).
* ``repo`` -- a fresh :class:`InMemoryIntegrationRepo` (canonical store + outbox).
* ``outbox_reader`` -- an :class:`InMemoryOutboxRepo` over ``repo`` for the relay.
* ``security_ctx`` / ``dev_token`` -- a dev :class:`SecurityContext` (tenant
  ``acme``, ``operator`` role) and the signed HS256 JWT for it, exercising the
  auth seam without OIDC.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

# --- make the src-layout packages importable without an editable install -------
# This service (edis_integration) and the Phase-2 ingestion app (used by the
# L1->L2 key test) are both src-layout and not pip-installed.
_INTEGRATION_SRC = Path(__file__).resolve().parents[1] / "src"
_INGESTION_SRC = Path(__file__).resolve().parents[3] / "apps" / "ingestion" / "src"
for _p in (_INTEGRATION_SRC, _INGESTION_SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from edis_contracts.security import SecurityContext  # noqa: E402
from edis_platform.authz.jwt import make_dev_token  # noqa: E402
from edis_platform.bus.base import make_sink, make_source  # noqa: E402
from edis_platform.bus.inproc import reset_brokers  # noqa: E402
from edis_platform.settings import Settings  # noqa: E402

from edis_integration.outbox.outbox_repo import InMemoryOutboxRepo  # noqa: E402
from edis_integration.pipeline.engine import InMemoryIntegrationRepo  # noqa: E402

_TENANT = "acme"


def utc(*args: int) -> datetime:
    """Convenience tz-aware UTC datetime constructor for fixtures/tests."""

    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_inproc_brokers() -> Iterator[None]:
    """Reset the process-global in-proc broker registry around every test."""

    reset_brokers()
    yield
    reset_brokers()


@pytest.fixture
def tenant_id() -> str:
    return _TENANT


@pytest.fixture
def platform_settings() -> Settings:
    """Fresh platform settings pinned to the in-proc bus (per-test isolation).

    A distinct instance per test -> a distinct in-proc broker (keyed
    ``id(settings)``), so a publisher and consumer in one test share a broker
    while different tests stay isolated.
    """

    return Settings(sink_backend="inproc")


@pytest_asyncio.fixture
async def sink(platform_settings: Settings) -> AsyncIterator:
    """A started in-proc :class:`EventSink` (stopped on teardown)."""

    s = make_sink(platform_settings)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


@pytest_asyncio.fixture
async def source(platform_settings: Settings) -> AsyncIterator:
    """A started in-proc :class:`MessageSource` sharing the sink's broker."""

    src = make_source(platform_settings)
    await src.start()
    try:
        yield src
    finally:
        await src.stop()


@pytest.fixture
def repo() -> InMemoryIntegrationRepo:
    """A fresh in-memory :class:`IntegrationRepo` (no Postgres)."""

    return InMemoryIntegrationRepo()


@pytest.fixture
def outbox_reader(repo: InMemoryIntegrationRepo) -> InMemoryOutboxRepo:
    """Adapt ``repo``'s in-memory outbox to the relay's :class:`OutboxReader`."""

    return InMemoryOutboxRepo(repo)


@pytest.fixture
def security_ctx() -> SecurityContext:
    """A dev principal: tenant ``acme``, ``operator`` role (write-capable)."""

    return SecurityContext(
        tenant_id=_TENANT,
        user_id="dev-operator",
        roles=["operator"],
        scopes=["ingest:write", "integration:admin"],
        token_id="test-token",
    )


@pytest.fixture
def dev_token(security_ctx: SecurityContext) -> str:
    """The signed HS256 dev JWT for ``security_ctx`` (default jwt secret)."""

    return make_dev_token(
        tenant_id=security_ctx.tenant_id,
        user_id=security_ctx.user_id,
        roles=list(security_ctx.roles),
        scopes=list(security_ctx.scopes),
        settings=Settings(),
    )
