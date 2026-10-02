"""Native relational producer and independent controlled visible references."""

from __future__ import annotations

from copy import deepcopy

import pytest

from aletheia_lab.evaluation.sqlite_evidence_source import (
    CONTROLS,
    EVALUATION_NAMESPACE,
    VIEWS,
    artifact_bytes,
    generate_source,
    producer_identity,
    visible_reference,
)
from aletheia_lab.project.identity import content_sha256


@pytest.fixture(scope="module")
def source():
    return generate_source(namespace="offline-source-fixture")


def test_native_census_replay_and_runtime_are_bound(source):
    assert source == generate_source(namespace="offline-source-fixture")
    assert source["producer"] == producer_identity()
    assert source["producer"]["name"] == "sqlite3 BLOB store"
    assert len(source["producer"]["python_wrapper_sha256"]) == 64
    assert source["producer"]["sqlite_source_id"]
    assert source["source_cluster_count"] == 1
    assert len(source["cases"]) == len(CONTROLS) == 5
    for case, (control, requested, installed) in zip(source["cases"], CONTROLS, strict=True):
        returned = bytes.fromhex(case["observation"]["returned_hex"])
        assert returned == artifact_bytes("offline-source-fixture", installed)
        assert case["observation"]["byte_count"] == len(returned)
        assert case["observation"]["consumed_sha256"] == content_sha256(returned)
        assert case["control"] == control
        assert case["reference"]["requested_sha256"] == content_sha256(
            artifact_bytes("offline-source-fixture", requested)
        )
        assert case["reference"]["status"] == (
            "no_binding_fault" if requested == installed else "binding_fault"
        )


def test_unchanged_native_statement_and_declaration_cannot_identify_returned_payload(source):
    healthy, replacement, restoration = source["cases"][:3]
    assert healthy["documents"][:3] == replacement["documents"][:3] == restoration["documents"][:3]
    assert healthy["observation"] == restoration["observation"]
    assert (
        healthy["observation"]["consumed_sha256"] != replacement["observation"]["consumed_sha256"]
    )
    for case in source["cases"]:
        trace = case["documents"][0]["text"]
        assert trace.startswith("SELECT ")
        assert "UPDATE" not in trace and "INSERT" not in trace
        assert case["observation"]["returned_hex"] not in trace
        assert "/Users/" not in str(case["documents"])


def test_reference_visible_frames_are_independent_and_have_full_locator_roles(source):
    expected_statuses = [
        "no_binding_fault",
        "binding_fault",
        "no_binding_fault",
        "no_binding_fault",
        "binding_fault",
    ]
    for case, status in zip(source["cases"], expected_statuses, strict=True):
        missing = visible_reference(case, "trace_only")
        full = visible_reference(case, "with_consumer")
        assert missing["resolution"] == {
            "state": "ambiguous",
            "compatible": ["binding_fault", "no_binding_fault"],
        }
        assert full["resolution"] == {"state": "identified", "compatible": [status]}
        assert missing["facts"] == full["facts"][:1]
        assert full["facts"][0]["pointer"] == "/documents/selection/selection/expected/sha256"
        assert full["facts"][1]["pointer"] == "/documents/cell/cell/sha256"
        assert full["facts"][0]["kind"] == "requested_endpoint"
        assert full["facts"][1]["kind"] == "loaded_endpoint"
        changed = deepcopy(case)
        changed["observation"]["returned_hex"] = artifact_bytes("offline-counterfactual", "b").hex()
        assert (
            visible_reference(changed, "with_consumer")["facts"][1]["digest"]
            != full["facts"][1]["digest"]
        )


def test_fixture_and_evaluation_bytes_are_disjoint(source):
    for variant in ("a", "b"):
        assert artifact_bytes(source["namespace"], variant) != artifact_bytes(
            EVALUATION_NAMESPACE, variant
        )
    with pytest.raises(ValueError):
        artifact_bytes("", "a")
    with pytest.raises(ValueError):
        artifact_bytes("fixture", "unknown")
    with pytest.raises(ValueError):
        visible_reference(source["cases"][0], "invented")
    assert VIEWS == ("trace_only", "with_consumer")
