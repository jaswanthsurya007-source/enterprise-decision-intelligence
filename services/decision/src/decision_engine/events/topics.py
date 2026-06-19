"""The L4 topic names, re-exported from the canonical contracts registry.

The single source of truth for every topic name is
:mod:`edis_contracts.topics`; this module just names the subset the decision engine
touches so call sites import one cohesive surface and there is no risk of a typo'd
literal drifting from the contract. Produced: recommendations + lifecycle. Consumed:
findings (C1) + feedback outcomes (the no-op recorder). Audit/lineage are emitted via
the governance SDK to their own topics.
"""

from __future__ import annotations

from edis_contracts.topics import (
    AUDIT,
    DECISIONS_LIFECYCLE,
    FEEDBACK_OUTCOMES,
    FINDINGS,
    LINEAGE,
    RECOMMENDATIONS,
)

#: Produced by L4 -- the prioritized actions (key = tenant_id).
RECOMMENDATIONS = RECOMMENDATIONS
#: Produced by L4 -- every status transition (key = recommendation_id).
DECISIONS_LIFECYCLE = DECISIONS_LIFECYCLE
#: Consumed by L4 -- the upstream detections (C1 finding consumer).
FINDINGS = FINDINGS
#: Consumed by L4 -- the feedback seam (no-op outcome recorder; key = recommendation_id).
FEEDBACK_OUTCOMES = FEEDBACK_OUTCOMES
#: Governance fan-out (emitted via the SDK).
AUDIT = AUDIT
LINEAGE = LINEAGE

__all__ = [
    "RECOMMENDATIONS",
    "DECISIONS_LIFECYCLE",
    "FINDINGS",
    "FEEDBACK_OUTCOMES",
    "AUDIT",
    "LINEAGE",
]
