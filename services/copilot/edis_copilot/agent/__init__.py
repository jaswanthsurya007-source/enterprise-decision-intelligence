"""The copilot agent: router, manual Opus tool-use loop, offline agent, and limits.

Public surface:

* :func:`edis_copilot.agent.loop.answer` — the ONE entrypoint:
  ``answer(question, ctx, *, registry, llm) -> CopilotAnswer``. Routes the question
  (Haiku ``messages.parse`` or rules), then runs the manual streaming Opus tool-use loop
  when a key is present, or the deterministic offline agent (real tools, templated
  grounded answer) when not. Streams SSE frames via an optional ``emit`` callback. Never
  raises into the request.
* :class:`edis_copilot.agent.synthesis.CopilotAnswer` — the structured, grounded turn output.
* :class:`edis_copilot.agent.router.Route` / :func:`edis_copilot.agent.router.route_question` — routing.
* :class:`edis_copilot.agent.limits.LoopLimits` — the iteration / budget / token caps.

Tenant is ALWAYS taken from the :class:`~app.tools.base.ToolContext` (the verified
principal) and injected into every tool call server-side — never from the model.
"""

from __future__ import annotations

from edis_copilot.agent.limits import LoopLimits, LoopState, MaxIterationsReached
from edis_copilot.agent.loop import answer
from edis_copilot.agent.router import Route, RouteModel, route_question, rule_route
from edis_copilot.agent.synthesis import CopilotAnswer, finalize_answer, offline_answer

__all__ = [
    "CopilotAnswer",
    "LoopLimits",
    "LoopState",
    "MaxIterationsReached",
    "Route",
    "RouteModel",
    "answer",
    "finalize_answer",
    "offline_answer",
    "route_question",
    "rule_route",
]
