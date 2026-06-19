"""Z3 — shared fixtures for the repo-wide end-to-end (full-chain) suite.

The crown-jewel test (:mod:`test_full_chain`) wires the **real pure entrypoints of
every EDIS layer** into one in-process run with **no Docker and no API keys**. To do
that it must import the src-layout packages of all the services. The repo installs the
shared libraries (``edis_contracts`` / ``edis_platform`` / ``edis_gov_sdk``) editable,
and ``make install`` editable-installs every service too; but so this suite is also
runnable from a bare checkout (only the libs installed), this module prepends each
service's import root to ``sys.path`` — exactly the pattern the per-service conftests
use (see ``services/integration/tests/conftest.py``).

Import roots wired here:

* ``apps/ingestion/src``        -> ``ingestion``           (L1)
* ``services/integration/src``  -> ``edis_integration``    (L2)
* ``services/intelligence/src`` -> ``edis_intelligence``   (L3)
* ``services/decision/src``     -> ``decision_engine``     (L4)
* ``services/copilot``          -> ``edis_copilot``        (L5, flat layout)

Anything that genuinely needs infra (Postgres / Redpanda / Redis) lives in
:mod:`test_full_chain_docker` behind ``@pytest.mark.integration`` and is skipped by the
default ``pytest -m "not integration"`` selection — so the in-process full-chain test
runs everywhere, keys or no keys, Docker or no Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is two levels up from this file (tests/e2e/conftest.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Each (relative import root) is prepended to sys.path so the src-layout service
#: packages import without an editable install. Order is irrelevant — the package
#: names are disjoint.
_IMPORT_ROOTS = (
    _REPO_ROOT / "apps" / "ingestion" / "src",
    _REPO_ROOT / "services" / "integration" / "src",
    _REPO_ROOT / "services" / "intelligence" / "src",
    _REPO_ROOT / "services" / "decision" / "src",
    _REPO_ROOT / "services" / "copilot",
)

for _root in _IMPORT_ROOTS:
    _s = str(_root)
    if _root.is_dir() and _s not in sys.path:
        sys.path.insert(0, _s)
