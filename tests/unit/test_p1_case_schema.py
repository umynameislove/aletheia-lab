"""Schema + writer tests for P1 benchmark cases."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import CaseManifest, DiagnosisInput
from aletheia_lab.benchmark.case_writer import dumps_deterministic, load_case_dir, write_case


def _manifest_dict(case_id="p1-data-drift-01-full", public_id="p1-case-01-full", condition="full"):
    return {
        "case_id": case_id,
        "public_id": public_id,
        "fault_type": "data_drift",
        "dataset_id": "telco_customer_churn",
        "dataset_sha256": "a" * 64,
        "split_manifest_sha256": "b" * 64,
        "injection_id": "drift_contract_s1",
        "injection_seed": 1,
        "injection_parameters": {"feature": "Contract", "seed": 1},
        "injection_setting": "drift_contract_s1",
        "severity_rank": 1,
        "evidence_condition": condition,
        "evidence_bundle_id": f"eb-{public_id}",
        "expected_diagnosis_behavior": "cite or abstain",
        "observable_signals": {
            "candidate_feature": "Contract",
            "psi": 0.5,
            "distribution_reference": {"Month-to-month": 1.0},
        },
        "artifacts": {"manifest": "manifest.json"},
        "reproduction": {"command": "python -m aletheia_lab benchmark generate-p1"},
        "ground_truth_ref": "ground_truth.json",
        "split": "dev",
        "tag": "P1",
    }


def _gt_dict():
    return {
        "cause_label": "data_drift",
        "causal_mechanism": "categorical_distribution_shift",
        "injected_change": "Contract shifted",
        "affected_components": ["Contract"],
        "expected_symptoms": ["metric_regression"],
        "injection_parameters": {"feature": "Contract", "seed": 1},
    }


def _inj_dict():
    return {
        "injection_id": "drift_contract_s1",
        "injector": "X",
        "fault_type": "data_drift",
        "feature": "Contract",
        "seed": 1,
        "target_distribution": {"Month-to-month": 0.8},
        "achieved_distribution": {"Month-to-month": 0.8},
        "reference_distribution": {"Month-to-month": 0.5},
        "psi": 0.5,
        "output_size": 100,
        "dataset_id": "telco_customer_churn",
        "dataset_sha256": "a" * 64,
    }


def test_manifest_roundtrip_no_data_loss(tmp_path):
    case_dir = tmp_path / "case"
    write_case(case_dir, _manifest_dict(), _gt_dict(), _inj_dict())
    loaded = load_case_dir(case_dir)
    assert loaded.manifest.case_id == "p1-data-drift-01-full"
    assert loaded.ground_truth.cause_label == "data_drift"
    assert loaded.injection.psi == 0.5
    assert loaded.diagnosis_input.public_id == "p1-case-01-full"


def test_invalid_manifest_fails_closed_without_partial_artifact(tmp_path):
    case_dir = tmp_path / "case"
    bad = _manifest_dict()
    del bad["case_id"]  # required field missing
    with pytest.raises(ValidationError):
        write_case(case_dir, bad, _gt_dict(), _inj_dict())
    # No partial files written.
    assert not case_dir.exists() or list(case_dir.glob("*.json")) == []


def test_unknown_evidence_condition_rejected():
    with pytest.raises(ValidationError):
        CaseManifest.model_validate(_manifest_dict(condition="totally_unknown"))
    with pytest.raises(ValidationError):
        DiagnosisInput.model_validate(
            {
                "public_id": "x",
                "evidence_condition": "weird",
                "dataset_id": "d",
                "dataset_sha256": "a",
                "split_manifest_sha256": "b",
                "task_prompt": "t",
                "observable_signals": {},
            }
        )


def test_writer_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ra = write_case(a, _manifest_dict(), _gt_dict(), _inj_dict())
    rb = write_case(b, _manifest_dict(), _gt_dict(), _inj_dict())
    assert ra["checksums"] == rb["checksums"]
    for name in ("manifest.json", "diagnosis_input.json", "ground_truth.json", "injection.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_writer_refuses_silent_overwrite(tmp_path):
    case_dir = tmp_path / "case"
    write_case(case_dir, _manifest_dict(), _gt_dict(), _inj_dict())
    with pytest.raises(FileExistsError):
        write_case(case_dir, _manifest_dict(), _gt_dict(), _inj_dict(), overwrite=False)
    # overwrite=True is allowed
    write_case(case_dir, _manifest_dict(), _gt_dict(), _inj_dict(), overwrite=True)


def test_dumps_deterministic_sorted_keys():
    assert dumps_deterministic({"b": 1, "a": 2}).index('"a"') < dumps_deterministic(
        {"b": 1, "a": 2}
    ).index('"b"')
