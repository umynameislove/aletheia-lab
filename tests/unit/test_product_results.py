"""Immutable ProductView result persistence and scope contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import (
    ProductLifecycleStore,
    build_product_lifecycle_record,
)
from aletheia_lab.product.results import ProductResultEnvelope, persist_product_result
from aletheia_lab.product.view import ProductView

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
    payload["graph"]["nodes"] = [
        node for node in payload["graph"]["nodes"] if node["kind"] in {"Snapshot", "EvidenceItem"}
    ]
    payload["graph"]["edges"] = [
        edge for edge in payload["graph"]["edges"] if edge["kind"] == "OBSERVED_IN"
    ]
    return ProductView.model_validate_json(json.dumps(payload))


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
