"""Typed playbooks: the ``ActionTemplate`` base + the built ``operational_fix``.

The MVP ships ONE fully-built playbook (:class:`OperationalFixTemplate`); the
registry (see :mod:`decision_engine.synthesis.playbook_registry`) declares the
others as typed stubs so the seam exists without the implementation.
"""

from __future__ import annotations

from decision_engine.synthesis.playbooks.base import (
    ActionTemplate,
    ActionType,
    BoundAction,
    EffortTier,
    PlaybookIntent,
)
from decision_engine.synthesis.playbooks.operational_fix import OperationalFixTemplate

__all__ = [
    "ActionTemplate",
    "ActionType",
    "BoundAction",
    "EffortTier",
    "PlaybookIntent",
    "OperationalFixTemplate",
]
