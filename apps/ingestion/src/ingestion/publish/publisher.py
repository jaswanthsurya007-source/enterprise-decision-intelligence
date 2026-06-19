"""The ingestion publisher: one place that knows topics + keys + audit.

Targets (arch §4.3):

* sales -> :data:`topics.RAW_SALES`, key = ``tenant_id``
* ops   -> :data:`topics.RAW_OPS`,   key = ``tenant_id|service``
* DLQ   -> :data:`topics.DLQ_INGEST`, key = ``tenant_id``

Every successful land/publish also emits an ``AuditEvent`` (``action=DATA_WRITE``)
on the **same** :class:`~edis_platform.bus.base.EventSink` via
:class:`~edis_gov_sdk.audit.AuditEmitter`, so the governance spine records the
write from the very first record. The publisher does **not** own the sink
lifecycle — the app/CLI starts and stops it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_contracts import topics
from edis_contracts.ingest import DLQRecord, IngestEnvelope
from edis_gov_sdk.audit import AuditEmitter

if TYPE_CHECKING:
    from edis_platform.bus.base import EventSink


#: Per-domain raw topic. Customer is a stub seam in the MVP but wired here.
_RAW_TOPIC = {
    "sales": topics.RAW_SALES,
    "ops": topics.RAW_OPS,
    "customer": topics.RAW_CUSTOMER,
}


def raw_key_for(env: IngestEnvelope) -> str:
    """Partition key per arch §4.3: ``tenant_id``; ops adds ``|service``.

    Ops keys by ``tenant_id|service`` so all events for one service stay on one
    partition (ordered); customer keys by ``tenant_id|session_id``.
    """

    if env.domain == "ops":
        service = env.payload.get("service", "")
        return f"{env.tenant_id}|{service}"
    if env.domain == "customer":
        session_id = env.payload.get("session_id", "")
        return f"{env.tenant_id}|{session_id}"
    return env.tenant_id


class IngestPublisher:
    """Publishes envelopes/DLQ records + emits the ``DATA_WRITE`` audit event."""

    def __init__(self, sink: "EventSink") -> None:
        self._sink = sink
        self._audit = AuditEmitter(sink)

    @property
    def sink(self) -> "EventSink":
        return self._sink

    async def publish_envelope(self, env: IngestEnvelope) -> None:
        """Publish a validated envelope to its ``edis.raw.<domain>.v1`` topic and audit it."""

        topic = _RAW_TOPIC.get(env.domain)
        if topic is None:  # pragma: no cover - guarded earlier by validator
            raise ValueError(f"no raw topic for domain {env.domain!r}")
        await self._sink.publish(topic, key=raw_key_for(env), value=env)

        trace_id = env.trace_context.get("trace_id") or env.trace_context.get("traceparent")
        await self._audit.emit(
            ctx=None,  # system/background producer — no principal
            action="DATA_WRITE",
            resource={"type": "raw_event", "id": str(env.event_id), "domain": env.domain},
            outcome="ALLOW",
            tenant_id=env.tenant_id,
            trace_id=trace_id,
        )

    async def publish_dlq(self, record: DLQRecord) -> None:
        """Publish a dead-letter record to ``edis.dlq.ingest.v1`` (keyed by tenant)."""

        await self._sink.publish(topics.DLQ_INGEST, key=record.tenant_id, value=record)
