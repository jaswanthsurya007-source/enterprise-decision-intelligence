"""Copilot budget accounting: ``count_tokens``-based per-tenant daily cost cap.

:mod:`edis_copilot.budget.accounting` provides :class:`BudgetAccountant` (the per-tenant daily
USD ledger the agent loop checks before each Opus iteration), :class:`CostModel` (the
published per-MTok prices), :func:`count_request_tokens` (the ``messages.count_tokens``
wrapper with an offline heuristic fallback), and :class:`BudgetExceeded`. All math is
pure and offline-testable; only token counting touches the SDK and only when a client
is supplied, so this package imports with no key.
"""

from __future__ import annotations

from edis_copilot.budget.accounting import (
    BudgetAccountant,
    BudgetExceeded,
    CostModel,
    count_request_tokens,
)

__all__ = [
    "BudgetAccountant",
    "BudgetExceeded",
    "CostModel",
    "count_request_tokens",
]
