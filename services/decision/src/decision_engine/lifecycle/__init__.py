"""Recommendation lifecycle (C2): a minimal FSM + the transition manager.

Two pieces:

* :mod:`~decision_engine.lifecycle.state_machine` -- the pure, infra-free FSM
  (``proposed -> accepted | rejected | expired``). Illegal transitions raise a
  :class:`~edis_platform.errors.ConflictError` (mapped to HTTP 409). No DB, no LLM, no
  bus -- just the legal-transition rule, so it is trivially unit-testable.
* :mod:`~decision_engine.lifecycle.manager` -- the :class:`LifecycleManager` that
  validates a transition against the FSM, persists the new status, appends a lifecycle
  row, publishes ``edis.decisions.lifecycle.v1``, emits a governance audit event, and
  (for ``accept``/``reject``) runs from an operator's request. It also drives the TTL
  sweeper that expires stale ``proposed`` recommendations.
"""

from __future__ import annotations

from decision_engine.lifecycle.manager import LifecycleManager
from decision_engine.lifecycle.state_machine import (
    LifecycleStateMachine,
    RecommendationStatus,
    is_terminal,
    legal_transitions,
)

__all__ = [
    "LifecycleManager",
    "LifecycleStateMachine",
    "RecommendationStatus",
    "is_terminal",
    "legal_transitions",
]
