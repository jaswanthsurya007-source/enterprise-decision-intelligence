"""Dimensional contribution analysis + candidate-cause contribution weighting.

Two related but distinct "contribution" computations live here, both pure:

1. :func:`dimensional_contributions` — *which dimension cell drove a change in an
   aggregate metric?* Given the per-cell baseline and incident values of a metric
   broken out by some dimension (e.g. ``revenue`` by ``region``), it attributes the
   aggregate change to each cell. The contribution of a cell is its absolute change
   as a share of the total absolute change across cells, so the cell responsible for
   most of the move (EMEA, in the demo) gets the largest ``contribution_pct``. This
   is the "the drop is concentrated in EMEA-web" attribution.

2. :func:`contribution_pct_from_causes` — *how much does each ranked candidate cause
   explain?* Given the ranked :class:`~edis_contracts.findings.CandidateCause` list
   from :mod:`~edis_intelligence.rca.correlation`, it assigns each a
   ``contribution_pct`` by normalizing a per-cause weight (``|correlation|`` by
   default) to sum to 100% across the kept causes. This produces the
   ``contribution_pct`` figures the demo cites (latency ~71%, error ~22%).

Both are deterministic and round to a stable number of decimals so the resulting
percentages are byte-stable and land cleanly in ``EvidenceBundle.allowed_numbers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from edis_contracts.findings import CandidateCause

_PCT_DECIMALS = 1


@dataclass(frozen=True)
class DimensionContribution:
    """One dimension cell's share of an aggregate metric change."""

    dimension_value: str  # e.g. "EMEA" (the value of the split dimension)
    baseline: float
    observed: float
    delta: float  # observed - baseline
    contribution_pct: float  # share of total |delta| across cells, 0..100


def dimensional_contributions(
    cells: Mapping[str, tuple[float, float]],
    *,
    decimals: int = _PCT_DECIMALS,
) -> list[DimensionContribution]:
    """Attribute an aggregate metric change across dimension cells.

    ``cells`` maps a dimension value (e.g. ``"EMEA"``) to ``(baseline, observed)``.
    Each cell's ``delta = observed - baseline``; its ``contribution_pct`` is
    ``|delta| / sum(|delta|) * 100`` — its share of the *total movement*, so the cell
    that moved most dominates. Returned sorted by ``contribution_pct`` descending
    (ties broken by dimension value for determinism). If nothing moved, every
    contribution is ``0.0``.
    """

    deltas = {k: (obs - base) for k, (base, obs) in cells.items()}
    total_abs = sum(abs(d) for d in deltas.values())

    out: list[DimensionContribution] = []
    for value, (base, obs) in cells.items():
        d = obs - base
        pct = round((abs(d) / total_abs * 100.0) if total_abs > 0 else 0.0, decimals)
        out.append(
            DimensionContribution(
                dimension_value=value,
                baseline=float(base),
                observed=float(obs),
                delta=float(d),
                contribution_pct=float(pct),
            )
        )
    out.sort(key=lambda c: (-c.contribution_pct, c.dimension_value))
    return out


def contribution_pct_from_causes(
    causes: Sequence[CandidateCause],
    *,
    weight: str = "correlation",
    decimals: int = _PCT_DECIMALS,
) -> list[CandidateCause]:
    """Return ``causes`` with ``contribution_pct`` assigned (normalized to ~100%).

    Each cause's weight is its ``|correlation|`` (``weight="correlation"``) or
    ``|observed_delta|`` (``weight="delta"``); weights are normalized across the list
    so they sum to 100%. The largest-|correlation| leading cause (the latency spike in
    the demo) gets the biggest share. Deterministic; preserves input order (which is
    already the rank order from :func:`rank_candidate_causes`).

    Rounding can leave the sum a hair off 100; the largest contributor absorbs the
    rounding residual so the reported percentages sum to exactly 100.0.
    """

    if not causes:
        return []

    def _w(c: CandidateCause) -> float:
        if weight == "delta":
            return abs(c.observed_delta)
        return abs(c.correlation)

    weights = [_w(c) for c in causes]
    total = sum(weights)
    if total <= 0.0:
        # Degenerate: split evenly.
        even = round(100.0 / len(causes), decimals)
        pcts = [even] * len(causes)
    else:
        pcts = [round(w / total * 100.0, decimals) for w in weights]

    # Absorb rounding residual into the largest contributor.
    residual = round(100.0 - sum(pcts), decimals)
    if abs(residual) >= 10 ** (-decimals):
        idx_max = max(range(len(pcts)), key=lambda i: pcts[i])
        pcts[idx_max] = round(pcts[idx_max] + residual, decimals)

    return [
        CandidateCause(
            metric_key=c.metric_key,
            dimensions=dict(c.dimensions),
            correlation=c.correlation,
            lag_minutes=c.lag_minutes,
            contribution_pct=float(pcts[i]),
            direction=c.direction,
            observed_delta=c.observed_delta,
        )
        for i, c in enumerate(causes)
    ]
