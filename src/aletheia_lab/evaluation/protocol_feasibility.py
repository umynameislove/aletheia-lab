"""Outcome-blind feasibility lint for a future registered diagnosis evaluation execution."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

FEASIBILITY_PLAN_SCHEMA_VERSION: Final = "diagnosis-protocol-feasibility-plan/v1"
FEASIBILITY_RECEIPT_SCHEMA_VERSION: Final = "diagnosis-protocol-feasibility-receipt/v1"
_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class DiagnosisFeasibilityError(ValueError):
    """Raised when a diagnosis evaluation feasibility plan cannot be audited safely."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DiagnosisArtifactBinding(_StrictFrozenModel):
    artifact_id: str
    role: Literal["dataset", "protocol", "runtime", "closeout", "governance"]
    relative_path: str
    expected_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _safe_identity(self) -> Self:
        if _CODE_PATTERN.fullmatch(self.artifact_id) is None:
            raise ValueError("artifact_id must be a canonical public code")
        _validate_relative_path(self.relative_path, label="artifact path")
        return self


class DiagnosisRuntimeCapability(_StrictFrozenModel):
    capability_id: str
    required: bool
    state: Literal["ready", "pending", "not_applicable"]
    import_reference: str | None = None
    evidence: str

    @model_validator(mode="after")
    def _state_is_explicit(self) -> Self:
        if _CODE_PATTERN.fullmatch(self.capability_id) is None:
            raise ValueError("capability_id must be a canonical public code")
        if not self.evidence.strip() or self.evidence != self.evidence.strip():
            raise ValueError("capability evidence must be non-blank and trimmed")
        if self.state == "ready" and self.import_reference is None:
            raise ValueError("ready runtime capability requires an import reference")
        if self.import_reference is not None:
            parts = self.import_reference.split(":")
            if len(parts) != 2 or not all(parts):
                raise ValueError("import reference must use module:attribute form")
        return self


class DiagnosisOutputPathPolicy(_StrictFrozenModel):
    staging_root: str
    object_root: str
    terminal_root: str
    paths_are_repository_relative: Literal[True]
    content_addressed_objects: Literal[True]
    create_only_publication: Literal[True]
    atomic_terminal_publication: Literal[True]
    partial_outcome_publication_forbidden: Literal[True]

    @model_validator(mode="after")
    def _paths_do_not_overlap(self) -> Self:
        paths = (self.staging_root, self.object_root, self.terminal_root)
        for path in paths:
            _validate_relative_path(path, label="output root")
        normalized = tuple(PurePosixPath(path).as_posix().rstrip("/") for path in paths)
        if len(set(normalized)) != len(normalized):
            raise ValueError("diagnosis evaluation output roots must be distinct")
        for left in normalized:
            for right in normalized:
                if left != right and right.startswith(f"{left}/"):
                    raise ValueError(
                        "diagnosis evaluation output roots must not contain one another"
                    )
        return self


class DiagnosisAttemptPolicy(_StrictFrozenModel):
    maximum_registered_executions: Literal[1]
    provider_attempt_ceiling: int = Field(ge=1, le=3)
    provider_retry_is_not_registered_rerun: Literal[True]
    initial_request_identity_immutable_across_retries: Literal[True]
    registered_rerun_forbidden: Literal[True]
    identical_replay_is_idempotent: Literal[True]
    conflicting_replay_fails_closed: Literal[True]


class DiagnosisCloseoutPolicy(_StrictFrozenModel):
    reducer_import_reference: str
    attempt_store_import_reference: str
    complete_request_census_required: Literal[True]
    current_authorization_rechecked: Literal[True]
    terminal_inventory_reconciled: Literal[True]
    technical_and_scientific_status_separate: Literal[True]
    no_scientific_default_from_structural_pass: Literal[True]

    @model_validator(mode="after")
    def _references_are_well_formed(self) -> Self:
        for reference in (
            self.reducer_import_reference,
            self.attempt_store_import_reference,
        ):
            parts = reference.split(":")
            if len(parts) != 2 or not all(parts):
                raise ValueError("closeout references must use module:attribute form")
        return self


class DiagnosisProtocolFeasibilityPlan(_StrictFrozenModel):
    schema_version: Literal["diagnosis-protocol-feasibility-plan/v1"]
    plan_status: Literal["outcome_blind_candidate"]
    protected_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    supported_python_minors: tuple[Literal["3.11", "3.12"], ...]
    test_profile: Literal["evaluation"]
    test_profile_timeout_seconds: Literal[300]
    artifacts: tuple[DiagnosisArtifactBinding, ...]
    runtime_capabilities: tuple[DiagnosisRuntimeCapability, ...]
    output_paths: DiagnosisOutputPathPolicy
    attempt_policy: DiagnosisAttemptPolicy
    closeout_policy: DiagnosisCloseoutPolicy

    @model_validator(mode="after")
    def _census_is_canonical(self) -> Self:
        if self.supported_python_minors != ("3.11", "3.12"):
            raise ValueError("diagnosis evaluation must retain Python 3.11 and 3.12 support")
        artifact_ids = tuple(item.artifact_id for item in self.artifacts)
        capability_ids = tuple(item.capability_id for item in self.runtime_capabilities)
        if artifact_ids != tuple(sorted(artifact_ids)) or len(set(artifact_ids)) != len(
            artifact_ids
        ):
            raise ValueError("artifact bindings must be unique and canonically sorted")
        if capability_ids != tuple(sorted(capability_ids)) or len(set(capability_ids)) != len(
            capability_ids
        ):
            raise ValueError("runtime capabilities must be unique and canonically sorted")
        required_roles = {"dataset", "protocol", "runtime", "closeout", "governance"}
        if {item.role for item in self.artifacts} != required_roles:
            raise ValueError(
                "diagnosis evaluation artifact census must cover every feasibility role"
            )
        return self


CheckStatus = Literal["pass", "block"]


class DiagnosisFeasibilityCheck(_StrictFrozenModel):
    code: str
    status: CheckStatus
    evidence: str


class DiagnosisFeasibilityReceipt(_StrictFrozenModel):
    schema_version: Literal["diagnosis-protocol-feasibility-receipt/v1"]
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal[
        "ready_for_registration_not_execution_authorized",
        "blocked_before_registration",
    ]
    protected_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    checks: tuple[DiagnosisFeasibilityCheck, ...]
    blocker_codes: tuple[str, ...]
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _derived_status_and_hash(self) -> Self:
        expected_blockers = tuple(item.code for item in self.checks if item.status == "block")
        if self.blocker_codes != expected_blockers:
            raise ValueError("feasibility blocker census does not reconcile")
        expected_status = (
            "blocked_before_registration"
            if expected_blockers
            else "ready_for_registration_not_execution_authorized"
        )
        if self.status != expected_status:
            raise ValueError("feasibility status is not derived from checks")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_execution_sha256(payload):
            raise ValueError("feasibility receipt hash does not reconcile")
        return self


def _validate_relative_path(value: str, *, label: str) -> None:
    if not value or "\\" in value:
        raise ValueError(f"{label} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{label} must be repository-relative and canonical")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_repository_file(root: Path, candidate: Path) -> bool:
    """Reject missing files and every symlink component before hashing."""

    try:
        relative = candidate.relative_to(root)
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError):
        return False
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _check(code: str, passed: bool, evidence: str) -> DiagnosisFeasibilityCheck:
    return DiagnosisFeasibilityCheck(
        code=code,
        status="pass" if passed else "block",
        evidence=evidence,
    )


def _resolve_import(reference: str) -> bool:
    module_name, attribute_name = reference.split(":", maxsplit=1)
    try:
        module = importlib.import_module(module_name)
    except (ImportError, RuntimeError):
        return False
    return hasattr(module, attribute_name)


def load_diagnosis_feasibility_plan(path: str | Path) -> DiagnosisProtocolFeasibilityPlan:
    """Load a strict, outcome-blind feasibility plan."""

    raw_text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise DiagnosisFeasibilityError(
            "diagnosis evaluation feasibility plan must be a JSON object"
        )
    return DiagnosisProtocolFeasibilityPlan.model_validate_json(raw_text)


def audit_diagnosis_feasibility(
    plan: DiagnosisProtocolFeasibilityPlan,
    *,
    repository_root: str | Path,
) -> DiagnosisFeasibilityReceipt:
    """Audit filesystem and runtime readiness without opening an outcome."""

    root = Path(repository_root).resolve(strict=True)
    checks: list[DiagnosisFeasibilityCheck] = []
    current_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    checks.append(
        _check(
            "python_supported",
            current_minor in plan.supported_python_minors,
            f"current={current_minor}; allowed={','.join(plan.supported_python_minors)}",
        )
    )

    for artifact in plan.artifacts:
        candidate = root / artifact.relative_path
        exists = _is_repository_file(root, candidate)
        checks.append(
            _check(
                f"artifact.{artifact.artifact_id}.present",
                exists,
                artifact.relative_path,
            )
        )
        digest_matches = exists and _file_sha256(candidate) == artifact.expected_sha256
        checks.append(
            _check(
                f"artifact.{artifact.artifact_id}.hash",
                digest_matches,
                artifact.expected_sha256,
            )
        )

    for capability in plan.runtime_capabilities:
        resolved = (
            capability.state == "ready"
            and capability.import_reference is not None
            and _resolve_import(capability.import_reference)
        )
        passed = not capability.required or resolved
        checks.append(
            _check(
                f"runtime.{capability.capability_id}",
                passed,
                f"state={capability.state}; {capability.evidence}",
            )
        )

    output_roots = (
        plan.output_paths.staging_root,
        plan.output_paths.object_root,
        plan.output_paths.terminal_root,
    )
    artifact_paths = {item.relative_path for item in plan.artifacts}
    disjoint = all(
        output not in artifact_paths
        and not any(path.startswith(f"{output}/") for path in artifact_paths)
        for output in output_roots
    )
    checks.append(
        _check(
            "artifact_paths_disjoint",
            disjoint,
            ",".join(output_roots),
        )
    )

    closeout_refs = (
        plan.closeout_policy.reducer_import_reference,
        plan.closeout_policy.attempt_store_import_reference,
    )
    checks.append(
        _check(
            "closeout_resolves",
            all(_resolve_import(reference) for reference in closeout_refs),
            ",".join(closeout_refs),
        )
    )
    checks.append(
        _check(
            "one_registered_execution",
            plan.attempt_policy.maximum_registered_executions == 1
            and plan.attempt_policy.registered_rerun_forbidden,
            (
                "one registered execution; provider retries remain bounded inside "
                "the immutable request"
            ),
        )
    )
    checks.append(
        _check(
            "outcome_blind",
            not plan.protected_outcomes_opened and not plan.execution_authorized,
            "protected_outcomes_opened=false; execution_authorized=false",
        )
    )

    canonical_checks = tuple(sorted(checks, key=lambda item: item.code))
    blockers = tuple(item.code for item in canonical_checks if item.status == "block")
    status: Literal[
        "ready_for_registration_not_execution_authorized",
        "blocked_before_registration",
    ] = (
        "blocked_before_registration"
        if blockers
        else "ready_for_registration_not_execution_authorized"
    )
    hash_payload = {
        "schema_version": FEASIBILITY_RECEIPT_SCHEMA_VERSION,
        "plan_sha256": canonical_execution_sha256(plan.model_dump(mode="json")),
        "status": status,
        "protected_outcomes_opened": False,
        "execution_authorized": False,
        "checks": [item.model_dump(mode="json") for item in canonical_checks],
        "blocker_codes": list(blockers),
    }
    return DiagnosisFeasibilityReceipt(
        schema_version=FEASIBILITY_RECEIPT_SCHEMA_VERSION,
        plan_sha256=canonical_execution_sha256(plan.model_dump(mode="json")),
        status=status,
        protected_outcomes_opened=False,
        execution_authorized=False,
        checks=canonical_checks,
        blocker_codes=blockers,
        receipt_sha256=canonical_execution_sha256(hash_payload),
    )
