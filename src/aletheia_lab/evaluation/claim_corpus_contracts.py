"""Immutable contracts for claim-corpus materialization readiness.

The contracts in this module are outcome-blind.  They describe prospective
families, request identities, normalized atomic outputs, and technical store
artifacts without authorizing a provider call or creating a research corpus.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN, normalize_text

FAMILY_INVENTORY_SCHEMA_VERSION: Final = "claim-corpus-family-inventory/v1"
CORPUS_AMENDMENT_SCHEMA_VERSION: Final = "claim-corpus-protocol-amendment/v1"
REQUEST_CENSUS_SCHEMA_VERSION: Final = "claim-corpus-request-census/v1"
DIAGNOSIS_OUTPUT_SCHEMA_VERSION: Final = "diagnosis-output/2"
CORPUS_ENTRY_SCHEMA_VERSION: Final = "claim-support-corpus-entry/v1"
CORPUS_MANIFEST_SCHEMA_VERSION: Final = "claim-support-corpus-manifest/v1"
CORPUS_RECEIPT_SCHEMA_VERSION: Final = "claim-support-corpus-store-receipt/v1"
READINESS_PLAN_SCHEMA_VERSION: Final = "claim-corpus-readiness-plan/v1"
READINESS_RECEIPT_SCHEMA_VERSION: Final = "claim-corpus-readiness-receipt/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Mechanism = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
FamilyRole = Literal["primary", "reserve"]
EvidenceCondition = Literal["full", "missing_key", "noisy"]
EligibleVariant = Literal["A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL"]
ClaimType = Literal[
    "cause_assertion",
    "evidence_statement",
    "uncertainty_statement",
    "recommended_action",
    "other",
]
SupportLabel = Literal[
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
]
OutputStatus = Literal["completed", "abstained", "parse_failure"]

MECHANISMS: Final[tuple[Mechanism, ...]] = (
    "data_drift",
    "preprocessing_mismatch",
    "label_noise",
)
EVIDENCE_CONDITIONS: Final[tuple[EvidenceCondition, ...]] = (
    "full",
    "missing_key",
    "noisy",
)
ELIGIBLE_VARIANTS: Final[tuple[EligibleVariant, ...]] = (
    "A1",
    "A2",
    "A3",
    "B0",
    "B1",
    "B2",
    "CodeGraph",
    "FULL",
)
SUPPORT_LABELS: Final[tuple[SupportLabel, ...]] = (
    "contradicted",
    "unsupported",
    "partially_supported",
    "fully_supported",
)


class ClaimCorpusContractError(ValueError):
    """Raised when a corpus contract is ambiguous, forged, or unsafe."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _canonical_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("artifact path must be canonical and repository-relative")
    return value


class SourceArtifactBinding(_StrictFrozenModel):
    path: str
    content_sha256: Sha256

    @field_validator("path")
    @classmethod
    def _path_is_canonical(cls, value: str) -> str:
        return _canonical_path(value)


class ClaimCorpusFamily(_StrictFrozenModel):
    family_id: str = Field(pattern=r"^ccf-(data-drift|preprocessing-mismatch|label-noise)-[a-z0-9-]+$")
    mechanism: Mechanism
    role: FamilyRole
    registered_order: int = Field(ge=1, le=7)
    seed: int = Field(ge=0)
    intervention_kind: str
    intervention_parameters: dict[str, object]
    evidence_conditions: tuple[EvidenceCondition, ...]
    invariants: tuple[str, ...] = Field(min_length=1)
    source_artifact: SourceArtifactBinding
    family_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"family_sha256"})

    @field_validator("intervention_kind")
    @classmethod
    def _kind_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="intervention kind", max_length=96)

    @field_validator("invariants")
    @classmethod
    def _invariants_are_bounded(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            normalize_text(value, label="family invariant", max_length=256)
            for value in values
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("family invariants must be unique")
        return normalized

    @model_validator(mode="after")
    def _identity_and_scope_reconcile(self) -> Self:
        slug = self.mechanism.replace("_", "-")
        if not self.family_id.startswith(f"ccf-{slug}-"):
            raise ValueError("family ID mechanism prefix does not match")
        expected_order = self.registered_order <= 5
        if (self.role == "primary") != expected_order:
            raise ValueError("orders 1-5 are primary and 6-7 are reserve")
        if self.evidence_conditions != EVIDENCE_CONDITIONS:
            raise ValueError("family evidence conditions differ from the freeze")
        if self.family_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("family identity does not match canonical fields")
        return self


class ClaimCorpusFamilyInventory(_StrictFrozenModel):
    schema_version: Literal["claim-corpus-family-inventory/v1"] = (
        FAMILY_INVENTORY_SCHEMA_VERSION
    )
    inventory_id: Literal["claim-support-development-families-v1"]
    parent_protocol_sha256: Sha256
    source_partition: Literal["development"] = "development"
    families: tuple[ClaimCorpusFamily, ...] = Field(min_length=21, max_length=21)
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    labels_generated: Literal[False] = False
    inventory_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"inventory_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        expected = tuple(
            (mechanism, role, order)
            for mechanism in MECHANISMS
            for order, role in (
                *((index, "primary") for index in range(1, 6)),
                *((index, "reserve") for index in range(6, 8)),
            )
        )
        actual = tuple(
            (item.mechanism, item.role, item.registered_order) for item in self.families
        )
        if actual != expected:
            raise ValueError("family inventory must contain the ordered 15+6 census")
        identifiers = tuple(item.family_id for item in self.families)
        hashes = tuple(item.family_sha256 for item in self.families)
        seeds = tuple(item.seed for item in self.families)
        if len(set(identifiers)) != 21 or len(set(hashes)) != 21 or len(set(seeds)) != 21:
            raise ValueError("family IDs, identities, and seeds must be globally unique")
        if self.inventory_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("family inventory hash does not match canonical content")
        return self


class ClaimCorpusProtocolAmendment(_StrictFrozenModel):
    schema_version: Literal["claim-corpus-protocol-amendment/v1"] = (
        CORPUS_AMENDMENT_SCHEMA_VERSION
    )
    amendment_id: Literal["claim-support-development-corpus-amendment-v1"]
    parent_protocol_path: str
    parent_protocol_sha256: Sha256
    historical_receipt_path: str
    historical_receipt_sha256: Sha256
    family_inventory_path: str
    family_inventory_sha256: Sha256
    change_scope: Literal["prospective_family_inventory_expansion_only"]
    parent_sampling_policy_unchanged: Literal[True] = True
    parent_variant_policy_unchanged: Literal[True] = True
    parent_label_policy_unchanged: Literal[True] = True
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    labels_generated: Literal[False] = False
    amendment_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"amendment_sha256"})

    @field_validator("parent_protocol_path", "historical_receipt_path", "family_inventory_path")
    @classmethod
    def _paths_are_canonical(cls, value: str) -> str:
        return _canonical_path(value)

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.amendment_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("corpus amendment hash does not match canonical content")
        return self


class ClaimCorpusRequest(_StrictFrozenModel):
    family_id: str
    family_sha256: Sha256
    mechanism: Mechanism
    family_role: FamilyRole
    evidence_condition: EvidenceCondition
    variant: EligibleVariant
    seed: int = Field(ge=0)
    source_partition: Literal["development"] = "development"
    provider_call_authorized: Literal[False] = False
    request_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"request_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.request_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("prospective request identity does not match")
        return self


class ClaimCorpusRequestCensus(_StrictFrozenModel):
    schema_version: Literal["claim-corpus-request-census/v1"] = (
        REQUEST_CENSUS_SCHEMA_VERSION
    )
    family_inventory_sha256: Sha256
    primary_requests: tuple[ClaimCorpusRequest, ...] = Field(min_length=360, max_length=360)
    reserve_requests: tuple[ClaimCorpusRequest, ...] = Field(min_length=144, max_length=144)
    reserve_activation: Literal["pre_execution_technical_ineligibility_only"]
    outcome_driven_activation_forbidden: Literal[True] = True
    provider_calls_executed: Literal[False] = False
    census_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"census_sha256"})

    @model_validator(mode="after")
    def _matrix_and_identity_reconcile(self) -> Self:
        all_requests = (*self.primary_requests, *self.reserve_requests)
        expected = tuple(
            (role, mechanism, order, condition, variant)
            for role, orders in (("primary", range(1, 6)), ("reserve", range(6, 8)))
            for mechanism in MECHANISMS
            for order in orders
            for condition in EVIDENCE_CONDITIONS
            for variant in ELIGIBLE_VARIANTS
        )
        actual = tuple(
            (
                item.family_role,
                item.mechanism,
                int(item.family_id.rsplit("-", 1)[-1]),
                item.evidence_condition,
                item.variant,
            )
            for item in all_requests
        )
        if actual != expected:
            raise ValueError("request census is incomplete, duplicated, or out of order")
        request_hashes = tuple(item.request_sha256 for item in all_requests)
        if len(request_hashes) != len(set(request_hashes)):
            raise ValueError("request identities must be unique")
        if self.census_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("request census hash does not match canonical content")
        return self


class MaterialClaimPart(_StrictFrozenModel):
    part_id: str = Field(pattern=r"^part-[a-z0-9][a-z0-9-]{0,62}$")
    text: str

    @field_validator("text")
    @classmethod
    def _text_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="material claim part", max_length=1024)


class AtomicClaimV2(_StrictFrozenModel):
    claim_local_id: str = Field(pattern=r"^claim-[1-5]$")
    claim_type: ClaimType
    claim_text: str
    material_parts: tuple[MaterialClaimPart, ...] = Field(min_length=1, max_length=8)
    visible_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("claim_text")
    @classmethod
    def _claim_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="atomic claim", max_length=2048)

    @model_validator(mode="after")
    def _claim_is_atomic_and_bound(self) -> Self:
        part_ids = tuple(part.part_id for part in self.material_parts)
        if len(part_ids) != len(set(part_ids)):
            raise ValueError("material claim part IDs must be unique")
        if len(self.visible_evidence_ids) != len(set(self.visible_evidence_ids)):
            raise ValueError("visible evidence IDs must be unique")
        return self


class DiagnosisOutputV2(_StrictFrozenModel):
    schema_version: Literal["diagnosis-output/2"] = DIAGNOSIS_OUTPUT_SCHEMA_VERSION
    output_status: OutputStatus
    atomic_claims: tuple[AtomicClaimV2, ...] = Field(max_length=5)
    abstention_reason: str | None
    parse_failure_code: str | None
    source_record_sha256: Sha256
    output_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"output_sha256"})

    @model_validator(mode="after")
    def _terminal_shape_and_identity_reconcile(self) -> Self:
        if self.output_status == "completed":
            if not self.atomic_claims or self.abstention_reason or self.parse_failure_code:
                raise ValueError("completed output requires claims and no terminal issue")
        elif self.output_status == "abstained":
            if self.atomic_claims or not self.abstention_reason or self.parse_failure_code:
                raise ValueError("abstention must contain only an abstention reason")
        elif self.atomic_claims or self.abstention_reason or not self.parse_failure_code:
            raise ValueError("parse failure must contain only a failure code")
        local_ids = tuple(item.claim_local_id for item in self.atomic_claims)
        if local_ids != tuple(f"claim-{index}" for index in range(1, len(local_ids) + 1)):
            raise ValueError("atomic claims must use contiguous canonical local IDs")
        if self.output_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("diagnosis output hash does not match canonical content")
        return self


class VisibleEvidenceRelation(_StrictFrozenModel):
    evidence_id: str
    text: str
    relation_polarity: Literal["supports", "contradicts", "neutral"]
    relation_scope: Literal["none", "partial", "entire"]

    @field_validator("text")
    @classmethod
    def _evidence_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="visible evidence", max_length=4096)

    @model_validator(mode="after")
    def _relations_are_unambiguous(self) -> Self:
        if self.relation_polarity == "neutral" and self.relation_scope != "none":
            raise ValueError("neutral evidence must have no material claim scope")
        if self.relation_polarity != "neutral" and self.relation_scope == "none":
            raise ValueError("non-neutral evidence must identify material claim scope")
        return self


class ClaimSupportCorpusEntry(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-entry/v1"] = CORPUS_ENTRY_SCHEMA_VERSION
    source_partition: Literal["development"] = "development"
    request_sha256: Sha256
    source_record_sha256: Sha256
    output_sha256: Sha256
    family_id: str
    mechanism: Mechanism
    evidence_condition: EvidenceCondition
    variant: EligibleVariant
    claim_local_id: str
    claim_type: ClaimType
    claim_text: str
    material_parts: tuple[MaterialClaimPart, ...]
    visible_evidence: tuple[VisibleEvidenceRelation, ...]
    automatic_label: SupportLabel
    hidden_ground_truth_present: Literal[False] = False
    human_judgment_present: Literal[False] = False
    main_outcome_present: Literal[False] = False
    entry_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"entry_sha256"})

    @model_validator(mode="after")
    def _entry_identity_reconciles(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.visible_evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("visible evidence records must be unique")
        if self.entry_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("corpus entry hash does not match canonical content")
        return self


class ClaimCorpusObjectPointer(_StrictFrozenModel):
    entry_sha256: Sha256
    object_sha256: Sha256


class ClaimCorpusManifest(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-manifest/v1"] = (
        CORPUS_MANIFEST_SCHEMA_VERSION
    )
    protocol_sha256: Sha256
    census_sha256: Sha256
    entries: tuple[ClaimCorpusObjectPointer, ...]
    object_sha256s: tuple[Sha256, ...]
    provider_calls_recorded: int = Field(ge=0)
    manifest_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_sha256"})

    @model_validator(mode="after")
    def _manifest_reconciles(self) -> Self:
        if tuple(sorted(self.object_sha256s)) != self.object_sha256s or len(
            self.object_sha256s
        ) != len(set(self.object_sha256s)):
            raise ValueError("manifest object hashes must be sorted and unique")
        if tuple(item.object_sha256 for item in self.entries) != self.object_sha256s:
            raise ValueError("entry pointers must exactly match the object inventory")
        if self.manifest_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("corpus manifest hash does not match")
        return self


class ClaimCorpusStoreReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-corpus-store-receipt/v1"] = (
        CORPUS_RECEIPT_SCHEMA_VERSION
    )
    run_id: str = Field(pattern=r"^ccrun-[0-9a-f]{64}$")
    manifest_sha256: Sha256
    entry_count: int = Field(ge=0)
    terminal: Literal[True] = True
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.run_id != f"ccrun-{self.manifest_sha256}":
            raise ValueError("run ID must be derived from the manifest")
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("store receipt hash does not match")
        return self


class ClaimCorpusReadinessPlan(_StrictFrozenModel):
    schema_version: Literal["claim-corpus-readiness-plan/v1"] = READINESS_PLAN_SCHEMA_VERSION
    parent_protocol_sha256: Sha256
    amendment_path: str
    amendment_sha256: Sha256
    family_inventory_path: str
    family_inventory_sha256: Sha256
    request_census_path: str
    request_census_sha256: Sha256
    diagnosis_schema_path: str
    diagnosis_schema_sha256: Sha256
    adapter_manifest_path: str
    adapter_manifest_sha256: Sha256
    instrument_manifest_path: str
    instrument_manifest_sha256: Sha256
    implementation_manifest_path: str
    implementation_manifest_sha256: Sha256
    primary_request_count: Literal[360] = 360
    reserve_request_count: Literal[144] = 144
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    plan_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @field_validator(
        "family_inventory_path",
        "amendment_path",
        "request_census_path",
        "diagnosis_schema_path",
        "adapter_manifest_path",
        "instrument_manifest_path",
        "implementation_manifest_path",
    )
    @classmethod
    def _paths_are_canonical(cls, value: str) -> str:
        return _canonical_path(value)

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.plan_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("readiness plan hash does not match")
        return self


class ClaimCorpusReadinessReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-corpus-readiness-receipt/v1"] = (
        READINESS_RECEIPT_SCHEMA_VERSION
    )
    plan_sha256: Sha256
    materialization_ready: Literal[True] = True
    primary_family_count: Literal[15] = 15
    reserve_family_count: Literal[6] = 6
    primary_request_count: Literal[360] = 360
    reserve_request_count: Literal[144] = 144
    adapter_count: Literal[8] = 8
    semantic_fixture_count: int = Field(ge=4)
    provider_calls_executed: Literal[False] = False
    outputs_generated: Literal[False] = False
    development_claim_pool_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    status: Literal["claim_corpus_materialization_ready_zero_outcome"] = (
        "claim_corpus_materialization_ready_zero_outcome"
    )
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("readiness receipt hash does not match")
        return self


__all__ = [
    "CORPUS_ENTRY_SCHEMA_VERSION",
    "DIAGNOSIS_OUTPUT_SCHEMA_VERSION",
    "ELIGIBLE_VARIANTS",
    "EVIDENCE_CONDITIONS",
    "FAMILY_INVENTORY_SCHEMA_VERSION",
    "MECHANISMS",
    "REQUEST_CENSUS_SCHEMA_VERSION",
    "SUPPORT_LABELS",
    "AtomicClaimV2",
    "ClaimCorpusContractError",
    "ClaimCorpusFamily",
    "ClaimCorpusFamilyInventory",
    "ClaimCorpusProtocolAmendment",
    "ClaimCorpusManifest",
    "ClaimCorpusReadinessPlan",
    "ClaimCorpusReadinessReceipt",
    "ClaimCorpusRequest",
    "ClaimCorpusRequestCensus",
    "ClaimCorpusStoreReceipt",
    "ClaimSupportCorpusEntry",
    "DiagnosisOutputV2",
    "MaterialClaimPart",
    "SourceArtifactBinding",
    "VisibleEvidenceRelation",
]
