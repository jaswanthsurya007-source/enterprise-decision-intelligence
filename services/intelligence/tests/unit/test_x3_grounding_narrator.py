"""X3 unit tests — the grounding guarantee, embeddings stub, and narrator fallback.

These lock the X3 pin: the grounding verifier extracts every numeric token from a
narrative and rejects any number not in ``EvidenceBundle.allowed_numbers`` within
tolerance; on rejection / no-key the narrator emits the deterministic TEMPLATE
narrative (``narrative_model=None``). The Voyage embedder degrades to a deterministic
1024-dim L2-normalized stub with no key. All infra-free, no API keys.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from edis_contracts.findings import EvidenceBundle, EvidenceItem

from edis_intelligence.grounding.embeddings import (
    EMBEDDING_DIM,
    STUB_MODEL,
    StubEmbedder,
    make_embedder,
    stub_embedding,
)
from edis_intelligence.grounding.prompts import (
    NARRATION_SYSTEM_PROMPT,
    render_evidence_user_turn,
    system_blocks,
)
from edis_intelligence.rca.narrator import (
    FakeNarrator,
    GroundedNarrator,
    TemplateNarrator,
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
                summary="revenue (region=EMEA, channel=web) was 61,000 vs an expected 95,000 (-35.8%).",
                values={
                    "observed_value": 61000.0,
                    "expected_value": 95000.0,
                    "deviation_pct": -35.8,
                },
            )
        ],
        allowed_numbers=allowed,
    )


# ---------------------------------------------------------------------------
# number extraction
# ---------------------------------------------------------------------------
def test_extract_numbers_handles_currency_percent_thousands_suffix() -> None:
    nums = extract_numbers(
        "Revenue fell to $61,000 (-35.8%), down from 95000, latency hit 1.4k ms."
    )
    assert 61000.0 in nums
    assert -35.8 in nums
    assert 95000.0 in nums
    assert 1400.0 in nums  # 1.4k rescaled


def test_extract_numbers_empty_when_no_numbers() -> None:
    assert extract_numbers("Revenue fell sharply, well outside the normal range.") == []


# ---------------------------------------------------------------------------
# grounding verifier
# ---------------------------------------------------------------------------
def test_verify_grounding_accepts_whitelisted_numbers() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    ok, unmatched = verify_grounding(
        "Revenue fell to $61,000 from $95,000, a -35.8% drop.", bundle.allowed_numbers
    )
    assert ok
    assert unmatched == []


def test_verify_grounding_rejects_invented_number() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    # 34000 (the raw delta) is NOT whitelisted -> must be rejected.
    ok, unmatched = verify_grounding("Revenue fell by $34,000 to $61,000.", bundle.allowed_numbers)
    assert not ok
    assert 34000.0 in unmatched


def test_verify_grounding_no_numbers_is_grounded() -> None:
    ok, unmatched = verify_grounding("Revenue dropped sharply.", [1.0, 2.0])
    assert ok and unmatched == []


def test_verify_grounding_tolerance_matches_rounded() -> None:
    # narrative rounds 60999.5 -> 61000; within 2% rel tol of the allowed 60999.5
    ok, _ = verify_grounding("about $61,000", [60999.5], rel_tol=0.02)
    assert ok


# ---------------------------------------------------------------------------
# template narrative + narrators
# ---------------------------------------------------------------------------
def test_template_narrative_is_grounded_by_construction() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8, 35.8])
    text = render_template_narrative(bundle)
    assert text
    ok, unmatched = verify_grounding(text, bundle.allowed_numbers)
    assert ok, unmatched


@pytest.mark.asyncio
async def test_grounded_narrator_no_client_uses_template() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8, 35.8])
    res = await GroundedNarrator(client=None).narrate(bundle)
    assert res.source == "template"
    assert res.narrative_model is None
    assert res.reason == "no_api_key"
    assert res.narrative


@pytest.mark.asyncio
async def test_template_narrator_always_template() -> None:
    bundle = _bundle([61000.0, 95000.0])
    res = await TemplateNarrator().narrate(bundle)
    assert res.source == "template" and res.narrative_model is None


@pytest.mark.asyncio
async def test_fake_narrator_grounded_text_passes_as_llm() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    res = await FakeNarrator("Revenue fell to $61,000 from $95,000 (-35.8%).").narrate(bundle)
    assert res.source == "llm"
    assert res.narrative_model == "fake-narrator"


@pytest.mark.asyncio
async def test_fake_narrator_ungrounded_text_falls_back_to_template() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    # 999 is not whitelisted -> guard rejects -> template fallback, model None.
    res = await FakeNarrator("Revenue fell to $61,000; 999 customers churned.").narrate(bundle)
    assert res.source == "template"
    assert res.narrative_model is None
    assert 999.0 in res.unmatched_numbers


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------
def test_system_blocks_cache_control_on_last_block() -> None:
    blocks = system_blocks()
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert (
        "never invent" in NARRATION_SYSTEM_PROMPT.lower()
        or "must not invent" in NARRATION_SYSTEM_PROMPT.lower()
    )


def test_render_evidence_user_turn_lists_allowed_numbers() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    text = render_evidence_user_turn(bundle)
    assert "ALLOWED NUMBERS" in text
    assert "61000" in text
    assert "95000" in text


def test_render_evidence_user_turn_is_deterministic() -> None:
    bundle = _bundle([61000.0, 95000.0, -35.8])
    assert render_evidence_user_turn(bundle) == render_evidence_user_turn(bundle)


# ---------------------------------------------------------------------------
# embeddings stub
# ---------------------------------------------------------------------------
def test_stub_embedding_is_deterministic_unit_vector() -> None:
    v1 = stub_embedding("revenue dropped in EMEA web")
    v2 = stub_embedding("revenue dropped in EMEA web")
    assert v1 == v2
    assert len(v1) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


def test_stub_embedding_empty_text_is_zero_vector() -> None:
    v = stub_embedding("")
    assert len(v) == EMBEDDING_DIM
    assert all(x == 0.0 for x in v)


def test_stub_embedding_distinguishes_texts() -> None:
    a = stub_embedding("revenue dropped in EMEA")
    b = stub_embedding("latency spiked in checkout-api")
    assert a != b


def test_make_embedder_without_key_is_stub() -> None:
    class _S:
        voyage_api_key = None

    emb = make_embedder(_S(), dim=EMBEDDING_DIM)
    assert isinstance(emb, StubEmbedder)
    assert emb.model == STUB_MODEL
    vec = emb.embed("hello world", input_type="document")
    assert len(vec) == EMBEDDING_DIM
