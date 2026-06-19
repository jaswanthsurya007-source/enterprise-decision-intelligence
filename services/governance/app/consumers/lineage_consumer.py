"""Lineage consumer -- drains ``edis.governance.lineage.v1`` into ``lineage_edge``.

Each :class:`~edis_contracts.events.LineageEvent` names the records one run read
(``inputs``) and produced (``outputs``); this consumer fans the cross-product
into ``(src -> dst)`` edge rows sharing the run's ``run_id`` so the graph can be
walked from any node (raw -> canonical -> metric -> finding -> decision). The
fold is guarded by a per-``lineage_id`` dedupe so a redelivered event does not
duplicate edges. Connects lazily; one bad record is logged and skipped.
"""

from __future__ import annotations

import asyncio

from edis_contracts import topics
from edis_contracts.events import LineageEvent
from edis_platform.bus.base import MessageSource, make_source, parse_message
from edis_platform.db.session import get_sessionmaker
from edis_platform.logging import get_logger
from edis_platform.settings import Settings

from app.repo import LineageRepository

logger = get_logger("edis.governance.lineage_consumer")

CONSUMER_GROUP = "governance-lineage"


class LineageConsumer:
    """Background consumer that materializes the lineage graph."""

    def __init__(self, settings: Settings, source: MessageSource | None = None) -> None:
        self._settings = settings
        self._source = source or make_source(settings)
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        await self._source.start()
        logger.info(
            "lineage consumer started",
            extra={"topic": topics.LINEAGE, "group": CONSUMER_GROUP},
        )
        sessionmaker = get_sessionmaker()
        try:
            async for msg in self._source.subscribe([topics.LINEAGE], CONSUMER_GROUP):
                if self._stopping.is_set():
                    break
                await self._handle(sessionmaker, msg)
        except asyncio.CancelledError:
            raise
        finally:
            await self._source.stop()
            logger.info("lineage consumer stopped")

    async def _handle(self, sessionmaker, msg) -> None:
        try:
            event = parse_message(msg)
            if not isinstance(event, LineageEvent):
                event = LineageEvent.model_validate(msg.value)
        except Exception:  # noqa: BLE001
            logger.exception("dropping malformed lineage event", extra={"topic": msg.topic})
            return

        try:
            async with sessionmaker() as session:
                repo = LineageRepository(session)
                if await repo.already_recorded(event.lineage_id):
                    logger.info(
                        "lineage event already recorded; skipping",
                        extra={"lineage_id": str(event.lineage_id)},
                    )
                    return
                n = await repo.insert_edges(
                    lineage_id=event.lineage_id,
                    run_id=event.run_id,
                    tenant_id=event.tenant_id,
                    inputs=event.inputs,
                    outputs=event.outputs,
                    stage=event.stage,
                    occurred_at=event.occurred_at,
                )
                await session.commit()
            logger.info(
                "lineage edges recorded",
                extra={
                    "lineage_id": str(event.lineage_id),
                    "run_id": str(event.run_id),
                    "tenant_id": event.tenant_id,
                    "stage": event.stage,
                    "edges": n,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to persist lineage event",
                extra={"lineage_id": str(event.lineage_id)},
            )

    async def stop(self) -> None:
        self._stopping.set()
        await self._source.stop()


async def run_lineage_consumer(settings: Settings) -> None:
    """Entry point used by the app lifespan to launch the lineage consumer task."""

    await LineageConsumer(settings).run()
