"""Audit consumer -- drains ``edis.governance.audit.v1`` into ``audit_log``.

Every layer emits :class:`~edis_contracts.governance.AuditEvent`s; this consumer
folds them into the append-only audit hypertable **idempotently on ``audit_id``**
(``ON CONFLICT DO NOTHING``), so an at-least-once bus delivering a record twice
records it once. Each event is committed in its own short transaction. The loop
connects lazily inside :meth:`run` (the bus ``start()``), so importing this module
never touches a broker or DB.

Robustness: a malformed payload or a transient DB error on one record is logged
and skipped -- a single bad event must never wedge the audit stream.
"""

from __future__ import annotations

import asyncio

from edis_contracts import topics
from edis_contracts.governance import AuditEvent
from edis_platform.bus.base import MessageSource, make_source, parse_message
from edis_platform.db.session import get_sessionmaker
from edis_platform.logging import get_logger
from edis_platform.settings import Settings

from app.repo import AuditRepository

logger = get_logger("edis.governance.audit_consumer")

#: Consumer-group id (replicas of the governance service share the partitions).
CONSUMER_GROUP = "governance-audit"


class AuditConsumer:
    """Background consumer that persists audit events idempotently."""

    def __init__(self, settings: Settings, source: MessageSource | None = None) -> None:
        self._settings = settings
        self._source = source or make_source(settings)
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        """Subscribe and persist until :meth:`stop` is called."""

        await self._source.start()
        logger.info(
            "audit consumer started",
            extra={"topic": topics.AUDIT, "group": CONSUMER_GROUP},
        )
        sessionmaker = get_sessionmaker()
        try:
            async for msg in self._source.subscribe([topics.AUDIT], CONSUMER_GROUP):
                if self._stopping.is_set():
                    break
                await self._handle(sessionmaker, msg)
        except asyncio.CancelledError:  # graceful shutdown
            raise
        finally:
            await self._source.stop()
            logger.info("audit consumer stopped")

    async def _handle(self, sessionmaker, msg) -> None:
        try:
            event = parse_message(msg)
            if not isinstance(event, AuditEvent):
                event = AuditEvent.model_validate(msg.value)
        except Exception:  # noqa: BLE001 - never wedge the stream on a bad record
            logger.exception("dropping malformed audit event", extra={"topic": msg.topic})
            return

        try:
            async with sessionmaker() as session:
                inserted = await AuditRepository(session).insert(event)
                await session.commit()
            logger.info(
                "audit event recorded",
                extra={
                    "audit_id": str(event.audit_id),
                    "tenant_id": event.tenant_id,
                    "action": event.action,
                    "outcome": event.outcome,
                    "inserted": inserted,
                },
            )
        except Exception:  # noqa: BLE001 - transient DB error: log + continue
            logger.exception(
                "failed to persist audit event",
                extra={"audit_id": str(event.audit_id)},
            )

    async def stop(self) -> None:
        """Signal the loop to stop after the current message."""

        self._stopping.set()
        await self._source.stop()


async def run_audit_consumer(settings: Settings) -> None:
    """Entry point used by the app lifespan to launch the audit consumer task."""

    await AuditConsumer(settings).run()
