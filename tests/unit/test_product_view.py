"""Shared ProductView schema and reference-integrity contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.product import ProductService
from aletheia_lab.product.view import ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"
_PACKAGED_FIXTURE = (
    Path(__file__).parents[2] / "src" / "aletheia_lab" / "product" / "synthetic_p6_view.json"
)
_FIXTURE_SHA256 = "3baa872c280d4d773bc658c0fc93a101130e5293f851e642d5fd9168425bcb8b"


def _payload() -> dict[str, object]:
    value = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(payload: dict[str, object]) -> ProductView:
    return ProductView.model_validate_json(json.dumps(payload))


def test_shared_fixture_is_exact_and_round_trips_through_product_view() -> None:
    raw = _FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    assert _PACKAGED_FIXTURE.read_bytes() == raw

    view = ProductView.model_validate_json(raw)

    assert view.model_dump(mode="json") == _payload()
    assert ProductView.model_validate_json(view.model_dump_json()) == view
    assert view.runtime.external_call is False
    assert view.conversation.turns[0].result_id == view.result.id
    assert view.conversation.turns[0].runtime == view.runtime
    assert view.result.denominators.independent_families is None
    assert view.claims[0].claim_type == "evidence_statement"
    assert view.snapshot.metric_changes[0].evidence_id == view.evidence[0].id
    assert view.snapshot.metric_changes[0].delta == -0.08
    assert view.evidence[0].reproduction_ref.record_kind == "snapshot_comparison"


def test_demo_view_returns_a_fresh_exact_fixture_after_restart(tmp_path: Path) -> None:
    expected = _payload()

    first = ProductService(tmp_path / "store").demo_view()
    first["demo_only"] = False
    second = ProductService(tmp_path / "store").demo_view()

    assert second == expected
    assert second["runtime"] == {
        "provider": "deterministic_mock",
        "model": "synthetic-demo-only",
        "external_call": False,
    }


def test_product_view_rejects_missing_extra_and_wrong_typed_fields() -> None:
    missing = _payload()
    missing.pop("runtime")
    with pytest.raises(ValidationError):
        _validate(missing)

    missing_turn_result = _payload()
    conversation = missing_turn_result["conversation"]
    assert isinstance(conversation, dict)
    turns = conversation["turns"]
    assert isinstance(turns, list)
    turns[0].pop("result_id")
    with pytest.raises(ValidationError):
        _validate(missing_turn_result)

    missing_claim_type = _payload()
    claims = missing_claim_type["claims"]
    assert isinstance(claims, list)
    claims[0].pop("claim_type")
    with pytest.raises(ValidationError):
        _validate(missing_claim_type)

    mismatched_turn_runtime = _payload()
    conversation = mismatched_turn_runtime["conversation"]
    assert isinstance(conversation, dict)
    turns = conversation["turns"]
    assert isinstance(turns, list)
    runtime = turns[0]["runtime"]
    assert isinstance(runtime, dict)
    runtime["model"] = "different-runtime"
    with pytest.raises(ValidationError, match="runtime"):
        _validate(mismatched_turn_runtime)

    extra = _payload()
    extra["evaluator_truth"] = "hidden"
    with pytest.raises(ValidationError):
        _validate(extra)

    wrong_type = _payload()
    result = wrong_type["result"]
    assert isinstance(result, dict)
    result["status"] = 1
    with pytest.raises(ValidationError):
        _validate(wrong_type)


def test_product_view_rejects_duplicate_and_dangling_references() -> None:
    duplicate = _payload()
    evidence = duplicate["evidence"]
    assert isinstance(evidence, list)
    evidence.append(dict(evidence[0]))
    with pytest.raises(ValidationError, match="unique"):
        _validate(duplicate)

    dangling = _payload()
    claims = dangling["claims"]
    assert isinstance(claims, list)
    claims[0]["citation_ids"] = ["demo-evidence-missing"]
    with pytest.raises(ValidationError, match="unavailable evidence"):
        _validate(dangling)

    dangling_edge = _payload()
    graph = dangling_edge["graph"]
    assert isinstance(graph, dict)
    graph["edges"][0]["target"] = "demo-node-missing"
    with pytest.raises(ValidationError, match="dangling edge"):
        _validate(dangling_edge)


def test_product_view_rejects_hidden_causal_and_zero_for_unknown_family_count() -> None:
    hidden = _payload()
    evidence = hidden["evidence"]
    assert isinstance(evidence, list)
    evidence[0]["visibility"] = "evaluator"
    with pytest.raises(ValidationError):
        _validate(hidden)

    causal = _payload()
    graph = causal["graph"]
    assert isinstance(graph, dict)
    graph["edges"][0]["kind"] = "CAUSES"
    with pytest.raises(ValidationError, match="causal"):
        _validate(causal)

    fabricated_zero = _payload()
    result = fabricated_zero["result"]
    assert isinstance(result, dict)
    denominators = result["denominators"]
    assert isinstance(denominators, dict)
    denominators["independent_families"] = 0
    with pytest.raises(ValidationError):
        _validate(fabricated_zero)


def test_product_view_rejects_incoherent_metric_projection() -> None:
    wrong_delta = _payload()
    snapshot = wrong_delta["snapshot"]
    assert isinstance(snapshot, dict)
    changes = snapshot["metric_changes"]
    assert isinstance(changes, list)
    changes[0]["delta"] = -0.081
    with pytest.raises(ValidationError, match="delta"):
        _validate(wrong_delta)

    wrong_snapshot = _payload()
    snapshot = wrong_snapshot["snapshot"]
    assert isinstance(snapshot, dict)
    changes = snapshot["metric_changes"]
    assert isinstance(changes, list)
    changes[0]["before"]["snapshot_id"] = "demo-snapshot-other"
    with pytest.raises(ValidationError, match="before observation"):
        _validate(wrong_snapshot)

    dangling_evidence = _payload()
    snapshot = dangling_evidence["snapshot"]
    assert isinstance(snapshot, dict)
    changes = snapshot["metric_changes"]
    assert isinstance(changes, list)
    changes[0]["evidence_id"] = "demo-evidence-missing"
    with pytest.raises(ValidationError, match="unavailable evidence"):
        _validate(dangling_evidence)

    wrong_reproduction_kind = _payload()
    evidence = wrong_reproduction_kind["evidence"]
    assert isinstance(evidence, list)
    evidence[0]["reproduction_ref"]["record_kind"] = "snapshot"
    with pytest.raises(ValidationError, match="reproduction record kind"):
        _validate(wrong_reproduction_kind)


def test_technical_failure_keeps_only_input_evidence_and_observation_graph() -> None:
    payload = _payload()
    result = payload["result"]
    assert isinstance(result, dict)
    result["status"] = "technical_failure"
    result["disposition"] = None
    denominators = result["denominators"]
    assert isinstance(denominators, dict)
    denominators["claims"] = 0
    payload["claims"] = []
    graph = payload["graph"]
    assert isinstance(graph, dict)
    nodes = graph["nodes"]
    edges = graph["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    graph["nodes"] = [node for node in nodes if node["kind"] in {"Snapshot", "EvidenceItem"}]
    graph["edges"] = [edge for edge in edges if edge["kind"] == "OBSERVED_IN"]

    failure = _validate(payload)

    assert failure.result.disposition is None
    assert failure.claims == ()
    assert failure.snapshot.metric_changes
    assert failure.evidence

    inconsistent = json.loads(json.dumps(payload))
    inconsistent["result"]["disposition"] = "abstain"
    with pytest.raises(ValidationError, match="technical failure"):
        _validate(inconsistent)
