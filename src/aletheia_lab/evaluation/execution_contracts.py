"""Neutral, versioned contracts for deterministic evaluation execution."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import (
    PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_json,
    canonical_project_sha256,
    normalize_text,
)

EXECUTION_CANONICAL_SCHEMA_VERSION: Final[Literal["evaluation-execution-canonical/v1"]] = (
    "evaluation-execution-canonical/v1"
)
MANIFEST_REFERENCE_SCHEMA_VERSION: Final[
    Literal["evaluation-manifest-reference/v1"]
] = "evaluation-manifest-reference/v1"
CASE_REFERENCE_SCHEMA_VERSION: Final[Literal["evaluation-case-reference/v1"]] = (
    "evaluation-case-reference/v1"
)
MODEL_POLICY_REFERENCE_SCHEMA_VERSION: Final[
    Literal["evaluation-model-policy-reference/v1"]
] = "evaluation-model-policy-reference/v1"
ATTEMPT_IDENTITY_SCHEMA_VERSION: Final[Literal["evaluation-attempt-identity/v1"]] = (
    "evaluation-attempt-identity/v1"
)
REQUEST_IDENTITY_SCHEMA_VERSION: Final[Literal["evaluation-request-identity/v1"]] = (
    "evaluation-request-identity/v1"
)
TECHNICAL_ISSUE_SCHEMA_VERSION: Final[Literal["evaluation-technical-issue/v1"]] = (
    "evaluation-technical-issue/v1"
)

_OPAQUE_REFERENCE_PATTERN: Final[str] = r"^ev-[0-9a-f]{64}$"
_LINEAGE_GRAPH_ID_PATTERN: Final[str] = r"^p3-lineage-graph-[0-9a-f]{64}$"
_COMMIT_REFERENCE_PATTERN: Final[str] = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_CODE_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"
_UTC_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProjectId = Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
SnapshotId = Annotated[str, Field(pattern=SNAPSHOT_ID_PATTERN)]
EvidenceBundleId = Annotated[str, Field(pattern=PROJECT_EVIDENCE_BUNDLE_ID_PATTERN)]
LineageGraphId = Annotated[str, Field(pattern=_LINEAGE_GRAPH_ID_PATTERN)]
OpaqueReference = Annotated[str, Field(pattern=_OPAQUE_REFERENCE_PATTERN)]
CommitReference = Annotated[str, Field(pattern=_COMMIT_REFERENCE_PATTERN)]
TechnicalCode = Annotated[str, Field(pattern=_CODE_PATTERN)]

ExecutionVisibility = Literal["public", "diagnosis", "evaluator"]
TechnicalSeverity = Literal["warning", "error", "blocker"]


class EvaluationContractError(ValueError):
    """Raised when neutral execution references are ambiguous or inconsistent."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


def canonical_execution_json(payload: object) -> str:
    """Return canonical JSON in the execution-identity namespace."""

    return canonical_project_json(
        {
            "schema_version": EXECUTION_CANONICAL_SCHEMA_VERSION,
            "payload": payload,
        }
    )


def canonical_execution_sha256(payload: object) -> str:
    """Return the canonical SHA-256 digest for one execution payload."""

    return canonical_project_sha256(
        {
            "schema_version": EXECUTION_CANONICAL_SCHEMA_VERSION,
            "payload": payload,
        }
    )


def _reference_id(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def _canonical_timestamp(value: str) -> str:
    normalized = normalize_text(value, label="execution timestamp", max_length=32)
    if _UTC_TIMESTAMP_PATTERN.fullmatch(normalized) is None:
        raise ValueError("execution timestamps must be canonical UTC values ending in Z")
    try:
        parsed = datetime.fromisoformat(normalized[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("execution timestamps must be valid calendar timestamps") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("execution timestamps must include UTC timezone information")
    return normalized


class EvaluationManifestReference(_StrictFrozenModel):
    """One authorized immutable manifest reference without scientific policy values."""

    schema_version: Literal["evaluation-manifest-reference/v1"] = (
        MANIFEST_REFERENCE_SCHEMA_VERSION
    )
    reference_id: OpaqueReference
    project_id: ProjectId
    snapshot_id: SnapshotId
    manifest_content_sha256: Sha256
    source_commit_ref: CommitReference
    authorization_state: Literal["authorized"]
    authorization_ref: OpaqueReference
    provenance_sha256: Sha256
    created_at: str
    frozen_at: str
    visibility: ExecutionVisibility

    @field_validator("created_at", "frozen_at")
    @classmethod
    def _timestamps_are_canonical(cls, value: str) -> str:
        return _canonical_timestamp(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "snapshot_id": self.snapshot_id,
            "manifest_content_sha256": self.manifest_content_sha256,
            "source_commit_ref": self.source_commit_ref,
            "authorization_state": self.authorization_state,
            "authorization_ref": self.authorization_ref,
            "provenance_sha256": self.provenance_sha256,
            "created_at": self.created_at,
            "frozen_at": self.frozen_at,
            "visibility": self.visibility,
        }

    @model_validator(mode="after")
    def _reference_identity_matches(self) -> Self:
        if self.reference_id != _reference_id(self.identity_payload()):
            raise ValueError("manifest reference_id does not match canonical identity")
        return self

    def canonical_sha256(self) -> str:
        return canonical_execution_sha256(self.model_dump(mode="json"))

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        snapshot_id: str,
        manifest_content_sha256: str,
        source_commit_ref: str,
        authorization_state: Literal["authorized"],
        authorization_ref: str,
        provenance_sha256: str,
        created_at: str,
        frozen_at: str,
        visibility: ExecutionVisibility,
    ) -> Self:
        payload = {
            "schema_version": MANIFEST_REFERENCE_SCHEMA_VERSION,
            "project_id": project_id,
            "snapshot_id": snapshot_id,
            "manifest_content_sha256": manifest_content_sha256,
            "source_commit_ref": source_commit_ref,
            "authorization_state": authorization_state,
            "authorization_ref": authorization_ref,
            "provenance_sha256": provenance_sha256,
            "created_at": created_at,
            "frozen_at": frozen_at,
            "visibility": visibility,
        }
        return cls(
            reference_id=_reference_id(payload),
            project_id=project_id,
            snapshot_id=snapshot_id,
            manifest_content_sha256=manifest_content_sha256,
            source_commit_ref=source_commit_ref,
            authorization_state=authorization_state,
            authorization_ref=authorization_ref,
            provenance_sha256=provenance_sha256,
            created_at=created_at,
            frozen_at=frozen_at,
            visibility=visibility,
        )


class EvaluationCaseReference(_StrictFrozenModel):
    """One opaque case reference bound to a project, snapshot and manifest."""

    schema_version: Literal["evaluation-case-reference/v1"] = CASE_REFERENCE_SCHEMA_VERSION
    reference_id: OpaqueReference
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    project_id: ProjectId
    snapshot_id: SnapshotId
    case_id: OpaqueReference
    family_id: OpaqueReference
    mechanism_id: OpaqueReference
    dataset_id: OpaqueReference
    variant_id: OpaqueReference
    variant_content_sha256: Sha256
    case_content_sha256: Sha256
    evidence_bundle_id: EvidenceBundleId
    evidence_content_sha256: Sha256
    lineage_graph_id: LineageGraphId
    lineage_sha256: Sha256
    visibility_projection_sha256: Sha256
    authorization_ref: OpaqueReference
    provenance_sha256: Sha256
    visibility: ExecutionVisibility

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_reference_id": self.manifest_reference_id,
            "manifest_content_sha256": self.manifest_content_sha256,
            "project_id": self.project_id,
            "snapshot_id": self.snapshot_id,
            "case_id": self.case_id,
            "family_id": self.family_id,
            "mechanism_id": self.mechanism_id,
            "dataset_id": self.dataset_id,
            "variant_id": self.variant_id,
            "variant_content_sha256": self.variant_content_sha256,
            "case_content_sha256": self.case_content_sha256,
            "evidence_bundle_id": self.evidence_bundle_id,
            "evidence_content_sha256": self.evidence_content_sha256,
            "lineage_graph_id": self.lineage_graph_id,
            "lineage_sha256": self.lineage_sha256,
            "visibility_projection_sha256": self.visibility_projection_sha256,
            "authorization_ref": self.authorization_ref,
            "provenance_sha256": self.provenance_sha256,
            "visibility": self.visibility,
        }

    @model_validator(mode="after")
    def _reference_identity_matches(self) -> Self:
        if self.reference_id != _reference_id(self.identity_payload()):
            raise ValueError("case reference_id does not match canonical identity")
        return self

    def canonical_sha256(self) -> str:
        return canonical_execution_sha256(self.model_dump(mode="json"))

    @classmethod
    def build(
        cls,
        *,
        manifest: EvaluationManifestReference,
        case_id: str,
        family_id: str,
        mechanism_id: str,
        dataset_id: str,
        variant_id: str,
        variant_content_sha256: str,
        case_content_sha256: str,
        evidence_bundle_id: str,
        evidence_content_sha256: str,
        lineage_graph_id: str,
        lineage_sha256: str,
        visibility_projection_sha256: str,
        provenance_sha256: str,
        visibility: ExecutionVisibility,
    ) -> Self:
        checked_manifest = EvaluationManifestReference.model_validate(
            manifest.model_dump(mode="python")
        )
        payload = {
            "schema_version": CASE_REFERENCE_SCHEMA_VERSION,
            "manifest_reference_id": checked_manifest.reference_id,
            "manifest_content_sha256": checked_manifest.manifest_content_sha256,
            "project_id": checked_manifest.project_id,
            "snapshot_id": checked_manifest.snapshot_id,
            "case_id": case_id,
            "family_id": family_id,
            "mechanism_id": mechanism_id,
            "dataset_id": dataset_id,
            "variant_id": variant_id,
            "variant_content_sha256": variant_content_sha256,
            "case_content_sha256": case_content_sha256,
            "evidence_bundle_id": evidence_bundle_id,
            "evidence_content_sha256": evidence_content_sha256,
            "lineage_graph_id": lineage_graph_id,
            "lineage_sha256": lineage_sha256,
            "visibility_projection_sha256": visibility_projection_sha256,
            "authorization_ref": checked_manifest.authorization_ref,
            "provenance_sha256": provenance_sha256,
            "visibility": visibility,
        }
        return cls(
            reference_id=_reference_id(payload),
            manifest_reference_id=checked_manifest.reference_id,
            manifest_content_sha256=checked_manifest.manifest_content_sha256,
            project_id=checked_manifest.project_id,
            snapshot_id=checked_manifest.snapshot_id,
            case_id=case_id,
            family_id=family_id,
            mechanism_id=mechanism_id,
            dataset_id=dataset_id,
            variant_id=variant_id,
            variant_content_sha256=variant_content_sha256,
            case_content_sha256=case_content_sha256,
            evidence_bundle_id=evidence_bundle_id,
            evidence_content_sha256=evidence_content_sha256,
            lineage_graph_id=lineage_graph_id,
            lineage_sha256=lineage_sha256,
            visibility_projection_sha256=visibility_projection_sha256,
            authorization_ref=checked_manifest.authorization_ref,
            provenance_sha256=provenance_sha256,
            visibility=visibility,
        )


class ModelPolicyReference(_StrictFrozenModel):
    """Opaque model-policy binding supplied by an authorized manifest owner."""

    schema_version: Literal["evaluation-model-policy-reference/v1"] = (
        MODEL_POLICY_REFERENCE_SCHEMA_VERSION
    )
    reference_id: OpaqueReference
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    project_id: ProjectId
    snapshot_id: SnapshotId
    policy_content_sha256: Sha256
    provider_ref: OpaqueReference
    model_ref: OpaqueReference
    model_version_ref: OpaqueReference
    resource_policy_ref: OpaqueReference
    prompt_policy_ref: OpaqueReference
    response_schema_sha256: Sha256
    authorization_ref: OpaqueReference
    provenance_sha256: Sha256
    visibility: ExecutionVisibility

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_reference_id": self.manifest_reference_id,
            "manifest_content_sha256": self.manifest_content_sha256,
            "project_id": self.project_id,
            "snapshot_id": self.snapshot_id,
            "policy_content_sha256": self.policy_content_sha256,
            "provider_ref": self.provider_ref,
            "model_ref": self.model_ref,
            "model_version_ref": self.model_version_ref,
            "resource_policy_ref": self.resource_policy_ref,
            "prompt_policy_ref": self.prompt_policy_ref,
            "response_schema_sha256": self.response_schema_sha256,
            "authorization_ref": self.authorization_ref,
            "provenance_sha256": self.provenance_sha256,
            "visibility": self.visibility,
        }

    @model_validator(mode="after")
    def _reference_identity_matches(self) -> Self:
        if self.reference_id != _reference_id(self.identity_payload()):
            raise ValueError("model-policy reference_id does not match canonical identity")
        return self

    def canonical_sha256(self) -> str:
        return canonical_execution_sha256(self.model_dump(mode="json"))

    @classmethod
    def build(
        cls,
        *,
        manifest: EvaluationManifestReference,
        policy_content_sha256: str,
        provider_ref: str,
        model_ref: str,
        model_version_ref: str,
        resource_policy_ref: str,
        prompt_policy_ref: str,
        response_schema_sha256: str,
        provenance_sha256: str,
        visibility: ExecutionVisibility,
    ) -> Self:
        checked_manifest = EvaluationManifestReference.model_validate(
            manifest.model_dump(mode="python")
        )
        payload = {
            "schema_version": MODEL_POLICY_REFERENCE_SCHEMA_VERSION,
            "manifest_reference_id": checked_manifest.reference_id,
            "manifest_content_sha256": checked_manifest.manifest_content_sha256,
            "project_id": checked_manifest.project_id,
            "snapshot_id": checked_manifest.snapshot_id,
            "policy_content_sha256": policy_content_sha256,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "model_version_ref": model_version_ref,
            "resource_policy_ref": resource_policy_ref,
            "prompt_policy_ref": prompt_policy_ref,
            "response_schema_sha256": response_schema_sha256,
            "authorization_ref": checked_manifest.authorization_ref,
            "provenance_sha256": provenance_sha256,
            "visibility": visibility,
        }
        return cls(
            reference_id=_reference_id(payload),
            manifest_reference_id=checked_manifest.reference_id,
            manifest_content_sha256=checked_manifest.manifest_content_sha256,
            project_id=checked_manifest.project_id,
            snapshot_id=checked_manifest.snapshot_id,
            policy_content_sha256=policy_content_sha256,
            provider_ref=provider_ref,
            model_ref=model_ref,
            model_version_ref=model_version_ref,
            resource_policy_ref=resource_policy_ref,
            prompt_policy_ref=prompt_policy_ref,
            response_schema_sha256=response_schema_sha256,
            authorization_ref=checked_manifest.authorization_ref,
            provenance_sha256=provenance_sha256,
            visibility=visibility,
        )


def _request_identity_payload(
    *,
    manifest: EvaluationManifestReference,
    case: EvaluationCaseReference,
    model_policy: ModelPolicyReference,
    context_sha256: str,
    prompt_sha256: str,
    response_schema_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": REQUEST_IDENTITY_SCHEMA_VERSION,
        "manifest_reference_id": manifest.reference_id,
        "manifest_content_sha256": manifest.manifest_content_sha256,
        "project_id": manifest.project_id,
        "snapshot_id": manifest.snapshot_id,
        "case_reference_id": case.reference_id,
        "case_content_sha256": case.case_content_sha256,
        "family_id": case.family_id,
        "mechanism_id": case.mechanism_id,
        "dataset_id": case.dataset_id,
        "variant_id": case.variant_id,
        "variant_content_sha256": case.variant_content_sha256,
        "evidence_bundle_id": case.evidence_bundle_id,
        "evidence_content_sha256": case.evidence_content_sha256,
        "visibility_projection_sha256": case.visibility_projection_sha256,
        "case_visibility": case.visibility,
        "model_policy_reference_id": model_policy.reference_id,
        "model_policy_content_sha256": model_policy.policy_content_sha256,
        "provider_ref": model_policy.provider_ref,
        "model_ref": model_policy.model_ref,
        "model_version_ref": model_policy.model_version_ref,
        "resource_policy_ref": model_policy.resource_policy_ref,
        "prompt_policy_ref": model_policy.prompt_policy_ref,
        "context_sha256": context_sha256,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": response_schema_sha256,
        "authorization_ref": manifest.authorization_ref,
    }


def _attempt_identity_payload(
    *,
    request_identity_sha256: str,
    attempt_ordinal: int,
) -> dict[str, object]:
    return {
        "schema_version": ATTEMPT_IDENTITY_SCHEMA_VERSION,
        "request_identity_sha256": request_identity_sha256,
        "attempt_ordinal": attempt_ordinal,
    }


class AttemptIdentity(_StrictFrozenModel):
    """Immutable identity for one bounded technical attempt."""

    schema_version: Literal["evaluation-attempt-identity/v1"] = ATTEMPT_IDENTITY_SCHEMA_VERSION
    attempt_id: OpaqueReference
    attempt_identity_sha256: Sha256
    request_identity_sha256: Sha256
    attempt_ordinal: int = Field(ge=1)
    manifest: EvaluationManifestReference
    case: EvaluationCaseReference
    model_policy: ModelPolicyReference
    variant_id: OpaqueReference
    variant_content_sha256: Sha256
    context_sha256: Sha256
    prompt_sha256: Sha256
    response_schema_sha256: Sha256

    @model_validator(mode="after")
    def _references_and_hashes_reconcile(self) -> Self:
        if (
            self.case.manifest_reference_id != self.manifest.reference_id
            or self.case.manifest_content_sha256 != self.manifest.manifest_content_sha256
            or self.model_policy.manifest_reference_id != self.manifest.reference_id
            or self.model_policy.manifest_content_sha256 != self.manifest.manifest_content_sha256
        ):
            raise ValueError("attempt references do not bind the same manifest")
        if (
            self.case.project_id != self.manifest.project_id
            or self.model_policy.project_id != self.manifest.project_id
        ):
            raise ValueError("attempt references do not bind the same project")
        if (
            self.case.snapshot_id != self.manifest.snapshot_id
            or self.model_policy.snapshot_id != self.manifest.snapshot_id
        ):
            raise ValueError("attempt references do not bind the same snapshot")
        if (
            self.case.authorization_ref != self.manifest.authorization_ref
            or self.model_policy.authorization_ref != self.manifest.authorization_ref
        ):
            raise ValueError("attempt references do not bind the same authorization")
        if self.response_schema_sha256 != self.model_policy.response_schema_sha256:
            raise ValueError("attempt response schema differs from the model-policy reference")
        if (
            self.variant_id != self.case.variant_id
            or self.variant_content_sha256 != self.case.variant_content_sha256
        ):
            raise ValueError("attempt variant does not match the case reference")

        request_payload = _request_identity_payload(
            manifest=self.manifest,
            case=self.case,
            model_policy=self.model_policy,
            context_sha256=self.context_sha256,
            prompt_sha256=self.prompt_sha256,
            response_schema_sha256=self.response_schema_sha256,
        )
        expected_request_sha256 = canonical_execution_sha256(request_payload)
        if self.request_identity_sha256 != expected_request_sha256:
            raise ValueError("request_identity_sha256 does not match immutable request fields")

        attempt_payload = _attempt_identity_payload(
            request_identity_sha256=self.request_identity_sha256,
            attempt_ordinal=self.attempt_ordinal,
        )
        expected_attempt_sha256 = canonical_execution_sha256(attempt_payload)
        if self.attempt_identity_sha256 != expected_attempt_sha256:
            raise ValueError("attempt_identity_sha256 does not match canonical attempt fields")
        if self.attempt_id != f"ev-{expected_attempt_sha256}":
            raise ValueError("attempt_id does not match attempt_identity_sha256")
        return self

    def canonical_sha256(self) -> str:
        return canonical_execution_sha256(self.model_dump(mode="json"))

    @classmethod
    def build(
        cls,
        *,
        manifest: EvaluationManifestReference,
        case: EvaluationCaseReference,
        model_policy: ModelPolicyReference,
        context_sha256: str,
        prompt_sha256: str,
        response_schema_sha256: str,
        attempt_ordinal: int,
    ) -> Self:
        checked_manifest = EvaluationManifestReference.model_validate(
            manifest.model_dump(mode="python")
        )
        checked_case = EvaluationCaseReference.model_validate(case.model_dump(mode="python"))
        checked_policy = ModelPolicyReference.model_validate(model_policy.model_dump(mode="python"))
        request_payload = _request_identity_payload(
            manifest=checked_manifest,
            case=checked_case,
            model_policy=checked_policy,
            context_sha256=context_sha256,
            prompt_sha256=prompt_sha256,
            response_schema_sha256=response_schema_sha256,
        )
        request_identity_sha256 = canonical_execution_sha256(request_payload)
        attempt_payload = _attempt_identity_payload(
            request_identity_sha256=request_identity_sha256,
            attempt_ordinal=attempt_ordinal,
        )
        attempt_identity_sha256 = canonical_execution_sha256(attempt_payload)
        return cls(
            attempt_id=f"ev-{attempt_identity_sha256}",
            attempt_identity_sha256=attempt_identity_sha256,
            request_identity_sha256=request_identity_sha256,
            attempt_ordinal=attempt_ordinal,
            manifest=checked_manifest,
            case=checked_case,
            model_policy=checked_policy,
            variant_id=checked_case.variant_id,
            variant_content_sha256=checked_case.variant_content_sha256,
            context_sha256=context_sha256,
            prompt_sha256=prompt_sha256,
            response_schema_sha256=response_schema_sha256,
        )


class TechnicalIssue(_StrictFrozenModel):
    """Public-safe technical issue with a hashed message rather than raw text."""

    schema_version: Literal["evaluation-technical-issue/v1"] = TECHNICAL_ISSUE_SCHEMA_VERSION
    issue_id: OpaqueReference
    issue_sha256: Sha256
    code: TechnicalCode
    stage: TechnicalCode
    severity: TechnicalSeverity
    subject_reference_id: OpaqueReference
    subject_sha256: Sha256
    public_message: str
    message_sha256: Sha256
    authorization_ref: OpaqueReference
    provenance_sha256: Sha256
    visibility: ExecutionVisibility

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "severity": self.severity,
            "subject_reference_id": self.subject_reference_id,
            "subject_sha256": self.subject_sha256,
            "public_message": self.public_message,
            "message_sha256": self.message_sha256,
            "authorization_ref": self.authorization_ref,
            "provenance_sha256": self.provenance_sha256,
            "visibility": self.visibility,
        }

    @model_validator(mode="after")
    def _issue_identity_matches(self) -> Self:
        if self.subject_reference_id != f"ev-{self.subject_sha256}":
            raise ValueError("subject_reference_id does not match subject_sha256")
        expected_public_message = _public_issue_message(
            code=self.code,
            stage=self.stage,
            severity=self.severity,
        )
        if self.public_message != expected_public_message:
            raise ValueError("public_message must be derived only from stable issue metadata")
        expected_sha256 = canonical_execution_sha256(self.identity_payload())
        if self.issue_sha256 != expected_sha256:
            raise ValueError("issue_sha256 does not match canonical issue fields")
        if self.issue_id != f"ev-{expected_sha256}":
            raise ValueError("issue_id does not match issue_sha256")
        return self

    @classmethod
    def build(
        cls,
        *,
        code: str,
        stage: str,
        severity: TechnicalSeverity,
        subject_reference_id: str,
        message: str,
        authorization_ref: str,
        provenance_sha256: str,
        visibility: ExecutionVisibility,
    ) -> Self:
        normalized_message = normalize_text(message, label="technical issue message", max_length=4096)
        message_sha256 = hashlib.sha256(normalized_message.encode("utf-8")).hexdigest()
        subject_sha256 = subject_reference_id.removeprefix("ev-")
        public_message = _public_issue_message(code=code, stage=stage, severity=severity)
        payload = {
            "schema_version": TECHNICAL_ISSUE_SCHEMA_VERSION,
            "code": code,
            "stage": stage,
            "severity": severity,
            "subject_reference_id": subject_reference_id,
            "subject_sha256": subject_sha256,
            "public_message": public_message,
            "message_sha256": message_sha256,
            "authorization_ref": authorization_ref,
            "provenance_sha256": provenance_sha256,
            "visibility": visibility,
        }
        issue_sha256 = canonical_execution_sha256(payload)
        return cls(
            issue_id=f"ev-{issue_sha256}",
            issue_sha256=issue_sha256,
            code=code,
            stage=stage,
            severity=severity,
            subject_reference_id=subject_reference_id,
            subject_sha256=subject_sha256,
            public_message=public_message,
            message_sha256=message_sha256,
            authorization_ref=authorization_ref,
            provenance_sha256=provenance_sha256,
            visibility=visibility,
        )


def _public_issue_message(*, code: str, stage: str, severity: TechnicalSeverity) -> str:
    return f"{severity}: {code} at {stage}"


def validate_unique_case_references(cases: tuple[EvaluationCaseReference, ...]) -> None:
    """Reject duplicate case/variant or reference identities before execution."""

    reference_ids: set[str] = set()
    case_variant_ids: set[tuple[str, str]] = set()
    for case in cases:
        checked = EvaluationCaseReference.model_validate(case.model_dump(mode="python"))
        if checked.reference_id in reference_ids:
            raise EvaluationContractError("duplicate evaluation case reference identity")
        case_variant_identity = (checked.case_id, checked.variant_id)
        if case_variant_identity in case_variant_ids:
            raise EvaluationContractError("duplicate opaque evaluation case/variant identity")
        reference_ids.add(checked.reference_id)
        case_variant_ids.add(case_variant_identity)
