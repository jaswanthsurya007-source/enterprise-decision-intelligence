"""Per-tenant token + cost accounting for the copilot agent loop.

The agent loop must not run an unbounded number of Opus iterations or burn a tenant's
budget without limit. This module provides:

* :func:`count_request_tokens` — a thin wrapper over ``client.messages.count_tokens``
  (the verified accounting primitive) used to price a request *before* sending it, with
  an offline character-heuristic fallback so accounting works with NO key. Never raises
  into the request path — a failed count degrades to the heuristic.
* :class:`CostModel` — the published per-MTok input/output prices for the copilot models
  (opus-4-8: $5/$25; haiku-4-5: $1/$5 — from the verified model table) turned into a
  USD estimate from a usage dict.
* :class:`BudgetAccountant` — an in-process, per-tenant daily USD ledger. ``check``
  enforces the cap *before* an iteration (raising :class:`BudgetExceeded` so the loop
  degrades rather than overspends); ``record`` adds the realized usage after a call. The
  ledger is process-local and resets per UTC day; a future version swaps the in-memory
  store for Redis behind the same interface (the loop only sees ``check`` / ``record``).

All math is pure and offline-testable; only :func:`count_request_tokens` touches the SDK
(and only when a client is supplied), so the whole module imports with no key.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from edis_platform.logging import get_logger

from edis_copilot.llm.models import MODEL_HAIKU, MODEL_OPUS

_log = get_logger(__name__)

#: Published per-million-token prices (USD) from the verified model table.
#: (input, output) per 1e6 tokens. Cache reads/writes are not separately priced here —
#: this is a conservative ceiling for the per-tenant cap, not a billing system.
_PRICES: dict[str, tuple[float, float]] = {
    MODEL_OPUS: (5.0, 25.0),
    MODEL_HAIKU: (1.0, 5.0),
}
_DEFAULT_PRICE = (5.0, 25.0)  # fall back to opus pricing for unknown models

#: Offline heuristic: characters per token (matches the packer's coarse estimate).
_CHARS_PER_TOKEN = 4


class BudgetExceeded(Exception):
    """Raised when a tenant's daily cost cap would be exceeded by the next call.

    The agent loop catches this and degrades (stops iterating / uses the offline
    template) rather than overspending — it is never surfaced as a 5xx.
    """

    def __init__(self, tenant_id: str, spent_usd: float, cap_usd: float) -> None:
        self.tenant_id = tenant_id
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd
        super().__init__(
            f"tenant {tenant_id!r} daily copilot budget exhausted: "
            f"${spent_usd:.4f} of ${cap_usd:.2f}"
        )


class CostModel:
    """Turn an Anthropic usage dict into a USD estimate using the published prices."""

    @staticmethod
    def price_for(model: str) -> tuple[float, float]:
        """Return ``(input_per_mtok, output_per_mtok)`` USD for ``model``."""

        return _PRICES.get(model, _DEFAULT_PRICE)

    @classmethod
    def usd(cls, model: str, *, input_tokens: int, output_tokens: int) -> float:
        """Estimate the USD cost of ``input_tokens`` + ``output_tokens`` on ``model``."""

        in_price, out_price = cls.price_for(model)
        return (input_tokens / 1e6) * in_price + (output_tokens / 1e6) * out_price

    @classmethod
    def usd_from_usage(cls, model: str, usage: dict[str, int]) -> float:
        """Estimate USD from a usage dict (``input_tokens`` + ``output_tokens``).

        Cached-read tokens are counted at the input rate too (a conservative ceiling
        for the cap; cache reads are actually ~0.1x but we never want to *under*-charge
        the budget guard).
        """

        in_tok = int(usage.get("input_tokens", 0) or 0) + int(
            usage.get("cache_read_input_tokens", 0) or 0
        )
        out_tok = int(usage.get("output_tokens", 0) or 0)
        return cls.usd(model, input_tokens=in_tok, output_tokens=out_tok)


async def count_request_tokens(
    client: Any | None,
    *,
    model: str,
    system: list[dict] | str | None = None,
    tools: list[dict] | None = None,
    messages: list[dict],
) -> int:
    """Count input tokens for a request, using the SDK when a client is present.

    Uses ``client.messages.count_tokens`` (the verified accounting primitive — never
    ``tiktoken``). With no client (offline) or on any SDK error, degrades to a
    deterministic character heuristic so accounting always returns a number and never
    raises into the request path.
    """

    if client is not None:
        try:
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            if system is not None:
                kwargs["system"] = system
            if tools is not None:
                kwargs["tools"] = tools
            resp = await client.messages.count_tokens(**kwargs)
            return int(getattr(resp, "input_tokens", 0) or 0)
        except Exception as exc:  # noqa: BLE001 - accounting must never break a turn
            _log.warning(
                "count_tokens failed; using offline heuristic",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
    return _heuristic_tokens(system, tools, messages)


def _heuristic_tokens(
    system: list[dict] | str | None, tools: list[dict] | None, messages: list[dict]
) -> int:
    """Deterministic offline token estimate: total char length / chars-per-token."""

    import json

    chars = 0
    if isinstance(system, str):
        chars += len(system)
    elif system:
        chars += len(json.dumps(system, default=str))
    if tools:
        chars += len(json.dumps(tools, default=str))
    chars += len(json.dumps(messages, default=str))
    return max(1, (chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


@dataclass
class _DayLedger:
    """One UTC day's spend for one tenant."""

    day: date
    spent_usd: float = 0.0


@dataclass
class BudgetAccountant:
    """In-process, per-tenant daily USD ledger guarding the copilot cost cap.

    ``cap_usd`` is the per-tenant daily ceiling (``0`` disables the cap entirely — the
    bare app / tests can run unbounded). The ledger is keyed by ``tenant_id`` and resets
    when the UTC day rolls over. Process-local in the MVP; the interface
    (``check`` / ``record``) is the seam a Redis-backed ledger drops behind later.
    """

    cap_usd: float = 5.0
    _ledger: dict[str, _DayLedger] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _today(self) -> date:
        return datetime.now(timezone.utc).date()

    def _entry(self, tenant_id: str) -> _DayLedger:
        today = self._today()
        entry = self._ledger.get(tenant_id)
        if entry is None or entry.day != today:
            entry = _DayLedger(day=today)
            self._ledger[tenant_id] = entry
        return entry

    async def spent(self, tenant_id: str) -> float:
        """Return the tenant's USD spend so far today."""

        async with self._lock:
            return self._entry(tenant_id).spent_usd

    async def check(self, tenant_id: str, *, projected_usd: float = 0.0) -> None:
        """Raise :class:`BudgetExceeded` if today's spend (+ projection) tops the cap.

        Called before an Opus iteration with the projected cost of that call; ``0``
        just checks the running total. A ``cap_usd`` of ``0`` disables the guard.
        """

        if self.cap_usd <= 0:
            return
        async with self._lock:
            entry = self._entry(tenant_id)
            if entry.spent_usd + projected_usd > self.cap_usd:
                raise BudgetExceeded(tenant_id, entry.spent_usd, self.cap_usd)

    async def record(self, tenant_id: str, usd: float) -> float:
        """Add ``usd`` to the tenant's daily spend; return the new running total."""

        async with self._lock:
            entry = self._entry(tenant_id)
            entry.spent_usd += max(0.0, float(usd))
            return entry.spent_usd

    async def record_usage(self, tenant_id: str, model: str, usage: dict[str, int]) -> float:
        """Convenience: price a usage dict and record it. Returns the new daily total."""

        return await self.record(tenant_id, CostModel.usd_from_usage(model, usage))
