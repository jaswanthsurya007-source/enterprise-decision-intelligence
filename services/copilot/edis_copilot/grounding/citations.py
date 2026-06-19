"""Citation + facts-used builder — link every grounded figure to its tool result.

The dashboard renders numbers as authoritative ONLY from the ``citations`` /
``facts_used`` provenance fields (never from free-text in the narrative — see
ARCHITECTURE §5.6). This module turns the per-turn list of
:class:`~app.tools.base.ToolResult` into:

* :class:`Citation` rows — one per tool result, with a stable ``marker`` (``[1]``,
  ``[2]`` …), the tool name, the human-readable ``source`` string the tool produced,
  and the flat list of numeric facts that result contributed to the grounding whitelist.
  This is exactly the "Citations" footer in the architecture's answer shape (§9): each
  marker maps a cited figure to the tool that returned it.
* ``facts_used`` — the deduplicated, ordered union of every allowed number across all
  tool results: the per-turn grounding whitelist the verifier checks against.

Pure and deterministic: markers are assigned in the (frozen) order the tools were
called this turn, so the citation footer is reproducible. No SDK, no key, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.tools.base import ToolResult


@dataclass(frozen=True)
class Citation:
    """One numbered citation linking a marker to the tool result it came from.

    ``marker`` is the in-text reference (``[1]``); ``tool`` is the tool name;
    ``source`` is the tool's own provenance string (e.g. ``"tool find_anomalies"``);
    ``numbers`` are the figures this result contributed (the facts the marker covers).
    """

    marker: str
    tool: str
    source: str
    numbers: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Render as the JSON the SSE ``citation`` frame / persisted answer carries."""

        return {
            "marker": self.marker,
            "tool": self.tool,
            "source": self.source,
            "numbers": list(self.numbers),
        }


@dataclass(frozen=True)
class CitationSet:
    """The full provenance of one answer: its numbered citations + the fact whitelist."""

    citations: tuple[Citation, ...] = ()
    facts_used: tuple[float, ...] = ()

    def markers_footer(self) -> str:
        """Render the ``Citations:`` footer line(s) matching the architecture answer shape."""

        if not self.citations:
            return ""
        lines = [f"{c.marker} {c.source}" for c in self.citations]
        return "Citations: " + "; ".join(lines)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Render the citations as a list of JSON dicts (for persistence / SSE)."""

        return [c.to_dict() for c in self.citations]


@dataclass
class _Acc:
    """Mutable accumulator for ``facts_used`` dedupe while preserving first-seen order."""

    seen: list[float] = field(default_factory=list)

    def add(self, n: float) -> None:
        # Dedupe within a tiny epsilon so 35.8 and 35.80000001 don't both appear.
        if not any(abs(n - s) <= 1e-9 for s in self.seen):
            self.seen.append(n)


def build_citations(results: list["ToolResult"]) -> CitationSet:
    """Build the numbered :class:`CitationSet` from this turn's tool results, in order.

    One :class:`Citation` per tool result (markers ``[1]``, ``[2]`` … in call order);
    empty results (no rows) are still cited so the trace is complete. ``facts_used`` is
    the ordered, de-duplicated union of every result's ``numbers`` — the grounding
    whitelist. Pure / deterministic.
    """

    citations: list[Citation] = []
    acc = _Acc()
    for i, r in enumerate(results, start=1):
        nums = tuple(float(n) for n in r.numbers)
        for n in nums:
            acc.add(n)
        citations.append(
            Citation(
                marker=f"[{i}]",
                tool=r.tool,
                source=r.citation or f"tool {r.tool}",
                numbers=nums,
            )
        )
    return CitationSet(citations=tuple(citations), facts_used=tuple(acc.seen))


def allowed_numbers(results: list["ToolResult"]) -> list[float]:
    """Flat list of every number any tool returned this turn (the grounding whitelist)."""

    out: list[float] = []
    for r in results:
        out.extend(float(n) for n in r.numbers)
    return out
