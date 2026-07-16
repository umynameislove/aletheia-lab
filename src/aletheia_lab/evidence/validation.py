"""Cross-artifact validation for EvidenceBundle v2."""

from __future__ import annotations

from collections.abc import Iterable

from aletheia_lab.benchmark.case_schema import (
    EVIDENCE_CONDITIONS,
    CaseManifest,
    DiagnosisInput,
    diagnosis_context_id_for,
    project_diagnosis_input,
)
from aletheia_lab.evidence.rubric import expected_behavior_for
from aletheia_lab.evidence.schema import (
    EvidenceBundle,
    contains_condition_or_rubric_label,
    project_diagnosis_evidence,
)


def validate_bundle_for_case(
    bundle: EvidenceBundle,
    manifest: CaseManifest,
    diagnosis_input: DiagnosisInput,
) -> None:
    """Require bundle identity and projection boundaries to match one P1 case."""

    expected_context_id = diagnosis_context_id_for(
        case_id=manifest.case_id, case_family_id=manifest.case_family_id
    )
    expected_pairs = (
        ("evidence_bundle_id", bundle.evidence_bundle_id, manifest.evidence_bundle_id),
        ("case_id", bundle.case_id, manifest.case_id),
        ("case_family_id", bundle.case_family_id, manifest.case_family_id),
        ("diagnosis_context_id", bundle.diagnosis_context_id, expected_context_id),
        ("evidence_condition", bundle.evidence_condition, manifest.evidence_condition),
        ("dataset_id", bundle.dataset_id, manifest.dataset_id),
        ("dataset_sha256", bundle.dataset_sha256, manifest.dataset_sha256),
        (
            "split_manifest_sha256",
            bundle.split_manifest_sha256,
            manifest.split_manifest_sha256,
        ),
    )
    mismatches = [name for name, actual, expected in expected_pairs if actual != expected]
    if mismatches:
        raise ValueError(f"evidence bundle does not match case artifact(s): {mismatches}")

    if diagnosis_input != project_diagnosis_input(manifest):
        raise ValueError("diagnosis_input is not the canonical manifest projection")
    if diagnosis_input.diagnosis_context_id != bundle.diagnosis_context_id:
        raise ValueError("diagnosis input and evidence bundle context IDs differ")
    if manifest.expected_diagnosis_behavior != expected_behavior_for(manifest.evidence_condition):
        raise ValueError("manifest expected behavior differs from the canonical condition rubric")

    diagnosis_evidence = project_diagnosis_evidence(bundle)
    if contains_condition_or_rubric_label(diagnosis_evidence.model_dump(mode="json")):
        raise ValueError("diagnosis evidence projection exposes evaluator-only rubric metadata")


def validate_sibling_bundles(bundles: Iterable[EvidenceBundle]) -> None:
    """Validate the family identity and context uniqueness of three sibling bundles."""

    materialized = tuple(bundles)
    conditions = tuple(bundle.evidence_condition for bundle in materialized)
    if len(materialized) != len(EVIDENCE_CONDITIONS) or set(conditions) != set(
        EVIDENCE_CONDITIONS
    ):
        raise ValueError("siblings must contain exactly one full, missing_key and noisy bundle")

    shared_fields = (
        "case_family_id",
        "dataset_id",
        "dataset_sha256",
        "split_manifest_sha256",
    )
    for field in shared_fields:
        if len({getattr(bundle, field) for bundle in materialized}) != 1:
            raise ValueError(f"sibling bundle {field} values differ")

    for field in ("case_id", "diagnosis_context_id", "evidence_bundle_id"):
        if len({getattr(bundle, field) for bundle in materialized}) != len(materialized):
            raise ValueError(f"sibling bundle {field} values must be unique")
