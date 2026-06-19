"""Consumers: the reactive ``edis.findings.v1`` tap that drives synthesis.

:class:`FindingConsumer` subscribes via ``make_source``, runs the deterministic
synthesize core, and publishes the recommendation + its initial lifecycle + audit/
lineage. (The no-op ``edis.feedback.outcomes.v1`` recorder lands in C2.)
"""

from __future__ import annotations

from decision_engine.consumers.finding_consumer import FindingConsumer

__all__ = ["FindingConsumer"]
