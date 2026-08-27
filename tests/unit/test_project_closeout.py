"""P3 closeout receipt census, projection and identity contracts."""

from __future__ import annotations

import pytest

from aletheia_lab.project import (
    P3_CLOSEOUT_SCHEMA_VERSION,
    PROJECT_STORE_SCHEMA_VERSION,
    ProjectCloseoutReceipt,
    canonical_project_sha256,
)

_PROJECT = "p3-project-" + "1" * 64


def _payload() -> dict[str, object]:
    types = (
        "project_bundle",
        "project_bundle",
        "snapshot",
        "snapshot",
        "snapshot_comparison",
        "regression_event",
        "evidence_bundle",
        "lineage_graph",
    )
    roles = (
        "before_bundle",
        "after_bundle",
        "before_snapshot",
        "after_snapshot",
        "comparison",
        "event",
        "evidence_bundle",
        "lineage_graph",
    )
    records = tuple(
        {
            "role": role,
            "record_id": f"record-{index}",
            "record_type": record_type,
            "schema_version": f"schema/{index}",
            "canonical_sha256": f"{index + 1:064x}",
            "object_sha256": f"{index + 11:064x}",
        }
        for index, (role, record_type) in enumerate(zip(roles, types, strict=True))
    )
    migrations = tuple(
        {"version": index, "migration_sha256": f"{index + 20:064x}"}
        for index in range(1, PROJECT_STORE_SCHEMA_VERSION + 1)
    )
    projections = tuple(
        {
            "visibility": visibility,
            "graph_id": f"p3-lineage-graph-{index + 50:064x}",
            "graph_sha256": f"{index + 30:064x}",
            "table_sha256": f"{index + 40:064x}",
            "node_count": index + 1,
            "edge_count": index,
        }
        for index, visibility in enumerate(("public", "diagnosis", "evaluator"))
    )
    identity = {
        "schema_version": P3_CLOSEOUT_SCHEMA_VERSION,
        "project_id": _PROJECT,
        "store_schema_version": PROJECT_STORE_SCHEMA_VERSION,
        "records": records,
        "migrations": migrations,
        "export_index_sha256": "f" * 64,
        "projections": projections,
        "causal_status": "unverified",
        "status": "p3_closeout_pass",
    }
    digest = canonical_project_sha256(identity)
    return {
        **identity,
        "closeout_id": f"p3-closeout-{digest}",
        "closeout_sha256": digest,
    }


def _rehash(payload: dict[str, object]) -> None:
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"closeout_id", "closeout_sha256"}
    }
    digest = canonical_project_sha256(identity)
    payload["closeout_id"] = f"p3-closeout-{digest}"
    payload["closeout_sha256"] = digest


def test_closeout_receipt_round_trips_canonical_generation() -> None:
    receipt = ProjectCloseoutReceipt.model_validate(_payload())

    assert receipt.status == "p3_closeout_pass"
    assert receipt.causal_status == "unverified"
    assert len(receipt.records) == 8
    assert tuple(value.visibility for value in receipt.projections) == (
        "public",
        "diagnosis",
        "evaluator",
    )
    assert ProjectCloseoutReceipt.model_validate_json(receipt.model_dump_json()) == receipt


def test_closeout_roles_may_bind_one_intentionally_reused_bundle() -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, tuple)
    records[1]["record_id"] = records[0]["record_id"]
    records[1]["canonical_sha256"] = records[0]["canonical_sha256"]
    records[1]["object_sha256"] = records[0]["object_sha256"]
    _rehash(payload)

    receipt = ProjectCloseoutReceipt.model_validate(payload)

    assert receipt.records[0].record_id == receipt.records[1].record_id


@pytest.mark.parametrize("field", ("closeout_id", "closeout_sha256"))
def test_closeout_receipt_rejects_forged_identity(field: str) -> None:
    payload = _payload()
    payload[field] = "p3-closeout-" + "0" * 64 if field == "closeout_id" else "0" * 64

    with pytest.raises(ValueError, match="identity"):
        ProjectCloseoutReceipt.model_validate(payload)


def test_closeout_receipt_rejects_missing_generation_member() -> None:
    payload = _payload()
    payload["records"] = payload["records"][:-1]  # type: ignore[index]

    with pytest.raises(ValueError, match="eight unique"):
        ProjectCloseoutReceipt.model_validate(payload)


def test_closeout_receipt_rejects_nonmonotonic_visibility_projection() -> None:
    payload = _payload()
    projections = payload["projections"]
    assert isinstance(projections, tuple)
    projections[0]["node_count"] = 9

    with pytest.raises(ValueError, match="not monotonic"):
        ProjectCloseoutReceipt.model_validate(payload)


def test_closeout_receipt_rejects_incomplete_migration_history() -> None:
    payload = _payload()
    payload["migrations"] = ()

    with pytest.raises(ValueError, match="migration census"):
        ProjectCloseoutReceipt.model_validate(payload)
