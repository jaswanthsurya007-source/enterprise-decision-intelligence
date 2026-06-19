"""P3 — POST /v1/copilot/chat streams well-formed SSE frames (TestClient, offline).

Drives the real FastAPI app (offline wiring: no LLM, no infra, no keys) through
``StreamingResponse`` and asserts the response is a well-formed Server-Sent Events stream:
the right media type, ``event:``/``data:`` framing, a routing frame, citation frames, and
a terminal ``done`` frame carrying the grounded answer. The tenant is taken from the
gateway-injected header, never the body.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from edis_copilot.agent.limits import LoopLimits
from edis_copilot.budget.accounting import BudgetAccountant
from edis_copilot.main import create_app
from edis_copilot.persistence.repository import InMemoryAnswerRepository
from edis_copilot.retrieval.embedder import StubEmbedder
from edis_copilot.retrieval.search import HybridSearcher
from edis_copilot.tools.registry import default_registry
from cp_testkit import TENANT


def _offline_app(data):
    """Build the copilot app over the seeded port with NO llm (offline agent) + no infra."""

    searcher = HybridSearcher(data, StubEmbedder())
    wiring = {
        "registry": default_registry(data=data, searcher=searcher),
        "data_port": data,
        "embedder": StubEmbedder(),
        "answers": InMemoryAnswerRepository(),
        "budget": BudgetAccountant(cap_usd=0.0),
        "limits": LoopLimits(),
        "llm": None,  # offline deterministic agent — no key
        "audit": None,
    }
    return create_app(wiring=wiring)


def _gateway_headers(tenant=TENANT):
    return {"X-EDIS-Tenant": tenant, "X-EDIS-User": "alice", "X-EDIS-Roles": "analyst"}


def _parse_sse(body: str):
    """Parse an SSE body into a list of ``(event, payload_or_None)`` and the raw blocks."""

    frames, blocks = [], []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        blocks.append(block)
        if block.startswith(":"):  # comment / heartbeat
            continue
        event, data_lines = None, []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        payload = json.loads("\n".join(data_lines)) if data_lines else None
        frames.append((event, payload))
    return frames, blocks


def test_chat_streams_wellformed_sse(data):
    """The chat endpoint returns a text/event-stream of well-formed event/data frames."""

    client = TestClient(_offline_app(data))
    resp = client.post(
        "/v1/copilot/chat",
        headers=_gateway_headers(),
        json={"question": "Why did revenue drop last week?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("cache-control") == "no-cache"

    frames, blocks = _parse_sse(resp.text)
    # Every non-comment block is a well-formed SSE event: an ``event:`` line + >=1 ``data:``.
    for block in blocks:
        if block.startswith(":"):
            continue
        lines = block.split("\n")
        assert any(line.startswith("event: ") for line in lines)
        assert any(line.startswith("data: ") for line in lines)

    types = [t for t, _ in frames]
    assert "route" in types
    assert "citation" in types
    assert types[-1] == "done"  # the terminal frame is ``done``


def test_done_frame_carries_grounded_answer(data):
    """The terminal ``done`` frame carries the grounded, cited answer payload."""

    client = TestClient(_offline_app(data))
    resp = client.post(
        "/v1/copilot/chat",
        headers=_gateway_headers(),
        json={"question": "Why did revenue drop last week?"},
    )
    frames, _ = _parse_sse(resp.text)
    done = next(p for t, p in frames if t == "done")
    assert done["grounding_passed"] is True
    assert "[unverified]" not in done["answer"]
    assert any(abs(n - 61000.0) < 1.0 for n in done["facts_used"])
    assert done["citations"]


def test_chat_tenant_from_header_not_body(data):
    """A ``tenant_id`` in the body cannot override the gateway header; no cross-tenant leak."""

    client = TestClient(_offline_app(data))
    resp = client.post(
        "/v1/copilot/chat",
        headers=_gateway_headers(tenant=TENANT),
        json={"question": "why did revenue drop", "tenant_id": "globex"},
    )
    frames, _ = _parse_sse(resp.text)
    done = next(p for t, p in frames if t == "done")
    # globex's 999999 point must never appear for an acme-headed request.
    assert all(abs(n - 999999.0) > 1.0 for n in done["facts_used"])


def test_chat_requires_identity(data):
    """No gateway header and no bearer JWT -> 401 (tenant must be verified)."""

    client = TestClient(_offline_app(data))
    resp = client.post("/v1/copilot/chat", json={"question": "hi"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
