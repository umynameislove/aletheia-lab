"""Regression tests for the outcome-blind diagnosis feasibility lint."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.protocol_feasibility import (
    DiagnosisArtifactBinding,
    DiagnosisFeasibilityReceipt,
    DiagnosisProtocolFeasibilityPlan,
    audit_diagnosis_feasibility,
    load_diagnosis_feasibility_plan,
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONED_PLAN = ROOT / "configs/evaluation/diagnosis_protocol_feasibility_plan.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_versioned_artifact_blockers(
    plan: DiagnosisProtocolFeasibilityPlan,
) -> set[str]:
    """Derive filesystem blockers without assuming local data archives exist."""

    blockers: set[str] = set()
    for artifact in plan.artifacts:
        relative = Path(artifact.relative_path)
        candidate = ROOT / relative
        current = ROOT
        has_symlink_component = False
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                has_symlink_component = True
                break
        present = candidate.is_file() and not has_symlink_component
        if artifact.role != "dataset":
            assert present, f"versioned repository artifact is unavailable: {relative}"
            assert _sha(candidate.read_bytes()) == artifact.expected_sha256, (
                f"versioned repository artifact hash changed: {relative}"
            )
            continue
        if not present:
            blockers.update(
                {
                    f"artifact.{artifact.artifact_id}.present",
                    f"artifact.{artifact.artifact_id}.hash",
                }
            )
        elif _sha(candidate.read_bytes()) != artifact.expected_sha256:
            blockers.add(f"artifact.{artifact.artifact_id}.hash")
    return blockers


def _ready_plan(tmp_path: Path) -> DiagnosisProtocolFeasibilityPlan:
    source = load_diagnosis_feasibility_plan(VERSIONED_PLAN)
    roles = ("dataset", "protocol", "runtime", "closeout", "governance")
    artifacts: list[DiagnosisArtifactBinding] = []
    for index, role in enumerate(roles):
        relative = f"inputs/{index}-{role}.json"
        data = f"artifact-{role}".encode()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append(
            DiagnosisArtifactBinding(
                artifact_id=f"artifact-{role}",
                role=role,  # type: ignore[arg-type]
                relative_path=relative,
                expected_sha256=_sha(data),
            )
        )
    capabilities = tuple(
        item.model_copy(
            update={
                "state": "ready",
                "import_reference": (
                    "aletheia_lab.evaluation.structural_closeout:reduce_structural_closeout"
                ),
            }
        )
        for item in source.runtime_capabilities
    )
    return DiagnosisProtocolFeasibilityPlan.model_validate(
        {
            **source.model_dump(mode="python"),
            "artifacts": tuple(sorted(artifacts, key=lambda item: item.artifact_id)),
            "runtime_capabilities": capabilities,
        }
    )


def test_versioned_plan_is_outcome_blind_and_blocks_missing_production_adapter() -> None:
    plan = load_diagnosis_feasibility_plan(VERSIONED_PLAN)
    receipt = audit_diagnosis_feasibility(
        plan,
        repository_root=ROOT,
    )

    assert receipt.protected_outcomes_opened is False
    assert receipt.execution_authorized is False
    assert receipt.status == "blocked_before_registration"
    expected_blockers = _expected_versioned_artifact_blockers(plan) | {
        "runtime.production-gateway-adapter"
    }
    assert set(receipt.blocker_codes) == expected_blockers
    checks = {item.code: item for item in receipt.checks}
    for artifact in plan.artifacts:
        present_code = f"artifact.{artifact.artifact_id}.present"
        hash_code = f"artifact.{artifact.artifact_id}.hash"
        assert checks[present_code].status == (
            "block" if present_code in expected_blockers else "pass"
        )
        assert checks[hash_code].status == ("block" if hash_code in expected_blockers else "pass")
    assert all(
        item.status == "pass"
        for item in receipt.checks
        if not item.code.startswith("artifact.")
        and item.code != "runtime.production-gateway-adapter"
    )


def test_complete_fixture_plan_is_ready_but_does_not_authorize_execution(
    tmp_path: Path,
) -> None:
    receipt = audit_diagnosis_feasibility(_ready_plan(tmp_path), repository_root=tmp_path)

    assert receipt.status == "ready_for_registration_not_execution_authorized"
    assert receipt.blocker_codes == ()
    assert receipt.execution_authorized is False
    assert receipt.protected_outcomes_opened is False


def test_missing_or_changed_artifact_fails_closed(tmp_path: Path) -> None:
    plan = _ready_plan(tmp_path)
    changed = tmp_path / plan.artifacts[0].relative_path
    changed.write_text("changed", encoding="utf-8")

    receipt = audit_diagnosis_feasibility(plan, repository_root=tmp_path)

    assert f"artifact.{plan.artifacts[0].artifact_id}.hash" in receipt.blocker_codes


def test_symlinked_artifact_fails_closed(tmp_path: Path) -> None:
    plan = _ready_plan(tmp_path)
    binding = plan.artifacts[0]
    path = tmp_path / binding.relative_path
    target = path.with_suffix(".target")
    target.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")

    receipt = audit_diagnosis_feasibility(plan, repository_root=tmp_path)
    assert f"artifact.{binding.artifact_id}.present" in receipt.blocker_codes


def test_symlinked_artifact_parent_fails_closed(tmp_path: Path) -> None:
    plan = _ready_plan(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    original = tmp_path / "inputs"
    relocated = external / "inputs"
    original.rename(relocated)
    try:
        original.symlink_to(relocated, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")

    receipt = audit_diagnosis_feasibility(plan, repository_root=tmp_path)

    present_codes = {
        item.code
        for item in receipt.checks
        if item.code.endswith(".present") and item.status == "block"
    }
    assert len(present_codes) == len(plan.artifacts)


@pytest.mark.parametrize(
    "mutation",
    (
        {"protected_outcomes_opened": True},
        {"execution_authorized": True},
        {"supported_python_minors": ["3.12"]},
        {
            "output_paths": {
                "staging_root": "artifacts/diagnosis",
                "object_root": "artifacts/diagnosis/objects",
                "terminal_root": "artifacts/diagnosis/terminal",
                "paths_are_repository_relative": True,
                "content_addressed_objects": True,
                "create_only_publication": True,
                "atomic_terminal_publication": True,
                "partial_outcome_publication_forbidden": True,
            }
        },
    ),
)
def test_outcome_opening_runtime_narrowing_and_path_overlap_are_rejected(
    mutation: dict[str, object],
) -> None:
    payload = json.loads(VERSIONED_PLAN.read_text(encoding="utf-8"))
    payload.update(mutation)

    with pytest.raises(ValidationError):
        DiagnosisProtocolFeasibilityPlan.model_validate_json(json.dumps(payload))


def test_receipt_hash_and_blocker_census_cannot_be_forged() -> None:
    receipt = audit_diagnosis_feasibility(
        load_diagnosis_feasibility_plan(VERSIONED_PLAN),
        repository_root=ROOT,
    )
    forged = receipt.model_dump(mode="json")
    forged["blocker_codes"] = []

    with pytest.raises(ValidationError):
        DiagnosisFeasibilityReceipt.model_validate_json(json.dumps(forged))
