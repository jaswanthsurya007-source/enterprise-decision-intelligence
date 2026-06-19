"""Shared pytest setup for the intelligence (L3) test suite.

The **unit** suite runs with NO infrastructure -- no Postgres, Redpanda, Redis, and
no Anthropic / Voyage API key. The pure analysis core (detectors, scoring, RCA,
forecast, evidence bundler) is deterministic and importable on numpy/pandas/
statsmodels alone, so the whole X1/X2 surface is exercised by a bare ``pytest``.

The repo installs ``edis_platform`` / ``edis_contracts`` editable but NOT
``edis_intelligence`` (src layout) -- so this module prepends the service ``src``
dir to ``sys.path``, mirroring the L2 suite's approach. Anything that genuinely
needs infra is marked ``@pytest.mark.integration`` and excluded from
``pytest -m "not integration"``.

X4 additions
------------
* A session ``autouse`` fixture that scrubs ``ANTHROPIC_API_KEY`` /
  ``VOYAGE_API_KEY`` / ``EDIS_ANTHROPIC_API_KEY`` / ``EDIS_VOYAGE_API_KEY`` from the
  environment and pins ``EDIS_SINK_BACKEND=inproc`` so NOTHING in the unit suite can
  reach the network or a broker, regardless of the developer's shell.
* ``no_keys_settings`` -- a fresh :class:`edis_platform.settings.Settings` with
  ``sink_backend="inproc"`` and no Anthropic/Voyage keys.
* ``in_memory_reader`` -- an empty :class:`InMemoryMetricReader`.
* ``demo_reader`` -- an :class:`InMemoryMetricReader` pre-loaded with the §9
  ``revenue_drop_emea`` demo (EMEA-web revenue level shift + leading EMEA
  ``checkout-api`` latency/error spikes), built by the deterministic
  :func:`build_demo_series` helper, which reuses the L1->L2 daily-series *shape*
  (``rollup_daily``: ``sum_value`` revenue, ``avg_value`` ops) at the §9 magnitudes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# --- make the src-layout package importable without an editable install --------
_INTELLIGENCE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_INTELLIGENCE_SRC) not in sys.path:
    sys.path.insert(0, str(_INTELLIGENCE_SRC))

# --- make the shared testkit importable by name under --import-mode=importlib --
# (conftest.py itself is loaded under a private module name in importlib mode and
# cannot be ``import``-ed, so the deterministic builders live in edis_l3_testkit.)
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from edis_l3_testkit import (  # noqa: E402  (path set up just above)
    DEMO_BASELINE_DAYS,
    DEMO_INCIDENT_DAYS,
    DEMO_START,
    DEMO_WEEKLY,
    build_clean_series,
    build_demo_series,
    make_demo_reader,
)

__all__ = [
    "DEMO_BASELINE_DAYS",
    "DEMO_INCIDENT_DAYS",
    "DEMO_START",
    "DEMO_WEEKLY",
    "build_clean_series",
    "build_demo_series",
    "make_demo_reader",
]


# ---------------------------------------------------------------------------
# Environment hygiene: no API keys, no real broker for the unit suite.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _no_network_env() -> None:
    """Scrub API keys + pin inproc sink so the unit suite cannot hit the network.

    Session-scoped + ``autouse`` so it applies to every test before any settings
    are constructed, mirroring the build guarantee that L3 is unit-testable with no
    keys and no Docker.
    """

    import os

    for var in (
        "ANTHROPIC_API_KEY",
        "VOYAGE_API_KEY",
        "EDIS_ANTHROPIC_API_KEY",
        "EDIS_VOYAGE_API_KEY",
    ):
        os.environ.pop(var, None)
    os.environ["EDIS_SINK_BACKEND"] = "inproc"


@pytest.fixture
def no_keys_settings():
    """A fresh platform :class:`Settings`: inproc sink, no Anthropic/Voyage key."""

    from edis_platform.settings import Settings

    return Settings(sink_backend="inproc", anthropic_api_key=None, voyage_api_key=None)


# ---------------------------------------------------------------------------
# Reader fixtures (the deterministic builders live in edis_l3_testkit).
# ---------------------------------------------------------------------------
@pytest.fixture
def in_memory_reader():
    """An empty :class:`InMemoryMetricReader` for tests that load their own cells."""

    from edis_intelligence.runner.pipeline import InMemoryMetricReader

    return InMemoryMetricReader()


@pytest.fixture
def demo_reader():
    """The §9 ``revenue_drop_emea`` demo, pre-loaded into an InMemoryMetricReader."""

    return make_demo_reader()
