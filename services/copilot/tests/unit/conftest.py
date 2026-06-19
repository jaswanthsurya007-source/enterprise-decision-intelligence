"""Shared fixtures: a seeded in-memory data port + a wired tool registry (no infra/keys).

Seeds the ``revenue_drop_emea`` demo shape (tenant ``acme``) so the tool tests exercise
realistic, deterministic data: an EMEA-web revenue level-shift finding with latency/
error candidate causes, daily revenue points, and vector docs embedded with the SAME
deterministic stub the L3 corpus uses — so offline semantic_search actually ranks the
relevant doc first.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edis_copilot.retrieval.embedder import StubEmbedder, stub_embedding
from edis_copilot.retrieval.search import HybridSearcher
from edis_copilot.tools.base import InMemoryDataPort, ToolContext
from edis_copilot.tools.registry import default_registry
from cp_testkit import OTHER_TENANT, TENANT


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext.for_tenant(TENANT)


@pytest.fixture
def other_ctx() -> ToolContext:
    return ToolContext.for_tenant(OTHER_TENANT)


@pytest.fixture
def data() -> InMemoryDataPort:
    port = InMemoryDataPort()
    base = datetime(2026, 6, 12, tzinfo=timezone.utc)

    # EMEA web revenue: ~95k/day before the drop, ~61k/day after (the demo).
    for d in range(-3, 0):  # 3 days before
        port.add_metric_point(
            TENANT,
            "revenue",
            base + timedelta(days=d),
            95000.0,
            dimensions={"region": "EMEA", "channel": "web"},
            unit="USD",
        )
    for d in range(0, 5):  # 5 days of the drop
        port.add_metric_point(
            TENANT,
            "revenue",
            base + timedelta(days=d),
            61000.0,
            dimensions={"region": "EMEA", "channel": "web"},
            unit="USD",
        )
    # NA web revenue (unaffected) to prove dimension filtering + grouping.
    for d in range(-3, 5):
        port.add_metric_point(
            TENANT,
            "revenue",
            base + timedelta(days=d),
            120000.0,
            dimensions={"region": "NA", "channel": "web"},
            unit="USD",
        )
    # A different tenant's data — must never leak.
    port.add_metric_point(
        OTHER_TENANT,
        "revenue",
        base,
        999999.0,
        dimensions={"region": "EMEA", "channel": "web"},
        unit="USD",
    )

    finding = {
        "finding_id": "f-7a3",
        "tenant_id": TENANT,
        "kind": "level_shift",
        "metric_key": "revenue",
        "dimensions": {"region": "EMEA", "channel": "web"},
        "window_start": "2026-06-12T00:00:00+00:00",
        "window_end": "2026-06-18T23:59:59+00:00",
        "detector": "stl_seasonal",
        "detector_version": "1.0",
        "observed_value": 61000.0,
        "expected_value": 95000.0,
        "deviation": -34000.0,
        "deviation_pct": -35.8,
        "score": 5.8,
        "severity": 0.86,
        "confidence": 0.91,
        "business_impact_input": 0.78,
        "candidate_causes": [
            {
                "metric_key": "latency_p95",
                "dimensions": {"region": "EMEA", "service": "checkout-api"},
                "correlation": 0.94,
                "lag_minutes": 120,
                "contribution_pct": 71.0,
                "direction": "leading",
                "observed_delta": 1220.0,
            }
        ],
        "narrative": "EMEA web revenue fell sharply following a checkout-api latency spike.",
        "status": "open",
        "created_at": "2026-06-18T12:00:00+00:00",
    }
    port.add_finding(finding)
    # The other tenant has a finding too — must not surface for acme.
    port.add_finding({**finding, "finding_id": "f-other", "tenant_id": OTHER_TENANT})

    # Vector docs embedded with the SAME deterministic stub used at query time.
    rev_text = "EMEA web revenue dropped due to a checkout-api availability regression"
    port.add_vector_doc(
        TENANT,
        "finding",
        "f-7a3",
        stub_embedding(rev_text),
        payload={"finding_id": "f-7a3", "metric_key": "revenue"},
        numbers=[61000.0, 95000.0, -35.8],
        text=rev_text,
    )
    rec_text = "Mitigate checkout-api latency in EMEA; estimated recovery 170000 USD"
    port.add_vector_doc(
        TENANT,
        "recommendation",
        "r-91c",
        stub_embedding(rec_text),
        payload={"recommendation_id": "r-91c", "title": "Mitigate checkout-api latency"},
        numbers=[170000.0, 0.84],
        text=rec_text,
    )
    unrelated = "page views in APAC grew steadily over the quarter"
    port.add_vector_doc(
        TENANT,
        "finding",
        "f-apac",
        stub_embedding(unrelated),
        payload={"finding_id": "f-apac"},
        numbers=[],
        text=unrelated,
    )
    # Other-tenant vector doc — must never be retrieved for acme.
    port.add_vector_doc(
        OTHER_TENANT,
        "finding",
        "f-globex",
        stub_embedding(rev_text),
        payload={"finding_id": "f-globex"},
        numbers=[1.0],
        text=rev_text,
    )
    return port


@pytest.fixture
def registry(data: InMemoryDataPort):
    searcher = HybridSearcher(data, StubEmbedder())
    return default_registry(data=data, searcher=searcher)
