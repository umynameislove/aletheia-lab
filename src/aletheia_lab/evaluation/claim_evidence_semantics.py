"""Content-bearing evidence and blind relation assignment for claim evaluation.

The module closes two deliberately separate boundaries:

* evaluator-side bindings connect one registered family/condition to an
  observed, content-addressed evidence context;
* provider-visible relation requests contain only claim text, claim type and
  that visible evidence.  Family, mechanism, condition, variant and outcomes
  are absent by construction.

It does not generate intervention evidence or call a provider.  Those actions
remain part of the separately authorized development execution.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimType,
    EvidenceCondition,
    VisibleEvidenceRelation,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256, normalize_text

VISIBLE_EVIDENCE_SCHEMA_VERSION: Final = "claim-visible-evidence-context/v1"
EVIDENCE_BINDING_SCHEMA_VERSION: Final = "claim-evidence-binding/v1"
RELATION_REQUEST_SCHEMA_VERSION: Final = "claim-relation-assignment-request/v1"
RELATION_RESPONSE_SCHEMA_VERSION: Final = "claim-relation-assignment-response/v1"
RELATION_ASSIGNMENT_VERSION: Final = "claim-visible-relation-assignment/1"
MAXIMUM_VISIBLE_EVIDENCE_ITEMS: Final = 32
# This is a serialization/resource ceiling, not a token estimate. Exact
# provider-token accounting over the complete prompt, claim and evidence is a
# separate mandatory live preflight.
MAXIMUM_VISIBLE_CONTEXT_UTF8_BYTES: Final = 12_000
EVIDENCE_SEMANTICS_POLICY_PATH: Final = (
    "configs/evaluation/claim_support_evidence_semantics_policy.json"
)
_PARENT_PROTOCOL_PATH: Final = "configs/evaluation/claim_support_corpus_protocol.json"
_INSTRUMENT_MANIFEST_PATH: Final = (
    "configs/evaluation/claim_support_automatic_instrument_manifest.json"
)
_FAIRNESS_FREEZE_PATH: Final = "configs/evaluation/diagnosis_variant_fairness_freeze.json"
RELATION_ASSIGNMENT_PROMPT: Final = (
    "Judge only whether the supplied visible evidence supports the exact written claim. "
    "Use no outside knowledge and do not infer hidden project state. For every evidence item, "
    "return its ID exactly once and in input order. Mark contradicts when it conflicts with any "
    "material claim part; otherwise mark supports with entire scope only when it establishes the "
    "whole claim, supports with partial scope when it establishes only a bounded part, or neutral "
    "with none scope when it neither establishes nor conflicts with the claim. Plausibility and "
    "temporal order alone are not support."
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
EvidenceKind = Literal["metric", "config", "log", "artifact", "dataset_profile", "lineage"]
RelationPolarity = Literal["supports", "contradicts", "neutral"]
RelationScope = Literal["none", "partial", "entire"]

_EVIDENCE_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_CONDITION_IN_ID: Final = re.compile(
    r"(?:^|[._:-])(?:full|noisy|missing[._:-]?key)(?:$|[._:-])",
    flags=re.IGNORECASE,
)
_FORBIDDEN_VISIBLE_MARKERS: Final[tuple[str, ...]] = (
    "answer key",
    "answer_key",
    "automatic label",
    "automatic_label",
    "case family id",
    "case_family_id",
    "evidence condition",
    "evidence_condition",
    "hidden ground truth",
    "hidden_ground_truth",
    "intervention parameters",
    "intervention_parameters",
    "main outcome",
    "main_outcome",
    "sealed outcome",
    "sealed_outcome",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_visible_leak(value: str, *, label: str) -> str:
    folded = " ".join(value.casefold().split())
    if any(marker in folded for marker in _FORBIDDEN_VISIBLE_MARKERS):
        raise ValueError(f"{label} exposes evaluator-only metadata")
    return value


class ModelVisibleEvidenceItem(_StrictFrozenModel):
    """One observed evidence item safe to render into a model request."""

    evidence_id: str = Field(pattern=_EVIDENCE_ID_PATTERN)
    kind: EvidenceKind
    title: str
    content: str
    content_sha256: Sha256
    source_content_sha256: Sha256

    @field_validator("evidence_id")
    @classmethod
    def _identifier_is_condition_blind(cls, value: str) -> str:
        if _CONDITION_IN_ID.search(value):
            raise ValueError("visible evidence ID exposes the evidence condition")
        return value

    @field_validator("title")
    @classmethod
    def _title_is_safe(cls, value: str) -> str:
        normalized = normalize_text(value, label="visible evidence title", max_length=256)
        return _reject_visible_leak(normalized, label="visible evidence title")

    @field_validator("content")
    @classmethod
    def _content_is_safe(cls, value: str) -> str:
        normalized = normalize_text(value, label="visible evidence content", max_length=4096)
        return _reject_visible_leak(normalized, label="visible evidence content")

    @model_validator(mode="after")
    def _content_hash_reconciles(self) -> Self:
        if self.content_sha256 != content_sha256(self.content.encode("utf-8")):
            raise ValueError("visible evidence content hash does not match")
        return self


class ModelVisibleEvidenceContext(_StrictFrozenModel):
    """Exact content-bearing payload shared by all variants for one context."""

    schema_version: Literal["claim-visible-evidence-context/v1"] = VISIBLE_EVIDENCE_SCHEMA_VERSION
    context_id: str = Field(pattern=r"^ccctx-[0-9a-f]{64}$")
    items: tuple[ModelVisibleEvidenceItem, ...] = Field(
        min_length=1, max_length=MAXIMUM_VISIBLE_EVIDENCE_ITEMS
    )
    context_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "items": tuple(item.model_dump(mode="json") for item in self.items),
        }

    @model_validator(mode="after")
    def _identity_and_budget_reconcile(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.items)
        if identifiers != tuple(sorted(identifiers)) or len(identifiers) != len(set(identifiers)):
            raise ValueError("visible evidence items must be unique and canonically ordered")
        expected = canonical_execution_sha256(self.identity_payload())
        if self.context_sha256 != expected or self.context_id != f"ccctx-{expected}":
            raise ValueError("visible evidence context identity does not match content")
        if len(_canonical_bytes(self.model_payload())) > MAXIMUM_VISIBLE_CONTEXT_UTF8_BYTES:
            raise ValueError("visible evidence context exceeds the conservative 12k-token budget")
        return self

    def model_payload(self) -> dict[str, object]:
        """Return only fields authorized for diagnosis and relation assignment."""

        return {
            "schema_version": self.schema_version,
            "context_id": self.context_id,
            "items": tuple(item.model_dump(mode="json") for item in self.items),
            "context_sha256": self.context_sha256,
        }


class ClaimEvidenceBinding(_StrictFrozenModel):
    """Evaluator-side link between one frozen case and its visible context."""

    schema_version: Literal["claim-evidence-binding/v1"] = EVIDENCE_BINDING_SCHEMA_VERSION
    source_partition: Literal["development"] = "development"
    family_id: str
    family_sha256: Sha256
    evidence_condition: EvidenceCondition
    source_projection_sha256: Sha256
    visible_context: ModelVisibleEvidenceContext
    hidden_ground_truth_present: Literal[False] = False
    evaluator_outcome_present: Literal[False] = False
    binding_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"binding_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.binding_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("claim evidence binding identity does not match")
        return self

    def model_payload(self) -> dict[str, object]:
        """Expose no evaluator-side family or condition fields."""

        return self.visible_context.model_payload()


class EvidenceRelationDecision(_StrictFrozenModel):
    """Blind semantic relation assigned to one cited evidence item."""

    evidence_id: str = Field(pattern=_EVIDENCE_ID_PATTERN)
    relation_polarity: RelationPolarity
    relation_scope: RelationScope

    @model_validator(mode="after")
    def _relation_is_coherent(self) -> Self:
        if self.relation_polarity == "neutral" and self.relation_scope != "none":
            raise ValueError("neutral evidence must use scope none")
        if self.relation_polarity != "neutral" and self.relation_scope == "none":
            raise ValueError("non-neutral evidence must identify claim scope")
        return self


class ClaimRelationAssignmentRequest(_StrictFrozenModel):
    """Internal request binding with a strictly smaller provider-visible view."""

    schema_version: Literal["claim-relation-assignment-request/v1"] = (
        RELATION_REQUEST_SCHEMA_VERSION
    )
    assignment_request_id: str = Field(pattern=r"^ccrel-[0-9a-f]{64}$")
    source_output_sha256: Sha256
    claim_local_id: str = Field(pattern=r"^claim-[1-5]$")
    claim_text: str
    claim_type: ClaimType
    visible_evidence: tuple[ModelVisibleEvidenceItem, ...] = Field(min_length=1, max_length=32)
    visible_context_sha256: Sha256
    assignment_request_sha256: Sha256

    @field_validator("claim_text")
    @classmethod
    def _claim_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="relation assignment claim", max_length=2048)

    def provider_payload(self) -> dict[str, object]:
        """Return exactly the three input fields frozen by the parent protocol."""

        return {
            "claim_text": self.claim_text,
            "claim_type": self.claim_type,
            "visible_evidence": tuple(
                item.model_dump(mode="json") for item in self.visible_evidence
            ),
        }

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_output_sha256": self.source_output_sha256,
            "claim_local_id": self.claim_local_id,
            "provider_payload": self.provider_payload(),
            "visible_context_sha256": self.visible_context_sha256,
        }

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.visible_evidence)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("relation assignment evidence IDs must be unique")
        expected = canonical_execution_sha256(self.identity_payload())
        if (
            self.assignment_request_sha256 != expected
            or self.assignment_request_id != f"ccrel-{expected}"
        ):
            raise ValueError("relation assignment request identity does not match")
        return self


class ClaimRelationAssignmentResponse(_StrictFrozenModel):
    """Parsed blind relation output, bound to exactly one assignment request."""

    schema_version: Literal["claim-relation-assignment-response/v1"] = (
        RELATION_RESPONSE_SCHEMA_VERSION
    )
    assignment_request_sha256: Sha256
    decisions: tuple[EvidenceRelationDecision, ...] = Field(min_length=1, max_length=32)
    response_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"response_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.decisions)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("relation assignment decisions must be unique")
        if self.response_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation assignment response identity does not match")
        return self


class ClaimEvidenceSemanticsPolicy(_StrictFrozenModel):
    """Prospective zero-outcome freeze for evidence and automatic relation semantics."""

    schema_version: Literal["claim-evidence-semantics-policy/v1"] = (
        "claim-evidence-semantics-policy/v1"
    )
    policy_id: Literal["claim-support-evidence-semantics-v1"]
    parent_protocol_sha256: Sha256
    automatic_instrument_manifest_sha256: Sha256
    fairness_freeze_sha256: Sha256
    visible_evidence_schema: Literal["claim-visible-evidence-context/v1"]
    maximum_visible_evidence_items: Literal[32]
    maximum_visible_context_utf8_bytes: Literal[12000]
    relation_assignment_version: Literal["claim-visible-relation-assignment/1"]
    implementation_ref: Literal[
        "aletheia_lab.evaluation.claim_evidence_semantics:parse_relation_assignment"
    ]
    implementation_source_sha256: Sha256
    provider: Literal["openai"]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    temperature: float = Field(ge=0.0, le=0.0)
    seed: Literal[17]
    maximum_output_tokens: Literal[600]
    maximum_attempts: Literal[2]
    # Relation assessment is deliberately one request per atomic claim.  A
    # completed diagnosis output may contain five claims, so the 360-output
    # census has a 1,800-request worst case.  Counting only source outputs
    # would understate the provider and cost ceiling.
    maximum_relation_requests_primary: Literal[1800]
    prompt: str
    permitted_provider_input_fields: tuple[
        Literal["claim_text", "claim_type", "visible_evidence"], ...
    ]
    withheld_provider_input_fields: tuple[
        Literal[
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        ],
        ...,
    ]
    response_schema: dict[str, object]
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    policy_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"policy_sha256"})

    @model_validator(mode="after")
    def _policy_reconciles(self) -> Self:
        if self.prompt != RELATION_ASSIGNMENT_PROMPT:
            raise ValueError("relation assignment prompt differs from the frozen rubric")
        if self.permitted_provider_input_fields != (
            "claim_text",
            "claim_type",
            "visible_evidence",
        ):
            raise ValueError("relation assignment provider fields differ from the parent protocol")
        expected_withheld = (
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        )
        if self.withheld_provider_input_fields != expected_withheld:
            raise ValueError("relation assignment withheld fields are incomplete")
        if self.response_schema != relation_assignment_response_schema():
            raise ValueError("relation assignment response schema changed")
        if self.policy_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("evidence semantics policy identity does not match")
        return self


def build_visible_evidence_item(
    *,
    evidence_id: str,
    kind: EvidenceKind,
    title: str,
    content: str,
    source_content_sha256: str,
) -> ModelVisibleEvidenceItem:
    """Build a content-addressed item from an observed source projection."""

    return ModelVisibleEvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        title=title,
        content=content,
        content_sha256=content_sha256(content.encode("utf-8")),
        source_content_sha256=source_content_sha256,
    )


def build_evidence_binding(
    request: ClaimCorpusRequest,
    *,
    items: Sequence[ModelVisibleEvidenceItem],
    source_projection_sha256: str,
) -> ClaimEvidenceBinding:
    """Bind observed evidence to a registered family and condition without a provider call."""

    checked = ClaimCorpusRequest.model_validate(request.model_dump(mode="python"))
    canonical_items = tuple(sorted(items, key=lambda item: item.evidence_id))
    context_payload = {
        "schema_version": VISIBLE_EVIDENCE_SCHEMA_VERSION,
        "items": tuple(item.model_dump(mode="json") for item in canonical_items),
    }
    context_sha256 = canonical_execution_sha256(context_payload)
    context = ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_sha256}",
        items=canonical_items,
        context_sha256=context_sha256,
    )
    payload = {
        "schema_version": EVIDENCE_BINDING_SCHEMA_VERSION,
        "source_partition": "development",
        "family_id": checked.family_id,
        "family_sha256": checked.family_sha256,
        "evidence_condition": checked.evidence_condition,
        "source_projection_sha256": source_projection_sha256,
        "visible_context": context.model_dump(mode="json"),
        "hidden_ground_truth_present": False,
        "evaluator_outcome_present": False,
    }
    return ClaimEvidenceBinding(
        family_id=checked.family_id,
        family_sha256=checked.family_sha256,
        evidence_condition=checked.evidence_condition,
        source_projection_sha256=source_projection_sha256,
        visible_context=context,
        binding_sha256=canonical_execution_sha256(payload),
    )


def validate_request_evidence_binding(
    request: ClaimCorpusRequest,
    binding: ClaimEvidenceBinding,
) -> ClaimEvidenceBinding:
    """Reject cross-family or cross-condition evidence replay."""

    checked_request = ClaimCorpusRequest.model_validate(request.model_dump(mode="python"))
    checked_binding = ClaimEvidenceBinding.model_validate(binding.model_dump(mode="python"))
    if (
        checked_binding.family_id != checked_request.family_id
        or checked_binding.family_sha256 != checked_request.family_sha256
        or checked_binding.evidence_condition != checked_request.evidence_condition
    ):
        raise ClaimCorpusContractError("visible evidence belongs to a different request context")
    return checked_binding


def build_relation_assignment_request(
    *,
    source_output_sha256: str,
    claim_local_id: str,
    claim_text: str,
    claim_type: ClaimType,
    cited_evidence_ids: Sequence[str],
    evidence_binding: ClaimEvidenceBinding,
) -> ClaimRelationAssignmentRequest:
    """Build a blind relation request over exactly the evidence cited by one claim."""

    evidence_by_id = {item.evidence_id: item for item in evidence_binding.visible_context.items}
    cited = tuple(cited_evidence_ids)
    if not cited or len(cited) != len(set(cited)) or not set(cited).issubset(evidence_by_id):
        raise ClaimCorpusContractError("claim cites unavailable or duplicate visible evidence")
    selected = tuple(evidence_by_id[evidence_id] for evidence_id in cited)
    draft = {
        "schema_version": RELATION_REQUEST_SCHEMA_VERSION,
        "source_output_sha256": source_output_sha256,
        "claim_local_id": claim_local_id,
        "provider_payload": {
            "claim_text": claim_text,
            "claim_type": claim_type,
            "visible_evidence": tuple(item.model_dump(mode="json") for item in selected),
        },
        "visible_context_sha256": evidence_binding.visible_context.context_sha256,
    }
    request_sha256 = canonical_execution_sha256(draft)
    try:
        return ClaimRelationAssignmentRequest(
            assignment_request_id=f"ccrel-{request_sha256}",
            source_output_sha256=source_output_sha256,
            claim_local_id=claim_local_id,
            claim_text=claim_text,
            claim_type=claim_type,
            visible_evidence=selected,
            visible_context_sha256=evidence_binding.visible_context.context_sha256,
            assignment_request_sha256=request_sha256,
        )
    except ValidationError as exc:
        raise ClaimCorpusContractError("relation assignment request is invalid") from exc


def parse_relation_assignment(
    request: ClaimRelationAssignmentRequest,
    payload: Mapping[str, object],
) -> ClaimRelationAssignmentResponse:
    """Validate one structured judge response against its blind request."""

    source = dict(payload)
    source.setdefault("schema_version", RELATION_RESPONSE_SCHEMA_VERSION)
    source.setdefault("assignment_request_sha256", request.assignment_request_sha256)
    decisions = source.get("decisions")
    if isinstance(decisions, list):
        source["decisions"] = tuple(decisions)
    if "response_sha256" not in source:
        source["response_sha256"] = canonical_execution_sha256(source)
    try:
        response = ClaimRelationAssignmentResponse.model_validate(source)
    except ValidationError as exc:
        raise ClaimCorpusContractError("relation assignment response is invalid") from exc
    if response.assignment_request_sha256 != request.assignment_request_sha256:
        raise ClaimCorpusContractError("relation assignment response belongs to another claim")
    expected_ids = tuple(item.evidence_id for item in request.visible_evidence)
    actual_ids = tuple(item.evidence_id for item in response.decisions)
    if actual_ids != expected_ids:
        raise ClaimCorpusContractError(
            "relation assignment must cover cited evidence once and in request order"
        )
    return response


def visible_relations_from_assignment(
    request: ClaimRelationAssignmentRequest,
    response: ClaimRelationAssignmentResponse,
) -> tuple[VisibleEvidenceRelation, ...]:
    """Join relation decisions back to immutable evidence text without trusting model text."""

    checked = parse_relation_assignment(request, response.model_dump(mode="python"))
    evidence = {item.evidence_id: item for item in request.visible_evidence}
    return tuple(
        VisibleEvidenceRelation(
            evidence_id=decision.evidence_id,
            text=evidence[decision.evidence_id].content,
            relation_polarity=decision.relation_polarity,
            relation_scope=decision.relation_scope,
        )
        for decision in checked.decisions
    )


def relation_assignment_response_schema() -> dict[str, object]:
    """Return the closed provider schema; identity fields are injected locally."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "evidence_id",
                        "relation_polarity",
                        "relation_scope",
                    ],
                    "properties": {
                        "evidence_id": {
                            "type": "string",
                        },
                        "relation_polarity": {
                            "type": "string",
                            "enum": ["supports", "contradicts", "neutral"],
                        },
                        "relation_scope": {
                            "type": "string",
                            "enum": ["none", "partial", "entire"],
                        },
                    },
                },
            }
        },
    }


def _required_file_sha256(root: Path, relative: str) -> str:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ClaimCorpusContractError(
            f"required semantics artifact is unavailable: {relative}"
        ) from exc
    if (
        candidate.is_symlink()
        or not resolved.is_file()
        or not resolved.is_relative_to(root.resolve())
    ):
        raise ClaimCorpusContractError(f"required semantics artifact is unsafe: {relative}")
    return content_sha256(resolved.read_bytes())


def build_evidence_semantics_policy(root: Path) -> ClaimEvidenceSemanticsPolicy:
    """Build the prospective policy from exact parent and implementation bytes."""

    checked_root = root.resolve()
    payload = {
        "schema_version": "claim-evidence-semantics-policy/v1",
        "policy_id": "claim-support-evidence-semantics-v1",
        "parent_protocol_sha256": _required_file_sha256(checked_root, _PARENT_PROTOCOL_PATH),
        "automatic_instrument_manifest_sha256": _required_file_sha256(
            checked_root, _INSTRUMENT_MANIFEST_PATH
        ),
        "fairness_freeze_sha256": _required_file_sha256(checked_root, _FAIRNESS_FREEZE_PATH),
        "visible_evidence_schema": VISIBLE_EVIDENCE_SCHEMA_VERSION,
        "maximum_visible_evidence_items": MAXIMUM_VISIBLE_EVIDENCE_ITEMS,
        "maximum_visible_context_utf8_bytes": MAXIMUM_VISIBLE_CONTEXT_UTF8_BYTES,
        "relation_assignment_version": RELATION_ASSIGNMENT_VERSION,
        "implementation_ref": (
            "aletheia_lab.evaluation.claim_evidence_semantics:parse_relation_assignment"
        ),
        "implementation_source_sha256": content_sha256(Path(__file__).read_bytes()),
        "provider": "openai",
        "model": "gpt-4.1",
        "model_snapshot": "gpt-4.1-2025-04-14",
        "temperature": 0.0,
        "seed": 17,
        "maximum_output_tokens": 600,
        "maximum_attempts": 2,
        "maximum_relation_requests_primary": 1800,
        "prompt": RELATION_ASSIGNMENT_PROMPT,
        "permitted_provider_input_fields": (
            "claim_text",
            "claim_type",
            "visible_evidence",
        ),
        "withheld_provider_input_fields": (
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        ),
        "response_schema": relation_assignment_response_schema(),
        "provider_calls_executed": False,
        "outputs_generated": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimEvidenceSemanticsPolicy.model_validate(
        {**payload, "policy_sha256": canonical_execution_sha256(payload)}
    )


def load_evidence_semantics_policy(root: Path) -> ClaimEvidenceSemanticsPolicy:
    """Verify that the tracked policy exactly matches current parent and code bytes."""

    expected = build_evidence_semantics_policy(root)
    path = root / EVIDENCE_SEMANTICS_POLICY_PATH
    try:
        actual = ClaimEvidenceSemanticsPolicy.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusContractError(
            "evidence semantics policy is unavailable or invalid"
        ) from exc
    if actual != expected:
        raise ClaimCorpusContractError(
            "tracked evidence semantics policy differs from current code"
        )
    return actual


__all__ = [
    "MAXIMUM_VISIBLE_CONTEXT_UTF8_BYTES",
    "RELATION_ASSIGNMENT_VERSION",
    "EVIDENCE_SEMANTICS_POLICY_PATH",
    "ClaimEvidenceBinding",
    "ClaimEvidenceSemanticsPolicy",
    "ClaimRelationAssignmentRequest",
    "ClaimRelationAssignmentResponse",
    "EvidenceRelationDecision",
    "ModelVisibleEvidenceContext",
    "ModelVisibleEvidenceItem",
    "build_evidence_binding",
    "build_evidence_semantics_policy",
    "build_relation_assignment_request",
    "build_visible_evidence_item",
    "load_evidence_semantics_policy",
    "parse_relation_assignment",
    "relation_assignment_response_schema",
    "validate_request_evidence_binding",
    "visible_relations_from_assignment",
]
