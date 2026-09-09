"""Prospective, outcome-blind source-claim expressiveness amendment for V2.

The V2 runtime inventory proves that authentic evidence transformations exist.
This module proves the separate claim-side condition: a source output can expose
an exact, cited measurement witness without erasing the diagnostic prose that a
variant may additionally produce.  It performs no provider call and never reads
V1 relation results, V1 labels, or human annotations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    AtomicClaimV2,
    DiagnosisOutputV2,
    EvidenceCondition,
    MaterialClaimPart,
    Mechanism,
    SourceArtifactBinding,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    provider_response_schema_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.claim_validation_v2_frames import measurements
from aletheia_lab.evaluation.claim_validation_v2_relation_frames import (
    _eligible_challenges,
    _frame_contexts,
    build_visible_context,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
    build_v2_runtime_readiness,
    verify_tracked_v2_runtime,
)
from aletheia_lab.evaluation.claim_validation_v2_runtime_contracts import (
    ChallengeFrame,
    ClaimSupportValidationV2RuntimeManifest,
    V2DiagnosisScheduleEntry,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    canonical_project_json,
    content_sha256,
)

EXPRESSIVENESS_AMENDMENT_PATH: Final = (
    "configs/evaluation/claim_support_validation_v2_expressiveness_amendment.json"
)
EXPRESSIVENESS_REVIEW_PATH: Final = (
    "configs/evaluation/claim_support_validation_v2_expressiveness_review.json"
)
EXPRESSIVENESS_AMENDMENT_SCHEMA_VERSION: Final = (
    "claim-support-validation-v2-expressiveness-amendment/v1"
)
EXPRESSIVENESS_REVIEW_SCHEMA_VERSION: Final = (
    "claim-support-validation-v2-expressiveness-review/v1"
)
SOURCE_CLAIM_SELECTION_ALGORITHM: Final = (
    "measurement-witness-first-then-canonical-claim-hash/v1"
)
MEASUREMENT_SELECTION_ALGORITHM: Final = (
    "visible-observed-delta-path-priority/v1"
)
PROVIDER_VARIANTS: Final = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
MEASUREMENT_PATH_PRIORITY: Final = (
    "payload.observed.",
    "payload.delta.",
    "payload.",
)
SHARED_EXPRESSIVENESS_INSTRUCTION: Final = """V2 source-evidence witness contract:
If you return a completed result, atomic_claims[0] must be an evidence_statement built only from exact numeric leaves visibly present in the supplied JSON evidence. Write each material part as `<full_json_path> = <exact_base10_value>` without rounding, and set claim_text to the material-part texts joined in the same order by `; `. Cite every evidence item used and no unavailable item.
Choose paths deterministically: prefer `payload.observed.*`, then `payload.delta.*`, then other `payload.*` paths, breaking ties by the full path. If both `ev-key-measurement` and `ev-performance-summary` have numeric leaves, the first part must use the first key-measurement path/value that is not identically duplicated in the performance summary, and the second part must use the first performance-summary path/value. If only `ev-performance-summary` is available, use its first eligible path/value as the single part. Do not infer, rename, transform, compare, or calculate a value.
Additional atomic claims may follow the variant's original diagnosis instruction. They must not alter atomic_claims[0]. If the original instruction requires abstention, return the schema-valid abstained result instead of inventing a witness."""
QUALIFICATION_PREAMBLE: Final = (
    "This is a synthetic V2 transport-and-expressiveness qualification, not a "
    "scientific diagnosis. The evidence is synthetic and the response is excluded "
    "from every corpus, label, estimand, and human packet."
)
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProviderVariant = Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
RelationFrame = Literal[
    "natural_context",
    "support_withdrawal",
    "partial_support_projection",
    "direct_counterevidence",
]
class ClaimValidationV2ExpressivenessError(ValueError):
    """Raised when the prospective source-claim contract is not reproducible."""
class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )
class ExpressivenessWitness(_StrictFrozenModel):
    family_id: str
    mechanism: Mechanism
    evidence_condition: EvidenceCondition
    cited_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    material_parts: tuple[str, ...] = Field(min_length=1, max_length=2)
    natural_context_eligible: Literal[True] = True
    challenge_frames_eligible: tuple[ChallengeFrame, ...]
    admitted_to_corpus: Literal[False] = False
    witness_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"witness_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        expected_challenges: tuple[ChallengeFrame, ...] = (
            ("direct_counterevidence",)
            if self.evidence_condition == "missing_key"
            else (
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            )
        )
        if (
            self.challenge_frames_eligible != expected_challenges
            or self.witness_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("expressiveness witness does not reconcile")
        return self
class ExpressivenessFrameCapacity(_StrictFrozenModel):
    frame: RelationFrame
    context_count: int = Field(ge=25, le=45)
    family_count: int = Field(ge=10, le=15)
    scheduled_diagnosis_cell_count: int = Field(ge=25, le=360)
    registered_minimums_satisfied: Literal[True] = True
class ExpressivenessQualificationProbe(_StrictFrozenModel):
    variant: ProviderVariant
    source_schedule_entry_sha256: Sha256
    prompt_text: str = Field(min_length=1)
    response_schema_json: str = Field(min_length=1)
    context: ModelVisibleEvidenceContext
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens: Literal[2048]
    contract_sha256: Sha256
    qualification_request_sha256: Sha256
    synthetic_only: Literal[True] = True
    admitted_to_corpus: Literal[False] = False

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"qualification_request_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.qualification_request_sha256 != canonical_execution_sha256(
            self.identity_payload()
        ):
            raise ValueError("expressiveness qualification request identity differs")
        return self
class ClaimSupportValidationV2ExpressivenessAmendment(_StrictFrozenModel):
    schema_version: Literal[
        "claim-support-validation-v2-expressiveness-amendment/v1"
    ] = EXPRESSIVENESS_AMENDMENT_SCHEMA_VERSION
    status: Literal["v2_expressiveness_contract_frozen_qualification_pending"]
    parent_protocol_sha256: Sha256
    parent_runtime_manifest_sha256: Sha256
    parent_runtime_readiness_sha256: Sha256
    evidence_census_sha256: Sha256
    shared_instruction_suffix: str = Field(min_length=1)
    measurement_selection_algorithm: Literal[
        "visible-observed-delta-path-priority/v1"
    ]
    measurement_path_priority: tuple[str, str, str]
    source_claim_selection_algorithm: Literal[
        "measurement-witness-first-then-canonical-claim-hash/v1"
    ]
    superseded_source_claim_selection_algorithm: Literal["canonical-claim-hash/v1"]
    witness_claim_ordinal: Literal[1]
    witness_claim_type: Literal["evidence_statement"]
    additional_variant_claims_permitted: Literal[True]
    abstention_preserved: Literal[True]
    local_acceptance_requires_exact_witness: Literal[True]
    deterministic_b0_uses_same_witness_algorithm: Literal[True]
    legacy_runtime_qualification_probes_superseded: Literal[True]
    generalization_to_free_form_diagnosis_forbidden: Literal[True]
    qualification_requires_all_parsed: Literal[True]
    qualification_probes: tuple[ExpressivenessQualificationProbe, ...] = Field(
        min_length=7, max_length=7
    )
    implementation_sources: tuple[SourceArtifactBinding, ...] = Field(
        min_length=4, max_length=4
    )
    v1_relation_outcomes_read: Literal[False] = False
    v1_label_frequencies_read: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    amendment_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"amendment_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        if (
            self.measurement_path_priority != MEASUREMENT_PATH_PRIORITY
            or tuple(item.variant for item in self.qualification_probes)
            != PROVIDER_VARIANTS
            or len({item.qualification_request_sha256 for item in self.qualification_probes})
            != 7
            or any(
                item.contract_sha256
                != canonical_execution_sha256(
                    {
                        "shared_instruction_suffix": self.shared_instruction_suffix,
                        "measurement_selection_algorithm": self.measurement_selection_algorithm,
                        "measurement_path_priority": self.measurement_path_priority,
                        "source_claim_selection_algorithm": self.source_claim_selection_algorithm,
                    }
                )
                for item in self.qualification_probes
            )
            or tuple(item.path for item in self.implementation_sources)
            != tuple(sorted(item.path for item in self.implementation_sources))
            or self.amendment_sha256
            != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 expressiveness amendment does not reconcile")
        return self
class ClaimSupportValidationV2ExpressivenessReview(_StrictFrozenModel):
    schema_version: Literal[
        "claim-support-validation-v2-expressiveness-review/v1"
    ] = EXPRESSIVENESS_REVIEW_SCHEMA_VERSION
    status: Literal["v2_expressiveness_review_pass_qualification_pending"]
    amendment_sha256: Sha256
    parent_protocol_sha256: Sha256
    parent_runtime_manifest_sha256: Sha256
    evidence_context_count: Literal[45]
    witness_context_count: Literal[45]
    witnesses: tuple[ExpressivenessWitness, ...] = Field(min_length=45, max_length=45)
    effective_frame_capacity: tuple[ExpressivenessFrameCapacity, ...] = Field(
        min_length=4, max_length=4
    )
    all_contexts_have_exact_source_witness: Literal[True]
    all_frames_meet_registered_minimums: Literal[True]
    prompt_schema_and_local_acceptance_bound: Literal[True]
    source_claim_expressiveness_review_required: Literal[False]
    next_gate: Literal["separately_authorized_seven_request_live_qualification"]
    live_qualification_authorized: Literal[False] = False
    v1_relation_outcomes_read: Literal[False] = False
    v1_label_frequencies_read: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    review_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"review_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        if (
            len({item.witness_sha256 for item in self.witnesses}) != 45
            or tuple(item.frame for item in self.effective_frame_capacity)
            != (
                "natural_context",
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            )
            or self.review_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 expressiveness review does not reconcile")
        return self


def _path_priority(path: str) -> tuple[int, str]:
    for rank, prefix in enumerate(MEASUREMENT_PATH_PRIORITY):
        if path.startswith(prefix):
            return rank, path
    return len(MEASUREMENT_PATH_PRIORITY), path


def _base10(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _first_measurement(
    item: ModelVisibleEvidenceItem,
    *,
    excluding_identical: Mapping[str, Decimal] | None = None,
) -> tuple[str, Decimal]:
    facts = measurements(item)
    eligible = tuple(
        path
        for path, value in facts.items()
        if excluding_identical is None or excluding_identical.get(path) != value
    )
    if not eligible:
        raise ClaimValidationV2ExpressivenessError(
            f"{item.evidence_id} has no eligible numeric measurement"
        )
    path = min(eligible, key=_path_priority)
    return path, facts[path]


def build_measurement_witness_claim(
    context: ModelVisibleEvidenceContext,
) -> AtomicClaimV2:
    """Build the deterministic review witness defined by the amendment."""

    items = {item.evidence_id: item for item in context.items}
    performance = items.get("ev-performance-summary")
    if performance is None:
        raise ClaimValidationV2ExpressivenessError(
            "expressiveness contract requires ev-performance-summary"
        )
    selected: list[tuple[str, str, Decimal]] = []
    key = items.get("ev-key-measurement")
    if key is not None:
        path, value = _first_measurement(
            key,
            excluding_identical=measurements(performance),
        )
        selected.append((key.evidence_id, path, value))
    path, value = _first_measurement(performance)
    selected.append((performance.evidence_id, path, value))
    texts = tuple(f"{path} = {_base10(value)}" for _, path, value in selected)
    return AtomicClaimV2(
        claim_local_id="claim-1",
        claim_type="evidence_statement",
        claim_text="; ".join(texts),
        material_parts=tuple(
            MaterialClaimPart(part_id=f"part-{ordinal}", text=text)
            for ordinal, text in enumerate(texts, start=1)
        ),
        visible_evidence_ids=tuple(evidence_id for evidence_id, _, _ in selected),
    )


def select_amended_source_claims(
    output: DiagnosisOutputV2,
    context: ModelVisibleEvidenceContext,
) -> tuple[AtomicClaimV2, ...]:
    """Validate and select the witness before one canonical diagnostic claim."""

    if output.output_status != "completed" or not output.atomic_claims:
        return ()
    witness = output.atomic_claims[0]
    if witness != build_measurement_witness_claim(context):
        raise ClaimValidationV2ExpressivenessError(
            "completed V2 output does not start with the exact required witness"
        )
    remaining = sorted(
        output.atomic_claims[1:],
        key=lambda claim: canonical_execution_sha256(
            {
                "algorithm": SOURCE_CLAIM_SELECTION_ALGORITHM,
                "output_sha256": output.output_sha256,
                "claim": claim.model_dump(mode="json"),
            }
        ),
    )
    return (witness, *remaining[:1])


def _diagnosis_output(claim: AtomicClaimV2) -> DiagnosisOutputV2:
    source_record_sha256 = canonical_execution_sha256(
        {"purpose": "offline-expressiveness-review", "claim": claim.model_dump(mode="json")}
    )
    payload: dict[str, object] = {
        "schema_version": "diagnosis-output/2",
        "output_status": "completed",
        "atomic_claims": (claim.model_dump(mode="json"),),
        "abstention_reason": None,
        "parse_failure_code": None,
        "source_record_sha256": source_record_sha256,
    }
    return DiagnosisOutputV2.model_validate(
        {
            **payload,
            "atomic_claims": (claim,),
            "output_sha256": canonical_execution_sha256(payload),
        }
    )


def _schedule_by_context(
    manifest: ClaimSupportValidationV2RuntimeManifest,
) -> dict[tuple[str, EvidenceCondition], V2DiagnosisScheduleEntry]:
    result: dict[tuple[str, EvidenceCondition], V2DiagnosisScheduleEntry] = {}
    for entry in manifest.diagnosis_schedule:
        result.setdefault((entry.family_id, entry.evidence_condition), entry)
    if len(result) != 45:
        raise ClaimValidationV2ExpressivenessError("V2 schedule lost a source context")
    return result


def _witness_reviews(
    manifest: ClaimSupportValidationV2RuntimeManifest,
    evidence_contexts: Mapping[str, ModelVisibleEvidenceContext],
    frame_contexts: Mapping[str, ModelVisibleEvidenceContext],
) -> tuple[ExpressivenessWitness, ...]:
    schedule = _schedule_by_context(manifest)
    result = []
    for frame in manifest.source_frames:
        context = evidence_contexts[frame.natural_context_sha256]
        claim = build_measurement_witness_claim(context)
        output = _diagnosis_output(claim)
        if select_amended_source_claims(output, context) != (claim,):
            raise ClaimValidationV2ExpressivenessError(
                "review witness differs from amended source-claim selection"
            )
        entry = schedule[(frame.family_id, frame.evidence_condition)]
        eligible, _ = _eligible_challenges(
            entry,
            frame,
            output,
            claim,
            frame_contexts,
        )
        ordered = tuple(
            challenge
            for challenge in (
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            )
            if challenge in eligible
        )
        payload: dict[str, object] = {
            "family_id": frame.family_id,
            "mechanism": frame.mechanism,
            "evidence_condition": frame.evidence_condition,
            "cited_evidence_ids": claim.visible_evidence_ids,
            "material_parts": tuple(part.text for part in claim.material_parts),
            "natural_context_eligible": True,
            "challenge_frames_eligible": ordered,
            "admitted_to_corpus": False,
        }
        result.append(
            ExpressivenessWitness.model_validate(
                {**payload, "witness_sha256": canonical_execution_sha256(payload)}
            )
        )
    return tuple(result)


def _synthetic_content(evidence_id: str) -> str:
    if evidence_id == "ev-key-measurement":
        payload: object = {"payload": {"probe_only_metric": 7}}
    elif evidence_id == "ev-performance-summary":
        payload = {"payload": {"observed": {"probe_score": 0.7}}}
    elif evidence_id == "ev-secondary-observation":
        payload = {"payload": {"synthetic_noise": 101}}
    else:
        payload = {"payload": {"synthetic_provenance": "qualification-only"}}
    return canonical_project_json(payload)


def _synthetic_context(original: ModelVisibleEvidenceContext) -> ModelVisibleEvidenceContext:
    items = tuple(
        build_visible_evidence_item(
            evidence_id=item.evidence_id,
            kind=item.kind,
            title="Synthetic V2 expressiveness probe",
            content=_synthetic_content(item.evidence_id),
            source_content_sha256=canonical_execution_sha256(
                {"purpose": "synthetic-v2-expressiveness", "evidence_id": item.evidence_id}
            ),
        )
        for item in original.items
    )
    return build_visible_context(items)


def _contract_sha256() -> str:
    return canonical_execution_sha256(
        {
            "shared_instruction_suffix": SHARED_EXPRESSIVENESS_INSTRUCTION,
            "measurement_selection_algorithm": MEASUREMENT_SELECTION_ALGORITHM,
            "measurement_path_priority": MEASUREMENT_PATH_PRIORITY,
            "source_claim_selection_algorithm": SOURCE_CLAIM_SELECTION_ALGORITHM,
        }
    )


def _qualification_probes(
    root: Path,
    manifest: ClaimSupportValidationV2RuntimeManifest,
    evidence_contexts: Mapping[str, ModelVisibleEvidenceContext],
) -> tuple[ExpressivenessQualificationProbe, ...]:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    contract_sha256 = _contract_sha256()
    result = []
    for variant in PROVIDER_VARIANTS:
        source = next(item for item in manifest.diagnosis_schedule if item.variant == variant)
        context = _synthetic_context(evidence_contexts[source.visible_context_sha256])
        prompt = "\n\n".join(
            (
                QUALIFICATION_PREAMBLE,
                freeze.prompt_policies[variant].instruction_contract,
                SHARED_EXPRESSIVENESS_INSTRUCTION,
            )
        )
        schema_json = json.dumps(
            provider_response_schema_v2(tuple(item.evidence_id for item in context.items)),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        payload: dict[str, object] = {
            "variant": variant,
            "source_schedule_entry_sha256": source.v2_request_sha256,
            "prompt_text": prompt,
            "response_schema_json": schema_json,
            "context": context.model_dump(mode="json"),
            "model_snapshot": "gpt-4.1-2025-04-14",
            "maximum_output_tokens": 2048,
            "contract_sha256": contract_sha256,
            "synthetic_only": True,
            "admitted_to_corpus": False,
        }
        result.append(
            ExpressivenessQualificationProbe.model_validate(
                {
                    **payload,
                    "context": context,
                    "qualification_request_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    return tuple(result)


def _implementation_sources(root: Path) -> tuple[SourceArtifactBinding, ...]:
    paths = tuple(
        sorted(
            (
                "src/aletheia_lab/evaluation/claim_corpus_normalization_recovery.py",
                "src/aletheia_lab/evaluation/claim_validation_v2_expressiveness.py",
                "src/aletheia_lab/evaluation/claim_validation_v2_frames.py",
                "src/aletheia_lab/evaluation/claim_validation_v2_relation_frames.py",
            )
        )
    )
    try:
        return tuple(
            SourceArtifactBinding(
                path=path,
                content_sha256=content_sha256((root / path).read_bytes()),
            )
            for path in paths
        )
    except OSError as exc:
        raise ClaimValidationV2ExpressivenessError(
            "expressiveness implementation source is unavailable"
        ) from exc


def build_v2_expressiveness_amendment(
    root: Path,
) -> ClaimSupportValidationV2ExpressivenessAmendment:
    """Freeze the prospective prompt, selection, and qualification contract."""

    root = root.resolve()
    verify_tracked_v2_runtime(root)
    _, _, evidence = _load_inputs(root)
    manifest = build_v2_runtime_manifest(root)
    readiness = build_v2_runtime_readiness(manifest)
    evidence_contexts = {
        binding.visible_context.context_sha256: binding.visible_context
        for binding in evidence.bindings
    }
    qualification_probes = _qualification_probes(root, manifest, evidence_contexts)
    implementation_sources = _implementation_sources(root)
    payload: dict[str, object] = {
        "schema_version": EXPRESSIVENESS_AMENDMENT_SCHEMA_VERSION,
        "status": "v2_expressiveness_contract_frozen_qualification_pending",
        "parent_protocol_sha256": manifest.protocol_sha256,
        "parent_runtime_manifest_sha256": manifest.manifest_sha256,
        "parent_runtime_readiness_sha256": readiness.readiness_sha256,
        "evidence_census_sha256": evidence.census_sha256,
        "shared_instruction_suffix": SHARED_EXPRESSIVENESS_INSTRUCTION,
        "measurement_selection_algorithm": MEASUREMENT_SELECTION_ALGORITHM,
        "measurement_path_priority": MEASUREMENT_PATH_PRIORITY,
        "source_claim_selection_algorithm": SOURCE_CLAIM_SELECTION_ALGORITHM,
        "superseded_source_claim_selection_algorithm": "canonical-claim-hash/v1",
        "witness_claim_ordinal": 1,
        "witness_claim_type": "evidence_statement",
        "additional_variant_claims_permitted": True,
        "abstention_preserved": True,
        "local_acceptance_requires_exact_witness": True,
        "deterministic_b0_uses_same_witness_algorithm": True,
        "legacy_runtime_qualification_probes_superseded": True,
        "generalization_to_free_form_diagnosis_forbidden": True,
        "qualification_requires_all_parsed": True,
        "qualification_probes": qualification_probes,
        "implementation_sources": implementation_sources,
        "v1_relation_outcomes_read": False,
        "v1_label_frequencies_read": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    identity = {
        **payload,
        "qualification_probes": tuple(
            item.model_dump(mode="json") for item in qualification_probes
        ),
        "implementation_sources": tuple(
            item.model_dump(mode="json") for item in implementation_sources
        ),
    }
    return ClaimSupportValidationV2ExpressivenessAmendment.model_validate(
        {**payload, "amendment_sha256": canonical_execution_sha256(identity)}
    )


def _effective_capacity(
    manifest: ClaimSupportValidationV2RuntimeManifest,
    witnesses: Sequence[ExpressivenessWitness],
) -> tuple[ExpressivenessFrameCapacity, ...]:
    frames: tuple[RelationFrame, ...] = (
        "natural_context",
        "support_withdrawal",
        "partial_support_projection",
        "direct_counterevidence",
    )
    pairs: dict[RelationFrame, set[tuple[str, EvidenceCondition]]] = {
        frame: set() for frame in frames
    }
    for witness in witnesses:
        pair = (witness.family_id, witness.evidence_condition)
        pairs["natural_context"].add(pair)
        for challenge_frame in witness.challenge_frames_eligible:
            pairs[challenge_frame].add(pair)
    result = []
    for frame, frame_pairs in pairs.items():
        result.append(
            ExpressivenessFrameCapacity(
                frame=frame,
                context_count=len(frame_pairs),
                family_count=len({family_id for family_id, _ in frame_pairs}),
                scheduled_diagnosis_cell_count=sum(
                    (item.family_id, item.evidence_condition) in frame_pairs
                    for item in manifest.diagnosis_schedule
                ),
                registered_minimums_satisfied=True,
            )
        )
    return tuple(result)


def build_v2_expressiveness_review(
    root: Path,
    amendment: ClaimSupportValidationV2ExpressivenessAmendment | None = None,
) -> ClaimSupportValidationV2ExpressivenessReview:
    """Audit all 45 contexts against the exact production witness functions."""

    root = root.resolve()
    checked_amendment = amendment or build_v2_expressiveness_amendment(root)
    manifest = build_v2_runtime_manifest(root)
    _, _, evidence = _load_inputs(root)
    evidence_contexts = {
        binding.visible_context.context_sha256: binding.visible_context
        for binding in evidence.bindings
    }
    frame_contexts = _frame_contexts(manifest, evidence)
    witnesses = _witness_reviews(manifest, evidence_contexts, frame_contexts)
    capacity = _effective_capacity(manifest, witnesses)
    payload: dict[str, object] = {
        "schema_version": EXPRESSIVENESS_REVIEW_SCHEMA_VERSION,
        "status": "v2_expressiveness_review_pass_qualification_pending",
        "amendment_sha256": checked_amendment.amendment_sha256,
        "parent_protocol_sha256": manifest.protocol_sha256,
        "parent_runtime_manifest_sha256": manifest.manifest_sha256,
        "evidence_context_count": 45,
        "witness_context_count": len(witnesses),
        "witnesses": witnesses,
        "effective_frame_capacity": capacity,
        "all_contexts_have_exact_source_witness": True,
        "all_frames_meet_registered_minimums": all(
            item.registered_minimums_satisfied for item in capacity
        ),
        "prompt_schema_and_local_acceptance_bound": True,
        "source_claim_expressiveness_review_required": False,
        "next_gate": "separately_authorized_seven_request_live_qualification",
        "live_qualification_authorized": False,
        "v1_relation_outcomes_read": False,
        "v1_label_frequencies_read": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    identity = {
        **payload,
        "witnesses": tuple(item.model_dump(mode="json") for item in witnesses),
        "effective_frame_capacity": tuple(
            item.model_dump(mode="json") for item in capacity
        ),
    }
    return ClaimSupportValidationV2ExpressivenessReview.model_validate(
        {**payload, "review_sha256": canonical_execution_sha256(identity)}
    )


def canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_tracked(
    path: Path,
    model: type[
        ClaimSupportValidationV2ExpressivenessAmendment
        | ClaimSupportValidationV2ExpressivenessReview
    ],
) -> ClaimSupportValidationV2ExpressivenessAmendment | ClaimSupportValidationV2ExpressivenessReview:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2ExpressivenessError(
            "tracked V2 expressiveness artifact is unavailable or invalid"
        ) from exc


def verify_tracked_v2_expressiveness(root: Path) -> dict[str, object]:
    """Rebuild both tracked artifacts without opening a provider boundary."""

    expected_amendment = build_v2_expressiveness_amendment(root)
    tracked_amendment = _load_tracked(
        root / EXPRESSIVENESS_AMENDMENT_PATH,
        ClaimSupportValidationV2ExpressivenessAmendment,
    )
    if tracked_amendment != expected_amendment:
        raise ClaimValidationV2ExpressivenessError(
            "tracked V2 expressiveness amendment is stale or altered"
        )
    expected_review = build_v2_expressiveness_review(root, expected_amendment)
    tracked_review = _load_tracked(
        root / EXPRESSIVENESS_REVIEW_PATH,
        ClaimSupportValidationV2ExpressivenessReview,
    )
    if tracked_review != expected_review:
        raise ClaimValidationV2ExpressivenessError(
            "tracked V2 expressiveness review is stale or altered"
        )
    return expected_review.model_dump(mode="json")


__all__ = [
    "EXPRESSIVENESS_AMENDMENT_PATH",
    "EXPRESSIVENESS_REVIEW_PATH",
    "ClaimSupportValidationV2ExpressivenessAmendment",
    "ClaimSupportValidationV2ExpressivenessReview",
    "ClaimValidationV2ExpressivenessError",
    "build_measurement_witness_claim",
    "build_v2_expressiveness_amendment",
    "build_v2_expressiveness_review",
    "canonical_json",
    "select_amended_source_claims",
    "verify_tracked_v2_expressiveness",
]
