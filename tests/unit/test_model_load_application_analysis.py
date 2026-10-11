from __future__ import annotations

from copy import deepcopy
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_application_analysis as study
from aletheia_lab.project.identity import content_sha256


def test_missing_optional_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(study, "version", missing)
    with pytest.raises(ValueError, match="optional application runtime"):
        study.environment()


@pytest.fixture
def evidence() -> dict[str, Any]:
    digests = {name: content_sha256(name.encode()) for name in ("A", "B")}
    operations = []
    for scope in study.NORMAL_SCOPES:
        load = scope in ("initial-load", "explicit-reload")
        predict = scope in (
            "initial-inference",
            "resident-after-path-update",
            "inference-after-reload",
            "inference-after-failed-reload",
        )
        method, route = "POST", "/v2/models/probe/infer"
        if scope in ("initial-load", "explicit-reload", "failed-reload", "unload"):
            route = "/v2/repository/models/probe/" + ("unload" if scope == "unload" else "load")
        elif scope in ("readiness", "metadata"):
            method, route = "GET", "/v2/models/probe" + ("/ready" if scope == "readiness" else "")
        op = {
            "scope": scope,
            "method": method,
            "route": route,
            "operator_pin_sha256": digests["A"]
            if scope == "initial-load"
            else digests["B"]
            if scope in ("explicit-reload", "failed-reload")
            else None,
            "counts": {
                "loader_calls": int(load),
                "reconstructions": int(load),
                "prediction_computations": int(predict),
            },
            "status": 422
            if scope == "failed-reload"
            else 404
            if scope == "inference-after-unload"
            else 200,
            "body": {
                "outputs": [
                    {
                        "data": [1, 1]
                        if scope in ("inference-after-reload", "inference-after-failed-reload")
                        else [0, 0]
                    }
                ]
            },
        }
        operations.append(op)
    return {
        "arm": "captured",
        "rows": operations,
        "counts": {"loader_calls": 2, "reconstructions": 2, "prediction_computations": 4},
        "artifact_sha256": digests,
        "reference": [
            {"scope": scope, "raw_hex": label.encode().hex()}
            for scope, label in (("initial-load", "A"), ("explicit-reload", "B"))
        ],
        "receipts": [
            {"scope": scope, "descriptor_sha256": digests[label]}
            for scope, label in (("initial-load", "A"), ("explicit-reload", "B"))
        ],
        "relations": dict.fromkeys(study.NORMAL_RELATIONS, True),
        "unsupported": [],
        "socket_connection_attempts": 0,
        "path_provenance_frame": {
            "model_settings": {
                "name": "probe",
                "implementation": "mlserver_sklearn.SKLearnModel",
                "parameters": {"version": "1", "uri": "model.joblib"},
                "cache_enabled": True,
            },
            "load_status": 200,
            "ready_status": 200,
            "metadata": operations[2]["body"],
            "path_pre_sha256": digests["A"],
            "path_post_sha256": digests["A"],
        },
    }


def test_faithful_census_and_semantics_are_separate(evidence: dict[str, Any]) -> None:
    study.validate_evidence(evidence, evidence["artifact_sha256"])
    assert study.semantic_checks(evidence)
    evidence["relations"]["response_cache_avoids_recomputation"] = False
    study.validate_evidence(evidence, evidence["artifact_sha256"])
    assert not study.semantic_checks(evidence)


@pytest.mark.parametrize(
    "mutation",
    [
        "deleted",
        "duplicate",
        "route",
        "pin",
        "float",
        "extra-count",
        "total",
        "raw",
        "scope",
        "receipt",
        "foreign-artifact",
    ],
)
def test_occurrence_and_snapshot_tamper_rejected(evidence: dict[str, Any], mutation: str) -> None:
    if mutation == "deleted":
        evidence["rows"].pop()
    elif mutation == "duplicate":
        evidence["rows"][1] = deepcopy(evidence["rows"][0])
    elif mutation == "route":
        evidence["rows"][0]["route"] = "/other"
    elif mutation == "pin":
        evidence["rows"][0]["operator_pin_sha256"] = evidence["artifact_sha256"]["B"]
    elif mutation == "float":
        evidence["rows"][0]["counts"]["loader_calls"] = 1.0
    elif mutation == "extra-count":
        evidence["rows"][0]["counts"]["extra"] = 0
    elif mutation == "total":
        evidence["counts"]["loader_calls"] = 0
    elif mutation == "raw":
        evidence["reference"][0]["raw_hex"] = b"C".hex()
    elif mutation == "scope":
        evidence["reference"][0]["scope"] = "metadata"
    elif mutation == "receipt":
        evidence["receipts"][0]["descriptor_sha256"] = evidence["artifact_sha256"]["B"]
    else:
        evidence["artifact_sha256"]["A"] = "0" * 64
    with pytest.raises(ValueError):
        study.validate_evidence(
            evidence, {name: content_sha256(name.encode()) for name in ("A", "B")}
        )


@pytest.mark.parametrize("problem", ["status", "prediction", "relation", "unsupported", "network"])
def test_equally_wrong_runs_do_not_pass_semantics(evidence: dict[str, Any], problem: str) -> None:
    if problem == "status":
        evidence["rows"][0]["status"] = 500
    elif problem == "prediction":
        evidence["rows"][3]["body"]["outputs"][0]["data"] = [1, 1]
    elif problem == "relation":
        evidence["relations"] = {}
    elif problem == "unsupported":
        evidence["unsupported"] = ["initial-load"]
    else:
        evidence["socket_connection_attempts"] = 1
    assert not study.semantic_checks(evidence)


@pytest.mark.parametrize("kind", ["compliant", "violation", "unknown", "no_new_load"])
def test_reference_and_receipt_decisions(evidence: dict[str, Any], kind: str) -> None:
    row = evidence["rows"][0]
    if kind == "violation":
        row["operator_pin_sha256"] = evidence["artifact_sha256"]["B"]
    elif kind == "unknown":
        row["status"] = 500
    elif kind == "no_new_load":
        row = evidence["rows"][1]
    assert study.reference(row, evidence["reference"]) == kind
    assert study.receipt_decision(row, evidence["receipts"]) == kind


def test_failed_arm_keeps_planned_denominator(
    evidence: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"arm": "captured", "status": "completed", "evidence": evidence},
        {"arm": "path-legal", "status": "worker_failed", "evidence": None},
    ]
    monkeypatch.setattr(study, "verify_chain", lambda *args, **kwargs: "pass")
    summary = study.compare(rows, directory=tmp_path)
    assert summary["operation_denominator"] == 12
    assert summary["planned_operation_denominator"] == 26
    assert summary["unscored_planned_operations"] == 14
    assert summary["decided_load_operations"] == 2
    assert summary["failed_explicit_load_operations"] == 1


def test_rule_result_replay_does_not_trust_verification_string(
    evidence: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"arm": "captured", "status": "completed", "evidence": evidence}]
    monkeypatch.setattr(study, "verify_chain", lambda *args, **kwargs: "pass")
    study.compare(rows, directory=tmp_path)
    assert study.compare(rows)["S_reference_agreement"] == 12
    rows[0]["comparators"]["initial-load"]["verification"] = "artifact_rule_mismatch"
    with pytest.raises(ValueError, match="contradicts"):
        study.compare(rows)


def test_missing_relations_and_relocated_computation_rejected(evidence: dict[str, Any]) -> None:
    evidence["relations"].pop("resident_model_and_object_reused")
    assert not study.semantic_checks(evidence)
    evidence["relations"]["resident_model_and_object_reused"] = True
    evidence["rows"][3]["counts"]["prediction_computations"] = 0
    evidence["rows"][1]["counts"]["prediction_computations"] = 1
    study.validate_evidence(evidence, evidence["artifact_sha256"])
    assert not study.semantic_checks(evidence)


def test_empty_or_ungrounded_frame_rejected(evidence: dict[str, Any]) -> None:
    evidence["path_provenance_frame"] = {}
    with pytest.raises(ValueError, match="path frame"):
        study.validate_evidence(evidence, evidence["artifact_sha256"])
