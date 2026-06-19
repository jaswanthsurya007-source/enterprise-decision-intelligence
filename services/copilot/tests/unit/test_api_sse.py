"""Chat SSE API + conversations + SSE framing (TestClient, offline, no infra/keys)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from edis_copilot.agent.limits import LoopLimits
from edis_copilot.api.sse import format_sse
from edis_copilot.budget.accounting import BudgetAccountant
from edis_copilot.main import create_app
from edis_copilot.persistence.repository import InMemoryAnswerRepository
from edis_copilot.retrieval.embedder import StubEmbedder
from edis_copilot.retrieval.search import HybridSearcher
from edis_copilot.tools.registry import default_registry
from cp_testkit import TENANT


def _app_with(data):
    """Build the copilot app over the seeded in-memory data port (offline: no llm)."""

    searcher = HybridSearcher(data, StubEmbedder())
    registry = default_registry(data=data, searcher=searcher)
    answers = InMemoryAnswerRepository()
    wiring = {
        "registry": registry,
        "data_port": data,
        "embedder": StubEmbedder(),
        "answers": answers,
        "budget": BudgetAccountant(cap_usd=0.0),
        "limits": LoopLimits(),
        "llm": None,  # offline deterministic agent
        "audit": None,
    }
    return create_app(wiring=wiring), answers


def _gateway_headers(tenant=TENANT, user="alice", roles="analyst"):
    """The trusted server-side identity headers the gateway injects."""

    return {"X-EDIS-Tenant": tenant, "X-EDIS-User": user, "X-EDIS-Roles": roles}


def _parse_sse(body: str):
    """Parse an SSE body into a list of (event, json-payload) tuples."""

    frames = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = None
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if data_lines:
            frames.append((event, json.loads("\n".join(data_lines))))
    return frames


def test_format_sse_encodes_event_and_data():
    out = format_sse({"type": "token", "text": "hi"}).decode()
    assert out.startswith("event: token\n")
    assert "data: " in out and out.endswith("\n\n")


def test_chat_streams_grounded_answer_offline(data):
    app, answers = _app_with(data)
    client = TestClient(app)
    resp = client.post(
        "/v1/copilot/chat",
        headers=_gateway_headers(),
        json={"question": "Why did revenue drop last week?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(resp.text)
    types = [t for t, _ in frames]
    assert "route" in types and "citation" in types and "done" in types
    done = next(p for t, p in frames if t == "done")
    assert done["grounding_passed"] is True
    assert any(abs(n - 61000.0) < 1.0 for n in done["facts_used"])  # grounded fact
    assert "[unverified]" not in done["answer"]
    # The turn was persisted (tenant-scoped, from the gateway header).
    assert len(answers._answers) == 1
    assert answers._answers[0]["tenant_id"] == TENANT


def test_chat_tenant_comes_from_header_not_body(data):
    app, _ = _app_with(data)
    client = TestClient(app)
    # A body cannot set tenant; even if it tried, the header is authoritative. The
    # 'globex' finding (999999) must never surface for the acme-headed request.
    resp = client.post(
        "/v1/copilot/chat",
        headers=_gateway_headers(tenant=TENANT),
        json={"question": "why did revenue drop", "tenant_id": "globex"},
    )
    done = next(p for t, p in _parse_sse(resp.text) if t == "done")
    assert all(abs(n - 999999.0) > 1.0 for n in done["facts_used"])


def test_chat_requires_identity():
    app, _ = _app_with(
        __import__("edis_copilot.tools.base", fromlist=["InMemoryDataPort"]).InMemoryDataPort()
    )
    client = TestClient(app)
    # No gateway header and no bearer JWT -> 401 from the platform auth dep.
    resp = client.post("/v1/copilot/chat", json={"question": "hi"})
    assert resp.status_code == 401


def test_conversations_is_tenant_scoped(data):
    app, answers = _app_with(data)
    answers.add_conversation(
        {
            "conversation_id": "c1",
            "tenant_id": TENANT,
            "user_id": "alice",
            "title": "EMEA drop",
            "created_at": "2026-06-18T00:00:00+00:00",
            "updated_at": "2026-06-18T00:00:00+00:00",
        }
    )
    answers.add_conversation(
        {
            "conversation_id": "c2",
            "tenant_id": "globex",
            "user_id": "bob",
            "title": "other",
            "created_at": "2026-06-18T00:00:00+00:00",
            "updated_at": "2026-06-18T00:00:00+00:00",
        }
    )
    client = TestClient(app)
    resp = client.get("/v1/copilot/conversations", headers=_gateway_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == TENANT
    ids = [c["conversation_id"] for c in body["conversations"]]
    assert ids == ["c1"]  # globex's c2 is never returned for acme


def test_health_and_tools_unauthenticated(data):
    app, _ = _app_with(data)
    client = TestClient(app)
    assert client.get("/v1/health").json()["status"] == "ok"
    assert client.get("/v1/tools").json()["order"][0] == "metric_lookup"
