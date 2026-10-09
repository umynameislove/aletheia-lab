from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.serving_contract_design import inspect_design

ROOT = Path(__file__).resolve().parents[2]


def design():
    """Synthetic declarations only; no qualification or scientific evidence."""
    paths = []
    predictions = []
    for phase in ("development", "validation"):
        paths.append(
            {
                "id": phase,
                "framework": phase,
                "phase": phase,
                "source_family_id": phase,
                "implementation_group_id": phase,
                "mechanism_group_id": "shared_known_mechanism",
                "group_rationale": "different source implementations; known mechanism",
                "contract": "request must use enrolled state",
                "native_entry": "declared public API",
                "update_scopes": ["U2"],
                "source": {
                    "url": "https://example.org/source",
                    "revision": "tag",
                    "sha256": ["0" * 64],
                },
                "exposure": {
                    "main_outcomes_observed": False,
                    "docs_source_read": True,
                    "mechanism_previously_exposed": True,
                    "classification": "source_informed_outcome_reserved",
                },
                "qualification": {
                    "status": "import_only",
                    "evidence": "synthetic declaration",
                    "main_update_fault_calls": 0,
                },
                "reference": {
                    "status": "planned",
                    "method": "separate raw tuple oracle",
                    "privileged_fields": ["reference_tuple"],
                    "excluded_from_comparators": True,
                    "identity_from_output_or_current_path": False,
                },
                "views": {
                    view: {
                        "fields": ["use_tuple", "request_id"],
                        "acquisition": "ordinary instrumented runtime",
                        "same_evidence_for_all_checkers": True,
                    }
                    for view in ("V0", "V1", "V2")
                },
            }
        )
        predictions.append(
            {
                "id": phase,
                "path_id": phase,
                "query": "authorized_state",
                "case": "official new-object update",
                "expected": "compliant",
                "falsifier": "separate tuple differs despite stated assumptions",
                "effective_repair": "new immutable object",
                "ineffective_repair": "sign old file",
                "requirements": ["use_tuple", "request_id"],
                "assumptions": ["honest hook"],
                "missing_witness": ["closure_unknown"],
                "adequacy_prediction": "sufficient",
                "view": "V2",
            }
        )
    return {
        "schema": "serving-contract-design/v1",
        "checkpoint": "CP1",
        "execution_authorized": False,
        "main_outcomes_observed": False,
        "model_inputs": ["historical_evidence", "pinned_source"],
        "corpus_role": "coverage_only_no_frozen_model_revision",
        "paths": paths,
        "predictions": predictions,
        "transfer_claims": {"level": "implementation_transfer", "blind_mechanism_discovery": False},
        "census": {
            "retain_failures_unknown_refusal_unattempted": True,
            "outcome_retry": "none",
            "infrastructure_retry_rule": "retain original attempt, disclose correction",
        },
    }


def test_checked_design_is_not_execution_or_scientific_qualification():
    plan = design()
    before = copy.deepcopy(plan)
    report = inspect_design(plan)
    assert plan == before
    assert report["design_sha256"] == digest(plan)
    assert report["path_count"] == report["prediction_count"] == 2
    assert report["mechanism_overlap"] == ["shared_known_mechanism"]
    assert report["pending_control_reference_qualification"] == ["development", "validation"]
    assert report["scientific_claims_verified"] is False
    assert report["execution_authorized"] is report["execution_ready"] is False


def test_even_claimed_qualified_controls_still_need_CP1_and_execution_seal():
    plan = design()
    for row in plan["paths"]:
        row["qualification"]["status"] = "control_qualified"
        row["reference"]["status"] = "control_qualified"
    report = inspect_design(plan)
    assert report["pending_control_reference_qualification"] == []
    assert report["execution_ready"] is False
    assert report["scientific_claims_verified"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "execution",
        "outcome",
        "source_blind",
        "missing_hash",
        "changed_hash",
        "corpus_inputs",
        "hidden_model_inputs",
        "corpus_revision",
        "same_implementation",
        "same_family",
        "same_phase",
        "duplicate_path",
        "scope",
        "qualification_outcome",
        "boolean_zero",
        "reference_output",
        "reference_access",
        "view_privilege",
        "unfair_rights",
        "premature_witness",
        "wrong_query",
        "duplicate_prediction",
        "no_falsifier",
        "no_assumption",
        "no_repair",
        "missing_requirement",
        "missing_but_called_sufficient",
        "private_requirement",
        "unknown_as_success",
        "outcome_retry",
        "drop_failures",
    ],
)
def test_changed_integrity_contracts_are_rejected(mutation):
    plan = design()
    path, prediction = plan["paths"][1], plan["predictions"][1]
    if mutation == "execution":
        plan["execution_authorized"] = True
    elif mutation == "outcome":
        plan["main_outcomes_observed"] = True
    elif mutation == "source_blind":
        path["exposure"]["classification"] = "source_unread_outcome_reserved"
    elif mutation == "missing_hash":
        path["source"].pop("sha256")
    elif mutation == "changed_hash":
        path["source"]["sha256"] = ["not-a-byte-pin"]
    elif mutation in {"corpus_inputs", "hidden_model_inputs"}:
        plan["model_inputs"].append(
            "corpus_v2_labels" if mutation == "corpus_inputs" else "new_frame_results"
        )
    elif mutation == "corpus_revision":
        plan["corpus_role"] = "fit_model_after_coding"
    elif mutation == "same_implementation":
        path["implementation_group_id"] = "development"
    elif mutation == "same_family":
        path["source_family_id"] = "development"
    elif mutation == "same_phase":
        path["phase"] = "development"
    elif mutation == "duplicate_path":
        path["id"] = "development"
    elif mutation == "scope":
        path["update_scopes"] = ["U6"]
    elif mutation in {"qualification_outcome", "boolean_zero"}:
        path["qualification"]["main_update_fault_calls"] = (
            1 if mutation == "qualification_outcome" else False
        )
    elif mutation == "reference_output":
        path["reference"]["identity_from_output_or_current_path"] = True
    elif mutation == "reference_access":
        path["reference"]["excluded_from_comparators"] = False
    elif mutation == "view_privilege":
        path["views"]["V2"]["fields"].append("reference_tuple")
    elif mutation == "unfair_rights":
        path["views"]["V2"]["same_evidence_for_all_checkers"] = False
    elif mutation == "premature_witness":
        prediction["view"] = "V3"
    elif mutation == "wrong_query":
        prediction["query"] = "model_name_only"
    elif mutation == "duplicate_prediction":
        prediction["id"] = "development"
    elif mutation == "no_falsifier":
        prediction["falsifier"] = ""
    elif mutation == "no_assumption":
        prediction["assumptions"] = []
    elif mutation == "no_repair":
        prediction["ineffective_repair"] = ""
    elif mutation == "missing_requirement":
        prediction["requirements"].append("undeclared_field")
    elif mutation == "missing_but_called_sufficient":
        prediction["requirements"].append("absent_required_witness")
        prediction["missing_witness"].append("absent_required_witness")
    elif mutation == "private_requirement":
        prediction["requirements"].append("reference_tuple")
    elif mutation == "unknown_as_success":
        prediction["expected"] = "unknown"
    elif mutation == "outcome_retry":
        plan["census"]["outcome_retry"] = "until_pass"
    else:
        plan["census"]["retain_failures_unknown_refusal_unattempted"] = False
    with pytest.raises(ValueError):
        inspect_design(plan)


def test_all_paths_need_forecasts_and_views():
    plan = design()
    plan["predictions"].pop()
    with pytest.raises(ValueError, match="unforecasted"):
        inspect_design(plan)
    plan = design()
    plan["paths"][0]["views"].pop("V1")
    with pytest.raises(ValueError, match="three ordinary"):
        inspect_design(plan)


@pytest.mark.parametrize("expected", [" UNKNOWN ", "Conflict"])
def test_nonconclusive_expected_case_and_space_do_not_become_success(expected):
    plan = design()
    plan["predictions"][0]["expected"] = expected
    with pytest.raises(ValueError, match="conclusive adequacy"):
        inspect_design(plan)


def test_cli_inspection_is_read_only_and_reports_no_private_design_path(tmp_path):
    file = tmp_path / "design.json"
    file.write_text(json.dumps(design()), encoding="utf-8")
    before = file.read_bytes()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/serving_contract_design.py"), "--design", str(file)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["execution_ready"] is False
    assert str(file) not in completed.stdout
    assert file.read_bytes() == before
    assert set(tmp_path.iterdir()) == {file}


def test_cli_rejects_array_and_broken_design_without_exposing_contents(tmp_path):
    file = tmp_path / "design.json"
    file.write_text('["private-input"]', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/serving_contract_design.py"), "--design", str(file)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "design_rejected"
    assert "private-input" not in completed.stdout


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_cli_rejects_fifo_without_opening_a_blocking_reader(tmp_path):
    file = tmp_path / "owned-design-pipe"
    os.mkfifo(file)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/serving_contract_design.py"), "--design", str(file)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "design_rejected"
