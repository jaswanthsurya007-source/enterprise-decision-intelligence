"""Chunked, format-detecting row readers for CSV / Parquet / JSONL.

Each reader yields ``(offset, row_dict)`` pairs in file order, where ``offset`` is
the zero-based row index *after* this row (so it is the resume point — the count
of rows fully read). :func:`iter_file_chunks` groups them into bounded chunks so
the loader never holds a whole file in memory and can checkpoint between chunks.

Format is inferred from the extension (``.csv`` / ``.jsonl`` / ``.ndjson`` /
``.json`` / ``.parquet`` / ``.pq``) and may be overridden. Parquet support is
optional (``pip install ingestion[parquet]`` -> ``pyarrow``); importing this
module never requires ``pyarrow``.

Readers are synchronous generators (file IO is cheap and bounded per chunk); the
loader runs them inside the async driver and yields control between chunks.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator

_CSV_EXT = {".csv", ".tsv"}
_JSONL_EXT = {".jsonl", ".ndjson"}
_JSON_EXT = {".json"}
_PARQUET_EXT = {".parquet", ".pq"}


def _detect_format(path: Path, fmt: str | None) -> str:
    if fmt:
        return fmt.lower()
    ext = path.suffix.lower()
    if ext in _CSV_EXT:
        return "csv"
    if ext in _JSONL_EXT:
        return "jsonl"
    if ext in _JSON_EXT:
        return "json"
    if ext in _PARQUET_EXT:
        return "parquet"
    raise ValueError(f"cannot infer format for {path!r}; pass fmt= explicitly")


def _read_csv(path: Path, start_offset: int) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            if i < start_offset:
                continue
            # strip empty -> None handled later by coercion; keep raw strings here.
            yield i + 1, {k: v for k, v in row.items() if k is not None}


def _read_jsonl(path: Path, start_offset: int) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        i = 0
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if i >= start_offset:
                yield i + 1, json.loads(line)
            i += 1


def _read_json(path: Path, start_offset: int) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = [data]
    for i, row in enumerate(data):
        if i >= start_offset:
            yield i + 1, row


def _read_parquet(path: Path, start_offset: int) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        import pyarrow.parquet as pq  # lazy: optional dependency
    except ImportError as exc:  # pragma: no cover - depends on extra install
        raise RuntimeError(
            "reading parquet requires the 'parquet' extra (pip install ingestion[parquet])"
        ) from exc

    table = pq.read_table(path)
    rows = table.to_pylist()
    for i, row in enumerate(rows):
        if i >= start_offset:
            yield i + 1, row


_READERS = {
    "csv": _read_csv,
    "jsonl": _read_jsonl,
    "json": _read_json,
    "parquet": _read_parquet,
}


def read_rows(
    path: str | Path, *, fmt: str | None = None, start_offset: int = 0
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(next_offset, row)`` for every row at/after ``start_offset``.

    ``next_offset`` is the resume point: the number of rows fully consumed through
    this one, so passing it back as ``start_offset`` continues exactly after it.
    """

    p = Path(path)
    fmt = _detect_format(p, fmt)
    reader = _READERS[fmt]
    yield from reader(p, start_offset)


def iter_file_chunks(
    path: str | Path,
    *,
    chunk_size: int,
    fmt: str | None = None,
    start_offset: int = 0,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Yield ``(offset_after_chunk, rows)`` chunks of up to ``chunk_size`` rows.

    ``offset_after_chunk`` is the checkpoint to persist once the chunk has been
    processed; resuming from it re-reads nothing already done.
    """

    chunk: list[dict[str, Any]] = []
    last_offset = start_offset
    for offset, row in read_rows(path, fmt=fmt, start_offset=start_offset):
        chunk.append(row)
        last_offset = offset
        if len(chunk) >= chunk_size:
            yield last_offset, chunk
            chunk = []
    if chunk:
        yield last_offset, chunk
