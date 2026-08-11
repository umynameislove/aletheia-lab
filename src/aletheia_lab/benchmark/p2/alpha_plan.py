"""Authoritative construction of the frozen Phase 2 alpha candidate plan."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.contracts import CandidatePlan, CandidateSlot
from aletheia_lab.benchmark.p2.identity import (
    P2_IDENTITY_SCHEMA_VERSION,
    SHA256_PATTERN,
    DataDriftParameters,
    FamilyIdentity,
    LabelNoiseParameters,
    PreprocessingBugParameters,
)
from aletheia_lab.benchmark.p2.validation import (
    FROZEN_ALPHA_SLOTS,
    FROZEN_DATA_DRIFT_TARGETS,
    FROZEN_LABEL_NOISE_RATES,
    FROZEN_PREPROCESSING_RANKS,
    PRIMARY_SLOT_COUNT,
    RESERVE_SLOT_COUNT,
    validate_frozen_alpha_plan,
)

ALPHA_PLAN_SCHEMA_VERSION: Literal["p2-candidate-plan/1"] = "p2-candidate-plan/1"
ALPHA_DATA_DRIFT_FEATURE: Literal["Contract"] = "Contract"
ALPHA_PREPROCESSING_TRANSFORM: Literal["one_hot_encoder"] = "one_hot_encoder"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class AlphaSystemBinding(BaseModel):
    """Frozen dataset, split and system inputs shared by every alpha slot.

    The empirical training distribution is an input because the benign drift
    control must target the actual frozen training distribution.  It is never
    inferred from an outcome or replaced with a convenient synthetic value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dataset_snapshot_id: str = Field(min_length=1, max_length=128)
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256
    model_specification_sha256: Sha256
    preprocessing_specification_sha256: Sha256
    reference_construction_id: str = Field(min_length=1, max_length=128)
    data_drift_injector_contract_version: str = Field(min_length=1, max_length=64)
    label_noise_injector_contract_version: str = Field(min_length=1, max_length=64)
    preprocessing_injector_contract_version: str = Field(min_length=1, max_length=64)
    empirical_contract_distribution: dict[str, float]
    data_drift_output_size: int = Field(gt=0)

    @field_validator("empirical_contract_distribution")
    @classmethod
    def _distribution_is_canonical(cls, value: dict[str, float]) -> dict[str, float]:
        expected_categories = {"Month-to-month", "One year", "Two year"}
        if set(value) != expected_categories:
            raise ValueError(
                "empirical_contract_distribution must contain the three frozen Contract labels"
            )
        if any(not math.isfinite(weight) or weight < 0.0 for weight in value.values()):
            raise ValueError("empirical Contract weights must be finite and non-negative")
        if not math.isclose(math.fsum(value.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("empirical Contract distribution must sum to one")
        return value

    @model_validator(mode="after")
    def _text_is_canonical(self) -> AlphaSystemBinding:
        for name in (
            "dataset_snapshot_id",
            "reference_construction_id",
            "data_drift_injector_contract_version",
            "label_noise_injector_contract_version",
            "preprocessing_injector_contract_version",
        ):
            value = getattr(self, name)
            if value != value.strip():
                raise ValueError(f"{name} must not contain surrounding whitespace")
        return self


def _parameters_for(slot_id: str, binding: AlphaSystemBinding) -> object:
    if slot_id.startswith("M1-"):
        distribution = (
            binding.empirical_contract_distribution
            if slot_id == "M1-B1"
            else dict(FROZEN_DATA_DRIFT_TARGETS[slot_id])
        )
        return DataDriftParameters(
            feature=ALPHA_DATA_DRIFT_FEATURE,
            target_distribution=distribution,
            output_size=binding.data_drift_output_size,
        )
    if slot_id.startswith("M2-"):
        return LabelNoiseParameters(
            flip_rate=FROZEN_LABEL_NOISE_RATES[slot_id],
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )
    source_rank, mapped_rank = FROZEN_PREPROCESSING_RANKS[slot_id]
    return PreprocessingBugParameters(
        target_feature=ALPHA_DATA_DRIFT_FEATURE,
        source_rank=source_rank,
        mapped_rank=mapped_rank,
        mode="inference_only",
        transform_name=ALPHA_PREPROCESSING_TRANSFORM,
    )


def _intervention_type(slot_id: str) -> str:
    if slot_id == "M1-B1":
        return "empirical_distribution_resampling_control"
    if slot_id.startswith("M1-"):
        return "categorical_distribution_shift"
    if slot_id == "M2-I1":
        return "training_target_label_repair"
    if slot_id == "M2-B1":
        return "target_label_serialization_roundtrip"
    if slot_id.startswith("M2-"):
        return "training_target_label_corruption"
    if slot_id == "M3-I1":
        return "inference_encoder_mapping_repair"
    if slot_id == "M3-B1":
        return "name_bound_column_order_permutation"
    return "inference_encoder_mapping_mismatch"


def _injector_version(fault_type: str, binding: AlphaSystemBinding) -> str:
    return {
        "data_drift": binding.data_drift_injector_contract_version,
        "label_noise": binding.label_noise_injector_contract_version,
        "preprocessing_bug": binding.preprocessing_injector_contract_version,
    }[fault_type]


def build_frozen_alpha_plan(binding: AlphaSystemBinding) -> CandidatePlan:
    """Materialise all 15 primary and 9 reserve slots from frozen inputs."""

    binding = AlphaSystemBinding.model_validate(binding.model_dump())
    slots: list[CandidateSlot] = []
    for slot_id, spec in FROZEN_ALPHA_SLOTS.items():
        identity = FamilyIdentity(
            dataset_snapshot_id=binding.dataset_snapshot_id,
            dataset_sha256=binding.dataset_sha256,
            model_data_split_manifest_sha256=binding.model_data_split_manifest_sha256,
            fault_type=spec.fault_type,  # type: ignore[arg-type]
            intervention_type=_intervention_type(slot_id),
            canonical_intervention_parameters=_parameters_for(slot_id, binding),  # type: ignore[arg-type]
            seed=spec.seed,
            reference_construction_id=binding.reference_construction_id,
            injector_contract_version=_injector_version(spec.fault_type, binding),
            model_specification_sha256=binding.model_specification_sha256,
            preprocessing_specification_sha256=binding.preprocessing_specification_sha256,
            identity_schema_version=P2_IDENTITY_SCHEMA_VERSION,
        )
        slots.append(
            CandidateSlot(
                slot_id=slot_id,
                fault_type=spec.fault_type,  # type: ignore[arg-type]
                slot_kind=spec.slot_kind,  # type: ignore[arg-type]
                role=spec.role,  # type: ignore[arg-type]
                reserve_order=spec.reserve_order,
                identity=identity,
            )
        )
    plan = CandidatePlan(
        schema_version=ALPHA_PLAN_SCHEMA_VERSION,
        primary_planned=PRIMARY_SLOT_COUNT,
        reserve_planned=RESERVE_SLOT_COUNT,
        slots=tuple(slots),
    )
    validate_frozen_alpha_plan(plan)
    return plan
