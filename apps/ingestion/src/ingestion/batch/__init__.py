"""Batch ingress — chunked, checkpointed file loading into the same pipeline core.

The batch path (arch §3) reads CSV / Parquet / JSONL files in bounded chunks,
processes every row through the *identical* normalization core the real-time path
uses (:func:`ingestion.pipeline.engine.ingest_record`), and checkpoints progress
by row offset so an interrupted load resumes without re-ingesting (idempotency
makes resume safe even if it does).

* :mod:`~ingestion.batch.readers` — format-detecting, chunked row readers.
* :mod:`~ingestion.batch.loader`  — the driver that feeds the pipeline + checkpoints.
"""

from __future__ import annotations

from ingestion.batch.loader import BatchLoadResult, BatchLoader
from ingestion.batch.readers import iter_file_chunks, read_rows

__all__ = [
    "BatchLoader",
    "BatchLoadResult",
    "iter_file_chunks",
    "read_rows",
]
