"""Validated contracts for the diagnosis main-study census builder."""

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ALL_CONDITIONS,
    CONTROLLED_VARIANTS,
    DiagnosisMainAnalysisCensus,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

SOURCE_CONTRACT_SCHEMA_VERSION: Final = "diagnosis-main-census-source-contract/v1"
PRIVATE_PACKET_SCHEMA_VERSION: Final = "diagnosis-main-private-census-packet/v1"
PUBLIC_SEAL_SCHEMA_VERSION: Final = "diagnosis-main-census-seal/v1"
QWEN_CENSUS_SCHEMA_VERSION: Final = "diagnosis-qwen-sensitivity-census/v1"

_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_SOURCE_IDS: Final = (
    "p2r_confirmatory_measurements",
    "p2_v3_3_label_noise_primary",
    "p2_v3_3_label_noise_replication",
    "p2_v3_3_label_noise_protocol",
)
_CORE_QWEN_CONDITIONS: Final = frozenset(("full", "missing_key", "noisy"))
_QWEN_VARIANTS: Final = frozenset(("B1", "A3"))


class DiagnosisMainCensusError(ValueError):
    """Raised when source material cannot produce the exact frozen census."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class CensusSourceArtifact(_StrictFrozenModel):
    source_id: str
    locator_role: str
    file_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_source_unit_count: int = Field(ge=0)
    expected_dataset_count: int = Field(ge=1)
    expected_mechanisms: tuple[str, ...] = Field(min_length=1)


class CensusSourceContract(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-census-source-contract/v1"]
    status: Literal["outcome_blind_complete_prior_source_census"]
    created_on: Literal["2026-09-20"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    selection_policy: Literal[
        "include_every_registered_source_unit_from_the_bound_artifacts_without_quality_or_effect_filtering"
    ]
    source_artifacts: tuple[CensusSourceArtifact, ...]
    family_derivation: dict[str, object]
    context_derivation: dict[str, object]
    request_derivation: dict[str, object]
    qwen_subset_derivation: dict[str, object]
    source_contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"source_contract_sha256"})

    @model_validator(mode="after")
    def _contract_reconciles(self) -> Self:
        source_ids = tuple(item.source_id for item in self.source_artifacts)
        if source_ids != _SOURCE_IDS:
            raise ValueError("census source artifacts changed or were reordered")
        if self.source_contract_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("census source contract identity does not match")
        family = self.family_derivation
        context = self.context_derivation
        request = self.request_derivation
        qwen = self.qwen_subset_derivation
        if (
            family.get("family_count") != 32
            or family.get("post_source_unit_exclusion_permitted") is not False
        ):
            raise ValueError("source contract no longer fixes the complete 32-family census")
        if context.get("context_count") != 128:
            raise ValueError("source contract no longer fixes 128 contexts")
        if (
            request.get("controlled_request_count") != 1024
            or request.get("b3_external_transfer_included") is not False
        ):
            raise ValueError("source contract no longer fixes the controlled request matrix")
        if qwen.get("family_count") != 12 or qwen.get("request_count") != 72:
            raise ValueError("source contract no longer fixes the Qwen sensitivity census")
        return self


class SourceArtifactReceipt(_StrictFrozenModel):
    source_id: str
    file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_unit_count: int = Field(ge=0)


class DiagnosisMainPrivateCensusPacket(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-private-census-packet/v1"]
    status: Literal["outcome_blind_census_locked_execution_not_authorized"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    source_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_receipts: tuple[SourceArtifactReceipt, ...]
    analysis_census: DiagnosisMainAnalysisCensus
    visible_contexts: tuple[ModelVisibleEvidenceContext, ...]
    qwen_family_ids: tuple[str, ...]
    qwen_request_ids: tuple[str, ...]
    packet_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"packet_sha256"})

    @model_validator(mode="after")
    def _packet_reconciles(self) -> Self:
        visible_ids = tuple(item.context_id for item in self.visible_contexts)
        if visible_ids != tuple(sorted(visible_ids)) or len(visible_ids) != len(set(visible_ids)):
            raise ValueError("visible contexts must be unique and canonically sorted")
        contexts = {item.context_id: item for item in self.analysis_census.contexts}
        if set(visible_ids) != set(contexts):
            raise ValueError("private visible contexts differ from the analysis census")
        for visible in self.visible_contexts:
            if contexts[visible.context_id].visible_context_sha256 != visible.context_sha256:
                raise ValueError("visible context content differs from its census binding")
        family_ids = {item.family_id for item in self.analysis_census.families}
        if (
            len(self.qwen_family_ids) != 12
            or len(set(self.qwen_family_ids)) != 12
            or not set(self.qwen_family_ids) <= family_ids
        ):
            raise ValueError("Qwen family subset is not an exact 12-family subset")
        expected_qwen = {
            request.request_id
            for request in self.analysis_census.requests
            if contexts[request.context_id].case_family_id in self.qwen_family_ids
            and contexts[request.context_id].evidence_condition in _CORE_QWEN_CONDITIONS
            and request.variant in _QWEN_VARIANTS
        }
        if tuple(sorted(expected_qwen)) != self.qwen_request_ids:
            raise ValueError("Qwen request IDs do not match the frozen 12x3x2 design")
        if self.packet_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("private census packet identity does not match")
        return self


class DiagnosisMainCensusSeal(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-census-seal/v1"]
    status: Literal["outcome_blind_census_locked_execution_not_authorized"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    source_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_receipts: tuple[SourceArtifactReceipt, ...]
    family_count: Literal[32]
    context_count: Literal[128]
    controlled_request_count: Literal[1024]
    dataset_count: Literal[2]
    superfamily_count: Literal[6]
    mechanism_counts: dict[str, int]
    contexts_per_condition: dict[str, int]
    requests_per_variant: dict[str, int]
    exact_duplicate_context_count: Literal[0]
    normalized_duplicate_context_count: Literal[0]
    cross_family_semantic_duplicate_count: Literal[0]
    analysis_census_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_packet_canonical_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_packet_byte_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_packet_locator_role: Literal["project_private_memory_artifact"]
    qwen_family_count: Literal[12]
    qwen_request_count: Literal[72]
    qwen_census_sha256: str = Field(pattern=_SHA256_PATTERN)
    b3_controlled_matrix_included: Literal[False]
    b3_reporting_class: Literal["separate_logdx_native_external_transfer"]
    seal_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"seal_sha256"})

    @model_validator(mode="after")
    def _seal_reconciles(self) -> Self:
        if self.mechanism_counts != {
            "data_drift": 10,
            "label_noise": 12,
            "preprocessing_mismatch": 10,
        }:
            raise ValueError("public seal mechanism counts changed")
        if self.contexts_per_condition != {condition: 32 for condition in ALL_CONDITIONS}:
            raise ValueError("public seal context allocation changed")
        if self.requests_per_variant != {variant: 128 for variant in CONTROLLED_VARIANTS}:
            raise ValueError("public seal request allocation changed")
        if self.seal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("public census seal identity does not match")
        return self


class QwenSensitivityCensus(_StrictFrozenModel):
    schema_version: Literal["diagnosis-qwen-sensitivity-census/v1"]
    status: Literal["request_census_locked_execution_not_authorized"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    analysis_census_sha256: str = Field(pattern=_SHA256_PATTERN)
    selection_rule: Literal["lowest_family_sha256_within_each_dataset_by_mechanism_superfamily"]
    family_count: Literal[12]
    families_per_superfamily: Literal[2]
    conditions: tuple[Literal["full"], Literal["missing_key"], Literal["noisy"]]
    variants: tuple[Literal["B1"], Literal["A3"]]
    request_count: Literal[72]
    family_ids: tuple[str, ...]
    request_ids: tuple[str, ...]
    replacement_after_outcome_forbidden: Literal[True]
    pooling_with_gpt_main_permitted: Literal[False]
    census_sha256: str = Field(pattern=_SHA256_PATTERN)

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"census_sha256"})

    @model_validator(mode="after")
    def _census_reconciles(self) -> Self:
        if (
            self.family_ids != tuple(sorted(self.family_ids))
            or len(self.family_ids) != self.family_count
            or len(set(self.family_ids)) != self.family_count
        ):
            raise ValueError("Qwen families must be 12 unique sorted IDs")
        if (
            self.request_ids != tuple(sorted(self.request_ids))
            or len(self.request_ids) != self.request_count
            or len(set(self.request_ids)) != self.request_count
        ):
            raise ValueError("Qwen requests must be 72 unique sorted IDs")
        if self.census_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("Qwen sensitivity census identity does not match")
        return self


__all__ = [
    "CensusSourceArtifact",
    "CensusSourceContract",
    "DiagnosisMainCensusError",
    "DiagnosisMainCensusSeal",
    "DiagnosisMainPrivateCensusPacket",
    "PRIVATE_PACKET_SCHEMA_VERSION",
    "PUBLIC_SEAL_SCHEMA_VERSION",
    "QWEN_CENSUS_SCHEMA_VERSION",
    "QwenSensitivityCensus",
    "SOURCE_CONTRACT_SCHEMA_VERSION",
    "SourceArtifactReceipt",
    "_CORE_QWEN_CONDITIONS",
    "_QWEN_VARIANTS",
]
