"""The minimal recommendation lifecycle FSM (pure; no infra, no LLM).

Per §5.4 the MVP lifecycle is intentionally small::

    proposed --accept--> accepted   (terminal)
    proposed --reject--> rejected   (terminal)
    proposed --expire--> expired    (terminal; driven by the TTL sweeper)

``accepted`` / ``rejected`` / ``expired`` are TERMINAL -- no further transition is
legal from them (the ``in_progress`` / ``outcome_recorded`` tail and a full FSM are the
deferred designed-stub work). Any transition not in the table is illegal and raises a
:class:`~edis_platform.errors.ConflictError`, which the API renders as HTTP 409.

This module is a pure transition table + validators: identical inputs always give the
same answer, it imports no DB / bus / SDK, and it is the single source of truth for what
the manager and the API are allowed to do. Unit-test it directly.
"""

from __future__ import annotations

from typing import Literal

from edis_platform.errors import ConflictError

#: Every recommendation status (mirrors the Recommendation contract literal). The MVP
#: FSM only *transitions* among proposed/accepted/rejected/expired; in_progress and
#: outcome_recorded exist in the contract for the deferred tail.
RecommendationStatus = Literal[
    "proposed", "accepted", "rejected", "expired", "in_progress", "outcome_recorded"
]

#: The legal transition table: from_status -> the set of statuses it may move to.
#: Only ``proposed`` is non-terminal in the MVP.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"accepted", "rejected", "expired"}),
    "accepted": frozenset(),
    "rejected": frozenset(),
    "expired": frozenset(),
    "in_progress": frozenset(),
    "outcome_recorded": frozenset(),
}

#: Operator-driven actions -> the status they move a recommendation to.
ACTION_TO_STATUS: dict[str, str] = {
    "accept": "accepted",
    "reject": "rejected",
    "expire": "expired",
}


def legal_transitions(from_status: str) -> frozenset[str]:
    """Return the set of statuses reachable from ``from_status`` (empty if terminal)."""

    return _TRANSITIONS.get(from_status, frozenset())


def is_terminal(status: str) -> bool:
    """True if ``status`` has no legal outgoing transition (a terminal state)."""

    return not legal_transitions(status)


def can_transition(from_status: str, to_status: str) -> bool:
    """True iff ``from_status -> to_status`` is a legal MVP transition."""

    return to_status in legal_transitions(from_status)


class LifecycleStateMachine:
    """The recommendation lifecycle FSM -- validates transitions; raises 409 on illegal.

    Stateless: it holds no recommendation, only the rules. The :class:`LifecycleManager`
    uses it to gate every status change before any side effect (persist / publish /
    audit) happens, so an illegal transition never produces a half-applied change.
    """

    def can_transition(self, from_status: str, to_status: str) -> bool:
        """True iff ``from_status -> to_status`` is legal."""

        return can_transition(from_status, to_status)

    def validate(self, from_status: str, to_status: str) -> None:
        """Raise :class:`ConflictError` (HTTP 409) if the transition is illegal.

        The message names the current and target states so the API surfaces a clear
        RFC 9457 problem detail (e.g. accepting an already-rejected recommendation).
        """

        if not can_transition(from_status, to_status):
            raise ConflictError(
                f"Illegal lifecycle transition: '{from_status}' -> '{to_status}'. "
                f"Legal next states from '{from_status}': "
                f"{', '.join(sorted(legal_transitions(from_status))) or '(none; terminal)'}."
            )

    def resolve_action(self, action: str) -> str:
        """Map an operator ``action`` (accept/reject/expire) to its target status.

        Raises :class:`ConflictError` for an unknown action (defensive; the API routes
        only ever pass accept/reject).
        """

        to_status = ACTION_TO_STATUS.get(action)
        if to_status is None:
            raise ConflictError(f"Unknown lifecycle action: '{action}'.")
        return to_status
