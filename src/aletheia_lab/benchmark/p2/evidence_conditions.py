"""Versioned, canonical evidence-condition bundles for diagnosis contexts.

The diagnosis projection module decides which observations are visible for a
family.  This module turns those projections into immutable transport
artifacts.  Every identifier and digest is derived from canonical content, and
validation rebuilds the complete sibling set from authoritative inputs instead
of trusting caller-provided metadata.

Bundle envelopes are evaluator-side records.  Only ``diagnosis_projection`` is
safe to pass to a diagnoser; candidate, family and condition identifiers exist
in the envelope solely for audit and pairing.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidateId,
    ContextEntry,
    ContextId,
    EvidenceConditionName,
    FamilyCensusEntry,
    FamilyId,
    Sha256,
    context_id_for,
)
from aletheia_lab.benchmark.p2.evidence_projection import (
    MechanismDiagnosisEvidence,
    build_diagnosis_contexts,
)
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate
from aletheia_lab.benchmark.p2.validation import ContractViolation

EVIDENCE_CONDITION_BUNDLE_SCHEMA_VERSION: Final[
    Literal["p2-evidence-condition-bundle/v2"]
] = "p2-evidence-condition-bundle/v2"
EVIDENCE_CONDITION_BUILDER_VERSION: Final[Literal["p2-condition-builder/v2"]] = (
    "p2-condition-builder/v2"
)
EVIDENCE_EXECUTION_ID_SCHEMA_VERSION: Final[Literal["p2-evidence-execution-id/v1"]] = (
    "p2-evidence-execution-id/v1"
)
EVIDENCE_BUNDLE_HASH_SCHEMA_VERSION: Final[Literal["p2-evidence-bundle-hash/v1"]] = (
    "p2-evidence-bundle-hash/v1"
)

EVIDENCE_BUNDLE_ID_PATTERN: Final[str] = r"^p2-evidence-bundle-[0-9a-f]{64}$"
EVIDENCE_EXECUTION_ID_PATTERN: Final[str] = r"^p2-execution-[0-9a-f]{64}$"

EvidenceBundleId = Annotated[str, Field(pattern=EVIDENCE_BUNDLE_ID_PATTERN)]
EvidenceExecutionId = Annotated[str, Field(pattern=EVIDENCE_EXECUTION_ID_PATTERN)]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class EvidenceConditionError(ContractViolation):
    """Raised when a condition bundle cannot be reproduced safely."""


def _fail(message: str) -> NoReturn:
    raise EvidenceConditionError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run model validators after any potentially unsafe construction."""

    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def evidence_execution_id_for(candidate: ValidatedMechanismCandidate) -> str:
    """Derive an opaque ID from the complete validated execution record."""

    candidate = _revalidated(candidate)
    digest = canonical_sha256(
        {
            "schema_version": EVIDENCE_EXECUTION_ID_SCHEMA_VERSION,
            "execution": candidate.execution.model_dump(mode="json"),
        }
    )
    return f"p2-execution-{digest}"


def _bundle_hash_payload(
    *,
    diagnosis_context_id: str,
    case_family_id: str,
    candidate_id: str,
    evidence_condition: str,
    source_execution_id: str,
    source_binding_sha256: str,
    diagnosis_projection: dict[str, object],
    diagnosis_projection_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": EVIDENCE_BUNDLE_HASH_SCHEMA_VERSION,
        "bundle_schema_version": EVIDENCE_CONDITION_BUNDLE_SCHEMA_VERSION,
        "builder_version": EVIDENCE_CONDITION_BUILDER_VERSION,
        "diagnosis_context_id": diagnosis_context_id,
        "case_family_id": case_family_id,
        "candidate_id": candidate_id,
        "evidence_condition": evidence_condition,
        "source_execution_id": source_execution_id,
        "source_binding_sha256": source_binding_sha256,
        "diagnosis_projection": diagnosis_projection,
        "diagnosis_projection_sha256": diagnosis_projection_sha256,
    }


class EvidenceConditionBundle(_StrictFrozenModel):
    """One immutable evaluator-side envelope around a safe diagnosis payload."""

    schema_version: Literal["p2-evidence-condition-bundle/v2"] = (
        EVIDENCE_CONDITION_BUNDLE_SCHEMA_VERSION
    )
    builder_version: Literal["p2-condition-builder/v2"] = EVIDENCE_CONDITION_BUILDER_VERSION
    evidence_bundle_id: EvidenceBundleId
    diagnosis_context_id: ContextId
    case_family_id: FamilyId
    candidate_id: CandidateId
    evidence_condition: EvidenceConditionName
    source_execution_id: EvidenceExecutionId
    source_binding_sha256: Sha256
    diagnosis_projection: dict[str, object]
    diagnosis_projection_sha256: Sha256
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def _identifiers_and_hashes_are_derived(self) -> EvidenceConditionBundle:
        expected_context_id = context_id_for(
            case_family_id=self.case_family_id,
            evidence_condition=self.evidence_condition,
        )
        if self.diagnosis_context_id != expected_context_id:
            raise ValueError("diagnosis_context_id must bind family and evidence condition")

        # Reuse the public diagnosis boundary as the single leakage policy.
        ContextEntry(
            diagnosis_context_id=self.diagnosis_context_id,
            case_family_id=self.case_family_id,
            evidence_condition=self.evidence_condition,
            diagnosis_projection=self.diagnosis_projection,
            diagnosis_projection_sha256=self.diagnosis_projection_sha256,
        )

        expected_projection_hash = canonical_sha256(self.diagnosis_projection)
        if self.diagnosis_projection_sha256 != expected_projection_hash:
            raise ValueError("diagnosis_projection_sha256 does not match canonical payload")

        expected_bundle_hash = canonical_sha256(
            _bundle_hash_payload(
                diagnosis_context_id=self.diagnosis_context_id,
                case_family_id=self.case_family_id,
                candidate_id=self.candidate_id,
                evidence_condition=self.evidence_condition,
                source_execution_id=self.source_execution_id,
                source_binding_sha256=self.source_binding_sha256,
                diagnosis_projection=self.diagnosis_projection,
                diagnosis_projection_sha256=self.diagnosis_projection_sha256,
            )
        )
        if self.bundle_sha256 != expected_bundle_hash:
            raise ValueError("bundle_sha256 does not match the canonical bundle")
        if self.evidence_bundle_id != f"p2-evidence-bundle-{expected_bundle_hash}":
            raise ValueError("evidence_bundle_id must be derived from bundle_sha256")
        return self


def _bundle_from_context(
    *,
    context: ContextEntry,
    candidate: ValidatedMechanismCandidate,
) -> EvidenceConditionBundle:
    source_execution_id = evidence_execution_id_for(candidate)
    source_binding_sha256 = candidate.binding_sha256()
    hash_payload = _bundle_hash_payload(
        diagnosis_context_id=context.diagnosis_context_id,
        case_family_id=context.case_family_id,
        candidate_id=candidate.candidate_id,
        evidence_condition=context.evidence_condition,
        source_execution_id=source_execution_id,
        source_binding_sha256=source_binding_sha256,
        diagnosis_projection=context.diagnosis_projection,
        diagnosis_projection_sha256=context.diagnosis_projection_sha256,
    )
    bundle_sha256 = canonical_sha256(hash_payload)
    return EvidenceConditionBundle(
        evidence_bundle_id=f"p2-evidence-bundle-{bundle_sha256}",
        diagnosis_context_id=context.diagnosis_context_id,
        case_family_id=context.case_family_id,
        candidate_id=candidate.candidate_id,
        evidence_condition=context.evidence_condition,
        source_execution_id=source_execution_id,
        source_binding_sha256=source_binding_sha256,
        diagnosis_projection=context.diagnosis_projection,
        diagnosis_projection_sha256=context.diagnosis_projection_sha256,
        bundle_sha256=bundle_sha256,
    )


def build_evidence_condition_bundles(
    *,
    candidate: ValidatedMechanismCandidate,
    family: FamilyCensusEntry,
    evidence: MechanismDiagnosisEvidence | None,
) -> tuple[EvidenceConditionBundle, ...]:
    """Build the canonical condition envelopes allowed for one family."""

    candidate = _revalidated(candidate)
    family = _revalidated(family)
    contexts = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)
    return tuple(_bundle_from_context(context=context, candidate=candidate) for context in contexts)


def validate_evidence_condition_bundles(
    bundles: tuple[EvidenceConditionBundle, ...],
    *,
    candidate: ValidatedMechanismCandidate,
    family: FamilyCensusEntry,
    evidence: MechanismDiagnosisEvidence | None,
) -> tuple[EvidenceConditionBundle, ...]:
    """Rebuild the complete bundle set and reject omission, tamper or reordering."""

    bundles = tuple(_revalidated(bundle) for bundle in bundles)
    expected = build_evidence_condition_bundles(
        candidate=candidate,
        family=family,
        evidence=evidence,
    )
    actual_payload = tuple(bundle.model_dump(mode="json") for bundle in bundles)
    expected_payload = tuple(bundle.model_dump(mode="json") for bundle in expected)
    if actual_payload != expected_payload:
        _fail("evidence bundles differ from the canonical condition build")
    return bundles


def is_evidence_bundle_id(value: str) -> bool:
    """Return whether ``value`` belongs to the evidence-bundle namespace."""

    return re.fullmatch(EVIDENCE_BUNDLE_ID_PATTERN, value) is not None
