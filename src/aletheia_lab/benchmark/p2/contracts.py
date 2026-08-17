"""Record schemas for the Phase 2 candidate lifecycle.

A candidate travels through four distinct decisions, and each one is recorded
separately so that no single field ever answers two questions at once:

1. **Technical disposition** — did the run satisfy the mechanical contract?
2. **Classification** — what did the measurement actually show?
3. **Admission** — does the benchmark accept this unit?
4. **Census** — which families and contexts exist as a result?

Collapsing these produces the failure mode this contract exists to prevent: a
candidate that was rejected for a hash mismatch becomes indistinguishable from
one that was excluded as a duplicate, and the resulting rate no longer tells you
whether the implementation or the grid design is at fault.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import (
    CANDIDATE_ID_PATTERN,
    FAMILY_ID_PATTERN,
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    FamilyIdentity,
    FaultTypeName,
)

CandidateId = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]
FamilyId = Annotated[str, Field(pattern=FAMILY_ID_PATTERN)]
SlotId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

SlotKind = Literal["primary", "reserve"]

CandidateRole = Literal[
    "fault_directed",
    "designed_improvement_control",
    "designed_benign_control",
]

MeasuredOutcome = Literal["regression", "improvement", "stable", "benign"]

AcceptedFamilyClass = Literal[
    "eligible_failure",
    "stable_control",
    "improvement_control",
    "benign_control",
]

EvidenceConditionName = Literal["full", "missing_key", "noisy"]

EligibilityPolicyVersion = Literal[
    "accuracy-regression/v1",
    "label-noise-impact/alpha-v1",
    "preprocessing-bug-impact/alpha-v1",
]

DuplicateMeasure = Literal[
    "identity_equality",
    "effective_fingerprint_equality",
    "total_variation_distance",
    "jaccard_similarity",
]

TechnicalRejectionReason = Literal[
    "invalid_parameter",
    "source_contract_mismatch",
    "one_factor_violation",
    "no_effective_intervention",
    "provenance_hash_mismatch",
    "metric_missing_or_non_finite",
    "ambiguous_or_multi_factor_intervention",
    "benign_equivalence_failure",
]

ValidExclusionReason = Literal[
    "exact_identity_duplicate",
    "effective_intervention_duplicate",
    "condition_construction_failure",
    "evidence_leakage",
    "artifact_binding_failure",
    "control_direction_violation",
    "protocol_amendment_probe",
    "protocol_amendment_superseded",
]

DuplicateKind = Literal["exact_identity", "effective_intervention", "near_duplicate"]

CONTEXT_ID_PATTERN: Final[str] = r"^p2-context-[0-9a-f]{64}$"
ContextId = Annotated[str, Field(pattern=CONTEXT_ID_PATTERN)]

#: Only a fault-directed candidate can ever become an eligible failure. The
#: control roles are declared before execution precisely so that a surprising
#: measurement cannot promote them.
_ROLE_TO_ALLOWED_CLASSES: Final[dict[str, frozenset[str]]] = {
    "fault_directed": frozenset({"eligible_failure", "stable_control", "improvement_control"}),
    "designed_improvement_control": frozenset({"improvement_control", "stable_control"}),
    "designed_benign_control": frozenset({"benign_control"}),
}

_OUTCOME_TO_CLASS: Final[dict[str, str]] = {
    "regression": "eligible_failure",
    "stable": "stable_control",
    "improvement": "improvement_control",
    "benign": "benign_control",
}

#: Evidence contexts permitted for each accepted family class.
CONTEXT_CARDINALITY: Final[dict[str, frozenset[str]]] = {
    "eligible_failure": frozenset({"full", "missing_key", "noisy"}),
    "stable_control": frozenset({"full"}),
    "improvement_control": frozenset({"full"}),
    "benign_control": frozenset(),
}


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return values


def _require_finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _derived_metric_outcome(delta: float, threshold: float) -> str:
    if delta <= -threshold:
        return "regression"
    if delta >= threshold:
        return "improvement"
    return "stable"


def context_id_for(*, case_family_id: str, evidence_condition: str) -> str:
    """Return an opaque context ID bound to one family-condition projection."""

    if re.fullmatch(FAMILY_ID_PATTERN, case_family_id) is None:
        raise ValueError("case_family_id must use the Phase 2 family namespace")
    if evidence_condition not in {"full", "missing_key", "noisy"}:
        raise ValueError("unknown evidence condition")
    digest = canonical_sha256(
        {
            "schema_version": "p2-context-identity/v1",
            "case_family_id": case_family_id,
            "evidence_condition": evidence_condition,
        }
    )
    return f"p2-context-{digest}"


_FORBIDDEN_PROJECTION_KEYS: Final[tuple[str, ...]] = (
    "admission",
    "answer_key",
    "artifact_path",
    "candidate_id",
    "classification",
    "distractor",
    "disposition",
    "evidence_condition",
    "expected_behavior",
    "expected_diagnosis",
    "expected_diagnosis_behavior",
    "expected_sufficiency",
    "evaluator",
    "ground_truth",
    "hidden_label",
    "cause_label",
    "hidden_intervention",
    "intervention_type",
    "flip",
    "mechanism",
    "mapping",
    "mutation",
    "original_label",
    "original_target",
    "provenance",
    "record_id",
    "seed",
    "target_feature",
    "fault_type",
    "intervention_parameters",
    "injection_parameters",
    "eligibility",
    "family_class",
    "candidate_role",
    "sufficiency",
    "scoring_threshold",
)

_FORBIDDEN_PROJECTION_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|_)(?:full|noisy|missing_key|distractor|data_drift|label_noise|"
    r"preprocessing_bug|training_target_label_corruption|"
    r"inference_encoder_mapping_mismatch|expected_sufficient|"
    r"expected_insufficient)(?:_|$)"
)

_FORBIDDEN_PROJECTION_VALUES: Final[frozenset[str]] = frozenset(
    {
        "full",
        "noisy",
        "missing_key",
        "missing-key",
        "distractor",
        "data_drift",
        "label_noise",
        "preprocessing_bug",
        "training_target_label_corruption",
        "inference_encoder_mapping_mismatch",
        "expected sufficient",
        "expected insufficient",
    }
)


def _assert_projection_is_diagnosis_safe(value: object, path: str = "$") -> None:
    """Reject evaluator-only semantics anywhere in a diagnosis projection."""

    if isinstance(value, dict):
        for raw_key, nested in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"diagnosis projection key at {path} must be a string")
            folded = (
                unicodedata.normalize("NFC", raw_key).casefold().replace("-", "_").replace(" ", "_")
            )
            if any(marker in folded for marker in _FORBIDDEN_PROJECTION_KEYS):
                raise ValueError(
                    f"diagnosis projection contains evaluator-only key at {path}.{raw_key}"
                )
            _assert_projection_is_diagnosis_safe(nested, f"{path}.{raw_key}")
        return
    if isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _assert_projection_is_diagnosis_safe(nested, f"{path}[{index}]")
        return
    if isinstance(value, str):
        folded_value = unicodedata.normalize("NFC", value).strip().casefold()
        normalized_value = re.sub(r"[-\s]+", "_", folded_value)
        if (
            normalized_value in _FORBIDDEN_PROJECTION_VALUES
            or _FORBIDDEN_PROJECTION_VALUE_PATTERN.search(normalized_value) is not None
        ):
            raise ValueError(f"diagnosis projection leaks evaluator metadata at {path}")
        if (
            folded_value.startswith(("/", "~/", "file://"))
            or re.match(r"^[a-z]:[\\/]", folded_value) is not None
        ):
            raise ValueError(f"diagnosis projection contains an absolute path at {path}")


# --------------------------------------------------------------------------- #
# 1. Candidate plan
# --------------------------------------------------------------------------- #


class CandidateSlot(_StrictFrozenModel):
    """One prespecified grid slot, fixed before any outcome is observed."""

    slot_id: SlotId
    fault_type: FaultTypeName
    slot_kind: SlotKind
    role: CandidateRole
    reserve_order: int | None = Field(default=None, ge=1)
    identity: FamilyIdentity

    @model_validator(mode="after")
    def _reserve_order_matches_kind(self) -> CandidateSlot:
        if self.slot_kind == "reserve" and self.reserve_order is None:
            raise ValueError("reserve slots must declare a reserve_order")
        if self.slot_kind == "primary" and self.reserve_order is not None:
            raise ValueError("primary slots must not declare a reserve_order")
        if self.identity.fault_type != self.fault_type:
            raise ValueError("slot fault_type must match its identity fault_type")
        return self


class CandidatePlan(_StrictFrozenModel):
    """The frozen grid: every slot, role, parameter and seed chosen up front."""

    schema_version: Literal["p2-candidate-plan/1"]
    primary_planned: int = Field(ge=0)
    reserve_planned: int = Field(ge=0)
    slots: tuple[CandidateSlot, ...]

    @model_validator(mode="after")
    def _plan_is_internally_consistent(self) -> CandidatePlan:
        _require_unique(tuple(slot.slot_id for slot in self.slots), "slot_id")
        primary = tuple(slot for slot in self.slots if slot.slot_kind == "primary")
        reserve = tuple(slot for slot in self.slots if slot.slot_kind == "reserve")
        if len(primary) != self.primary_planned:
            raise ValueError("primary_planned does not match the number of primary slots")
        if len(reserve) != self.reserve_planned:
            raise ValueError("reserve_planned does not match the number of reserve slots")
        for fault_type in {slot.fault_type for slot in reserve}:
            orders = sorted(
                slot.reserve_order
                for slot in reserve
                if slot.fault_type == fault_type and slot.reserve_order is not None
            )
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError(
                    f"reserve_order for {fault_type} must be a gapless sequence starting at 1"
                )
        return self


# --------------------------------------------------------------------------- #
# 2. Candidate execution
# --------------------------------------------------------------------------- #


class ExecutedCandidate(_StrictFrozenModel):
    """One slot actually executed, bound to its fingerprint and source hashes."""

    candidate_id: CandidateId
    slot_id: SlotId
    fault_type: FaultTypeName
    role: CandidateRole
    slot_kind: SlotKind
    proposed_family_sha256: Sha256
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256


class ReserveRecoveryObservation(_StrictFrozenModel):
    """One pre-amendment result proving that intervention ran but stayed stable."""

    slot_id: SlotId
    declared_intervention_rate: float = Field(gt=0.0, le=0.5)
    achieved_intervention_rate: float = Field(gt=0.0, le=0.5)
    primary_metric_delta: float
    threshold: float = Field(gt=0.0)
    measured_outcome: Literal["stable"]

    @field_validator(
        "declared_intervention_rate",
        "achieved_intervention_rate",
        "primary_metric_delta",
        "threshold",
    )
    @classmethod
    def _finite_observation(cls, value: float) -> float:
        return _require_finite(value, "reserve recovery observation")

    @model_validator(mode="after")
    def _observation_matches_the_frozen_policy(self) -> ReserveRecoveryObservation:
        if not math.isclose(self.threshold, 0.01, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("reserve recovery must preserve the frozen 0.01 threshold")
        if not -self.threshold < self.primary_metric_delta < self.threshold:
            raise ValueError("a reserve recovery observation must remain inside the stable band")
        return self


class ReserveRecoveryAuthorization(_StrictFrozenModel):
    """Hash-bound amendment authorizing one outcome-blind reserve promotion.

    All reserve outcomes are measured, but only the predeclared promoted slot may
    enter mechanism coverage. Probe slots are excluded regardless of outcome,
    which prevents optional stopping and post-outcome candidate selection.
    """

    schema_version: Literal["p2-reserve-recovery-authorization/1"]
    protocol_version: Literal["complete-prespecified-reserve-recovery/v1"]
    trigger: Literal["missing_mechanism_coverage"]
    root_cause: Literal["effective_intervention_below_frozen_primary_threshold"]
    fault_type: FaultTypeName
    source_store_sha256: Sha256
    source_candidate_plan_sha256: Sha256
    source_candidate_census_sha256: Sha256
    source_coverage_audit_sha256: Sha256
    source_observations: tuple[ReserveRecoveryObservation, ...]
    activated_reserve_slot_ids: tuple[SlotId, ...]
    probe_slot_ids: tuple[SlotId, ...]
    promoted_reserve_slot_id: SlotId
    superseded_primary_slot_id: SlotId
    primary_metric: Literal["accuracy"]
    threshold: float = Field(gt=0.0)
    preserves_primary_measurements: Literal[True]
    executes_complete_reserve_set: Literal[True]

    @model_validator(mode="after")
    def _authorization_is_outcome_blind_and_complete(self) -> ReserveRecoveryAuthorization:
        _require_unique(self.activated_reserve_slot_ids, "activated reserve slot IDs")
        _require_unique(self.probe_slot_ids, "reserve recovery probe slot IDs")
        observation_ids = tuple(item.slot_id for item in self.source_observations)
        _require_unique(observation_ids, "reserve recovery observation slot IDs")
        if observation_ids != tuple(sorted(observation_ids)):
            raise ValueError("reserve recovery observations must use canonical slot order")
        if self.activated_reserve_slot_ids != tuple(sorted(self.activated_reserve_slot_ids)):
            raise ValueError("activated reserve slots must use canonical order")
        if self.probe_slot_ids != tuple(sorted(self.probe_slot_ids)):
            raise ValueError("reserve recovery probes must use canonical order")
        if self.promoted_reserve_slot_id in self.probe_slot_ids:
            raise ValueError("the promoted reserve cannot also be a sensitivity probe")
        if set(self.probe_slot_ids) | {self.promoted_reserve_slot_id} != set(
            self.activated_reserve_slot_ids
        ):
            raise ValueError("probe and promoted slots must partition the activated reserve set")
        if not math.isclose(self.threshold, 0.01, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("reserve recovery must preserve the frozen 0.01 threshold")
        return self


class CandidateExecution(_StrictFrozenModel):
    """Which slots ran, and which reserve slots deliberately did not."""

    schema_version: Literal["p2-candidate-execution/1"]
    executed: tuple[ExecutedCandidate, ...]
    inactive_reserve_slot_ids: tuple[SlotId, ...]
    reserve_recovery_authorization: ReserveRecoveryAuthorization | None = None

    @model_validator(mode="after")
    def _no_duplicate_entries(self) -> CandidateExecution:
        _require_unique(tuple(item.candidate_id for item in self.executed), "candidate_id")
        _require_unique(tuple(item.slot_id for item in self.executed), "executed slot_id")
        _require_unique(self.inactive_reserve_slot_ids, "inactive_reserve_slot_ids")
        executed_slots = {item.slot_id for item in self.executed}
        overlap = executed_slots & set(self.inactive_reserve_slot_ids)
        if overlap:
            raise ValueError(f"slots cannot be both executed and inactive: {sorted(overlap)}")
        if self.reserve_recovery_authorization is not None:
            authorization = self.reserve_recovery_authorization
            if not set(authorization.activated_reserve_slot_ids) <= executed_slots:
                raise ValueError("authorized reserve slots must all be executed")
        return self


# --------------------------------------------------------------------------- #
# 3. Technical disposition
# --------------------------------------------------------------------------- #


class TechnicalDispositionEntry(_StrictFrozenModel):
    """Did this candidate satisfy the mechanical contract?"""

    candidate_id: CandidateId
    disposition: Literal["technically_valid", "technical_rejected"]
    rejection_reason: TechnicalRejectionReason | None = None
    detail: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _reason_matches_disposition(self) -> TechnicalDispositionEntry:
        if self.disposition == "technical_rejected" and self.rejection_reason is None:
            raise ValueError("a technical rejection must carry a machine-readable reason")
        if self.disposition == "technically_valid" and self.rejection_reason is not None:
            raise ValueError("a technically valid candidate must not carry a rejection reason")
        return self


class TechnicalDisposition(_StrictFrozenModel):
    """The technical verdict for every executed candidate."""

    schema_version: Literal["p2-technical-disposition/1"]
    entries: tuple[TechnicalDispositionEntry, ...]

    @model_validator(mode="after")
    def _one_entry_per_candidate(self) -> TechnicalDisposition:
        _require_unique(tuple(entry.candidate_id for entry in self.entries), "candidate_id")
        return self


# --------------------------------------------------------------------------- #
# 4. Classification record
# --------------------------------------------------------------------------- #


class ClassificationRecord(_StrictFrozenModel):
    """What the measurement showed, and which policy read it.

    The eligibility policy version lives here rather than in the family
    identity: replacing a policy may change how a candidate is classified, but
    it does not create a different experimental unit.
    """

    schema_version: Literal["p2-classification-record/1"]
    candidate_id: CandidateId
    role: CandidateRole
    eligibility_policy_version: EligibilityPolicyVersion
    primary_metric: Literal["accuracy"]
    reference_value: float
    observed_value: float
    delta: float
    threshold: float = Field(gt=0.0)
    measured_outcome: MeasuredOutcome
    family_class: AcceptedFamilyClass | None = None
    equivalence_checks_passed: bool | None = None
    deviation_note: str | None = Field(default=None, max_length=1024)

    @field_validator("reference_value", "observed_value", "delta", "threshold")
    @classmethod
    def _finite_metrics(cls, value: float) -> float:
        return _require_finite(value, "metric value")

    @model_validator(mode="after")
    def _classification_is_self_consistent(self) -> ClassificationRecord:
        if not 0.0 <= self.reference_value <= 1.0:
            raise ValueError("accuracy reference_value must lie in [0, 1]")
        if not 0.0 <= self.observed_value <= 1.0:
            raise ValueError("accuracy observed_value must lie in [0, 1]")
        if not -1.0 <= self.delta <= 1.0:
            raise ValueError("accuracy delta must lie in [-1, 1]")
        if abs(self.delta - (self.observed_value - self.reference_value)) > 1e-12:
            raise ValueError("delta must equal observed_value - reference_value")
        if not math.isclose(self.threshold, 0.01, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the frozen alpha policies require threshold 0.01")

        metric_outcome = _derived_metric_outcome(self.delta, self.threshold)

        if self.role == "designed_benign_control":
            if self.equivalence_checks_passed is not True:
                raise ValueError("a benign control must record passing equivalence checks")
            if metric_outcome != "stable":
                raise ValueError(
                    "a benign control must remain inside the primary-metric stable band"
                )
            if self.measured_outcome != "benign" or self.family_class != "benign_control":
                raise ValueError(
                    "a benign control with passing equivalence must be classified as benign_control"
                )
            return self
        elif self.measured_outcome == "benign":
            raise ValueError("only a declared benign control may report a benign outcome")
        elif self.equivalence_checks_passed is not None:
            raise ValueError("equivalence_checks_passed is reserved for benign controls")

        # A control that moved the wrong way is still measured honestly. It
        # carries no family class, and admission excludes it with the
        # control_direction_violation reason rather than relabelling it.
        direction_violation = (
            self.role == "designed_improvement_control" and metric_outcome == "regression"
        )
        if direction_violation:
            if self.measured_outcome != "regression":
                raise ValueError("measured_outcome must be derived from delta and threshold")
            if self.family_class is not None:
                raise ValueError(
                    "an improvement control with regression outcome is a direction "
                    "violation and must not claim a family class"
                )
            if not self.deviation_note:
                raise ValueError("a control direction violation must include a deviation_note")
            return self

        if self.family_class is None:
            raise ValueError(
                "only a control whose measurement contradicts its declared direction "
                "may omit a family class"
            )

        if self.measured_outcome != metric_outcome:
            raise ValueError(
                "measured_outcome must be derived from delta and threshold; "
                f"expected {metric_outcome!r}"
            )

        if (
            self.role == "designed_improvement_control"
            and metric_outcome == "stable"
            and not self.deviation_note
        ):
            raise ValueError(
                "an improvement control with stable outcome must include a deviation_note"
            )

        expected_class = _OUTCOME_TO_CLASS[self.measured_outcome]
        if self.family_class != expected_class:
            raise ValueError(
                f"measured outcome {self.measured_outcome!r} implies {expected_class!r}, "
                f"got {self.family_class!r}"
            )
        allowed = _ROLE_TO_ALLOWED_CLASSES[self.role]
        if self.family_class not in allowed:
            raise ValueError(
                f"role {self.role!r} cannot produce family class {self.family_class!r}"
            )
        return self


class ClassificationLedger(_StrictFrozenModel):
    """Exactly one classification for each technically valid candidate."""

    schema_version: Literal["p2-classification-ledger/1"]
    entries: tuple[ClassificationRecord, ...]

    @model_validator(mode="after")
    def _one_entry_per_candidate(self) -> ClassificationLedger:
        _require_unique(tuple(entry.candidate_id for entry in self.entries), "candidate_id")
        return self


# --------------------------------------------------------------------------- #
# 5. Admission record
# --------------------------------------------------------------------------- #


class AdmissionRecord(_StrictFrozenModel):
    """Does the benchmark accept this technically valid candidate?"""

    schema_version: Literal["p2-admission-record/1"]
    candidate_id: CandidateId
    admission: Literal["accepted", "excluded_valid"]
    case_family_id: FamilyId | None = None
    family_class: AcceptedFamilyClass | None = None
    exclusion_reason: ValidExclusionReason | None = None
    detail: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _admission_fields_match(self) -> AdmissionRecord:
        if self.admission == "accepted":
            if self.case_family_id is None or self.family_class is None:
                raise ValueError("an accepted candidate must carry a family ID and class")
            if self.exclusion_reason is not None:
                raise ValueError("an accepted candidate must not carry an exclusion reason")
        else:
            if self.exclusion_reason is None:
                raise ValueError("an excluded candidate must carry a machine-readable reason")
            if self.case_family_id is not None or self.family_class is not None:
                raise ValueError("an excluded candidate must not claim family membership")
        return self


class AdmissionLedger(_StrictFrozenModel):
    """Exactly one admission decision for each technically valid candidate."""

    schema_version: Literal["p2-admission-ledger/1"]
    entries: tuple[AdmissionRecord, ...]

    @model_validator(mode="after")
    def _one_entry_per_candidate(self) -> AdmissionLedger:
        _require_unique(tuple(entry.candidate_id for entry in self.entries), "candidate_id")
        return self


# --------------------------------------------------------------------------- #
# 6. Family census
# --------------------------------------------------------------------------- #


class FamilyCensusEntry(_StrictFrozenModel):
    """One accepted family and the candidate that produced it."""

    case_family_id: FamilyId
    candidate_id: CandidateId
    fault_type: FaultTypeName
    family_class: AcceptedFamilyClass
    proposed_family_sha256: Sha256
    origin_slot_kind: SlotKind = "primary"
    reserve_promotion_authorized: bool = False

    @model_validator(mode="after")
    def _reserve_origin_is_explicit(self) -> FamilyCensusEntry:
        if self.origin_slot_kind == "primary" and self.reserve_promotion_authorized:
            raise ValueError("a primary family cannot claim reserve promotion")
        return self

    @model_validator(mode="after")
    def _family_id_matches_fingerprint(self) -> FamilyCensusEntry:
        if self.case_family_id != f"p2-family-{self.proposed_family_sha256}":
            raise ValueError("case_family_id must be the namespaced family fingerprint")
        return self


class FamilyCensus(_StrictFrozenModel):
    """Every accepted family, with no counts the reader has to take on trust."""

    schema_version: Literal["p2-family-census/1"]
    entries: tuple[FamilyCensusEntry, ...]

    @model_validator(mode="after")
    def _families_and_candidates_are_unique(self) -> FamilyCensus:
        _require_unique(tuple(entry.case_family_id for entry in self.entries), "case_family_id")
        _require_unique(tuple(entry.candidate_id for entry in self.entries), "candidate_id")
        return self


# --------------------------------------------------------------------------- #
# 7. Context census
# --------------------------------------------------------------------------- #


class ContextEntry(_StrictFrozenModel):
    """One evidence context projected from an accepted family."""

    diagnosis_context_id: ContextId
    case_family_id: FamilyId
    evidence_condition: EvidenceConditionName
    diagnosis_projection: dict[str, object]
    diagnosis_projection_sha256: Sha256

    @model_validator(mode="after")
    def _projection_is_bound_and_safe(self) -> ContextEntry:
        expected_context_id = context_id_for(
            case_family_id=self.case_family_id,
            evidence_condition=self.evidence_condition,
        )
        if self.diagnosis_context_id != expected_context_id:
            raise ValueError("diagnosis_context_id must bind family and evidence condition")
        _assert_projection_is_diagnosis_safe(self.diagnosis_projection)
        try:
            expected_projection_hash = canonical_sha256(self.diagnosis_projection)
        except (TypeError, ValueError) as exc:
            raise ValueError("diagnosis projection is not canonical JSON") from exc
        if self.diagnosis_projection_sha256 != expected_projection_hash:
            raise ValueError("diagnosis_projection_sha256 does not match projection bytes")
        return self


class ContextCensus(_StrictFrozenModel):
    """Every evidence context, bound to the family it belongs to."""

    schema_version: Literal["p2-context-census/1"]
    entries: tuple[ContextEntry, ...]

    @model_validator(mode="after")
    def _contexts_are_unique(self) -> ContextCensus:
        _require_unique(
            tuple(entry.diagnosis_context_id for entry in self.entries), "diagnosis_context_id"
        )
        pairs = tuple(
            f"{entry.case_family_id}:{entry.evidence_condition}" for entry in self.entries
        )
        _require_unique(pairs, "family and condition pair")
        return self


# --------------------------------------------------------------------------- #
# 8. Duplicate audit
# --------------------------------------------------------------------------- #


class DuplicateFinding(_StrictFrozenModel):
    """One duplicate relationship discovered between two candidates."""

    kind: DuplicateKind
    fault_type: FaultTypeName
    measure: DuplicateMeasure
    candidate_id: CandidateId
    duplicate_of_candidate_id: CandidateId
    candidate_basis_sha256: Sha256
    duplicate_of_basis_sha256: Sha256
    measure_value: float | None = None
    detail: str | None = Field(default=None, max_length=1024)

    @field_validator("measure_value")
    @classmethod
    def _measure_is_finite_fraction(cls, value: float | None) -> float | None:
        if value is None:
            return None
        _require_finite(value, "measure_value")
        if not 0.0 <= value <= 1.0:
            raise ValueError("measure_value must lie in [0, 1]")
        return value

    @model_validator(mode="after")
    def _semantics_are_consistent(self) -> DuplicateFinding:
        if self.candidate_id == self.duplicate_of_candidate_id:
            raise ValueError("a candidate cannot duplicate itself")
        if self.kind == "exact_identity":
            if self.measure != "identity_equality":
                raise ValueError("exact identity findings require identity_equality")
            if self.measure_value is not None:
                raise ValueError("exact identity findings must not carry a measure_value")
            if self.candidate_basis_sha256 != self.duplicate_of_basis_sha256:
                raise ValueError("exact identity findings require equal identity fingerprints")
        elif self.kind == "effective_intervention":
            if self.measure != "effective_fingerprint_equality":
                raise ValueError(
                    "effective duplicate findings require effective_fingerprint_equality"
                )
            if self.measure_value is not None:
                raise ValueError("effective duplicate findings must not carry a measure_value")
            if self.candidate_basis_sha256 != self.duplicate_of_basis_sha256:
                raise ValueError(
                    "effective duplicate findings require equal intervention fingerprints"
                )
        else:
            if self.measure_value is None:
                raise ValueError("a near-duplicate finding must record its measure_value")
            if self.candidate_basis_sha256 == self.duplicate_of_basis_sha256:
                raise ValueError(
                    "equal intervention fingerprints are effective duplicates, not near duplicates"
                )
            if self.fault_type == "data_drift":
                if self.measure != "total_variation_distance":
                    raise ValueError("data-drift near duplicates require total-variation distance")
                if self.measure_value >= 0.02:
                    raise ValueError("data-drift near-duplicate distance must be below 0.02")
            else:
                if self.measure != "jaccard_similarity":
                    raise ValueError("M2/M3 near duplicates require Jaccard similarity")
                if self.measure_value < 0.90:
                    raise ValueError("M2/M3 near-duplicate similarity must be at least 0.90")
        return self


class DuplicateAudit(_StrictFrozenModel):
    """Exact, effective and near-duplicate findings for one alpha run."""

    schema_version: Literal["p2-duplicate-audit/1"]
    findings: tuple[DuplicateFinding, ...]

    @model_validator(mode="after")
    def _findings_are_unique(self) -> DuplicateAudit:
        pairs = tuple(
            ":".join(
                (
                    finding.kind,
                    *sorted((finding.candidate_id, finding.duplicate_of_candidate_id)),
                )
            )
            for finding in self.findings
        )
        _require_unique(pairs, "duplicate finding")
        return self


# --------------------------------------------------------------------------- #
# 9. Alpha validity report
# --------------------------------------------------------------------------- #


class AlphaValidityReport(_StrictFrozenModel):
    """The reconciled candidate flow for one alpha generation.

    Every count here is checked against the entry lists by
    :func:`aletheia_lab.benchmark.p2.validation.validate_alpha_report`; the
    schema alone cannot prove that a count matches its membership.
    """

    schema_version: Literal["p2-alpha-validity-report/1"]
    primary_planned: int = Field(ge=0)
    reserve_planned: int = Field(ge=0)
    planned_total: int = Field(ge=0)
    executed: int = Field(ge=0)
    activated_reserve: int = Field(ge=0)
    inactive_reserve: int = Field(ge=0)
    technically_valid: int = Field(ge=0)
    technical_rejected: int = Field(ge=0)
    accepted: int = Field(ge=0)
    excluded_valid: int = Field(ge=0)
    eligible_failure: int = Field(ge=0)
    stable_control: int = Field(ge=0)
    improvement_control: int = Field(ge=0)
    benign_control: int = Field(ge=0)
    context_count: int = Field(ge=0)
    mechanism_coverage_passed: bool
    gate_status: Literal["pass", "pass_with_deviation", "fail"]
    deviation_note: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _declared_equations_hold(self) -> AlphaValidityReport:
        if self.planned_total != self.primary_planned + self.reserve_planned:
            raise ValueError("planned_total must equal primary_planned + reserve_planned")
        if self.executed != self.primary_planned + self.activated_reserve:
            raise ValueError("executed must equal primary_planned + activated_reserve")
        if self.inactive_reserve != self.reserve_planned - self.activated_reserve:
            raise ValueError("inactive_reserve must equal reserve_planned - activated_reserve")
        if self.planned_total != self.executed + self.inactive_reserve:
            raise ValueError("planned_total must equal executed + inactive_reserve")
        if self.executed != self.technically_valid + self.technical_rejected:
            raise ValueError("executed must equal technically_valid + technical_rejected")
        if self.technically_valid != self.accepted + self.excluded_valid:
            raise ValueError("technically_valid must equal accepted + excluded_valid")
        classes = (
            self.eligible_failure
            + self.stable_control
            + self.improvement_control
            + self.benign_control
        )
        if self.accepted != classes:
            raise ValueError("accepted must equal the sum of the four family classes")
        expected_contexts = (
            3 * self.eligible_failure + self.stable_control + self.improvement_control
        )
        if self.context_count != expected_contexts:
            raise ValueError(
                "context_count must be derived from family classes, not chosen independently"
            )
        if self.accepted > 15:
            raise ValueError("accepted family count cannot exceed the 15-family alpha target")
        if self.accepted < 12 or not self.mechanism_coverage_passed:
            expected_status = "fail"
        elif self.accepted < 15:
            expected_status = "pass_with_deviation"
        else:
            expected_status = "pass"
        if self.gate_status != expected_status:
            raise ValueError(
                f"gate_status must be {expected_status!r} for the reported count and coverage"
            )
        if self.gate_status != "pass" and not self.deviation_note:
            raise ValueError("a non-pass alpha gate must include a deviation_note")
        if self.gate_status == "pass" and self.deviation_note is not None:
            raise ValueError("a clean alpha pass must not carry a deviation_note")
        return self
