"""Governance emitters over the real in-process bus -- no Docker, no broker.

Pure-python (runs everywhere in CI). Drives :class:`AuditEmitter` and
:class:`LineageEmitter` through the public ``inproc`` EventSink exactly as a
service would, and asserts the built event lands on the correct governance topic,
keyed by tenant, and rehydrates into its canonical contract model via
``parse_message``. The :class:`ExplainabilityClient` is exercised with httpx's
in-memory ``MockTransport`` so no live governance service is required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from edis_contracts import topics
from edis_contracts.governance import AuditEvent, Decision, Evidence
from edis_contracts.events import LineageEvent
from edis_contracts.security import SecurityContext
from edis_gov_sdk import (
    AuditEmitter,
    ExplainabilityClient,
    LineageEmitter,
    emit_audit,
    emit_lineage,
)
from edis_gov_sdk.explain import DECISIONS_PATH
from edis_platform.bus import make_sink, make_source, parse_message
from edis_platform.bus.inproc import reset_brokers
from edis_platform.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_broker():
    """Each test gets a clean in-process broker registry."""

    reset_brokers()
    yield
    reset_brokers()


def _settings() -> Settings:
    # Build one Settings and share it between sink and source so they resolve to
    # the SAME in-process broker (the registry is keyed by id(settings)).
    return Settings(sink_backend="inproc")


def _ctx() -> SecurityContext:
    return SecurityContext(
        tenant_id="tenant-a",
        user_id="user-1",
        roles=["analyst"],
        scopes=["read:metrics"],
    )


async def _subscribe_one(source, topic: str, group: str):
    """Start consuming ``topic`` for ``group`` before any publish; return the task."""

    stream = source.subscribe([topic], group=group)
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)  # let the consumer register its group queue
    return stream, task


async def test_audit_event_published_to_audit_topic() -> None:
    settings = _settings()
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    stream, task = await _subscribe_one(source, topics.AUDIT, group="audit-consumer")

    emitter = AuditEmitter(sink)
    built = await emitter.emit(
        _ctx(),
        action="DATA_READ",
        resource={"type": "metric", "id": "revenue"},
        outcome="ALLOW",
        trace_id="trace-xyz",
    )

    msg = await asyncio.wait_for(task, timeout=2.0)

    # Routed to the audit topic and keyed by tenant (preserves per-tenant order).
    assert msg.topic == topics.AUDIT
    assert msg.key == "tenant-a"

    parsed = parse_message(msg)
    assert isinstance(parsed, AuditEvent)
    assert parsed.tenant_id == "tenant-a"
    assert parsed.action == "DATA_READ"
    assert parsed.outcome == "ALLOW"
    assert parsed.resource == {"type": "metric", "id": "revenue"}
    assert parsed.actor == {"type": "user", "id": "user-1", "roles": ["analyst"]}
    assert parsed.trace_id == "trace-xyz"

    # The returned event matches what was published (same audit_id, UTC time).
    assert parsed.audit_id == built.audit_id
    assert parsed.occurred_at == built.occurred_at
    assert built.occurred_at.tzinfo is timezone.utc

    await stream.aclose()
    await source.stop()
    await sink.stop()


async def test_audit_emit_without_context_uses_system_actor_and_explicit_tenant() -> None:
    settings = _settings()
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    stream, task = await _subscribe_one(source, topics.AUDIT, group="audit-consumer")

    await emit_audit(
        sink,
        None,
        action="AI_DECISION",
        resource={"type": "recommendation", "id": "rec-7"},
        outcome="ALLOW",
        decision_id=uuid4(),
        tenant_id="tenant-b",
    )

    msg = await asyncio.wait_for(task, timeout=2.0)
    parsed = parse_message(msg)
    assert isinstance(parsed, AuditEvent)
    assert parsed.tenant_id == "tenant-b"
    assert parsed.actor == {"type": "system", "id": "system", "roles": []}
    assert parsed.decision_id is not None

    await stream.aclose()
    await source.stop()
    await sink.stop()


async def test_audit_emit_requires_a_tenant() -> None:
    settings = _settings()
    sink = make_sink(settings)
    await sink.start()
    with pytest.raises(ValueError):
        await emit_audit(
            sink,
            None,
            action="DATA_READ",
            resource={"type": "metric"},
            outcome="ALLOW",
        )
    await sink.stop()


async def test_lineage_event_published_to_lineage_topic() -> None:
    settings = _settings()
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    stream, task = await _subscribe_one(source, topics.LINEAGE, group="lineage-consumer")

    run_id = uuid4()
    inputs = [{"type": "raw_event", "id": "evt-1"}]
    outputs = [{"type": "canonical_order", "id": "ord-1"}]

    built = await emit_lineage(
        sink,
        tenant_id="tenant-a",
        run_id=run_id,
        inputs=inputs,
        outputs=outputs,
        stage="integration",
    )

    msg = await asyncio.wait_for(task, timeout=2.0)
    assert msg.topic == topics.LINEAGE
    assert msg.key == "tenant-a"

    parsed = parse_message(msg)
    assert isinstance(parsed, LineageEvent)
    assert parsed.tenant_id == "tenant-a"
    assert parsed.run_id == run_id
    assert parsed.inputs == inputs
    assert parsed.outputs == outputs
    assert parsed.stage == "integration"
    assert parsed.lineage_id == built.lineage_id
    # tz-aware UTC survives the JSON round-trip (offset 0; pydantic may use its
    # own UTC tzinfo rather than the timezone.utc singleton).
    assert parsed.occurred_at.utcoffset() == timezone.utc.utcoffset(None)
    assert built.occurred_at.tzinfo is timezone.utc

    await stream.aclose()
    await source.stop()
    await sink.stop()


async def test_lineage_emitter_class_publishes() -> None:
    settings = _settings()
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    stream, task = await _subscribe_one(source, topics.LINEAGE, group="g")

    emitter = LineageEmitter(sink)
    await emitter.emit(
        tenant_id="tenant-c",
        run_id=uuid4(),
        inputs=[],
        outputs=[{"type": "metric_observation", "id": "m-1"}],
        stage="intelligence",
    )

    msg = await asyncio.wait_for(task, timeout=2.0)
    parsed = parse_message(msg)
    assert isinstance(parsed, LineageEvent)
    assert parsed.tenant_id == "tenant-c"
    assert parsed.stage == "intelligence"

    await stream.aclose()
    await source.stop()
    await sink.stop()


async def test_explainability_client_posts_decision() -> None:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(202)

    transport = httpx.MockTransport(_handler)
    http_client = httpx.AsyncClient(transport=transport)

    decision = Decision(
        decision_id=uuid4(),
        tenant_id="tenant-a",
        decision_type="recommendation",
        subject_id=uuid4(),
        actor={"type": "service", "id": "decision"},
        rationale="playbook matched finding",
        evidence=[
            Evidence(
                evidence_id=uuid4(),
                kind="finding",
                summary="revenue point anomaly",
                snapshot={"observed_value": 100.0, "expected_value": 150.0},
            )
        ],
        created_at=datetime.now(timezone.utc),
    )

    client = ExplainabilityClient("http://governance:8000/", client=http_client)
    await client.write_decision(decision)

    assert captured["method"] == "POST"
    assert captured["url"] == f"http://governance:8000{DECISIONS_PATH}"
    # Body is the verbatim contract JSON (round-trips back to the same Decision).
    assert Decision.model_validate_json(captured["body"]) == decision

    # Injected client is NOT closed by the SDK (caller owns its lifecycle).
    assert not http_client.is_closed
    await client.aclose()
    assert not http_client.is_closed
    await http_client.aclose()


async def test_explainability_client_raises_on_error_status() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    http_client = httpx.AsyncClient(transport=transport)

    decision = Decision(
        decision_id=uuid4(),
        tenant_id="tenant-a",
        decision_type="copilot_answer",
        subject_id=uuid4(),
        rationale="x",
        created_at=datetime.now(timezone.utc),
    )

    client = ExplainabilityClient("http://governance:8000", client=http_client)
    with pytest.raises(httpx.HTTPStatusError):
        await client.write_decision(decision)

    await http_client.aclose()
