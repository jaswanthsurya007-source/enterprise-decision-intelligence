"""Sales connector — a thin ``domain="sales"`` adapter over the simulator.

Exposes only the sales stream of the deterministic generator as a
:class:`~ingestion.connectors.base.SourceConnector`. Used by the live stream /
seed paths that want sales-only ingress; the combined
:class:`~ingestion.connectors.base.SimulatorConnector` is preferred when both
domains are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator

from ingestion.connectors.base import SimulatorConnector, SourceRecord
from ingestion.simulator.anomalies import AnomalyState
from ingestion.simulator.generator import SimConfig


@dataclass
class SalesSimConnector:
    """Sales-only view of the simulator (delegates to :class:`SimulatorConnector`)."""

    cfg: SimConfig = field(default_factory=SimConfig)
    start: datetime | None = None
    n_days: int = 1
    anomalies: list[AnomalyState] = field(default_factory=list)
    record_delay: float = 0.0

    def iter_records(self) -> AsyncIterator[SourceRecord]:
        return SimulatorConnector(
            cfg=self.cfg,
            start=self.start,
            n_days=self.n_days,
            anomalies=self.anomalies,
            domains=("sales",),
            record_delay=self.record_delay,
        ).iter_records()
