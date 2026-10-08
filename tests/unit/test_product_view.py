"""Shared ProductView schema and reference-integrity contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.product import ProductService
from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"
_PACKAGED_FIXTURE = (
    Path(__file__).parents[2] / "src" / "aletheia_lab" / "product" / "synthetic_p6_view.json"
)
_FIXTURE_SHA256 = "0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2"


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
    assert view.conversation.turns[0].missing_evidence[-1] == COUNTERFACTUAL_NOT_AVAILABLE
    assert view.claims[0].missing_evidence == (COUNTERFACTUAL_NOT_AVAILABLE,)


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

    duplicate_identity = _payload()
    snapshot = duplicate_identity["snapshot"]
    assert isinstance(snapshot, dict)
    changes = snapshot["metric_changes"]
    assert isinstance(changes, list)
    copied_change = json.loads(json.dumps(changes[0]))
    copied_change["evidence_id"] = "demo-evidence-metric-duplicate-identity"
    changes.append(copied_change)
    with pytest.raises(ValidationError, match="unique run, metric and step identity"):
        _validate(duplicate_identity)


def test_product_view_requires_exact_graph_nodes_and_conclusion_edges() -> None:
    empty_graph = _payload()
    empty_graph["graph"] = {"nodes": [], "edges": []}
    with pytest.raises(ValidationError, match="exact required node sources"):
        _validate(empty_graph)

    missing_citation = _payload()
    missing_citation["graph"]["edges"] = [
        edge for edge in missing_citation["graph"]["edges"] if edge["kind"] != "CITES"
    ]
    with pytest.raises(ValidationError, match="citation edges"):
        _validate(missing_citation)

    wrong_disposition = _payload()
    disposition_edge = next(
        edge
        for edge in wrong_disposition["graph"]["edges"]
        if edge["kind"] == "ASSIGNED_DISPOSITION"
    )
    disposition_edge["source"] = "demo-node-evidence"
    with pytest.raises(ValidationError, match="disposition edges"):
        _validate(wrong_disposition)

    duplicate_observation = _payload()
    observed = next(
        edge for edge in duplicate_observation["graph"]["edges"] if edge["kind"] == "OBSERVED_IN"
    )
    copied_edge = dict(observed)
    copied_edge["id"] = "demo-edge-observed-duplicate"
    duplicate_observation["graph"]["edges"].append(copied_edge)
    with pytest.raises(ValidationError, match="semantically unique"):
        _validate(duplicate_observation)


def test_product_view_locks_counterfactual_marker_and_metric_evidence_parity() -> None:
    duplicate_marker = _payload()
    marker = "Counterfactual comparison: not_available"
    duplicate_marker["conversation"]["turns"][0]["missing_evidence"] = [marker, marker]
    with pytest.raises(ValidationError, match="at most once"):
        _validate(duplicate_marker)

    duplicate_claim_marker = _payload()
    duplicate_claim_marker["claims"][0]["missing_evidence"] = [marker, marker]
    with pytest.raises(ValidationError, match="at most once"):
        _validate(duplicate_claim_marker)

    marker_not_last = _payload()
    marker_not_last["conversation"]["turns"][0]["missing_evidence"] = [
        marker,
        "A real evidence request",
    ]
    with pytest.raises(ValidationError, match="must be last"):
        _validate(marker_not_last)

    marker_outside_missing = _payload()
    marker_outside_missing["result"]["summary"] = marker
    with pytest.raises(ValidationError, match="only in missing evidence"):
        _validate(marker_outside_missing)

    extra_metric_evidence = _payload()
    copied_evidence = dict(extra_metric_evidence["evidence"][0])
    copied_evidence["id"] = "demo-evidence-metric-extra"
    extra_metric_evidence["evidence"].append(copied_evidence)
    with pytest.raises(ValidationError, match="one-to-one parity"):
        _validate(extra_metric_evidence)


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
    payload["conversation"]["turns"][-1]["missing_evidence"] = []
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
    assert COUNTERFACTUAL_NOT_AVAILABLE not in failure.conversation.turns[-1].missing_evidence

    technical_with_marker = json.loads(json.dumps(payload))
    technical_with_marker["conversation"]["turns"][-1]["missing_evidence"] = [
        COUNTERFACTUAL_NOT_AVAILABLE
    ]
    with pytest.raises(ValidationError, match="technical failure"):
        _validate(technical_with_marker)

    inconsistent = json.loads(json.dumps(payload))
    inconsistent["result"]["disposition"] = "abstain"
    with pytest.raises(ValidationError, match="technical failure"):
        _validate(inconsistent)
