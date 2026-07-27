"""Phase 2 candidate and family identity.

A family identity answers one question: which research-defining inputs produced
this experimental unit? It must be stable across the evidence conditions,
prompt variants and repeated attempts that belong to the same family, and it
must change whenever any input that defines the experiment changes.

Identity therefore excludes everything downstream of the experiment: the
measured outcome, the eligibility policy and its verdict, the evidence
condition, the diagnosis provider, split membership, timestamps and reviewer
decisions. Those are recorded elsewhere and may change without creating a new
family or inflating the independent sample size.

Phase 1 keeps its own ``p1-family-`` namespace and its own identity function.
Nothing in this module reads or rewrites Phase 1 identities.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256, normalize_text

P2_IDENTITY_SCHEMA_VERSION: Final[Literal["p2-family-identity/v1"]] = "p2-family-identity/v1"
P2_CANDIDATE_SCHEMA_VERSION: Final[Literal["p2-candidate/v1"]] = "p2-candidate/v1"
P2_FAMILY_CENSUS_SCHEMA_VERSION: Final[Literal["p2-family-census/1"]] = "p2-family-census/1"
P2_SEED_NAMESPACE: Final[Literal["p2-alpha-seed/v1"]] = "p2-alpha-seed/v1"

P2_FAMILY_PREFIX: Final[str] = "p2-family-"
P2_CANDIDATE_PREFIX: Final[str] = "p2-candidate-"

SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
FAMILY_ID_PATTERN: Final[str] = r"^p2-family-[0-9a-f]{64}$"
CANDIDATE_ID_PATTERN: Final[str] = r"^p2-candidate-[0-9a-f]{64}$"
SLOT_ID_PATTERN: Final[str] = r"^M[123]-(?:F|S|I|B|R)[0-9]{1,2}$"

_P1_NAMESPACE = re.compile(r"^p1-", flags=re.IGNORECASE)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

FaultTypeName = Literal["data_drift", "label_noise", "preprocessing_bug"]

FlipDirection = Literal["symmetric", "yes_to_no", "no_to_yes"]
LabelNoiseScope = Literal["train"]
SelectionPolicy = Literal["seeded_record_hash"]
PreprocessingMode = Literal["inference_only", "both"]


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DataDriftParameters(_StrictFrozenModel):
    """Canonical intervention parameters for a categorical distribution shift."""

    intervention_kind: Literal["data_drift"] = "data_drift"
    feature: str = Field(min_length=1, max_length=128)
    target_distribution: dict[str, float]
    output_size: int = Field(gt=0)

    @field_validator("target_distribution")
    @classmethod
    def _positive_finite_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("target_distribution must not be empty")
        normalized_categories: set[str] = set()
        for category, weight in value.items():
            if not category.strip():
                raise ValueError("target_distribution categories must not be blank")
            if category != category.strip() or category != normalize_text(category):
                raise ValueError(
                    "target_distribution categories must be trimmed Unicode NFC strings"
                )
            normalized = normalize_text(category)
            if normalized in normalized_categories:
                raise ValueError("target_distribution categories collide after Unicode NFC")
            normalized_categories.add(normalized)
            if not math.isfinite(weight):
                raise ValueError(f"target_distribution weight is not finite: {category!r}")
            if weight == 0.0 and math.copysign(1.0, weight) < 0:
                raise ValueError(f"target_distribution weight is negative zero: {category!r}")
            if weight < 0:
                raise ValueError(f"target_distribution weight is negative: {category!r}")
        total = math.fsum(value.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError("target_distribution must have a positive total")
        return value

    @field_validator("feature")
    @classmethod
    def _canonical_feature(cls, value: str) -> str:
        if value != value.strip() or value != normalize_text(value):
            raise ValueError("feature must be a trimmed Unicode NFC string")
        return value


class LabelNoiseParameters(_StrictFrozenModel):
    """Canonical intervention parameters for training-label corruption."""

    intervention_kind: Literal["label_noise"] = "label_noise"
    # Zero is reserved for the predeclared semantics-preserving serialization
    # control. Frozen-plan validation forbids it for every fault-directed slot.
    flip_rate: float = Field(ge=0.0, le=0.5, allow_inf_nan=False)
    flip_direction: FlipDirection
    selection_policy: SelectionPolicy
    scope: LabelNoiseScope


class PreprocessingBugParameters(_StrictFrozenModel):
    """Canonical intervention parameters for an inference transform regression.

    Category ranks rather than category names are recorded, so the identity does
    not depend on a dataset-specific label and cannot be chosen after seeing an
    outcome.
    """

    intervention_kind: Literal["preprocessing_bug"] = "preprocessing_bug"
    target_feature: str = Field(min_length=1, max_length=128)
    source_rank: int | None = Field(default=None, gt=0)
    mapped_rank: int | None = Field(default=None, gt=0)
    mode: PreprocessingMode
    transform_name: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _ranks_differ(self) -> PreprocessingBugParameters:
        if (self.source_rank is None) != (self.mapped_rank is None):
            raise ValueError("source_rank and mapped_rank must both be present or both be absent")
        if self.source_rank is not None and self.source_rank == self.mapped_rank:
            raise ValueError("source_rank and mapped_rank must differ")
        return self

    @field_validator("target_feature", "transform_name")
    @classmethod
    def _canonical_identity_text(cls, value: str) -> str:
        if value != value.strip() or value != normalize_text(value):
            raise ValueError("preprocessing identity strings must be trimmed Unicode NFC")
        return value


InterventionParameters = Annotated[
    DataDriftParameters | LabelNoiseParameters | PreprocessingBugParameters,
    Field(discriminator="intervention_kind"),
]

_FAULT_TO_PARAMETER_KIND: Final[dict[str, str]] = {
    "data_drift": "data_drift",
    "label_noise": "label_noise",
    "preprocessing_bug": "preprocessing_bug",
}


class FamilyIdentity(_StrictFrozenModel):
    """The twelve research-defining inputs that determine a Phase 2 family.

    Field order in this class is documentation only; the canonical payload is
    key-sorted, so reordering the declarations cannot change an identity hash.
    """

    # Data source (3)
    dataset_snapshot_id: str = Field(min_length=1, max_length=128)
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256

    # Intervention and reference (6)
    fault_type: FaultTypeName
    intervention_type: str = Field(min_length=1, max_length=128)
    canonical_intervention_parameters: InterventionParameters
    seed: int = Field(ge=0)
    reference_construction_id: str = Field(min_length=1, max_length=128)
    injector_contract_version: str = Field(min_length=1, max_length=64)

    # System and identity (3)
    model_specification_sha256: Sha256
    preprocessing_specification_sha256: Sha256
    identity_schema_version: Literal["p2-family-identity/v1"]

    @field_validator(
        "dataset_snapshot_id",
        "intervention_type",
        "reference_construction_id",
        "injector_contract_version",
    )
    @classmethod
    def _no_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("identity strings must not have leading or trailing whitespace")
        if value != normalize_text(value):
            raise ValueError("identity strings must already be Unicode NFC")
        return value

    @field_validator("dataset_snapshot_id")
    @classmethod
    def _not_phase_one_namespace(cls, value: str) -> str:
        if _P1_NAMESPACE.match(value):
            raise ValueError("Phase 1 identifiers must not seed a Phase 2 identity")
        return value

    @model_validator(mode="after")
    def _parameters_match_fault_type(self) -> FamilyIdentity:
        """A mechanism must carry its own parameter model, not another one's.

        ``model_post_init`` is deliberately not used here: exceptions raised
        there escape as plain errors instead of validation errors, which would
        make this constraint invisible to callers that catch ``ValidationError``.
        """

        parameters = self.canonical_intervention_parameters
        expected = _FAULT_TO_PARAMETER_KIND[self.fault_type]
        if parameters.intervention_kind != expected:
            raise ValueError(
                f"fault_type {self.fault_type!r} requires {expected!r} intervention parameters, "
                f"got {parameters.intervention_kind!r}"
            )
        return self

    def identity_payload(self) -> dict[str, object]:
        """Return the exact twelve-field payload that is hashed.

        ``model_dump`` is not used directly so that adding an unrelated helper
        field to this class can never silently widen the identity.
        """

        return {
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "dataset_sha256": self.dataset_sha256,
            "model_data_split_manifest_sha256": self.model_data_split_manifest_sha256,
            "fault_type": self.fault_type,
            "intervention_type": self.intervention_type,
            "canonical_intervention_parameters": self.canonical_intervention_parameters.model_dump(
                mode="json"
            ),
            "seed": self.seed,
            "reference_construction_id": self.reference_construction_id,
            "injector_contract_version": self.injector_contract_version,
            "model_specification_sha256": self.model_specification_sha256,
            "preprocessing_specification_sha256": self.preprocessing_specification_sha256,
            "identity_schema_version": self.identity_schema_version,
        }


IDENTITY_FIELD_NAMES: Final[tuple[str, ...]] = (
    "dataset_snapshot_id",
    "dataset_sha256",
    "model_data_split_manifest_sha256",
    "fault_type",
    "intervention_type",
    "canonical_intervention_parameters",
    "seed",
    "reference_construction_id",
    "injector_contract_version",
    "model_specification_sha256",
    "preprocessing_specification_sha256",
    "identity_schema_version",
)


def proposed_family_sha256(identity: FamilyIdentity) -> str:
    """Return the family fingerprint for ``identity``.

    The fingerprint is computable before the candidate runs, because identity
    does not depend on any measured outcome. That is what allows duplicate
    detection to happen before compute is spent.
    """

    payload = identity.identity_payload()
    if set(payload) != set(IDENTITY_FIELD_NAMES):
        raise ValueError("identity payload must contain exactly the twelve identity fields")
    return canonical_sha256(payload)


def family_id_for(identity: FamilyIdentity) -> str:
    """Return the namespaced Phase 2 family identifier."""

    return f"{P2_FAMILY_PREFIX}{proposed_family_sha256(identity)}"


def candidate_id_for(*, slot_id: str, family_fingerprint: str) -> str:
    """Return the namespaced candidate identifier for one slot execution.

    A candidate is a slot executed against one fingerprint. Two slots that
    resolve to the same fingerprint remain two candidates, which is what makes
    an exact identity duplicate detectable instead of invisible.
    """

    if not re.fullmatch(SLOT_ID_PATTERN, slot_id):
        raise ValueError(f"slot_id does not match the Phase 2 slot pattern: {slot_id!r}")
    if not re.fullmatch(SHA256_PATTERN, family_fingerprint):
        raise ValueError("family_fingerprint must be a lowercase SHA-256 digest")
    payload = {
        "candidate_schema_version": P2_CANDIDATE_SCHEMA_VERSION,
        "slot_id": slot_id,
        "proposed_family_sha256": family_fingerprint,
    }
    return f"{P2_CANDIDATE_PREFIX}{canonical_sha256(payload)}"
