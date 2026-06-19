"""Root copilot test fixtures (P3) — no Docker, no keys, in-process bus only.

This conftest sits at the package root so it is visible to every test module under
``tests/`` (including ``tests/unit/``). It complements the seeding fixtures in
``tests/unit/conftest.py`` (``data`` / ``ctx`` / ``other_ctx`` / ``registry`` and the
scripted :class:`FakeLLM`) by adding the cross-cutting fixtures the P3 suite leans on:

* ``settings``       — platform :class:`Settings` with ``sink_backend="inproc"`` and NO
  API keys, so nothing the tests touch can reach a broker, a database, or Anthropic/Voyage.
* ``security``       — a :class:`SecurityContext` for the demo tenant (``acme``), exactly
  as the gateway would inject it; the tenant a tool is scoped to comes only from here.
* ``security_other`` — the cross-tenant principal (``globex``) used to prove isolation.

Everything is constructible offline; importing this module needs no infrastructure and no
keys. DB/broker-backed tests live behind ``@pytest.mark.integration`` (``tests/integration``)
and are excluded by ``pytest -m "not integration"``.
"""

from __future__ import annotations

import pytest
from edis_contracts.security import SecurityContext
from edis_platform.settings import Settings

#: The demo tenant whose data the seeded fixtures populate (EMEA revenue drop).
TENANT = "acme"
#: A second tenant whose data must never leak across the boundary.
OTHER_TENANT = "globex"


@pytest.fixture
def settings() -> Settings:
    """Offline platform settings: in-process bus, no DB URL, no API keys.

    ``sink_backend="inproc"`` keeps any (audit/event) sink the wiring builds in-process;
    the absence of ``anthropic_api_key`` / ``voyage_api_key`` forces the deterministic
    offline LLM + stub embedder paths. Importing/using it opens no resource.
    """

    return Settings(sink_backend="inproc", database_url="", jwt_secret="test-secret")


@pytest.fixture
def security() -> SecurityContext:
    """The verified principal for the demo tenant, as the gateway would inject it.

    The copilot derives the tenant every read-only tool is scoped to from this context
    alone — never from the request body and never from LLM output.
    """

    return SecurityContext(tenant_id=TENANT, user_id="u-analyst", roles=["analyst"], scopes=[])


@pytest.fixture
def security_other() -> SecurityContext:
    """A second-tenant principal, used to prove cross-tenant reads never surface."""

    return SecurityContext(tenant_id=OTHER_TENANT, user_id="u-bob", roles=["analyst"], scopes=[])
