"""The :class:`SourceConnector` protocol + the simulator's implementation.

Every ingress source — the simulator, a file reader, a future real connector —
exposes one async method, :meth:`SourceConnector.iter_records`, yielding
:class:`SourceRecord`\\ s. The pipeline driver consumes that stream and runs each
record through the *single* normalization core, so real-time and batch ingress
never diverge (arch §3: "one code path, two ingress modes").

:class:`SimulatorConnector` is the deterministic simulator's implementation: it
walks the generator day-by-day (optionally with a configurable inter-record
delay for a believable live stream) and yields the sales + ops source dicts with
their ``anomaly_label`` ground truth. The per-domain
:class:`~ingestion.connectors.sales.SalesSimConnector` /
:class:`~ingestion.connectors.ops.OpsSimConnector` are thin filters over it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Protocol, runtime_checkable

from edis_contracts.ingest import Domain

from ingestion.simulator.anomalies import AnomalyState
from ingestion.simulator.generator import SimConfig, Simulator


@dataclass
class SourceRecord:
    """One untrusted record on its way into the pipeline.

    ``raw`` is the messy source dict; ``domain`` selects the per-domain coercer +
    validator; ``anomaly_label`` carries the simulator's ground truth (``None``
    for normal records) so it can be stamped on the envelope for downstream eval.
    """

    domain: Domain
    raw: dict
    anomaly_label: str | None = None
    source_system: str | None = None


@runtime_checkable
class SourceConnector(Protocol):
    """Async ingress protocol: yield :class:`SourceRecord`\\ s to the pipeline."""

    def iter_records(self) -> AsyncIterator[SourceRecord]:
        """Yield source records until the source is exhausted (or cancelled)."""
        ...


@dataclass
class SimulatorConnector:
    """Deterministic simulator as a :class:`SourceConnector`.

    Parameters
    ----------
    cfg:
        The generator config (seed/tenant/baselines).
    start:
        First UTC day to generate (defaults to ``n_days`` ending today).
    n_days:
        Number of consecutive days to emit.
    anomalies:
        Anomaly schedule applied to every covered day.
    domains:
        Which domains to emit (``("sales", "ops")`` by default).
    record_delay:
        Optional per-record ``asyncio.sleep`` (seconds) so a "live stream" trickles
        in believably; ``0`` (default) yields as fast as possible (batch/seed).
    order:
        ``"interleaved"`` (default) emits a day's sales then ops then advances;
        ``"chronological"`` sorts all of a day's records by event-time.
    """

    cfg: SimConfig = field(default_factory=SimConfig)
    start: datetime | None = None
    n_days: int = 1
    anomalies: list[AnomalyState] = field(default_factory=list)
    domains: tuple[Domain, ...] = ("sales", "ops")
    record_delay: float = 0.0
    order: str = "interleaved"

    def _start_day(self) -> datetime:
        if self.start is not None:
            return self.start
        today = datetime.now(timezone.utc)
        return datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(
            days=self.n_days - 1
        )

    async def iter_records(self) -> AsyncIterator[SourceRecord]:
        sim = Simulator(self.cfg)
        for a in self.anomalies:
            sim.add_anomaly(a)

        for day in sim.days(self._start_day(), self.n_days):
            records: list[SourceRecord] = []
            if "sales" in self.domains:
                for raw in day.sales:
                    records.append(
                        SourceRecord(
                            domain="sales",
                            raw={k: v for k, v in raw.items() if k != "anomaly_label"},
                            anomaly_label=raw.get("anomaly_label"),
                            source_system=self.cfg.source_system,
                        )
                    )
            if "ops" in self.domains:
                for raw in day.ops:
                    records.append(
                        SourceRecord(
                            domain="ops",
                            raw={k: v for k, v in raw.items() if k != "anomaly_label"},
                            anomaly_label=raw.get("anomaly_label"),
                            source_system=self.cfg.source_system,
                        )
                    )

            if self.order == "chronological":
                records.sort(key=lambda r: r.raw.get("order_ts") or r.raw.get("event_ts") or "")

            for rec in records:
                yield rec
                if self.record_delay:
                    await asyncio.sleep(self.record_delay)
