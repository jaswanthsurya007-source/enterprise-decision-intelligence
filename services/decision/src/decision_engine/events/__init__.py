"""Decision-engine event production (C2).

* :mod:`~decision_engine.events.topics` -- the L4 topic names this service produces to /
  consumes from, re-exported from the canonical :mod:`edis_contracts.topics` registry so
  there is one source of truth and no string drift.
* :mod:`~decision_engine.events.producer` -- :class:`DecisionEventProducer`, the thin
  publisher that emits :class:`~edis_contracts.decisions.Recommendation` and
  :class:`~edis_contracts.decisions.RecommendationLifecycleEvent` on the correct topics
  with the correct keys (per §4.3: recommendations keyed by ``tenant_id``, lifecycle keyed
  by ``recommendation_id``).
"""

from __future__ import annotations

from decision_engine.events.producer import DecisionEventProducer

__all__ = ["DecisionEventProducer"]
