"""Golden-JSON schema-stability tests for the EDIS canonical contracts.

`libs/edis-contracts` is the single source of truth: every service imports it,
so a breaking schema change must fail loudly. This test serializes the JSON
Schema (``model_json_schema()``) of every key payload model to a committed
golden file under ``tests/golden/*.json`` on first run, and asserts byte-for-byte
equality thereafter. Any accidental field add/remove/rename or **type drift**
(e.g. the ``int``-vs-``str`` ``schema_version`` mismatch §4.3 eliminates) flips
the golden diff and fails CI.

Regenerating goldens is intentional and explicit -- never automatic in CI. To
accept a deliberate contract change, run::

    EDIS_UPDATE_GOLDEN=1 pytest libs/edis-contracts/tests/test_schema_stability.py

and commit the updated ``tests/golden/*.json`` files alongside the contract
change, so the diff is reviewable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from edis_contracts.canonical import (
    CanonicalCustomer,
    CanonicalOrder,
    CanonicalOrderLine,
    CanonicalProduct,
    CustomerActivity,
    MetricObservation,
    OpsEvent,
    SourceRef,
)
from edis_contracts.decisions import (
    ConfidenceScore,
    ImpactEstimate,
    OutcomeReport,
    Recommendation,
    RecommendationLifecycleEvent,
)
from edis_contracts.events import CanonicalEvent, LineageEvent, MetricPoint
from edis_contracts.findings import (
    CandidateCause,
    EvidenceBundle,
    EvidenceItem,
    Finding,
    Forecast,
)
from edis_contracts.governance import AuditEvent, Decision, Evidence
from edis_contracts.ingest import (
    CustomerPayloadV1,
    DLQRecord,
    IngestEnvelope,
    OpsPayloadV1,
    QuarantinedRecord,
    SalesPayloadV1,
)

GOLDEN_DIR = Path(__file__).parent / "golden"

#: ``True`` only when a human explicitly asks to (re)write goldens. CI never sets
#: this, so a drift surfaces as a failing assertion rather than a silent rewrite.
UPDATE_GOLDEN = os.environ.get("EDIS_UPDATE_GOLDEN") == "1"

# --- Key payload models named in §4.3 of ARCHITECTURE.md (the bus contracts) ---
# These are the wire payloads every service must agree on; they get the strictest
# coverage. Ordered for readability; the test is keyed by class name, not order.
PAYLOAD_MODELS: list[type[BaseModel]] = [
    IngestEnvelope,
    CanonicalEvent,
    MetricPoint,
    Finding,
    Forecast,
    Recommendation,
    RecommendationLifecycleEvent,
    OutcomeReport,
    AuditEvent,
    LineageEvent,
    Decision,
]

# --- Every other public contract model -- locked down too, so structural drift
# in the supporting types (sub-objects, dimensions, etc.) is caught as well. ---
SUPPORTING_MODELS: list[type[BaseModel]] = [
    # ingest
    SalesPayloadV1,
    OpsPayloadV1,
    CustomerPayloadV1,
    DLQRecord,
    QuarantinedRecord,
    # canonical
    SourceRef,
    CanonicalCustomer,
    CanonicalProduct,
    CanonicalOrder,
    CanonicalOrderLine,
    OpsEvent,
    CustomerActivity,
    MetricObservation,
    # findings
    CandidateCause,
    EvidenceItem,
    EvidenceBundle,
    # decisions
    ImpactEstimate,
    ConfidenceScore,
    # governance
    Evidence,
]

ALL_MODELS: list[type[BaseModel]] = PAYLOAD_MODELS + SUPPORTING_MODELS

#: Payload models that carry a top-level ``schema_version`` and must keep it an
#: ``int`` forever (a generic bus reader pulls it off any event without type
#: drift -- see §4.3 "schema_version is int = 1 on every payload, uniformly").
SCHEMA_VERSIONED_MODELS: list[type[BaseModel]] = [
    IngestEnvelope,
    CanonicalEvent,
    MetricPoint,
    Finding,
    Forecast,
    Recommendation,
    RecommendationLifecycleEvent,
    OutcomeReport,
    AuditEvent,
    LineageEvent,
    Decision,
    DLQRecord,
    QuarantinedRecord,
    EvidenceBundle,
    Evidence,
]


def _schema_json(model: type[BaseModel]) -> str:
    """Deterministic, diff-friendly JSON Schema string for a model.

    ``sort_keys=True`` makes the golden insensitive to dict iteration order so a
    pydantic/Python upgrade that merely reorders keys does not cause a false
    failure -- only genuine structural/type changes do.
    """
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _golden_path(model: type[BaseModel]) -> Path:
    return GOLDEN_DIR / f"{model.__name__}.json"


@pytest.fixture(scope="session", autouse=True)
def _ensure_golden_dir() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_schema_matches_golden(model: type[BaseModel]) -> None:
    """The model's JSON Schema must equal its committed golden file.

    On first run (or with ``EDIS_UPDATE_GOLDEN=1``) the golden is written and the
    case is reported as the bootstrap path; on every subsequent run it asserts
    equality, so any field/type drift fails the build.
    """
    current = _schema_json(model)
    path = _golden_path(model)

    if UPDATE_GOLDEN or not path.exists():
        path.write_text(current, encoding="utf-8")
        if not UPDATE_GOLDEN:
            # Bootstrap: golden did not exist yet. It is now written; commit it.
            pytest.skip(f"wrote initial golden for {model.__name__} -> commit it")
        return

    expected = path.read_text(encoding="utf-8")
    assert current == expected, (
        f"JSON Schema for {model.__name__} drifted from its golden file.\n"
        f"If this change is intentional, regenerate with "
        f"EDIS_UPDATE_GOLDEN=1 pytest and commit {path.name}."
    )


@pytest.mark.parametrize("model", SCHEMA_VERSIONED_MODELS, ids=lambda m: m.__name__)
def test_schema_version_is_integer(model: type[BaseModel]) -> None:
    """``schema_version`` must be an integer field on every versioned payload.

    This is the specific drift §4.3 calls out: an accidental ``int`` -> ``str``
    flip would let a generic reader silently mis-parse the version. We assert the
    JSON Schema type is exactly ``integer`` and the default is the int ``1``.
    """
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    assert "schema_version" in props, (
        f"{model.__name__} is registered as schema-versioned but has no " f"schema_version field"
    )

    field = props["schema_version"]
    assert field.get("type") == "integer", (
        f"{model.__name__}.schema_version must be type 'integer', got "
        f"{field.get('type')!r} -- int-vs-str drift detected"
    )

    default = field.get("default")
    assert default == 1 and isinstance(default, int) and not isinstance(default, bool), (
        f"{model.__name__}.schema_version default must be the int 1, got "
        f"{default!r} ({type(default).__name__})"
    )

    # schema_version must NOT be required: it always has the int default 1 so a
    # producer can omit it and a reader can always rely on it being present.
    assert "schema_version" not in schema.get(
        "required", []
    ), f"{model.__name__}.schema_version should have a default and not be required"


def test_all_payload_models_have_a_golden() -> None:
    """Every key payload model is covered by a golden file (no silent gaps)."""
    for model in PAYLOAD_MODELS:
        path = _golden_path(model)
        assert path.exists(), (
            f"missing golden for key payload {model.__name__}: {path} -- run the "
            f"suite once to bootstrap it, then commit tests/golden/"
        )


def test_no_orphan_golden_files() -> None:
    """Catch goldens left behind after a model is renamed/removed.

    Every ``*.json`` in tests/golden must correspond to a model currently under
    test, so a stale golden cannot mask a removed contract.
    """
    if not GOLDEN_DIR.exists():
        pytest.skip("golden dir not bootstrapped yet")
    known = {f"{m.__name__}.json" for m in ALL_MODELS}
    on_disk = {p.name for p in GOLDEN_DIR.glob("*.json")}
    orphans = on_disk - known
    assert not orphans, (
        f"orphan golden files (no matching model under test): {sorted(orphans)} -- "
        f"delete them or restore the model"
    )
