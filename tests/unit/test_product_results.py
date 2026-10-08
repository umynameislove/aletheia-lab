"""Immutable ProductView result persistence and scope contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import (
    ProductLifecycleStore,
    build_product_lifecycle_record,
)
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.results import ProductResultEnvelope, persist_product_result
from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"
_PROJECT_ID = "p3-project-" + "1" * 64
_FOREIGN_PROJECT_ID = "p3-project-" + "9" * 64
_SNAPSHOT_ID = "p3-snapshot-" + "2" * 64
_FOREIGN_SNAPSHOT_ID = "p3-snapshot-" + "8" * 64
_BASELINE_ID = "p3-snapshot-" + "3" * 64
_PENDING_RESULT_ID = "p6-result-" + "0" * 64


def _view(*, project_id: str = _PROJECT_ID) -> ProductView:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["demo_only"] = False
    payload["project"]["id"] = project_id
    payload["project"]["display_name"] = "Imported project"
    payload["snapshot"]["id"] = _SNAPSHOT_ID
    payload["snapshot"]["baseline_id"] = _BASELINE_ID
    for change in payload["snapshot"]["metric_changes"]:
        if change["before"] is not None:
            change["before"]["snapshot_id"] = _BASELINE_ID
        if change["after"] is not None:
            change["after"]["snapshot_id"] = _SNAPSHOT_ID
    payload["result"]["id"] = _PENDING_RESULT_ID
    payload["conversation"]["turns"][0]["snapshot_id"] = _SNAPSHOT_ID
    payload["conversation"]["turns"][0]["result_id"] = _PENDING_RESULT_ID
    for node in payload["graph"]["nodes"]:
        if node["kind"] == "Snapshot":
            node["source_id"] = _SNAPSHOT_ID
        elif node["kind"] == "Disposition":
            node["source_id"] = _PENDING_RESULT_ID
    return ProductView.model_validate_json(json.dumps(payload))


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _technical_failure_view() -> ProductView:
    payload = _view().model_dump(mode="json")
    payload["result"]["status"] = "technical_failure"
    payload["result"]["disposition"] = None
    payload["result"]["denominators"]["claims"] = 0
    payload["claims"] = []
    payload["conversation"]["turns"][-1]["missing_evidence"] = []
    payload["graph"]["nodes"] = [
        node for node in payload["graph"]["nodes"] if node["kind"] in {"Snapshot", "EvidenceItem"}
    ]
    payload["graph"]["edges"] = [
        edge for edge in payload["graph"]["edges"] if edge["kind"] == "OBSERVED_IN"
    ]
    return ProductView.model_validate_json(json.dumps(payload))


def _derived_view(parent: ProductView) -> ProductView:
    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="claim",
        selection_id=parent.claims[0].id,
        question="What remains uncertain?",
    )
    return build_product_follow_up_view(parent, selection)


def test_result_persists_idempotently_and_reloads_byte_stably(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)

    first = persist_product_result(store_root, _view())
    second = persist_product_result(store_root, _view())
    reloaded = ProductService(store_root).view(first.result.id)

    assert first == second
    assert first.result.id.startswith("p6-result-")
    assert first.result.id != _PENDING_RESULT_ID
    assert _canonical_bytes(reloaded) == _canonical_bytes(first.model_dump(mode="json"))


def test_derived_result_persists_parent_scope_without_mutating_parent(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    parent = persist_product_result(store_root, _view())
    child_input = _derived_view(parent)

    first = persist_product_result(
        store_root,
        child_input,
        parent_result_id=parent.result.id,
    )
    second = persist_product_result(
        store_root,
        child_input,
        parent_result_id=parent.result.id,
    )

    assert first == second
    assert first.result.id != parent.result.id
    assert first.conversation.turns[:-1] == parent.conversation.turns
    assert ProductService(store_root).view(parent.result.id) == parent.model_dump(mode="json")
    assert ProductService(store_root).view(first.result.id) == first.model_dump(mode="json")
    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get(first.result.id, expected_kind="result")
    assert record.parent_result_id == parent.result.id


def test_scoped_follow_up_persists_idempotently_with_immutable_parent(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    parent = persist_product_result(store_root, _view())
    parent_bytes = _canonical_bytes(parent.model_dump(mode="json"))
    service = ProductService(store_root)

    first = ProductView.model_validate_json(
        json.dumps(
            service.follow_up(
                parent.result.id,
                "claim",
                parent.claims[0].id,
                "What remains uncertain about this exact claim?",
            )
        )
    )
    second = ProductView.model_validate_json(
        json.dumps(
            service.follow_up(
                parent.result.id,
                "claim",
                parent.claims[0].id,
                "What remains uncertain about this exact claim?",
            )
        )
    )

    assert first == second
    assert first.result.id != parent.result.id
    assert first.conversation.turns[:-1] == parent.conversation.turns
    assert first.evidence == parent.evidence
    assert _canonical_bytes(ProductService(store_root).view(parent.result.id)) == parent_bytes
    assert ProductService(store_root).view(first.result.id) == first.model_dump(mode="json")
    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get(first.result.id, expected_kind="result")
    assert record.parent_result_id == parent.result.id


def test_derived_result_rejects_mutated_parent_history(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    parent = persist_product_result(store_root, _view())
    payload = _derived_view(parent).model_dump(mode="json")
    payload["conversation"]["turns"][0]["answer"] = "Mutated prior answer"
    child = ProductView.model_validate_json(json.dumps(payload))

    with pytest.raises(ProductError) as captured:
        persist_product_result(
            store_root,
            child,
            parent_result_id=parent.result.id,
        )

    assert captured.value.code == "result_parent_invalid"
    assert parent.result.id not in captured.value.safe_message
    assert str(store_root) not in captured.value.safe_message


def test_derived_result_rejects_runtime_change_and_missing_new_claim(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    parent = persist_product_result(store_root, _view())

    runtime_payload = _derived_view(parent).model_dump(mode="json")
    runtime_payload["runtime"]["model"] = "changed-runtime"
    runtime_payload["conversation"]["turns"][-1]["runtime"] = runtime_payload["runtime"]
    changed_runtime = ProductView.model_validate_json(json.dumps(runtime_payload))
    with pytest.raises(ProductError) as runtime_error:
        persist_product_result(
            store_root,
            changed_runtime,
            parent_result_id=parent.result.id,
        )
    assert runtime_error.value.code == "result_parent_invalid"

    claim_payload = _derived_view(parent).model_dump(mode="json")
    new_claim_id = claim_payload["claims"].pop()["id"]
    claim_payload["result"]["denominators"]["claims"] -= 1
    removed_node_ids = {
        node["id"]
        for node in claim_payload["graph"]["nodes"]
        if node["kind"] == "AtomicClaim" and node["source_id"] == new_claim_id
    }
    claim_payload["graph"]["nodes"] = [
        node for node in claim_payload["graph"]["nodes"] if node["id"] not in removed_node_ids
    ]
    claim_payload["graph"]["edges"] = [
        edge
        for edge in claim_payload["graph"]["edges"]
        if edge["source"] not in removed_node_ids and edge["target"] not in removed_node_ids
    ]
    missing_claim = ProductView.model_validate_json(json.dumps(claim_payload))
    with pytest.raises(ProductError) as claim_error:
        persist_product_result(
            store_root,
            missing_claim,
            parent_result_id=parent.result.id,
        )
    assert claim_error.value.code == "result_parent_invalid"


def test_derived_result_rejects_technical_failure_promotion(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    parent = persist_product_result(store_root, _technical_failure_view())
    child_payload = _view().model_dump(mode="json")
    child_payload["project"] = parent.project.model_dump(mode="json")
    child_payload["snapshot"] = parent.snapshot.model_dump(mode="json")
    child_payload["runtime"] = parent.runtime.model_dump(mode="json")
    child_payload["evidence"] = [item.model_dump(mode="json") for item in parent.evidence]
    child_payload["conversation"]["id"] = parent.conversation.id
    child_payload["conversation"]["turns"] = [
        *(turn.model_dump(mode="json") for turn in parent.conversation.turns),
        child_payload["conversation"]["turns"][-1],
    ]
    child_payload["conversation"]["turns"][-1]["snapshot_id"] = parent.snapshot.id
    child_payload["conversation"]["turns"][-1]["runtime"] = parent.runtime.model_dump(mode="json")
    child_payload["conversation"]["turns"][-1]["id"] = "p6-turn-" + "a" * 64
    child_payload["claims"][0]["turn_id"] = child_payload["conversation"]["turns"][-1]["id"]
    child_payload["claims"][0]["citation_ids"] = [parent.evidence[0].id]
    child_payload["claims"][0]["counterevidence_ids"] = []
    child_claim = child_payload["claims"][0]
    child_payload["graph"] = build_product_graph(
        snapshot_id=parent.snapshot.id,
        evidence_ids=tuple(item.id for item in parent.evidence),
        claim_citations={child_claim["id"]: tuple(child_claim["citation_ids"])},
        disposition_id=_PENDING_RESULT_ID,
        result_scope_id=child_payload["conversation"]["turns"][-1]["id"],
    ).model_dump(mode="json")
    promoted = ProductView.model_validate_json(json.dumps(child_payload))

    with pytest.raises(ProductError) as captured:
        persist_product_result(
            store_root,
            promoted,
            parent_result_id=parent.result.id,
        )
    assert captured.value.code == "result_parent_invalid"


def test_technical_failure_persists_without_fabricating_a_disposition(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)

    persisted = persist_product_result(store_root, _technical_failure_view())
    reloaded = ProductService(store_root).view(persisted.result.id)

    assert reloaded == persisted.model_dump(mode="json")
    assert persisted.result.status == "technical_failure"
    assert persisted.result.disposition is None
    assert persisted.claims == ()
    assert persisted.snapshot.metric_changes
    assert {node.kind for node in persisted.graph.nodes} == {"Snapshot", "EvidenceItem"}
    assert COUNTERFACTUAL_NOT_AVAILABLE not in persisted.conversation.turns[-1].missing_evidence


def test_technical_failure_follow_up_stays_technical_and_reloads(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    service = ProductService(store_root)
    parent = persist_product_result(store_root, _technical_failure_view())
    evidence_node = next(node for node in parent.graph.nodes if node.kind == "EvidenceItem")

    child = service.follow_up(
        parent.result.id,
        "node",
        evidence_node.id,
        "Can this technical failure support a diagnosis?",
    )
    child_id = str(child["result"]["id"])

    assert child["result"]["status"] == "technical_failure"
    assert child["result"]["disposition"] is None
    assert child["claims"] == []
    assert (
        child["conversation"]["turns"][:-1]
        == parent.model_dump(mode="json")["conversation"]["turns"]
    )
    assert child["conversation"]["turns"][-1]["abstained"] is True
    assert COUNTERFACTUAL_NOT_AVAILABLE not in json.dumps(child)
    assert {node["kind"] for node in child["graph"]["nodes"]} <= {
        "Snapshot",
        "EvidenceItem",
    }
    assert {edge["kind"] for edge in child["graph"]["edges"]} <= {"OBSERVED_IN"}
    assert ProductService(store_root).view(child_id) == child
    assert ProductService(store_root).view(parent.result.id) == parent.model_dump(mode="json")
    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get(child_id, expected_kind="result")
    assert record.parent_result_id == parent.result.id


def test_result_rejects_demo_and_foreign_store_id(tmp_path: Path) -> None:
    local_store = tmp_path / "local-store"
    foreign_store = tmp_path / "foreign-store"
    ProductService(local_store)
    ProductService(foreign_store)
    demo = ProductView.model_validate_json(_FIXTURE.read_bytes())

    with pytest.raises(ProductError) as demo_error:
        persist_product_result(local_store, demo)
    assert demo_error.value.code == "result_invalid"

    foreign = persist_product_result(
        foreign_store,
        _view(project_id=_FOREIGN_PROJECT_ID),
    )
    with pytest.raises(ProductError) as missing:
        ProductService(local_store).view(foreign.result.id)

    assert missing.value.code == "record_not_found"
    assert foreign.result.id not in missing.value.safe_message
    assert str(foreign_store) not in missing.value.safe_message


def test_result_lookup_maps_persistent_tamper_to_safe_error(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    persisted = persist_product_result(store_root, _view())
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        connection.execute(
            "UPDATE product_records SET payload_json = ? WHERE record_id = ?",
            ('{"schema_version":"p6-result-envelope/v1","view":{}}', persisted.result.id),
        )

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).view(persisted.result.id)

    assert captured.value.code == "store_integrity_error"
    assert persisted.result.id not in captured.value.safe_message
    assert str(store_root) not in captured.value.safe_message


@pytest.mark.parametrize(
    ("record_project_id", "record_snapshot_id", "hidden_scope_id"),
    [
        (_FOREIGN_PROJECT_ID, _SNAPSHOT_ID, _FOREIGN_PROJECT_ID),
        (_PROJECT_ID, _FOREIGN_SNAPSHOT_ID, _FOREIGN_SNAPSHOT_ID),
    ],
)
def test_result_rejects_envelope_with_mismatched_scope(
    tmp_path: Path,
    record_project_id: str,
    record_snapshot_id: str,
    hidden_scope_id: str,
) -> None:
    store_root = tmp_path / "store"
    ProductService(store_root)
    envelope = ProductResultEnvelope(view=_view().model_dump(mode="json"))
    record = build_product_lifecycle_record(
        record_kind="result",
        project_id=record_project_id,
        snapshot_id=record_snapshot_id,
        payload=envelope.model_dump(mode="json"),
    )
    with ProductLifecycleStore(store_root) as lifecycle:
        lifecycle.put(record)

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).view(record.record_id)

    assert captured.value.code == "result_integrity_error"
    assert record.record_id not in captured.value.safe_message
    assert hidden_scope_id not in captured.value.safe_message
    assert str(store_root) not in captured.value.safe_message
