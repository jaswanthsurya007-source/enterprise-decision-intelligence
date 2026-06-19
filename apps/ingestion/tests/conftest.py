"""Shared pytest fixtures for the ingestion (L1) test suite.

The whole unit suite runs **without Docker** — no Postgres, Redpanda or Redis.
That is achieved by overriding the platform/ingestion settings so the event bus
is the in-process backend and the idempotency guard is the in-memory store, and
by always passing ``writer=None`` to the pipeline (no DB). Anything that genuinely
needs infra is marked ``@pytest.mark.integration`` and excluded from
``pytest -m "not integration"``.

Fixtures provided here:

* ``platform_settings`` — a :class:`edis_platform.settings.Settings` pinned to
  ``sink_backend="inproc"`` (a *fresh instance per test* so the in-proc broker
  registry, keyed by ``id(settings)``, is isolated between tests).
* ``ingestion_settings`` — :class:`ingestion.config.IngestionSettings` pinned to
  ``idempotency_backend="memory"``.
* ``sink`` / ``source`` — started in-proc :class:`EventSink` / :class:`MessageSource`
  over the same settings, torn down automatically.
* ``publisher`` — an :class:`ingestion.publish.publisher.IngestPublisher` over the
  started sink (topics + keys + the ``DATA_WRITE`` audit emission).
* ``idem`` — a fresh :class:`InMemoryIdempotencyStore` so dedupe is testable.
* ``security_ctx`` / ``dev_token`` — a dev :class:`SecurityContext` and the signed
  HS256 JWT for it (``operator`` role), exercising the auth seam without OIDC.

The repo installs ``edis_platform`` editable but not ``ingestion`` (src layout),
so this module also prepends ``apps/ingestion/src`` to ``sys.path`` — making the
suite runnable with a bare ``pytest`` from the app directory.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

# The unit suite runs with NO database: force stateless publish-only ingestion so
# the app never probes/connects to Postgres. Set before any settings are built.
os.environ.setdefault("EDIS_INGEST_PERSIST", "false")
os.environ.setdefault("EDIS_SINK_BACKEND", "inproc")

# --- make the src-layout package importable without an editable install -------
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from edis_contracts.security import SecurityContext  # noqa: E402
from edis_platform.authz.jwt import make_dev_token  # noqa: E402
from edis_platform.bus.base import make_sink, make_source  # noqa: E402
from edis_platform.bus.inproc import reset_brokers  # noqa: E402
from edis_platform.settings import Settings  # noqa: E402

from ingestion.config import IngestionSettings  # noqa: E402
from ingestion.pipeline.idempotency import InMemoryIdempotencyStore  # noqa: E402
from ingestion.publish.publisher import IngestPublisher  # noqa: E402

_TENANT = "acme"


def _utc(*args: int) -> datetime:
    """Convenience tz-aware UTC datetime constructor for fixtures/tests."""

    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_inproc_brokers() -> Iterator[None]:
    """Reset the process-global in-proc broker registry around every test."""

    reset_brokers()
    yield
    reset_brokers()


@pytest.fixture
def platform_settings() -> Settings:
    """Fresh platform settings pinned to the in-proc bus (per-test isolation)."""

    # A distinct instance per test -> a distinct in-proc broker (keyed id(settings)).
    return Settings(sink_backend="inproc")


@pytest.fixture
def ingestion_settings() -> IngestionSettings:
    """Ingestion settings pinned to the in-memory idempotency backend."""

    return IngestionSettings(idempotency_backend="memory")


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
def publisher(sink) -> IngestPublisher:
    """An :class:`IngestPublisher` over the started in-proc sink."""

    return IngestPublisher(sink)


@pytest.fixture
def idem() -> InMemoryIdempotencyStore:
    """A fresh in-memory idempotency guard (dedupe testable without Redis)."""

    return InMemoryIdempotencyStore()


@pytest.fixture
def tenant_id() -> str:
    return _TENANT


@pytest.fixture
def security_ctx() -> SecurityContext:
    """A dev principal: tenant ``acme``, ``operator`` role (write-capable)."""

    return SecurityContext(
        tenant_id=_TENANT,
        user_id="dev-operator",
        roles=["operator"],
        scopes=["ingest:write"],
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
