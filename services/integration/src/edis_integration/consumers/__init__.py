"""Ingress consumers -- one normalization core, two ingress modes.

Both wrap the same :func:`~edis_integration.pipeline.engine.process_envelope`
orchestration and the same transactional-outbox relay, so a record is processed
identically whether it arrived reactively or in bulk:

* :class:`StreamConsumer` -- subscribes ``edis.raw.sales/ops.v1`` via
  ``make_source``, processes each envelope, relay-publishes the staged events;
  stoppable (``stop()``) and bounded (``run(max_messages=...)``) for tests.
* :class:`BatchLoader` -- drains a bounded set of envelopes, then drains the
  outbox once; returns a :class:`BatchResult` summary (also the engine behind the
  ops/admin reprocess route).
"""

from __future__ import annotations

from edis_integration.consumers.batch_loader import BatchLoader, BatchResult
from edis_integration.consumers.stream_consumer import RAW_TOPICS, StreamConsumer

__all__ = ["StreamConsumer", "RAW_TOPICS", "BatchLoader", "BatchResult"]
