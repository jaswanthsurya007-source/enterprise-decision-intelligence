"""The chunked, checkpointed batch loader — files through the same pipeline core.

:class:`BatchLoader` reads a file in bounded chunks
(:func:`~ingestion.batch.readers.iter_file_chunks`), runs every row through the
*identical* :func:`ingestion.pipeline.engine.ingest_record` the real-time path
uses, and (when a :class:`~ingestion.storage.raw_writer.RawWriter` is supplied)
checkpoints the row offset after each chunk so an interrupted load resumes
without re-ingesting. Idempotency makes a resume that overlaps a checkpoint safe
regardless.

It is fully async and infra-optional: with the in-memory idempotency store and
the in-proc sink (and ``writer=None``) it runs in a unit test with no Postgres,
Redis or broker. Heavy file reads happen inside ``asyncio.to_thread`` so the
event loop is never blocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from edis_contracts.ingest import Domain

from ingestion.batch.readers import iter_file_chunks
from ingestion.pipeline.engine import IngestOutcome, ingest_record

if TYPE_CHECKING:
    from ingestion.pipeline.idempotency import IdempotencyStore
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.storage.raw_writer import RawWriter


@dataclass
class BatchLoadResult:
    """Tally of a batch load (per-outcome counts + final checkpoint offset)."""

    source_key: str
    domain: str
    landed: int = 0
    duplicate: int = 0
    dlq: int = 0
    total: int = 0
    final_offset: int = 0

    def add(self, outcome: IngestOutcome) -> None:
        self.total += 1
        if outcome is IngestOutcome.LANDED:
            self.landed += 1
        elif outcome is IngestOutcome.DUPLICATE:
            self.duplicate += 1
        else:
            self.dlq += 1

    def as_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "domain": self.domain,
            "landed": self.landed,
            "duplicate": self.duplicate,
            "dlq": self.dlq,
            "total": self.total,
            "final_offset": self.final_offset,
        }


@dataclass
class BatchLoader:
    """Drive file rows through the shared pipeline core, chunked + checkpointed."""

    publisher: "IngestPublisher"
    idem: "IdempotencyStore"
    writer: "RawWriter | None" = None
    chunk_size: int = 1000
    publish_after_land: bool = True

    async def load_file(
        self,
        path: str | Path,
        *,
        domain: Domain,
        tenant_id: str,
        source_system: str = "batch",
        fmt: str | None = None,
        resume: bool = True,
    ) -> BatchLoadResult:
        """Load every row of ``path`` (as ``domain``) through the pipeline.

        When a writer is present the per-source checkpoint is honored (``resume``)
        and advanced after each chunk. ``source_key`` is the absolute file path so
        two loads of distinct files keep independent checkpoints.
        """

        p = Path(path)
        source_key = str(p.resolve())
        result = BatchLoadResult(source_key=source_key, domain=domain)

        start_offset = 0
        if resume and self.writer is not None:
            start_offset = await self.writer.get_checkpoint(tenant_id, source_key)
        result.final_offset = start_offset

        # Read chunks off-thread so a large file never blocks the loop; each chunk
        # is then processed on the loop and the offset persisted.
        loop_chunks = await asyncio.to_thread(
            lambda: list(
                iter_file_chunks(p, chunk_size=self.chunk_size, fmt=fmt, start_offset=start_offset)
            )
        )

        for offset_after_chunk, rows in loop_chunks:
            for raw in rows:
                res = await ingest_record(
                    domain,
                    raw,
                    tenant_id=tenant_id,
                    source_system=source_system,
                    ctx_sink=self.publisher,
                    idem=self.idem,
                    writer=self.writer,
                    publish_after_land=self.publish_after_land,
                )
                result.add(res.outcome)
            result.final_offset = offset_after_chunk
            if self.writer is not None:
                await self.writer.set_checkpoint(
                    tenant_id,
                    source_key,
                    offset_after_chunk,
                    rows_ingested=result.landed,
                )

        return result
