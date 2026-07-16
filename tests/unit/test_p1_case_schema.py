"""Schema + writer tests for P1 benchmark cases (fixtures via conftest)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import CaseManifest, DiagnosisInput
from aletheia_lab.benchmark.case_writer import (
    dumps_deterministic,
    load_case_dir_schema_only,
    write_case,
)


def test_manifest_roundtrip_no_data_loss(
    tmp_path, p1_manifest_factory, p1_ground_truth_factory, p1_injection_factory
):
    case_dir = tmp_path / "case"
    write_case(case_dir, p1_manifest_factory(), p1_ground_truth_factory(), p1_injection_factory())
    loaded = load_case_dir_schema_only(case_dir)
    assert loaded.manifest.case_id == "p1-data-drift-01-full"
    assert loaded.ground_truth.hidden_failure_cause is not None
    assert loaded.ground_truth.hidden_failure_cause.cause_label == "data_drift"
    assert loaded.injection.psi == 0.0
    assert loaded.diagnosis_input.diagnosis_context_id.startswith("p1-context-")
    assert "evidence_condition" not in loaded.diagnosis_input.model_dump()
    assert "public_id" not in loaded.diagnosis_input.model_dump()


def test_invalid_manifest_fails_closed_without_partial_artifact(
    tmp_path, p1_manifest_factory, p1_ground_truth_factory, p1_injection_factory
):
    case_dir = tmp_path / "case"
    bad = p1_manifest_factory()
    del bad["case_id"]  # required field missing
    with pytest.raises(ValidationError):
        write_case(case_dir, bad, p1_ground_truth_factory(), p1_injection_factory())
    assert not case_dir.exists() or list(case_dir.glob("*.json")) == []


def test_unknown_manifest_evidence_condition_rejected(p1_manifest_factory):
    with pytest.raises(ValidationError):
        CaseManifest.model_validate(p1_manifest_factory(condition="totally_unknown"))


def test_diagnosis_input_rejects_evaluator_condition_label():
    with pytest.raises(ValidationError):
        DiagnosisInput.model_validate(
            {
                "diagnosis_context_id": "p1-context-" + "0" * 64,
                "evidence_condition": "weird",
                "dataset_id": "d",
                "dataset_sha256": "a",
                "split_manifest_sha256": "b",
                "task_prompt": "t",
                "observable_signals": {},
            }
        )


def test_writer_is_deterministic(
    tmp_path, p1_manifest_factory, p1_ground_truth_factory, p1_injection_factory
):
    a, b = tmp_path / "a", tmp_path / "b"
    ra = write_case(a, p1_manifest_factory(), p1_ground_truth_factory(), p1_injection_factory())
    rb = write_case(b, p1_manifest_factory(), p1_ground_truth_factory(), p1_injection_factory())
    assert ra["checksums"] == rb["checksums"]
    for name in ("manifest.json", "diagnosis_input.json", "ground_truth.json", "injection.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_writer_refuses_silent_overwrite(
    tmp_path, p1_manifest_factory, p1_ground_truth_factory, p1_injection_factory
):
    case_dir = tmp_path / "case"
    write_case(case_dir, p1_manifest_factory(), p1_ground_truth_factory(), p1_injection_factory())
    with pytest.raises(FileExistsError):
        write_case(
            case_dir,
            p1_manifest_factory(),
            p1_ground_truth_factory(),
            p1_injection_factory(),
            overwrite=False,
        )
    write_case(
        case_dir,
        p1_manifest_factory(),
        p1_ground_truth_factory(),
        p1_injection_factory(),
        overwrite=True,
    )


def test_dumps_deterministic_sorted_keys():
    rendered = dumps_deterministic({"b": 1, "a": 2})
    assert rendered.index('"a"') < rendered.index('"b"')
