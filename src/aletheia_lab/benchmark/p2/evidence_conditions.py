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
from dataclasses import dataclass
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
from aletheia_lab.benchmark.p2.identity import FaultTypeName
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
EvidenceBundleDuplicateKind = Literal["exact_replay", "effective_content", "near_content"]
_CONDITION_ORDER: Final[dict[str, int]] = {"full": 0, "missing_key": 1, "noisy": 2}
_NEAR_DUPLICATE_THRESHOLD: Final[float] = 0.90


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
    fault_type: str,
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
        "fault_type": fault_type,
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
    fault_type: FaultTypeName
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
                fault_type=self.fault_type,
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


class EvidenceBundleDuplicateFinding(_StrictFrozenModel):
    """One canonical duplicate relationship between bundles from two families."""

    kind: EvidenceBundleDuplicateKind
    evidence_condition: EvidenceConditionName
    evidence_bundle_id: EvidenceBundleId
    duplicate_of_bundle_id: EvidenceBundleId
    case_family_id: FamilyId
    duplicate_of_family_id: FamilyId
    content_sha256: Sha256
    duplicate_of_content_sha256: Sha256
    similarity: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _finding_semantics_are_consistent(self) -> EvidenceBundleDuplicateFinding:
        if self.evidence_bundle_id == self.duplicate_of_bundle_id:
            if self.kind != "exact_replay":
                raise ValueError("equal bundle IDs must be classified as an exact replay")
        elif self.kind == "exact_replay":
            raise ValueError("an exact replay must carry equal bundle IDs")
        if self.case_family_id == self.duplicate_of_family_id and self.kind != "exact_replay":
            raise ValueError("condition siblings within one family are not duplicate families")
        if self.kind in {"exact_replay", "effective_content"}:
            if self.content_sha256 != self.duplicate_of_content_sha256:
                raise ValueError("blocking duplicates require equal content hashes")
            if self.similarity != 1.0:
                raise ValueError("blocking duplicates require similarity 1.0")
        else:
            if self.content_sha256 == self.duplicate_of_content_sha256:
                raise ValueError("equal content hashes are effective duplicates, not near duplicates")
            if not _NEAR_DUPLICATE_THRESHOLD <= self.similarity < 1.0:
                raise ValueError("near-content similarity must lie in [0.90, 1.0)")
        return self


class EvidenceBundleDuplicateAudit(_StrictFrozenModel):
    """Deterministic duplicate findings for a collection of evidence bundles."""

    schema_version: Literal["p2-evidence-bundle-duplicate-audit/v1"] = (
        "p2-evidence-bundle-duplicate-audit/v1"
    )
    findings: tuple[EvidenceBundleDuplicateFinding, ...]

    @model_validator(mode="after")
    def _findings_are_canonical(self) -> EvidenceBundleDuplicateAudit:
        keys = tuple(
            (
                finding.evidence_condition,
                finding.kind,
                finding.case_family_id,
                finding.duplicate_of_family_id,
            )
            for finding in self.findings
        )
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate audit findings must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("duplicate audit findings must use canonical order")
        return self

    def has_blockers(self) -> bool:
        """Return whether exact or effective duplicates invalidate the collection."""

        return any(finding.kind != "near_content" for finding in self.findings)


@dataclass(frozen=True, slots=True)
class EvidenceConditionBuild:
    """Authoritative inputs needed to reproduce one family's condition set."""

    candidate: ValidatedMechanismCandidate
    family: FamilyCensusEntry
    evidence: MechanismDiagnosisEvidence | None


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
        fault_type=candidate.fault_type,
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
        fault_type=candidate.fault_type,
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


def _content_payload(bundle: EvidenceConditionBundle) -> dict[str, object]:
    items = bundle.diagnosis_projection.get("items")
    if not isinstance(items, list):  # pragma: no cover - projection builder fixes the shape
        _fail("diagnosis projection items must be a list")
    return {
        "schema_version": "p2-evidence-content-fingerprint/v1",
        "fault_type": bundle.fault_type,
        "evidence_condition": bundle.evidence_condition,
        "items": items,
    }


def evidence_content_sha256(bundle: EvidenceConditionBundle) -> str:
    """Hash diagnosis content while intentionally excluding family envelope bindings."""

    bundle = _revalidated(bundle)
    return canonical_sha256(_content_payload(bundle))


def _evidence_atoms(value: object, path: str = "$") -> frozenset[str]:
    if isinstance(value, dict):
        atoms: set[str] = set()
        for key in sorted(value):
            atoms.update(_evidence_atoms(value[key], f"{path}.{key}"))
        return frozenset(atoms)
    if isinstance(value, list):
        atoms = set()
        for index, nested in enumerate(value):
            atoms.update(_evidence_atoms(nested, f"{path}[{index}]"))
        return frozenset(atoms)
    return frozenset({canonical_sha256({"path": path, "value": value})})


def evidence_content_similarity(
    left: EvidenceConditionBundle,
    right: EvidenceConditionBundle,
) -> float:
    """Return deterministic Jaccard similarity over canonical diagnosis atoms."""

    left = _revalidated(left)
    right = _revalidated(right)
    left_atoms = _evidence_atoms(_content_payload(left))
    right_atoms = _evidence_atoms(_content_payload(right))
    union = left_atoms | right_atoms
    if not union:  # pragma: no cover - condition projections always contain items
        return 1.0
    return len(left_atoms & right_atoms) / len(union)


def audit_evidence_bundle_duplicates(
    bundles: tuple[EvidenceConditionBundle, ...],
) -> EvidenceBundleDuplicateAudit:
    """Classify cross-family exact, effective and near-content duplicates."""

    bundles = tuple(_revalidated(bundle) for bundle in bundles)
    findings: list[EvidenceBundleDuplicateFinding] = []
    for index, left in enumerate(bundles):
        for right in bundles[index + 1 :]:
            if left.evidence_condition != right.evidence_condition:
                continue
            if left.fault_type != right.fault_type:
                continue
            if (
                left.case_family_id == right.case_family_id
                and left.evidence_bundle_id != right.evidence_bundle_id
            ):
                continue
            left_hash = evidence_content_sha256(left)
            right_hash = evidence_content_sha256(right)
            if left.evidence_bundle_id == right.evidence_bundle_id:
                kind: EvidenceBundleDuplicateKind = "exact_replay"
                similarity = 1.0
            elif left_hash == right_hash:
                kind = "effective_content"
                similarity = 1.0
            else:
                similarity = evidence_content_similarity(left, right)
                if similarity < _NEAR_DUPLICATE_THRESHOLD:
                    continue
                kind = "near_content"
            first, second = sorted(
                (left, right),
                key=lambda bundle: (bundle.case_family_id, bundle.evidence_bundle_id),
            )
            findings.append(
                EvidenceBundleDuplicateFinding(
                    kind=kind,
                    evidence_condition=first.evidence_condition,
                    evidence_bundle_id=first.evidence_bundle_id,
                    duplicate_of_bundle_id=second.evidence_bundle_id,
                    case_family_id=first.case_family_id,
                    duplicate_of_family_id=second.case_family_id,
                    content_sha256=evidence_content_sha256(first),
                    duplicate_of_content_sha256=evidence_content_sha256(second),
                    similarity=similarity,
                )
            )
    findings.sort(
        key=lambda finding: (
            finding.evidence_condition,
            finding.kind,
            finding.case_family_id,
            finding.duplicate_of_family_id,
        )
    )
    return EvidenceBundleDuplicateAudit(findings=tuple(findings))


def build_evidence_bundle_collection(
    builds: tuple[EvidenceConditionBuild, ...],
) -> tuple[EvidenceConditionBundle, ...]:
    """Build a canonical multi-family collection from authoritative sources."""

    family_ids = tuple(build.family.case_family_id for build in builds)
    candidate_ids = tuple(build.candidate.candidate_id for build in builds)
    if len(set(family_ids)) != len(family_ids):
        _fail("an evidence collection must not repeat a family")
    if len(set(candidate_ids)) != len(candidate_ids):
        _fail("an evidence collection must not repeat a candidate")
    bundles = tuple(
        bundle
        for build in builds
        for bundle in build_evidence_condition_bundles(
            candidate=build.candidate,
            family=build.family,
            evidence=build.evidence,
        )
    )
    return tuple(
        sorted(
            bundles,
            key=lambda bundle: (
                bundle.case_family_id,
                _CONDITION_ORDER[bundle.evidence_condition],
            ),
        )
    )


def validate_evidence_bundle_collection(
    bundles: tuple[EvidenceConditionBundle, ...],
    *,
    builds: tuple[EvidenceConditionBuild, ...],
) -> EvidenceBundleDuplicateAudit:
    """Reproduce a collection and reject replay or effective duplicate inflation."""

    bundles = tuple(_revalidated(bundle) for bundle in bundles)
    expected = build_evidence_bundle_collection(builds)
    actual_payload = tuple(
        bundle.model_dump(mode="json")
        for bundle in sorted(
            bundles,
            key=lambda bundle: (
                bundle.case_family_id,
                _CONDITION_ORDER[bundle.evidence_condition],
            ),
        )
    )
    expected_payload = tuple(bundle.model_dump(mode="json") for bundle in expected)
    if actual_payload != expected_payload:
        _fail("evidence collection differs from authoritative condition builds")
    audit = audit_evidence_bundle_duplicates(bundles)
    if audit.has_blockers():
        _fail("evidence collection contains exact or effective duplicate content")
    return audit


def is_evidence_bundle_id(value: str) -> bool:
    """Return whether ``value`` belongs to the evidence-bundle namespace."""

    return re.fullmatch(EVIDENCE_BUNDLE_ID_PATTERN, value) is not None
