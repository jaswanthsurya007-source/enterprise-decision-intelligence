"""Hard caps for the manual agent loop: iterations + per-tenant budget.

The manual Opus tool-use loop must terminate and must not overspend. :class:`LoopLimits`
bundles the two caps the loop enforces every iteration:

* ``max_iterations`` — the hard ceiling on plan->tool->synthesize cycles. The loop stops
  and degrades (synthesizes from whatever tool results it has) once reached, so a model
  that keeps requesting tools can never spin forever.
* the per-tenant daily cost cap — enforced via the injected
  :class:`~app.budget.accounting.BudgetAccountant`. Before each Opus call the loop
  projects the call's cost and calls :meth:`BudgetAccountant.check`; a
  :class:`~app.budget.accounting.BudgetExceeded` degrades the turn.

:class:`LoopState` is the small mutable bookkeeping the loop threads through iterations
(iteration counter, accumulated tool results, the degrade reason). Pure data + a couple
of guards; no SDK, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.tools.base import ToolResult


class MaxIterationsReached(Exception):
    """The loop hit its iteration ceiling and must degrade to synthesis."""


@dataclass(frozen=True)
class LoopLimits:
    """The loop's hard caps. ``daily_cost_cap_usd`` of 0 disables the budget guard."""

    max_iterations: int = 6
    max_output_tokens: int = 64000
    tool_timeout_s: float = 15.0
    daily_cost_cap_usd: float = 5.0

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")


@dataclass
class LoopState:
    """Mutable per-turn loop bookkeeping (iteration count + accumulated tool results).

    ``results`` accumulates every :class:`~app.tools.base.ToolResult` produced this turn
    (in call order) — the basis for the citations + the grounding whitelist. ``degraded``
    / ``degrade_reason`` record whether the turn ended cleanly or was capped/blocked, so
    the answer can lower its confidence and the audit can record why.
    """

    iteration: int = 0
    results: list["ToolResult"] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    cache_read_input_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)

    def next_iteration(self, limits: LoopLimits) -> int:
        """Advance the iteration counter, raising once the cap is exceeded."""

        self.iteration += 1
        if self.iteration > limits.max_iterations:
            raise MaxIterationsReached(f"exceeded max_iterations={limits.max_iterations}")
        return self.iteration

    def mark_degraded(self, reason: str) -> None:
        """Record that the turn degraded (capped, refused, budget, error)."""

        self.degraded = True
        self.degrade_reason = reason
