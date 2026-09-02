"""Outcome-blind protocol and feasibility audit for the claim-support corpus.

This module freezes where development claims may come from, which diagnosis
variants and mechanism tracks are eligible, how atomic claims are defined, and
how automatic support labels are assigned.  It deliberately does not execute a
model, extract a real claim, assign a label, or open a main/sealed outcome.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, NoReturn, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.project.identity import content_sha256

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mechanism = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
SupportLabel = Literal[
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
]
Variant = Literal["A1", "A2", "A3", "B0", "B1", "B2", "B3", "CodeGraph", "FULL"]

MECHANISMS: Final[tuple[Mechanism, ...]] = (
    "data_drift",
    "preprocessing_mismatch",
    "label_noise",
)
ELIGIBLE_VARIANTS: Final[tuple[Variant, ...]] = (
    "A1",
    "A2",
    "A3",
    "B0",
    "B1",
    "B2",
    "CodeGraph",
    "FULL",
)
LABEL_ORDER: Final[tuple[SupportLabel, ...]] = (
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
)
EXPECTED_BLOCKERS: Final[tuple[str, ...]] = (
    "automatic_instrument_manifest_pending",
    "diagnosis_output_v2_schema_pending",
    "insufficient_development_family_census",
    "label_noise_family_manifest_pending",
    "preprocessing_family_manifest_pending",
    "reserve_family_census_pending",
)


class ClaimCorpusProtocolError(ValueError):
    """Raised when a corpus protocol or its tracked receipt is invalid."""


def _fail(message: str) -> NoReturn:
    raise ClaimCorpusProtocolError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class BoundArtifact(_StrictFrozenModel):
    role: Literal[
        "validation_protocol",
        "variant_fairness_freeze",
        "mechanism_filter",
        "fault_family_inventory",
    ]
    path: str
    content_sha256: Sha256

    @field_validator("path")
    @classmethod
    def _path_is_canonical(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise ValueError("bound artifact paths must be canonical repository-relative paths")
        return value


class SourceBoundary(_StrictFrozenModel):
    source_partition: Literal["development"]
    permitted_endpoint: Literal["evidence_accountability"]
    mechanism_inventory: tuple[Mechanism, ...]
    causal_diagnosis_scoring_permitted: Literal[False]
    admitted_causal_track_required: Literal[False]
    preserve_mechanism_disposition: Literal[True]
    synthetic_claims_forbidden: Literal[True]
    padded_claims_forbidden: Literal[True]
    main_outcomes_forbidden: Literal[True]
    sealed_outcomes_forbidden: Literal[True]

    @model_validator(mode="after")
    def _inventory_is_frozen(self) -> Self:
        if self.mechanism_inventory != MECHANISMS:
            raise ValueError("mechanism inventory must preserve the frozen order")
        return self


class MechanismFamilyPlan(_StrictFrozenModel):
    mechanism: Mechanism
    primary_family_count: Literal[5]
    reserve_family_count: Literal[2]
    evidence_conditions: tuple[Literal["full", "missing_key", "noisy"], ...]
    family_manifest_required_before_materialization: Literal[True]

    @field_validator("evidence_conditions")
    @classmethod
    def _conditions_are_frozen(
        cls, values: tuple[Literal["full", "missing_key", "noisy"], ...]
    ) -> tuple[Literal["full", "missing_key", "noisy"], ...]:
        if values != ("full", "missing_key", "noisy"):
            raise ValueError("each mechanism must use the three frozen evidence conditions")
        return values


class FamilyCensusPolicy(_StrictFrozenModel):
    mechanism_plans: tuple[MechanismFamilyPlan, ...]
    primary_family_total: Literal[15]
    reserve_family_total: Literal[6]
    minimum_distinct_families_per_automatic_label: Literal[10]
    minimum_eligible_outputs_per_automatic_label: Literal[25]
    target_claims_per_automatic_label: Literal[50]
    maximum_claims_per_family_per_label: Literal[5]
    maximum_claims_per_output_per_label: Literal[2]
    family_identity_bound_before_provider_call: Literal[True]
    complete_primary_request_census_before_provider_call: Literal[True]

    @model_validator(mode="after")
    def _plan_is_coherent(self) -> Self:
        if tuple(item.mechanism for item in self.mechanism_plans) != MECHANISMS:
            raise ValueError("mechanism family plans must preserve the frozen inventory")
        if len(self.mechanism_plans) * 5 != self.primary_family_total:
            raise ValueError("primary family total does not match mechanism plans")
        if len(self.mechanism_plans) * 2 != self.reserve_family_total:
            raise ValueError("reserve family total does not match mechanism plans")
        required = (
            self.minimum_distinct_families_per_automatic_label
            * self.maximum_claims_per_family_per_label
        )
        if required != self.target_claims_per_automatic_label:
            raise ValueError("family diversity and per-family cap must equal the label quota")
        return self


class VariantEligibilityPolicy(_StrictFrozenModel):
    eligible_variants: tuple[Variant, ...]
    excluded_variant: Literal["B3"]
    exclusion_reason: Literal[
        "external_native_output_is_not_comparable_to_the_atomic_claim_evidence_contract"
    ]
    excluded_output_pooling_forbidden: Literal[True]
    each_eligible_variant_requires_normalized_claim_adapter: Literal[True]
    fairness_freeze_must_remain_unchanged: Literal[True]

    @model_validator(mode="after")
    def _variants_are_frozen(self) -> Self:
        if self.eligible_variants != ELIGIBLE_VARIANTS:
            raise ValueError("claim-corpus variants must preserve the frozen eligible set")
        if self.excluded_variant in self.eligible_variants:
            raise ValueError("the external native variant cannot enter the claim corpus")
        return self


class AtomicClaimPolicy(_StrictFrozenModel):
    normalized_schema_ref: Literal["diagnosis-output/2"]
    required_schema_manifest_path: str
    allowed_claim_types: tuple[
        Literal["cause_assertion"],
        Literal["evidence_statement"],
        Literal["uncertainty_statement"],
        Literal["recommended_action"],
        Literal["other"],
    ]
    extraction_source: Literal["schema_native_atomic_claim_fields_only"]
    punctuation_or_sentence_splitting_forbidden: Literal[True]
    free_text_fallback_forbidden: Literal[True]
    maximum_atomic_claims_per_output: Literal[5]
    visible_evidence_ids_required: Literal[True]
    source_record_sha256_required: Literal[True]

    @field_validator("required_schema_manifest_path")
    @classmethod
    def _schema_path_is_frozen(cls, value: str) -> str:
        if value != "configs/evaluation/diagnosis_output_v2_schema.json":
            raise ValueError("diagnosis output schema path must remain frozen")
        return value


class AutomaticRelationDefinition(_StrictFrozenModel):
    label: SupportLabel
    decision_rule: Literal[
        "visible_evidence_conflicts_with_any_material_claim_part",
        "visible_evidence_supports_no_material_claim_part",
        "visible_evidence_supports_some_but_not_all_material_claim_parts",
        "visible_evidence_supports_all_material_claim_parts_without_conflict",
    ]


class AutomaticLabelPolicy(_StrictFrozenModel):
    ordered_labels: tuple[SupportLabel, ...]
    precedence: tuple[AutomaticRelationDefinition, ...]
    permitted_input_fields: tuple[
        Literal["claim_text"], Literal["claim_type"], Literal["visible_evidence"]
    ]
    withheld_input_fields: tuple[
        Literal["mechanism"],
        Literal["evidence_condition"],
        Literal["variant"],
        Literal["hidden_ground_truth"],
        Literal["human_judgment"],
        Literal["main_outcome"],
    ]
    required_implementation_manifest_path: str
    implementation_frozen_before_claim_materialization: Literal[True]
    human_judgment_may_rewrite_automatic_label: Literal[False]
    model_as_human_rater_forbidden: Literal[True]

    @model_validator(mode="after")
    def _label_rules_are_frozen(self) -> Self:
        if self.ordered_labels != LABEL_ORDER:
            raise ValueError("automatic labels must preserve the validation protocol order")
        if tuple(item.label for item in self.precedence) != LABEL_ORDER:
            raise ValueError("automatic relation precedence must preserve label order")
        expected_rules = (
            "visible_evidence_conflicts_with_any_material_claim_part",
            "visible_evidence_supports_no_material_claim_part",
            "visible_evidence_supports_some_but_not_all_material_claim_parts",
            "visible_evidence_supports_all_material_claim_parts_without_conflict",
        )
        if tuple(item.decision_rule for item in self.precedence) != expected_rules:
            raise ValueError("automatic support decisions must preserve the frozen semantics")
        expected_path = "configs/evaluation/claim_support_automatic_instrument_manifest.json"
        if self.required_implementation_manifest_path != expected_path:
            raise ValueError("automatic instrument manifest path must remain frozen")
        return self


class ContingencyPolicy(_StrictFrozenModel):
    reserve_use: Literal["pre_execution_technical_ineligibility_only"]
    reserve_order: Literal["mechanism_local_registered_order"]
    automatic_label_may_trigger_reserve: Literal[False]
    human_judgment_may_trigger_reserve: Literal[False]
    output_driven_early_stopping_forbidden: Literal[True]
    label_specific_expansion_forbidden: Literal[True]
    adaptive_family_generation_forbidden: Literal[True]
    quota_or_threshold_change_after_output_forbidden: Literal[True]
    insufficient_completed_stratum_action: Literal["block_without_padding_or_replacement"]


class ClaimSupportCorpusProtocol(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-protocol/v1"]
    protocol_id: Literal["claim-support-development-corpus-v1"]
    freeze_status: Literal["outcome_blind_protocol_freeze"]
    frozen_before_provider_calls: Literal[True]
    provider_calls_executed: Literal[False]
    development_claim_pool_materialized: Literal[False]
    automatic_labels_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    parent_validation_protocol_sha256: Sha256
    bound_artifacts: tuple[BoundArtifact, ...]
    source_boundary: SourceBoundary
    family_census: FamilyCensusPolicy
    variant_eligibility: VariantEligibilityPolicy
    atomic_claim_policy: AtomicClaimPolicy
    automatic_label_policy: AutomaticLabelPolicy
    contingency_policy: ContingencyPolicy
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def _identity_is_derived(self) -> Self:
        roles = tuple(item.role for item in self.bound_artifacts)
        expected_roles = (
            "validation_protocol",
            "variant_fairness_freeze",
            "mechanism_filter",
            "fault_family_inventory",
        )
        if roles != expected_roles or len(set(roles)) != len(roles):
            raise ValueError("bound artifact roles must be unique and preserve the frozen order")
        payload = self.model_dump(mode="json", exclude={"protocol_sha256"})
        if self.protocol_sha256 != canonical_sha256(payload):
            raise ValueError("protocol_sha256 is not derived from the canonical protocol")
        return self


class MechanismCensus(_StrictFrozenModel):
    mechanism: Mechanism
    declared_primary_families: int = Field(ge=0)
    required_primary_families: Literal[5]
    required_reserve_families: Literal[2]


class ClaimSupportCorpusFeasibilityReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-feasibility-receipt/v1"]
    protocol_sha256: Sha256
    mechanism_census: tuple[MechanismCensus, ...]
    current_declared_family_total: int = Field(ge=0)
    required_primary_family_total: Literal[15]
    required_reserve_family_total: Literal[6]
    current_maximum_selectable_claims_per_label: int = Field(ge=0)
    target_claims_per_label: Literal[50]
    blocker_codes: tuple[str, ...]
    materialization_ready: bool
    development_claim_pool_materialized: Literal[False]
    validation_sample_materialized: Literal[False]
    automatic_labels_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    status: Literal[
        "corpus_protocol_frozen_source_expansion_required",
        "corpus_protocol_frozen_materialization_ready",
    ]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_coherent(self) -> Self:
        blockers = tuple(sorted(set(self.blocker_codes)))
        if self.blocker_codes != blockers:
            raise ValueError("blocker codes must be unique and sorted")
        if self.materialization_ready == bool(blockers):
            raise ValueError("materialization readiness must be the inverse of blockers")
        expected_status = (
            "corpus_protocol_frozen_materialization_ready"
            if self.materialization_ready
            else "corpus_protocol_frozen_source_expansion_required"
        )
        if self.status != expected_status:
            raise ValueError("receipt status does not match materialization readiness")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("receipt_sha256 is not derived from the canonical receipt")
        return self


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"required JSON artifact is unavailable or invalid: {path.name}: {type(exc).__name__}")
    if not isinstance(payload, dict):
        _fail(f"required JSON artifact must contain one object: {path.name}")
    return payload


def _resolve_bound_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        _fail(f"bound artifact is unavailable: {relative}: {type(exc).__name__}")
    if candidate.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
        _fail(f"bound artifact must be one regular repository-local file: {relative}")
    return resolved


def load_claim_support_corpus_protocol(path: Path) -> ClaimSupportCorpusProtocol:
    """Load and strictly validate the frozen corpus protocol."""

    try:
        return ClaimSupportCorpusProtocol.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ClaimCorpusProtocolError("claim-support corpus protocol is unavailable or invalid") from exc


def load_claim_support_corpus_feasibility_receipt(
    path: Path,
) -> ClaimSupportCorpusFeasibilityReceipt:
    """Load and strictly validate the tracked outcome-blind feasibility receipt."""

    try:
        return ClaimSupportCorpusFeasibilityReceipt.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ClaimCorpusProtocolError("claim-support corpus receipt is unavailable or invalid") from exc


def _verify_bound_artifacts(protocol: ClaimSupportCorpusProtocol, root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for artifact in protocol.bound_artifacts:
        path = _resolve_bound_path(root, artifact.path)
        if content_sha256(path.read_bytes()) != artifact.content_sha256:
            _fail(f"bound artifact content changed: {artifact.role}")
        paths[artifact.role] = path
    parent = _load_json_object(paths["validation_protocol"])
    if parent.get("protocol_sha256") != protocol.parent_validation_protocol_sha256:
        _fail("parent validation protocol identity does not match the corpus protocol")
    return paths


def _verify_scientific_boundaries(
    protocol: ClaimSupportCorpusProtocol, paths: dict[str, Path]
) -> None:
    fairness = _load_json_object(paths["variant_fairness_freeze"])
    variants = fairness.get("variants")
    if not isinstance(variants, list):
        _fail("variant fairness freeze has no valid variant inventory")
    variant_ids = tuple(
        item.get("variant_id") for item in variants if isinstance(item, dict)
    )
    if variant_ids != (*ELIGIBLE_VARIANTS[:6], "B3", *ELIGIBLE_VARIANTS[6:]):
        _fail("variant fairness inventory changed after the corpus freeze")
    if fairness.get("protected_outcomes_opened") is not False:
        _fail("protected diagnosis outcomes were opened before corpus materialization")
    mechanism_filter = _load_json_object(paths["mechanism_filter"])
    inventory = mechanism_filter.get("mechanism_inventory")
    if not isinstance(inventory, list) or tuple(inventory) != MECHANISMS:
        _fail("mechanism filter inventory changed after the corpus freeze")
    accountability = mechanism_filter.get("evidence_accountability_track")
    if not isinstance(accountability, list) or tuple(accountability) != MECHANISMS:
        _fail("all frozen mechanisms must remain on the evidence-accountability track")
    if mechanism_filter.get("causal_diagnosis_scoring_for_non_admitted_forbidden") is not True:
        _fail("non-admitted mechanisms must remain excluded from causal diagnosis scoring")


def _declared_family_counts(path: Path) -> dict[Mechanism, int]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        _fail(f"fault family inventory is unavailable or invalid: {type(exc).__name__}")
    if not isinstance(payload, dict) or not isinstance(payload.get("fault_types"), dict):
        _fail("fault family inventory must contain a fault_types mapping")
    fault_types = payload["fault_types"]
    assert isinstance(fault_types, dict)
    aliases = {
        "data_drift": "data_drift",
        "preprocessing_mismatch": "preprocessing_bug",
        "label_noise": "label_noise",
    }
    counts: dict[Mechanism, int] = {}
    for mechanism in MECHANISMS:
        record = fault_types.get(aliases[mechanism])
        settings: object = None
        if isinstance(record, dict):
            injection = record.get("injection")
            if isinstance(injection, dict):
                settings = injection.get("settings")
        counts[mechanism] = len(settings) if isinstance(settings, list) else 0
    return counts


def _future_manifest_matches(path: Path, required_fields: dict[str, object]) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in required_fields.items()
    ):
        return False
    declared_sha = payload.get("manifest_sha256")
    if not isinstance(declared_sha, str):
        return False
    unhashed = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    return declared_sha == canonical_sha256(unhashed)


def audit_claim_support_corpus_protocol(
    protocol: ClaimSupportCorpusProtocol,
    root: Path,
) -> ClaimSupportCorpusFeasibilityReceipt:
    """Recompute source feasibility without materializing claims or outcomes."""

    paths = _verify_bound_artifacts(protocol, root)
    _verify_scientific_boundaries(protocol, paths)
    counts = _declared_family_counts(paths["fault_family_inventory"])
    blockers: list[str] = []
    if counts["preprocessing_mismatch"] < 5:
        blockers.append("preprocessing_family_manifest_pending")
    if counts["label_noise"] < 5:
        blockers.append("label_noise_family_manifest_pending")
    if sum(min(counts[item], 5) for item in MECHANISMS) < 15:
        blockers.append("insufficient_development_family_census")
    if sum(counts.values()) < 21:
        blockers.append("reserve_family_census_pending")
    schema_path = root / protocol.atomic_claim_policy.required_schema_manifest_path
    if not _future_manifest_matches(
        schema_path,
        {
            "schema_version": "diagnosis-output-schema-manifest/v1",
            "schema_ref": "diagnosis-output/2",
            "frozen_before_provider_calls": True,
            "provider_calls_executed": False,
        },
    ):
        blockers.append("diagnosis_output_v2_schema_pending")
    instrument_path = root / protocol.automatic_label_policy.required_implementation_manifest_path
    if not _future_manifest_matches(
        instrument_path,
        {
            "schema_version": "claim-support-automatic-instrument-manifest/v1",
            "corpus_protocol_sha256": protocol.protocol_sha256,
            "frozen_before_claim_materialization": True,
            "claim_pool_materialized": False,
            "automatic_labels_generated": False,
        },
    ):
        blockers.append("automatic_instrument_manifest_pending")
    blockers = sorted(set(blockers))
    declared_total = sum(counts.values())
    current_maximum = min(
        declared_total * protocol.family_census.maximum_claims_per_family_per_label,
        protocol.family_census.target_claims_per_automatic_label,
    )
    payload = {
        "schema_version": "claim-support-corpus-feasibility-receipt/v1",
        "protocol_sha256": protocol.protocol_sha256,
        "mechanism_census": tuple(
            {
                "mechanism": plan.mechanism,
                "declared_primary_families": counts[plan.mechanism],
                "required_primary_families": plan.primary_family_count,
                "required_reserve_families": plan.reserve_family_count,
            }
            for plan in protocol.family_census.mechanism_plans
        ),
        "current_declared_family_total": declared_total,
        "required_primary_family_total": protocol.family_census.primary_family_total,
        "required_reserve_family_total": protocol.family_census.reserve_family_total,
        "current_maximum_selectable_claims_per_label": current_maximum,
        "target_claims_per_label": protocol.family_census.target_claims_per_automatic_label,
        "blocker_codes": tuple(blockers),
        "materialization_ready": not blockers,
        "development_claim_pool_materialized": False,
        "validation_sample_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
        "status": (
            "corpus_protocol_frozen_materialization_ready"
            if not blockers
            else "corpus_protocol_frozen_source_expansion_required"
        ),
    }
    return ClaimSupportCorpusFeasibilityReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_sha256(payload)}
    )


def verify_tracked_claim_support_corpus_protocol(
    root: Path,
    protocol_path: Path,
    receipt_path: Path,
) -> ClaimSupportCorpusFeasibilityReceipt:
    """Verify the protocol and exact tracked receipt against repository state."""

    protocol = load_claim_support_corpus_protocol(protocol_path)
    tracked = load_claim_support_corpus_feasibility_receipt(receipt_path)
    recomputed = audit_claim_support_corpus_protocol(protocol, root)
    if tracked != recomputed:
        _fail("tracked corpus feasibility receipt does not match repository state")
    return recomputed
