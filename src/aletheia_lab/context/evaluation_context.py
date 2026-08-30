"""Build deterministic, visibility-safe evaluation context without I/O."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    TechnicalIssue,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.project.identity import (
    PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
    PROJECT_EVIDENCE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_ID_PATTERN,
)
from aletheia_lab.project.regression import ProjectEvidenceView

EVALUATION_CONTEXT_SCHEMA_VERSION: Final[Literal["evaluation-context/v1"]] = (
    "evaluation-context/v1"
)
CONTEXT_BLOCKER_STAGE: Final[Literal["context_boundary"]] = "context_boundary"

_OPAQUE_REFERENCE_PATTERN: Final[str] = r"^ev-[0-9a-f]{64}$"
_FALLBACK_OPAQUE_REFERENCE: Final[str] = f"ev-{'0' * 64}"
_FALLBACK_SHA256: Final[str] = "0" * 64
_ABSOLUTE_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^[a-z]:[\\/]|^\\\\|^//|^/)", flags=re.IGNORECASE
)
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE
)
_FORBIDDEN_KEY_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "answer",
        "cache",
        "condition",
        "credential",
        "email",
        "evaluator",
        "gold",
        "groundtruth",
        "hidden",
        "label",
        "metadata",
        "outcome",
        "password",
        "path",
        "payload",
        "prompt",
        "raw",
        "rubric",
        "secret",
        "summary",
        "token",
        "variant",
    }
)
_FORBIDDEN_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"answer[ _-]?key|"
    r"api[ _-]?key|"
    r"bearer[ _-]?token|"
    r"causal[ _-]?mechanism|"
    r"condition|"
    r"condition[ _-]?label|"
    r"evaluator|"
    r"gold[ _-]?(?:label|rationale)|"
    r"ground[ _-]?truth|"
    r"hidden[ _-]?(?:label|outcome|failure|ground)|"
    r"password|"
    r"secret|"
    r"symlink|"
    r"variant|"
    r"junction"
    r")",
    flags=re.IGNORECASE,
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProjectId = Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
SnapshotId = Annotated[str, Field(pattern=SNAPSHOT_ID_PATTERN)]
EvidenceBundleId = Annotated[str, Field(pattern=PROJECT_EVIDENCE_BUNDLE_ID_PATTERN)]
EvidenceId = Annotated[str, Field(pattern=PROJECT_EVIDENCE_ID_PATTERN)]
OpaqueReference = Annotated[str, Field(pattern=_OPAQUE_REFERENCE_PATTERN)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class ContextBoundaryError(ValueError):
    """Fail-closed error exposing only a public-safe structured technical issue."""

    def __init__(self, issue: TechnicalIssue) -> None:
        self.issue = issue
        super().__init__(issue.public_message)


def _normalized_token(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def _contains_control_or_bidi(value: str) -> bool:
    return any(
        unicodedata.category(character).startswith("C")
        or unicodedata.bidirectional(character)
        in {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
        for character in value
    )


def _text_violation(value: str) -> str | None:
    if _contains_control_or_bidi(value):
        return "unicode_control"
    if not value.isascii():
        return "unicode_homoglyph"
    if _ABSOLUTE_PATH_PATTERN.search(value) is not None:
        return "absolute_path"
    if _EMAIL_PATTERN.search(value) is not None:
        return "email_like_pii"
    if _FORBIDDEN_TEXT_PATTERN.search(value) is not None:
        return "forbidden_text"
    return None


def find_visibility_violation(payload: object) -> str | None:
    """Return one stable blocker code without retaining sensitive source text."""

    def inspect(value: object) -> str | None:
        if value is None or isinstance(value, bool | int):
            return None
        if isinstance(value, float):
            return None if math.isfinite(value) else "nonfinite_number"
        if isinstance(value, str):
            return _text_violation(value)
        if isinstance(value, BaseModel):
            return inspect(value.model_dump(mode="json"))
        if isinstance(value, Mapping):
            for key in sorted(value, key=lambda item: str(item)):
                if not isinstance(key, str):
                    return "nonstring_mapping_key"
                normalized_key = _normalized_token(key)
                if any(marker in normalized_key for marker in _FORBIDDEN_KEY_MARKERS):
                    return "forbidden_field"
                key_violation = _text_violation(key)
                if key_violation is not None:
                    return key_violation
                nested_violation = inspect(value[key])
                if nested_violation is not None:
                    return nested_violation
            return None
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            for nested in value:
                nested_violation = inspect(nested)
                if nested_violation is not None:
                    return nested_violation
            return None
        return "unsupported_outbound_value"

    return inspect(payload)


class ContextEvidenceReference(_StrictFrozenModel):
    """One payload-free evidence reference eligible for outbound context."""

    evidence_id: EvidenceId
    source_sha256: Sha256
    provenance_evidence_ids: tuple[EvidenceId, ...]

    @field_validator("provenance_evidence_ids")
    @classmethod
    def _canonical_provenance(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("context evidence provenance IDs must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _does_not_self_reference(self) -> Self:
        if self.evidence_id in self.provenance_evidence_ids:
            raise ValueError("context evidence cannot cite itself")
        return self


class EvaluationContextPayload(_StrictFrozenModel):
    """Canonical outbound payload containing only approved evidence addresses."""

    schema_version: Literal["evaluation-context/v1"] = EVALUATION_CONTEXT_SCHEMA_VERSION
    context_id: OpaqueReference
    context_sha256: Sha256
    project_id: ProjectId
    snapshot_id: SnapshotId
    case_reference_id: OpaqueReference
    case_id: OpaqueReference
    evidence_bundle_id: EvidenceBundleId
    visibility_projection_sha256: Sha256
    selected_evidence: tuple[ContextEvidenceReference, ...]
    omitted_evidence_ids: tuple[EvidenceId, ...]

    @field_validator("selected_evidence")
    @classmethod
    def _canonical_selected(
        cls, values: tuple[ContextEvidenceReference, ...]
    ) -> tuple[ContextEvidenceReference, ...]:
        identifiers = tuple(value.evidence_id for value in values)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("selected context evidence IDs must be unique")
        source_hashes = tuple(value.source_sha256 for value in values)
        if len(source_hashes) != len(set(source_hashes)):
            raise ValueError("selected context evidence must not contain source aliases")
        return tuple(sorted(values, key=lambda value: value.evidence_id))

    @field_validator("omitted_evidence_ids")
    @classmethod
    def _canonical_omitted(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("omitted context evidence IDs must be unique")
        return tuple(sorted(values))

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "snapshot_id": self.snapshot_id,
            "case_reference_id": self.case_reference_id,
            "case_id": self.case_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "visibility_projection_sha256": self.visibility_projection_sha256,
            "selected_evidence": [
                value.model_dump(mode="json") for value in self.selected_evidence
            ],
            "omitted_evidence_ids": list(self.omitted_evidence_ids),
        }

    @model_validator(mode="after")
    def _identity_and_partition_reconcile(self) -> Self:
        selected_ids = {value.evidence_id for value in self.selected_evidence}
        if selected_ids & set(self.omitted_evidence_ids):
            raise ValueError("selected and omitted evidence sets must not overlap")
        expected_sha256 = canonical_execution_sha256(self.identity_payload())
        if self.context_sha256 != expected_sha256:
            raise ValueError("context_sha256 does not match canonical context content")
        if self.context_id != f"ev-{expected_sha256}":
            raise ValueError("context_id does not match context_sha256")
        if find_visibility_violation(self.model_dump(mode="json")) is not None:
            raise ValueError("evaluation context contains unsafe outbound material")
        return self

    def canonical_json(self) -> str:
        return canonical_execution_json(self.model_dump(mode="json"))


def _context_blocker(case: EvaluationCaseReference, code: str) -> ContextBoundaryError:
    issue = TechnicalIssue.build(
        code=code,
        stage=CONTEXT_BLOCKER_STAGE,
        severity="blocker",
        subject_reference_id=case.reference_id,
        message="Evaluation context was rejected at the visibility boundary.",
        authorization_ref=case.authorization_ref,
        provenance_sha256=case.provenance_sha256,
        visibility="public",
    )
    return ContextBoundaryError(issue)


def _untrusted_input_blocker(code: str) -> ContextBoundaryError:
    issue = TechnicalIssue.build(
        code=code,
        stage=CONTEXT_BLOCKER_STAGE,
        severity="blocker",
        subject_reference_id=_FALLBACK_OPAQUE_REFERENCE,
        message="Evaluation context was rejected at the visibility boundary.",
        authorization_ref=_FALLBACK_OPAQUE_REFERENCE,
        provenance_sha256=_FALLBACK_SHA256,
        visibility="public",
    )
    return ContextBoundaryError(issue)


def _checked_case(case: EvaluationCaseReference) -> EvaluationCaseReference:
    return EvaluationCaseReference.model_validate(case.model_dump(mode="python"))


def _checked_view(view: ProjectEvidenceView) -> ProjectEvidenceView:
    return ProjectEvidenceView.model_validate(view.model_dump(mode="python"))


def build_evaluation_context(
    *,
    case: EvaluationCaseReference,
    evidence_view: ProjectEvidenceView,
    selected_evidence_ids: tuple[str, ...],
) -> EvaluationContextPayload:
    """Build one pure, deterministic context from a P3 whitelist projection."""

    if not isinstance(case, EvaluationCaseReference):
        raise _untrusted_input_blocker("invalid_case_reference")
    if case.visibility not in {"public", "diagnosis", "evaluator"}:
        raise _untrusted_input_blocker("unknown_visibility")
    try:
        checked_case = _checked_case(case)
    except ValidationError:
        raise _untrusted_input_blocker("invalid_case_reference") from None
    try:
        checked_view = _checked_view(evidence_view)
    except (AttributeError, ValidationError):
        raise _context_blocker(checked_case, "invalid_evidence_projection") from None
    if checked_case.visibility not in {"public", "diagnosis"}:
        raise _context_blocker(checked_case, "visibility_not_outbound")
    if checked_case.project_id != checked_view.project_id:
        raise _context_blocker(checked_case, "cross_project_evidence")
    if checked_case.evidence_bundle_id != checked_view.evidence_bundle_id:
        raise _context_blocker(checked_case, "stale_evidence_bundle")
    if checked_case.visibility_projection_sha256 != checked_view.view_sha256:
        raise _context_blocker(checked_case, "stale_visibility_projection")
    violation = find_visibility_violation(checked_view.model_dump(mode="json"))
    if violation is not None:
        raise _context_blocker(checked_case, violation)
    view_source_hashes = tuple(value.source_sha256 for value in checked_view.items)
    if len(view_source_hashes) != len(set(view_source_hashes)):
        raise _context_blocker(checked_case, "duplicate_evidence_alias")

    if len(selected_evidence_ids) != len(set(selected_evidence_ids)):
        raise _context_blocker(checked_case, "duplicate_selected_evidence")
    by_id = {value.evidence_id: value for value in checked_view.items}
    if any(identifier not in by_id for identifier in selected_evidence_ids):
        raise _context_blocker(checked_case, "unknown_selected_evidence")
    selected = tuple(
        sorted(
            (
                ContextEvidenceReference(
                    evidence_id=by_id[identifier].evidence_id,
                    source_sha256=by_id[identifier].source_sha256,
                    provenance_evidence_ids=by_id[identifier].provenance_links,
                )
                for identifier in selected_evidence_ids
            ),
            key=lambda value: value.evidence_id,
        )
    )
    source_hashes = tuple(value.source_sha256 for value in selected)
    if len(source_hashes) != len(set(source_hashes)):
        raise _context_blocker(checked_case, "duplicate_evidence_alias")
    selected_ids = {value.evidence_id for value in selected}
    omitted = tuple(sorted(set(by_id) - selected_ids))
    identity_payload = {
        "schema_version": EVALUATION_CONTEXT_SCHEMA_VERSION,
        "project_id": checked_case.project_id,
        "snapshot_id": checked_case.snapshot_id,
        "case_reference_id": checked_case.reference_id,
        "case_id": checked_case.case_id,
        "evidence_bundle_id": checked_view.evidence_bundle_id,
        "visibility_projection_sha256": checked_view.view_sha256,
        "selected_evidence": [value.model_dump(mode="json") for value in selected],
        "omitted_evidence_ids": list(omitted),
    }
    context_sha256 = canonical_execution_sha256(identity_payload)
    return EvaluationContextPayload(
        context_id=f"ev-{context_sha256}",
        context_sha256=context_sha256,
        project_id=checked_case.project_id,
        snapshot_id=checked_case.snapshot_id,
        case_reference_id=checked_case.reference_id,
        case_id=checked_case.case_id,
        evidence_bundle_id=checked_view.evidence_bundle_id,
        visibility_projection_sha256=checked_view.view_sha256,
        selected_evidence=selected,
        omitted_evidence_ids=omitted,
    )


def validate_matched_context_information(
    contexts: tuple[EvaluationContextPayload, ...]
) -> None:
    """Require equivalent evidence information for contexts sharing an opaque case ID."""

    by_case: dict[str, EvaluationContextPayload] = {}
    for context in contexts:
        checked = EvaluationContextPayload.model_validate(context.model_dump(mode="python"))
        previous = by_case.get(checked.case_id)
        if previous is None:
            by_case[checked.case_id] = checked
            continue
        previous_information = (
            previous.project_id,
            previous.snapshot_id,
            previous.evidence_bundle_id,
            previous.visibility_projection_sha256,
            previous.selected_evidence,
            previous.omitted_evidence_ids,
        )
        current_information = (
            checked.project_id,
            checked.snapshot_id,
            checked.evidence_bundle_id,
            checked.visibility_projection_sha256,
            checked.selected_evidence,
            checked.omitted_evidence_ids,
        )
        if current_information != previous_information:
            raise ValueError("matched case contexts do not expose equal information")
