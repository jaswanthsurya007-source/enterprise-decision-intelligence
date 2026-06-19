"""Unit tests for the frozen registry, the packer, the LLM seam, and the app factory."""

from __future__ import annotations

import pytest

from edis_copilot.llm.client import has_anthropic_key, make_anthropic_client
from edis_copilot.llm.models import MODEL_HAIKU, MODEL_OPUS, opus_request_kwargs
from edis_copilot.llm.prompts import COPILOT_SYSTEM_PROMPT, system_blocks
from edis_copilot.retrieval.embedder import EMBEDDING_DIM, StubEmbedder, stub_embedding
from edis_copilot.retrieval.packer import estimate_tokens, pack_results
from edis_copilot.tools.base import Tool
from edis_copilot.tools.registry import FROZEN_TOOL_ORDER, ToolRegistry


def test_registry_frozen_order_is_deterministic(registry):
    assert registry.names == list(FROZEN_TOOL_ORDER)
    schemas = registry.anthropic_tools()
    assert [s["name"] for s in schemas] == list(FROZEN_TOOL_ORDER)
    # Rendering twice is byte-stable (cacheable prefix).
    assert registry.anthropic_tools() == schemas


def test_registry_rejects_tenant_id_in_schema():
    class LeakyTool(Tool):
        name = "leaky"
        description = "x"
        input_schema = {
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}},
        }

        async def run(self, ctx, **kwargs):  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(ValueError, match="tenant_id"):
        ToolRegistry([LeakyTool()])


def test_no_tool_schema_exposes_tenant(registry):
    for schema in registry.anthropic_tools():
        props = schema["input_schema"].get("properties", {})
        assert "tenant_id" not in props and "tenant" not in props


def test_packer_keeps_whole_rows_within_budget():
    rows = [{"i": i, "v": "x" * 40} for i in range(50)]
    packed = pack_results(rows, token_budget=100, chars_per_token=4)
    assert packed.kept < len(rows)
    assert packed.dropped == len(rows) - packed.kept
    assert packed.truncated is True
    # Kept rows are the original objects, never edited (no number could be mangled).
    assert packed.rows == rows[: packed.kept]


def test_packer_keeps_first_row_even_if_oversized():
    big = [{"v": "y" * 10_000}]
    packed = pack_results(big, token_budget=10, chars_per_token=4)
    assert packed.kept == 1 and packed.rows == big


def test_estimate_tokens_is_deterministic():
    obj = {"b": 2, "a": 1}
    assert estimate_tokens(obj) == estimate_tokens({"a": 1, "b": 2})  # sorted keys


def test_stub_embedding_is_deterministic_and_unit_norm():
    a = stub_embedding("revenue dropped in EMEA")
    b = stub_embedding("revenue dropped in EMEA")
    assert a == b and len(a) == EMBEDDING_DIM
    assert sum(x * x for x in a) == pytest.approx(1.0, abs=1e-9)


def test_stub_embedder_model_string():
    assert StubEmbedder().model == "stub-hash-1024"


def test_model_constants():
    assert MODEL_OPUS == "claude-opus-4-8"
    assert MODEL_HAIKU == "claude-haiku-4-5"
    kw = opus_request_kwargs()
    assert kw["model"] == MODEL_OPUS
    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["output_config"] == {"effort": "high"}
    # Verified: no sampling params / budget_tokens on opus-4-8.
    assert "temperature" not in kw and "budget_tokens" not in kw


def test_client_returns_none_without_key():
    class _S:
        anthropic_api_key = None

    assert make_anthropic_client(_S()) is None
    assert has_anthropic_key(_S()) is False


def test_system_prompt_is_frozen_and_cached():
    blocks = system_blocks()
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == COPILOT_SYSTEM_PROMPT
    # Constant: no interpolation markers that would break the cache prefix.
    for marker in ("{", "}", "now(", "uuid"):
        assert marker not in COPILOT_SYSTEM_PROMPT


def test_app_imports_and_serves_tools_with_no_infra():
    from fastapi.testclient import TestClient

    from edis_copilot.main import create_app

    app = create_app()
    client = TestClient(app)
    assert client.get("/v1/health").json()["status"] == "ok"
    body = client.get("/v1/tools").json()
    assert body["order"] == list(FROZEN_TOOL_ORDER)
    assert len(body["tools"]) == 4
