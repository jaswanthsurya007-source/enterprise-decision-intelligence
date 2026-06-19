"""Ops connector — a thin ``domain="ops"`` adapter over the simulator.

Exposes only the operations-log stream of the deterministic generator as a
:class:`~ingestion.connectors.base.SourceConnector`. The ops stream is where an
``outage`` anomaly manifests (latency_p95 / error_rate blow-out), correlated in
time with the dependent sales-revenue drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator

from ingestion.connectors.base import SimulatorConnector, SourceRecord
from ingestion.simulator.anomalies import AnomalyState
from ingestion.simulator.generator import SimConfig


@dataclass
class OpsSimConnector:
    """Ops-only view of the simulator (delegates to :class:`SimulatorConnector`)."""

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
            domains=("ops",),
            record_delay=self.record_delay,
        ).iter_records()
