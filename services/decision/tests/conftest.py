"""Shared pytest setup for the decision (L4) test suite.

The **unit** suite runs with NO infrastructure -- no Postgres, Redpanda, Redis -- and NO
Anthropic API key. The whole L4 synthesis + scoring + lifecycle + API surface is
deterministic and importable on pydantic / fastapi / sqlalchemy alone (the SQLAlchemy ORM
imports fine without a live engine), so ``pytest -m "not integration"`` passes on a bare
laptop. Anything that genuinely needs Docker is marked ``@pytest.mark.integration`` and
excluded by ``-m "not integration"``.

The repo installs ``edis_platform`` / ``edis_contracts`` / ``edis_gov_sdk`` editable but
NOT ``decision_engine`` (src layout), so this module prepends the service ``src`` dir to
``sys.path`` (mirroring the L2/L3 suites).

Fixtures
--------
* ``_no_network_env`` (session, autouse) -- scrub every Anthropic key var and pin
  ``EDIS_SINK_BACKEND=inproc`` so nothing in the unit suite can reach a key or a broker.
* ``no_keys_settings`` -- a fresh inproc :class:`edis_platform.settings.Settings`, no key.
* ``dev_ctx`` / ``operator_ctx`` / ``viewer_ctx`` -- :class:`SecurityContext`s for authz.
* ``dev_token`` / ``operator_token`` / ``viewer_token`` -- matching dev JWTs.
* ``demo_finding`` -- the §9 ``revenue_drop_emea`` EMEA-web revenue level-shift Finding,
  driven by a leading EMEA ``checkout-api`` latency/error regression, built by the
  deterministic :func:`build_demo_finding` (and re-exported from the testkit).
* ``fixed_now`` -- a deterministic ``now`` inside the demo window so
  ``affected_days_remaining`` is the seeded value (5).
* ``InMemoryRecommendationRepo`` / ``FakeSink`` -- infra-free collaborators for the
  consumers, lifecycle manager, outcome recorder, and the API.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# --- make the src-layout package importable without an editable install --------
_DECISION_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_DECISION_SRC) not in sys.path:
    sys.path.insert(0, str(_DECISION_SRC))

# --- make the shared testkit importable by name under --import-mode=importlib --
# (conftest.py is loaded under a private module name in importlib mode and cannot be
# ``import``-ed, so the deterministic builders live in edis_l4_testkit.)
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from edis_l4_testkit import (  # noqa: E402  (path set up just above)
    DEMO_TENANT,
    FakeSink,
    InMemoryRecommendationRepo,
    build_demo_finding,
)

__all__ = [
    "DEMO_TENANT",
    "FakeSink",
    "InMemoryRecommendationRepo",
    "build_demo_finding",
]


# ---------------------------------------------------------------------------
# Environment hygiene: no API keys, no real broker for the unit suite.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _no_network_env() -> None:
    """Scrub Anthropic keys + pin inproc sink so the unit suite cannot hit the network."""

    import os

    for var in ("ANTHROPIC_API_KEY", "EDIS_ANTHROPIC_API_KEY"):
        os.environ.pop(var, None)
    os.environ["EDIS_SINK_BACKEND"] = "inproc"


@pytest.fixture
def no_keys_settings():
    """A fresh platform :class:`Settings`: inproc sink, no Anthropic key."""

    from edis_platform.settings import Settings

    return Settings(sink_backend="inproc", anthropic_api_key=None)


# ---------------------------------------------------------------------------
# Deterministic demo finding + a fixed `now` inside its window.
# ---------------------------------------------------------------------------
@pytest.fixture
def demo_finding():
    """The §9 ``revenue_drop_emea`` EMEA-web revenue level-shift Finding."""

    return build_demo_finding()


@pytest.fixture
def fixed_now() -> datetime:
    """A deterministic ``now`` inside the demo window -> affected_days_remaining == 5."""

    return datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Security contexts + dev JWTs for the authz tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def operator_ctx():
    from edis_contracts.security import SecurityContext

    return SecurityContext(tenant_id=DEMO_TENANT, user_id="op-1", roles=["operator"], scopes=[])


@pytest.fixture
def viewer_ctx():
    from edis_contracts.security import SecurityContext

    return SecurityContext(tenant_id=DEMO_TENANT, user_id="view-1", roles=["viewer"], scopes=[])


@pytest.fixture
def dev_ctx(operator_ctx):
    """Default principal for tests that just need a tenant-scoped operator."""

    return operator_ctx


@pytest.fixture
def operator_token(no_keys_settings):
    from edis_platform.authz.jwt import make_dev_token

    return make_dev_token(DEMO_TENANT, "op-1", ["operator"], [], no_keys_settings)


@pytest.fixture
def viewer_token(no_keys_settings):
    from edis_platform.authz.jwt import make_dev_token

    return make_dev_token(DEMO_TENANT, "view-1", ["viewer"], [], no_keys_settings)


@pytest.fixture
def dev_token(operator_token):
    return operator_token


# ---------------------------------------------------------------------------
# Infra-free collaborators.
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def in_memory_repo() -> InMemoryRecommendationRepo:
    return InMemoryRecommendationRepo()
