"""Ground-truth boundary tests: the diagnosis-visible payload must be clean."""

from __future__ import annotations

from aletheia_lab.benchmark.case_schema import (
    FORBIDDEN_TERMS,
    CaseManifest,
    DiagnosisInput,
    ObservableSignals,
    project_diagnosis_input,
)
from aletheia_lab.benchmark.case_writer import (
    diagnosis_input_leakage,
    load_case_dir_schema_only,
    write_case,
)

_GROUND_TRUTH_FIELDS = {"cause_label", "causal_mechanism", "injected_change", "expected_symptoms"}


def test_projection_excludes_ground_truth_fields(p1_manifest_factory):
    manifest = CaseManifest.model_validate(p1_manifest_factory())
    visible = project_diagnosis_input(manifest).model_dump()
    keys = set(visible) | set(visible["observable_signals"])
    assert keys.isdisjoint(_GROUND_TRUTH_FIELDS)


def test_diagnosis_input_has_no_forbidden_terms(p1_manifest_factory):
    manifest = CaseManifest.model_validate(p1_manifest_factory())
    assert diagnosis_input_leakage(project_diagnosis_input(manifest)) == []


def test_leakage_guard_fires_when_ground_truth_injected():
    poisoned = DiagnosisInput(
        public_id="p1-case-01-full",
        evidence_condition="full",
        dataset_id="d",
        dataset_sha256="a",
        split_manifest_sha256="b",
        task_prompt="t",
        observable_signals=ObservableSignals(notes=["the cause is data_drift (ground_truth)"]),
    )
    leaks = diagnosis_input_leakage(poisoned)
    assert "data_drift" in leaks and "ground_truth" in leaks


def test_hidden_ground_truth_readable_by_evaluator_path(
    tmp_path, p1_manifest_factory, p1_ground_truth_factory, p1_injection_factory
):
    case_dir = tmp_path / "case"
    write_case(case_dir, p1_manifest_factory(), p1_ground_truth_factory(), p1_injection_factory())
    loaded = load_case_dir_schema_only(case_dir)
    assert loaded.ground_truth.cause_label == "data_drift"
    assert loaded.ground_truth.injection_parameters["feature"] == "Contract"


def test_forbidden_terms_cover_cause_and_injection_naming():
    assert "data_drift" in FORBIDDEN_TERMS
    assert "ground_truth" in FORBIDDEN_TERMS
    assert "injection" in FORBIDDEN_TERMS
