"""Audit-consumer idempotency -- pure-python, no Docker.

The audit log is append-only and **idempotent on ``audit_id``**: an at-least-once
bus that redelivers the same :class:`AuditEvent` must record it exactly once. The
production guarantee lives in two places that this file pins:

1. :meth:`app.repo.AuditRepository.insert` issues ``INSERT ... ON CONFLICT
   (audit_id) DO NOTHING RETURNING audit_id`` and returns ``True`` only when a new
   row was written. (Verified against Postgres in
   ``test_audit_consumer.py``, which needs Docker.)
2. :class:`app.consumers.audit_consumer.AuditConsumer` parses each message and
   calls that repo once per delivery.

To prove the dedupe behavior without a database (no ``aiosqlite`` and the repo
uses Postgres-only ``ON CONFLICT ... RETURNING``), we substitute an in-memory fake
that implements the **same** ``insert(event) -> bool`` contract, drive the real
``AuditConsumer._handle`` through it, and assert: first delivery inserts, every
redelivery is a no-op, and the stored row is keyed by ``audit_id``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4


from edis_contracts import topics
from edis_contracts.governance import AuditEvent
from edis_platform.bus.base import Message

from app.consumers.audit_consumer import AuditConsumer


# --------------------------------------------------------------------------- #
# In-memory test doubles mirroring the real repo + session contract.
# --------------------------------------------------------------------------- #
class FakeAuditRepository:
    """Idempotent-on-``audit_id`` audit store, mirroring ``AuditRepository.insert``.

    ``insert`` returns ``True`` on first sight of an ``audit_id`` and ``False`` on
    any redelivery -- exactly the semantics ``ON CONFLICT DO NOTHING RETURNING``
    gives the real repository. Rows are kept so tests can assert exactly-once.
    """

    def __init__(self) -> None:
        self.rows: dict[UUID, AuditEvent] = {}
        self.insert_calls: int = 0

    async def insert(self, event: AuditEvent) -> bool:
        self.insert_calls += 1
        if event.audit_id in self.rows:
            return False
        self.rows[event.audit_id] = event
        return True


class FakeSession:
    """Minimal async session: records commits; no SQL is executed."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _fake_sessionmaker(session: FakeSession):
    """Return a callable that yields ``session`` as an async context manager.

    Matches how the consumer uses ``async with sessionmaker() as session``.
    """

    @asynccontextmanager
    async def _cm():
        async with session as s:
            yield s

    def _factory():
        return _cm()

    return _factory


class _NoopSource:
    """A MessageSource stand-in so the consumer constructs without a broker."""

    async def start(self) -> None:  # pragma: no cover - not exercised here
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    def subscribe(self, topics_list, group):  # pragma: no cover
        raise NotImplementedError


def _audit_event(audit_id: UUID, *, action: str = "DATA_READ") -> AuditEvent:
    return AuditEvent(
        audit_id=audit_id,
        occurred_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        tenant_id="tenant-a",
        actor={"type": "user", "id": "user-1", "roles": ["analyst"]},
        action=action,  # type: ignore[arg-type]
        resource={"type": "metric", "id": "revenue"},
        outcome="ALLOW",
        trace_id="trace-1",
    )


def _message_for(event: AuditEvent) -> Message:
    """Wire message exactly as the bus delivers it (value is a plain JSON dict)."""

    return Message(
        topic=topics.AUDIT,
        key=event.tenant_id,
        value=event.model_dump(mode="json"),
        headers={},
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_duplicate_audit_id_is_recorded_once(monkeypatch, settings) -> None:
    """Redelivering the same ``audit_id`` writes one row; later deliveries no-op."""

    repo = FakeAuditRepository()
    session = FakeSession()

    # The consumer builds an AuditRepository(session) internally; swap in the fake.
    import app.consumers.audit_consumer as mod

    monkeypatch.setattr(mod, "AuditRepository", lambda _session: repo)

    consumer = AuditConsumer(settings, source=_NoopSource())
    sessionmaker = _fake_sessionmaker(session)

    audit_id = uuid4()
    event = _audit_event(audit_id)
    msg = _message_for(event)

    # First delivery -> inserted.
    await consumer._handle(sessionmaker, msg)
    # Two redeliveries of the identical event -> no-ops.
    await consumer._handle(sessionmaker, msg)
    await consumer._handle(sessionmaker, _message_for(_audit_event(audit_id)))

    assert repo.insert_calls == 3  # the consumer attempted all three
    assert len(repo.rows) == 1  # but only one row exists
    assert audit_id in repo.rows
    assert repo.rows[audit_id].action == "DATA_READ"
    # The consumer commits each delivery in its own short transaction.
    assert session.commits == 3


async def test_distinct_audit_ids_each_insert(monkeypatch, settings) -> None:
    """Different ``audit_id``s are independent rows (no false dedupe)."""

    repo = FakeAuditRepository()
    session = FakeSession()

    import app.consumers.audit_consumer as mod

    monkeypatch.setattr(mod, "AuditRepository", lambda _session: repo)

    consumer = AuditConsumer(settings, source=_NoopSource())
    sessionmaker = _fake_sessionmaker(session)

    ids = [uuid4() for _ in range(3)]
    for aid in ids:
        await consumer._handle(sessionmaker, _message_for(_audit_event(aid)))

    assert len(repo.rows) == 3
    assert set(repo.rows) == set(ids)


async def test_malformed_event_is_dropped_not_persisted(monkeypatch, settings) -> None:
    """A payload that fails contract validation is skipped, never wedging the stream."""

    repo = FakeAuditRepository()
    session = FakeSession()

    import app.consumers.audit_consumer as mod

    monkeypatch.setattr(mod, "AuditRepository", lambda _session: repo)

    consumer = AuditConsumer(settings, source=_NoopSource())
    sessionmaker = _fake_sessionmaker(session)

    # Missing required fields (no audit_id/occurred_at/outcome) -> validation fails.
    bad = Message(topic=topics.AUDIT, key="tenant-a", value={"action": "DATA_READ"}, headers={})
    await consumer._handle(sessionmaker, bad)  # must not raise

    assert repo.insert_calls == 0
    assert repo.rows == {}
    assert session.commits == 0


async def test_repo_insert_contract_is_idempotent() -> None:
    """The fake encodes the contract the real repo guarantees: first True, rest False."""

    repo = FakeAuditRepository()
    event = _audit_event(uuid4())

    assert await repo.insert(event) is True  # new
    assert await repo.insert(event) is False  # duplicate
    assert await repo.insert(_audit_event(event.audit_id)) is False  # same id, new obj
    assert len(repo.rows) == 1
