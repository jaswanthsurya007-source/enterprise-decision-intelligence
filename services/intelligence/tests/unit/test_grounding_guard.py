"""X4 THE ADVERSARIAL TEST -- the grounding guard rejects an invented number.

THE GROUNDING GUARANTEE (the X3 pin) is the load-bearing safety property of L3:
the narrator is given ONLY the EvidenceBundle (computed facts + an
``allowed_numbers`` whitelist). After generation, the grounding verifier extracts
every numeric token from the narrative and asserts each matches a value in
``allowed_numbers`` within a small relative tolerance. On ANY unmatched number ->
DISCARD the LLM narrative and emit the deterministic TEMPLATE
(``narrative_model=None``).

This module is the adversarial guard test: a faithful narrative passes (kept as
``source="llm"``); a narrative that smuggles in a single number NOT in the
whitelist is REJECTED and replaced by the template, with the offending number
surfaced for audit. We assert through BOTH the pure verifier and the full
:class:`FakeNarrator` path, and confirm the template that replaces it is itself
grounded by construction. Infra-free; no API keys.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from edis_contracts.findings import EvidenceBundle, EvidenceItem

from edis_intelligence.rca.narrator import (
    FakeNarrator,
    GroundedNarrator,
    extract_numbers,
    render_template_narrative,
    verify_grounding,
)


def _bundle(allowed: list[float], items: list[EvidenceItem] | None = None) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id=uuid4(),
        tenant_id="acme",
        finding_id=uuid4(),
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        items=items
        or [
            EvidenceItem(
                kind="metric_window",
                metric_key="revenue",
                dimensions={"region": "EMEA", "channel": "web"},
                summary="revenue (channel=web, region=EMEA) was 61,000 vs an expected 95,000 (-35.8%).",
                values={
                    "observed_value": 61000.0,
                    "expected_value": 95000.0,
                    "deviation_pct": -35.8,
                },
            ),
        ],
        allowed_numbers=allowed,
    )


# A faithful narrative: every figure ($61K, $95K, -35.8%) is whitelisted.
_FAITHFUL = "EMEA web revenue fell to $61,000 from an expected $95,000, a -35.8% drop."
# Adversarial: smuggles in 34,000 (the raw delta) which is NOT in the whitelist.
_ADVERSARIAL = "EMEA web revenue fell by $34,000 to $61,000, a -35.8% decline."


# ---------------------------------------------------------------------------
# pure verifier
# ---------------------------------------------------------------------------
def test_verifier_accepts_faithful_narrative() -> None:
    ok, unmatched = verify_grounding(_FAITHFUL, [61000.0, 95000.0, -35.8])
    assert ok
    assert unmatched == []


def test_verifier_rejects_invented_number() -> None:
    ok, unmatched = verify_grounding(_ADVERSARIAL, [61000.0, 95000.0, -35.8])
    assert not ok
    assert 34000.0 in unmatched
    # only the smuggled number is flagged; the grounded ones are not
    assert 61000.0 not in unmatched
    assert -35.8 not in unmatched


def test_verifier_rejects_even_a_single_off_by_more_than_tolerance() -> None:
    # 95001 is within 2% of allowed 95000 (kept); 200000 is far outside (rejected).
    ok, unmatched = verify_grounding("Numbers: 95,001 and 200,000.", [95000.0], rel_tol=0.02)
    assert not ok
    assert 200000.0 in unmatched
    assert 95001.0 not in unmatched


# ---------------------------------------------------------------------------
# full narrator path -- adversarial replacement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_faithful_narrative_kept_as_llm() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    res = await FakeNarrator(_FAITHFUL).narrate(bundle)
    assert res.source == "llm"
    assert res.narrative == _FAITHFUL
    assert res.narrative_model == "fake-narrator"


@pytest.mark.asyncio
async def test_adversarial_narrative_replaced_by_template() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    res = await FakeNarrator(_ADVERSARIAL).narrate(bundle)
    # discarded -> deterministic template, model None, offending number surfaced
    assert res.source == "template"
    assert res.narrative_model is None
    assert res.narrative != _ADVERSARIAL
    assert 34000.0 in res.unmatched_numbers
    # the replacement template is itself fully grounded
    ok, unmatched = verify_grounding(res.narrative, bundle.allowed_numbers)
    assert ok, unmatched


@pytest.mark.asyncio
async def test_grounded_narrator_with_fake_client_rejecting_transport() -> None:
    """Drive the production GroundedNarrator with a fake Claude transport (no network).

    The fake transport returns an ungrounded narration outcome; the GroundedNarrator
    must run the guard, reject it, and fall back to the template -- exercising the
    SAME code path the real ClaudeNarrationClient feeds, with nothing hitting the API.
    """

    from edis_intelligence.grounding.claude_client import NarrationOutcome

    class _FakeClient:
        """A ClaudeNarrationClient-shaped stub returning a fixed outcome."""

        def __init__(self, text: str) -> None:
            self._text = text

        async def narrate(self, bundle: EvidenceBundle) -> NarrationOutcome:
            return NarrationOutcome(ok=True, text=self._text, model="claude-opus-4-8")

    bundle = _bundle([61000.0, 95000.0, -35.8])

    # ungrounded -> template
    narr = GroundedNarrator(client=_FakeClient(_ADVERSARIAL))  # type: ignore[arg-type]
    res = await narr.narrate(bundle)
    assert res.source == "template"
    assert res.narrative_model is None
    assert 34000.0 in res.unmatched_numbers

    # grounded -> kept as llm with the real model name
    narr_ok = GroundedNarrator(client=_FakeClient(_FAITHFUL))  # type: ignore[arg-type]
    res_ok = await narr_ok.narrate(bundle)
    assert res_ok.source == "llm"
    assert res_ok.narrative_model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_refusal_outcome_falls_back_to_template() -> None:
    """A model refusal (ok=False) is discarded -> template, never blocks the finding."""

    from edis_intelligence.grounding.claude_client import NarrationOutcome

    class _RefusingClient:
        async def narrate(self, bundle: EvidenceBundle) -> NarrationOutcome:
            return NarrationOutcome(
                ok=False,
                text=None,
                model=None,
                stop_reason="refusal",
                refusal_category="other",
            )

    bundle = _bundle([61000.0, 95000.0, -35.8])
    res = await GroundedNarrator(client=_RefusingClient()).narrate(bundle)  # type: ignore[arg-type]
    assert res.source == "template"
    assert res.narrative_model is None
    assert res.reason and "refusal" in res.reason
    assert res.narrative  # a finding always carries SOME narrative


def test_number_extractor_does_not_misread_identifiers_or_units() -> None:
    # digits glued to identifiers ("p95", "checkout-api") are NOT numbers; "1440 min"
    # is 1440 (not 1.44e9); a range hyphen is a separator, not a sign.
    nums = extract_numbers(
        "latency_p95 on checkout-api hit 1,400 ms over 1440 min (101,917-106,378)."
    )
    assert 1400.0 in nums
    assert 1440.0 in nums
    assert 101917.0 in nums
    assert 106378.0 in nums
    # the "95" in p95 must not appear as a standalone number
    assert 95.0 not in nums
    # the range hyphen did not negate the second bound
    assert -106378.0 not in nums


def test_template_replacement_is_grounded_for_rich_bundle() -> None:
    items = [
        EvidenceItem(
            kind="metric_window",
            metric_key="revenue",
            dimensions={"region": "EMEA"},
            summary="revenue (region=EMEA) was 61,000 vs an expected 95,000 (-35.8%).",
            values={"observed_value": 61000.0, "expected_value": 95000.0, "deviation_pct": -35.8},
        ),
        EvidenceItem(
            kind="candidate_cause",
            metric_key="latency_p95",
            dimensions={"region": "EMEA", "service": "checkout-api"},
            summary="latency_p95 moved by 1220 and is leading the target (correlation -0.94, lag 120 min, ~71% of impact).",
            values={
                "correlation": -0.94,
                "lag_minutes": 120.0,
                "observed_delta": 1220.0,
                "contribution_pct": 71.0,
            },
        ),
    ]
    bundle = _bundle([61000.0, 95000.0, -35.8, 35.8, -0.94, 0.94, 120.0, 1220.0, 71.0], items=items)
    text = render_template_narrative(bundle)
    assert text
    ok, unmatched = verify_grounding(text, bundle.allowed_numbers)
    assert ok, unmatched
