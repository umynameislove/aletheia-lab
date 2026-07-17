"""Strict, deterministic EvidenceBundle v3 contract.

The internal bundle records provenance and evaluator-only evidence.  A separate
whitelist projection is the only representation a diagnoser may receive.
Condition/rubric labels, internal case IDs and evaluator-only provenance are
therefore absent by construction rather than removed by a blacklist.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.evidence.rubric import (
    EvidenceCondition,
    EvidenceRole,
    condition_rubric_for,
)

EVIDENCE_ITEM_SCHEMA_VERSION: Final[Literal["evidence-item/3"]] = "evidence-item/3"
EVIDENCE_BUNDLE_SCHEMA_VERSION: Final[Literal["evidence-bundle/3"]] = "evidence-bundle/3"
DIAGNOSIS_EVIDENCE_VIEW_SCHEMA_VERSION: Final[Literal["diagnosis-evidence-view/2"]] = (
    "diagnosis-evidence-view/2"
)

EvidenceKind = Literal[
    "metric",
    "config",
    "log",
    "artifact",
    "dataset_profile",
    "lineage",
    "counterfactual",
    "human_note",
]
EvidenceVisibility = Literal["public", "diagnosis", "evaluator"]
RedactionState = Literal["none", "redacted", "withheld"]
MetadataValue = str | int | float | bool | None

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_DIAGNOSIS_VISIBLE = frozenset({"public", "diagnosis"})
_CONDITION_LABEL_IN_IDENTIFIER = re.compile(
    r"(?:^|[._:-])(?:full|noisy|missing[._:-]?key)(?:$|[._:-])",
    flags=re.IGNORECASE,
)
_CONDITION_LABEL_IN_VISIBLE_TEXT = re.compile(
    r"\b(?:full|noisy|missing[ _-]?key)\b",
    flags=re.IGNORECASE,
)
_EVALUATOR_INTENT_LABEL = re.compile(
    r"\b(?:distractor|decisive|sufficient|insufficient|"
    r"expected[._:\-\s]+behavior)\b",
    flags=re.IGNORECASE,
)

# Structural answer-key markers only.  This is deliberately not called a full
# semantic audit: human review remains required before P1-G5 can close.
HIDDEN_GROUND_TRUTH_MARKERS: tuple[str, ...] = (
    "ground_truth",
    "ground truth",
    "answer_key",
    "answer key",
    "hidden_failure_cause",
    "hidden failure cause",
    "cause_label",
    "causal_mechanism",
    "categorical_distribution_shift",
    "injection_parameters",
    "injection script",
    "data_drift",
    "data drift",
    "data-drift",
)


def canonical_json(payload: object) -> str:
    """Serialize JSON canonically for hashing (no NaN/Inf, no whitespace)."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest of UTF-8 text."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_sha256_for(content: str) -> str:
    """Return the canonical checksum for evidence content."""

    return sha256_text(content)


def _normalize_for_scan(value: str) -> str:
    return " ".join(value.casefold().split())


def _structural_marker_matches(payload: object) -> tuple[str, ...]:
    text = _normalize_for_scan(canonical_json(payload))
    return tuple(
        marker for marker in HIDDEN_GROUND_TRUTH_MARKERS if _normalize_for_scan(marker) in text
    )


def _validate_relative_posix_path(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("source_path must be a non-empty canonical relative path")
    if "\\" in value or PureWindowsPath(value).is_absolute():
        raise ValueError("source_path must use normalized POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("source_path must be relative")
    if (
        value != path.as_posix()
        or value in {".", ".."}
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("source_path must be normalized and cannot traverse parents")
    return value


def _identifier_contains_condition_label(value: str) -> bool:
    """Return whether an opaque diagnosis-visible ID encodes a P1 condition."""

    return _CONDITION_LABEL_IN_IDENTIFIER.search(value) is not None


def _metadata_is_finite(value: MetadataValue) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceMetadataEntry(_StrictFrozenModel):
    """One immutable metadata key/value pair."""

    key: str
    value: MetadataValue

    @field_validator("key")
    @classmethod
    def _nonempty_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("metadata keys must not be blank")
        return value

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: MetadataValue) -> MetadataValue:
        if not _metadata_is_finite(value):
            raise ValueError("metadata numeric values must be finite")
        return value


class EvidenceItem(_StrictFrozenModel):
    """One immutable evidence item with content integrity and provenance metadata."""

    schema_version: Literal["evidence-item/3"]
    evidence_id: str = Field(pattern=_ID_PATTERN)
    kind: EvidenceKind
    evidence_roles: tuple[EvidenceRole, ...]
    title: str
    content: str
    source_path: str
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    collector_version: str
    collected_at: str | None = None
    visibility: EvidenceVisibility
    redaction_state: RedactionState = "none"
    metadata: tuple[EvidenceMetadataEntry, ...] = ()
    provenance_links: tuple[str, ...] = ()

    @classmethod
    def from_content(
        cls,
        *,
        evidence_id: str,
        kind: EvidenceKind,
        evidence_roles: tuple[EvidenceRole, ...],
        title: str,
        content: str,
        source_path: str,
        collector_version: str,
        visibility: EvidenceVisibility,
        collected_at: str | None = None,
        redaction_state: RedactionState = "none",
        metadata: dict[str, MetadataValue] | None = None,
        provenance_links: tuple[str, ...] = (),
    ) -> Self:
        """Construct an item while deriving, rather than trusting, its checksum."""

        return cls(
            schema_version=EVIDENCE_ITEM_SCHEMA_VERSION,
            evidence_id=evidence_id,
            kind=kind,
            evidence_roles=evidence_roles,
            title=title,
            content=content,
            source_path=source_path,
            content_sha256=content_sha256_for(content),
            collector_version=collector_version,
            collected_at=collected_at,
            visibility=visibility,
            redaction_state=redaction_state,
            metadata=tuple(
                EvidenceMetadataEntry(key=key, value=value)
                for key, value in sorted((metadata or {}).items())
            ),
            provenance_links=provenance_links,
        )

    @field_validator("title", "content", "collector_version")
    @classmethod
    def _nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence text fields must not be blank")
        return value

    @field_validator("source_path")
    @classmethod
    def _normalized_source_path(cls, value: str) -> str:
        return _validate_relative_posix_path(value)

    @field_validator("collected_at")
    @classmethod
    def _timezone_aware_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("collected_at must be ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("collected_at must include a timezone")
        return value

    @field_validator("evidence_roles")
    @classmethod
    def _nonempty_unique_sorted_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("evidence_roles must contain at least one observable role")
        if len(set(value)) != len(value):
            raise ValueError("tuple values must be unique")
        return tuple(sorted(value))

    @field_validator("provenance_links")
    @classmethod
    def _unique_sorted_links(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("tuple values must be unique")
        return tuple(sorted(value))

    @field_validator("metadata")
    @classmethod
    def _valid_metadata(
        cls, value: tuple[EvidenceMetadataEntry, ...]
    ) -> tuple[EvidenceMetadataEntry, ...]:
        keys = tuple(item.key for item in value)
        if len(set(keys)) != len(keys):
            raise ValueError("metadata keys must be unique")
        return tuple(sorted(value, key=lambda item: item.key))

    @model_validator(mode="after")
    def _integrity_and_visibility(self) -> EvidenceItem:
        if self.content_sha256 != content_sha256_for(self.content):
            raise ValueError("content_sha256 does not match canonical evidence content")
        if self.evidence_id in self.provenance_links:
            raise ValueError("evidence item cannot cite itself as provenance")
        if self.redaction_state == "withheld" and self.visibility != "evaluator":
            raise ValueError("withheld evidence must be evaluator-only")
        return self


class EvidenceBundle(_StrictFrozenModel):
    """Internal EvidenceBundle v2 for exactly one benchmark context."""

    schema_version: Literal["evidence-bundle/3"]
    validation_state: Literal["schema_validated"] = "schema_validated"
    evidence_bundle_id: str = Field(pattern=_ID_PATTERN)
    case_id: str = Field(pattern=_ID_PATTERN)
    case_family_id: str = Field(pattern=r"^p1-family-[0-9a-f]{64}$")
    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    evidence_condition: EvidenceCondition
    dataset_id: str = Field(pattern=_ID_PATTERN)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    split_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    items: tuple[EvidenceItem, ...]
    required_evidence_roles: tuple[EvidenceRole, ...]
    missing_required_evidence_roles: tuple[EvidenceRole, ...]
    intentionally_withheld_evidence_roles: tuple[EvidenceRole, ...]

    @field_validator(
        "required_evidence_roles",
        "missing_required_evidence_roles",
        "intentionally_withheld_evidence_roles",
    )
    @classmethod
    def _unique_sorted_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence-role lists must contain unique values")
        return tuple(sorted(value))

    @field_validator("items")
    @classmethod
    def _canonical_item_order(cls, value: tuple[EvidenceItem, ...]) -> tuple[EvidenceItem, ...]:
        if not value:
            raise ValueError("evidence bundle must contain at least one item")
        ids = tuple(item.evidence_id for item in value)
        if len(set(ids)) != len(ids):
            raise ValueError("evidence_id must be unique within a bundle")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @property
    def diagnosis_visible_items(self) -> tuple[EvidenceItem, ...]:
        """Return items eligible for a diagnosis projection."""

        return tuple(
            item
            for item in self.items
            if item.visibility in _DIAGNOSIS_VISIBLE and item.redaction_state != "withheld"
        )

    @model_validator(mode="after")
    def _condition_and_visibility_contract(self) -> EvidenceBundle:
        rubric = condition_rubric_for(self.evidence_condition)
        required = set(rubric.required_evidence_roles)
        if set(self.required_evidence_roles) != required:
            raise ValueError("required_evidence_roles do not match the condition rubric")
        withheld = set(rubric.intentionally_withheld_evidence_roles)
        if set(self.intentionally_withheld_evidence_roles) != withheld:
            raise ValueError(
                "intentionally_withheld_evidence_roles do not match the condition rubric"
            )

        visible_roles = {
            role for item in self.diagnosis_visible_items for role in item.evidence_roles
        }
        missing = required - visible_roles
        if set(self.missing_required_evidence_roles) != missing:
            raise ValueError("missing_required_evidence_roles do not match visible evidence")
        if missing != withheld:
            raise ValueError(
                "missing required roles must exactly match the canonical controlled withholding"
            )
        if withheld & visible_roles:
            raise ValueError("an intentionally withheld evidence role is diagnosis-visible")

        materialized_withheld_roles = {
            role
            for item in self.items
            if item.visibility == "evaluator" and item.redaction_state == "withheld"
            for role in item.evidence_roles
        }
        if materialized_withheld_roles != withheld:
            raise ValueError(
                "canonical withheld roles must be materialized as evaluator-only withheld items"
            )

        by_id = {item.evidence_id: item for item in self.items}
        unknown_links = {
            link for item in self.items for link in item.provenance_links if link not in by_id
        }
        if unknown_links:
            raise ValueError(
                f"provenance links reference unknown evidence IDs: {sorted(unknown_links)}"
            )

        for item in self.diagnosis_visible_items:
            if _identifier_contains_condition_label(item.evidence_id):
                raise ValueError(
                    f"diagnosis-visible evidence ID {item.evidence_id!r} exposes a condition label"
                )
            matches = _structural_marker_matches(item.model_dump(mode="json"))
            if matches:
                raise ValueError(
                    f"diagnosis-visible evidence {item.evidence_id!r} contains hidden marker(s): "
                    f"{list(matches)}"
                )
            projected_text = canonical_json(
                {
                    "evidence_id": item.evidence_id,
                    "evidence_roles": item.evidence_roles,
                    "title": item.title,
                    "content": item.content,
                }
            )
            if (
                _CONDITION_LABEL_IN_VISIBLE_TEXT.search(projected_text)
                or "distractor" in projected_text.casefold()
                or _EVALUATOR_INTENT_LABEL.search(projected_text)
            ):
                raise ValueError(
                    f"diagnosis-visible evidence {item.evidence_id!r} exposes evaluator intent"
                )
        return self

    def canonical_sha256(self) -> str:
        """Return a deterministic hash of the complete validated bundle."""

        return sha256_text(canonical_json(self.model_dump(mode="json")))


class DiagnosisEvidenceItem(_StrictFrozenModel):
    """Whitelisted item fields exposed to a diagnoser."""

    evidence_id: str = Field(pattern=_ID_PATTERN)
    kind: EvidenceKind
    evidence_roles: tuple[EvidenceRole, ...]
    title: str
    content: str
    content_sha256: str = Field(pattern=_SHA256_PATTERN)


class DiagnosisEvidenceView(_StrictFrozenModel):
    """Condition-blind evidence projection safe to render into a prompt."""

    schema_version: Literal["diagnosis-evidence-view/2"]
    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    items: tuple[DiagnosisEvidenceItem, ...]

    def canonical_sha256(self) -> str:
        """Return the deterministic projection hash."""

        return sha256_text(canonical_json(self.model_dump(mode="json")))


def project_diagnosis_evidence(bundle: EvidenceBundle) -> DiagnosisEvidenceView:
    """Whitelist a condition-blind diagnosis view from a validated internal bundle."""

    return DiagnosisEvidenceView(
        schema_version=DIAGNOSIS_EVIDENCE_VIEW_SCHEMA_VERSION,
        diagnosis_context_id=bundle.diagnosis_context_id,
        items=tuple(
            DiagnosisEvidenceItem(
                evidence_id=item.evidence_id,
                kind=item.kind,
                evidence_roles=item.evidence_roles,
                title=item.title,
                content=item.content,
                content_sha256=item.content_sha256,
            )
            for item in bundle.diagnosis_visible_items
        ),
    )


def contains_condition_or_rubric_label(payload: object) -> bool:
    """Return whether a diagnosis payload exposes evaluator-only rubric data.

    Ordinary prose is not scanned for words such as ``full`` because they can
    be legitimate observations.  Structured evaluator keys and condition labels
    embedded in diagnosis-visible identifiers are rejected.
    """

    forbidden_keys = {
        "evidence_condition",
        "causal_claim_sufficiency",
        "expected_diagnosis_behavior",
        "allowed_claim_levels",
        "forbidden_claim_levels",
        "intentionally_withheld_evidence_roles",
        "missing_required_evidence_roles",
    }

    def _contains(value: object, *, parent_key: str | None = None) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in forbidden_keys:
                    return True
                if (
                    key in {"evidence_id", "diagnosis_context_id"}
                    and isinstance(nested, str)
                    and _identifier_contains_condition_label(nested)
                ):
                    return True
                if key in {"evidence_id", "evidence_roles", "title"} and _contains(
                    nested, parent_key=key
                ):
                    return True
                if isinstance(nested, str) and "distractor" in nested.casefold():
                    return True
                if _contains(nested, parent_key=key):
                    return True
            return False
        if isinstance(value, (list, tuple)):
            return any(_contains(item, parent_key=parent_key) for item in value)
        if isinstance(value, str):
            if _CONDITION_LABEL_IN_VISIBLE_TEXT.search(value):
                return True
            if "distractor" in value.casefold():
                return True
            if parent_key in {"evidence_id", "evidence_roles", "title"}:
                return _EVALUATOR_INTENT_LABEL.search(value) is not None
        return False

    return _contains(payload)


def is_sha256(value: str) -> bool:
    """Return whether ``value`` is a lowercase SHA-256 digest."""

    return re.fullmatch(_SHA256_PATTERN, value) is not None
