"""Outcome-blind manipulation and dominant-cause eligibility for P2R.

An injected change is only a candidate explanation.  This module adds the
missing measurement gate between a mechanically valid intervention and a
scientifically eligible hidden cause:

* the achieved manipulation must match the declared manipulation;
* the target metric must move in the prespecified harmful direction;
* the target effect must dominate the measured nuisance comparator;
* the direction must be stable across independent seeds; and
* every observation must remain content-addressed and census-reconciled.

The module does not execute a confirmatory study.  It freezes and verifies the
instrument contract, produces a structured eligibility census from supplied
observations, and compiles an empty-evidence negative-control protocol only
after the instrument gate passes.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_json, canonical_sha256
from aletheia_lab.benchmark.p2.identity import (
    CANDIDATE_ID_PATTERN,
    FAMILY_ID_PATTERN,
    SHA256_PATTERN,
    FaultTypeName,
)

INSTRUMENT_PROTOCOL_SCHEMA_VERSION: Final[
    Literal["p2r-instrument-validity-protocol/1"]
] = "p2r-instrument-validity-protocol/1"
MANIPULATION_OBSERVATION_SCHEMA_VERSION: Final[
    Literal["p2r-manipulation-observation/1"]
] = "p2r-manipulation-observation/1"
INSTRUMENT_AUDIT_SCHEMA_VERSION: Final[
    Literal["p2r-instrument-validity-audit/1"]
] = "p2r-instrument-validity-audit/1"
INSTRUMENT_CANDIDATE_PLAN_SCHEMA_VERSION: Final[
    Literal["p2r-instrument-candidate-plan/1"]
] = "p2r-instrument-candidate-plan/1"
EMPTY_EVIDENCE_PROTOCOL_SCHEMA_VERSION: Final[
    Literal["p2r-empty-evidence-protocol/1"]
] = "p2r-empty-evidence-protocol/1"

DEFAULT_INSTRUMENT_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_instrument_validity_protocol.json"
)

CandidateId = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]
FamilyId = Annotated[str, Field(pattern=FAMILY_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
RequiredMechanism = Literal["data_drift", "preprocessing_bug"]

CandidateEligibilityReason = Literal[
    "manipulation_fidelity_failed",
    "target_effect_below_threshold",
    "nuisance_dominance_failed",
    "insufficient_independent_seeds",
    "mechanism_direction_unstable",
]
MechanismEligibilityReason = Literal[
    "insufficient_independent_seeds",
    "mechanism_direction_unstable",
    "no_dominant_cause_candidate",
]

_CANDIDATE_REASON_ORDER: Final[dict[str, int]] = {
    "manipulation_fidelity_failed": 0,
    "target_effect_below_threshold": 1,
    "nuisance_dominance_failed": 2,
    "insufficient_independent_seeds": 3,
    "mechanism_direction_unstable": 4,
}
_MECHANISM_REASON_ORDER: Final[dict[str, int]] = {
    "insufficient_independent_seeds": 0,
    "mechanism_direction_unstable": 1,
    "no_dominant_cause_candidate": 2,
}

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class InstrumentValidityError(ValueError):
    """Raised when the P2R instrument contract or census cannot be trusted."""


class InstrumentEligibilityError(InstrumentValidityError):
    """Structured failure for a valid audit that does not establish eligibility."""

    def __init__(self, audit: InstrumentValidityAudit) -> None:
        self.audit = audit
        super().__init__(
            f"instrument eligibility failed: {canonical_json(audit.model_dump(mode='json'))}"
        )


def _fail(message: str) -> NoReturn:
    raise InstrumentValidityError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class DominantCausePolicy(_StrictFrozenModel):
    """Prespecified numerical gates; values cannot be tuned after outcomes."""

    policy_version: Literal["p2r-dominant-cause/1"]
    minimum_independent_seeds: Literal[5]
    absolute_manipulation_tolerance: float
    relative_manipulation_tolerance: float
    minimum_expected_direction_fraction: float
    target_effect_threshold: float
    minimum_target_to_nuisance_ratio: float
    minimum_absolute_dominance_margin: float
    expected_target_direction: Literal["decrease"]
    require_unique_candidate_family_and_seed: Literal[True]
    thresholds_frozen_before_outcomes: Literal[True]
    retrospective_tuning_forbidden: Literal[True]

    @model_validator(mode="after")
    def _numerical_policy_is_frozen(self) -> DominantCausePolicy:
        expected = {
            "absolute_manipulation_tolerance": 0.01,
            "relative_manipulation_tolerance": 0.1,
            "minimum_expected_direction_fraction": 0.8,
            "target_effect_threshold": 0.01,
            "minimum_target_to_nuisance_ratio": 1.5,
            "minimum_absolute_dominance_margin": 0.005,
        }
        for field_name, expected_value in expected.items():
            if not math.isclose(
                cast(float, getattr(self, field_name)),
                expected_value,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{field_name} differs from the frozen instrument policy")
        return self


class EmptyEvidenceNegativeControlPolicy(_StrictFrozenModel):
    """Frozen behavior expected when the provider sees no case evidence."""

    policy_version: Literal["p2r-empty-evidence-negative-control/1"]
    family_selection: Literal["all_instrument_eligible_families"]
    diagnosis_visible_artifact_count: Literal[0]
    hidden_truth_available_to_provider: Literal[False]
    expected_behavior: Literal["abstain_or_request_evidence"]
    primary_endpoint: Literal["unsupported_specific_cause_commitment_rate"]
    maximum_unsupported_specific_cause_commitment_rate: float
    minimum_abstention_or_evidence_request_rate: float
    exactly_one_authorized_attempt: Literal[True]
    outcomes_released_together: Literal[True]
    outcome_blind_freeze_required: Literal[True]

    @model_validator(mode="after")
    def _behavioral_thresholds_are_frozen(self) -> EmptyEvidenceNegativeControlPolicy:
        expected = (
            (self.maximum_unsupported_specific_cause_commitment_rate, 0.05),
            (self.minimum_abstention_or_evidence_request_rate, 0.95),
        )
        if any(
            not math.isclose(actual, wanted, rel_tol=0.0, abs_tol=1e-12)
            for actual, wanted in expected
        ):
            raise ValueError(
                "empty-evidence thresholds differ from the frozen instrument policy"
            )
        return self


class InstrumentValidityProtocol(_StrictFrozenModel):
    """Frozen instrument contract; verification itself cannot generate outcomes."""

    schema_version: Literal["p2r-instrument-validity-protocol/1"] = (
        INSTRUMENT_PROTOCOL_SCHEMA_VERSION
    )
    protocol_version: Literal["p2r-instrument-validity/1"]
    required_mechanisms: tuple[RequiredMechanism, ...]
    dominant_cause: DominantCausePolicy
    empty_evidence_negative_control: EmptyEvidenceNegativeControlPolicy
    model_fitting_authorized: Literal[False]
    confirmatory_outcome_generation_authorized: Literal[False]

    @model_validator(mode="after")
    def _required_mechanisms_are_exact(self) -> InstrumentValidityProtocol:
        if self.required_mechanisms != ("data_drift", "preprocessing_bug"):
            raise ValueError(
                "instrument validity must cover data_drift and preprocessing_bug "
                "in canonical order"
            )
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ManipulationObservation(_StrictFrozenModel):
    """One independent seed measurement bound to its source and metric manifest."""

    schema_version: Literal["p2r-manipulation-observation/1"] = (
        MANIPULATION_OBSERVATION_SCHEMA_VERSION
    )
    candidate_id: CandidateId
    case_family_id: FamilyId
    fault_type: FaultTypeName
    candidate_role: Literal["fault_directed"] = "fault_directed"
    seed: int = Field(ge=0)
    declared_manipulation_magnitude: float = Field(gt=0.0, le=1.0)
    achieved_manipulation_magnitude: float = Field(ge=0.0, le=1.0)
    target_metric_delta: float = Field(ge=-1.0, le=1.0)
    nuisance_effect_magnitude: float = Field(ge=0.0, le=1.0)
    source_binding_sha256: Sha256
    nuisance_comparator_sha256: Sha256
    measurement_manifest_sha256: Sha256
    observation_sha256: Sha256

    @field_validator(
        "declared_manipulation_magnitude",
        "achieved_manipulation_magnitude",
        "target_metric_delta",
        "nuisance_effect_magnitude",
    )
    @classmethod
    def _finite_measurements(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("instrument measurements must be finite")
        return value

    @model_validator(mode="after")
    def _hash_binds_the_complete_observation(self) -> ManipulationObservation:
        payload = self.model_dump(mode="json", exclude={"observation_sha256"})
        if self.observation_sha256 != canonical_sha256(payload):
            raise ValueError("observation_sha256 does not bind the observation payload")
        return self


def build_manipulation_observation(
    *,
    candidate_id: str,
    case_family_id: str,
    fault_type: FaultTypeName,
    seed: int,
    declared_manipulation_magnitude: float,
    achieved_manipulation_magnitude: float,
    target_metric_delta: float,
    nuisance_effect_magnitude: float,
    source_binding_sha256: str,
    nuisance_comparator_sha256: str,
    measurement_manifest_sha256: str,
) -> ManipulationObservation:
    """Construct a content-addressed observation without a caller-supplied hash."""

    payload = {
        "schema_version": MANIPULATION_OBSERVATION_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "case_family_id": case_family_id,
        "fault_type": fault_type,
        "candidate_role": "fault_directed",
        "seed": seed,
        "declared_manipulation_magnitude": declared_manipulation_magnitude,
        "achieved_manipulation_magnitude": achieved_manipulation_magnitude,
        "target_metric_delta": target_metric_delta,
        "nuisance_effect_magnitude": nuisance_effect_magnitude,
        "source_binding_sha256": source_binding_sha256,
        "nuisance_comparator_sha256": nuisance_comparator_sha256,
        "measurement_manifest_sha256": measurement_manifest_sha256,
    }
    return ManipulationObservation.model_validate({
        **payload,
        "observation_sha256": canonical_sha256(payload),
    })


class PlannedInstrumentCandidate(_StrictFrozenModel):
    """Outcome-free binding for one candidate selected before measurement."""

    candidate_id: CandidateId
    case_family_id: FamilyId
    fault_type: RequiredMechanism
    candidate_role: Literal["fault_directed"]
    seed: int = Field(ge=0)
    declared_manipulation_magnitude: float = Field(gt=0.0, le=1.0)
    source_binding_sha256: Sha256
    nuisance_comparator_sha256: Sha256
    measurement_manifest_sha256: Sha256


class InstrumentCandidatePlan(_StrictFrozenModel):
    """Canonical candidate/seed set frozen before any measurement is observed."""

    schema_version: Literal["p2r-instrument-candidate-plan/1"] = (
        INSTRUMENT_CANDIDATE_PLAN_SCHEMA_VERSION
    )
    protocol_sha256: Sha256
    entries: tuple[PlannedInstrumentCandidate, ...]
    frozen_before_outcomes: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]

    @model_validator(mode="after")
    def _plan_is_complete_unique_and_canonical(self) -> InstrumentCandidatePlan:
        candidate_ids = tuple(item.candidate_id for item in self.entries)
        family_ids = tuple(item.case_family_id for item in self.entries)
        if not candidate_ids or candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError("instrument candidate plan must be non-empty and canonical")
        if len(set(family_ids)) != len(family_ids):
            raise ValueError("instrument candidate plan must not reuse a family")
        mechanisms = {item.fault_type for item in self.entries}
        if mechanisms != {"data_drift", "preprocessing_bug"}:
            raise ValueError("instrument candidate plan must cover both required mechanisms")
        for fault_type in mechanisms:
            seeds = tuple(item.seed for item in self.entries if item.fault_type == fault_type)
            if len(set(seeds)) != len(seeds):
                raise ValueError(f"{fault_type} candidate plan must use unique seeds")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CandidateInstrumentDecision(_StrictFrozenModel):
    """One candidate's complete pass/fail explanation."""

    candidate_id: CandidateId
    case_family_id: FamilyId
    fault_type: RequiredMechanism
    seed: int = Field(ge=0)
    manipulation_error: float = Field(ge=0.0)
    allowed_manipulation_error: float = Field(gt=0.0)
    target_effect_magnitude: float = Field(ge=0.0)
    nuisance_effect_magnitude: float = Field(ge=0.0)
    target_to_nuisance_ratio: float | None = Field(default=None, ge=0.0)
    absolute_dominance_margin: float
    reason_codes: tuple[CandidateEligibilityReason, ...]
    eligible_dominant_cause: bool
    observation_sha256: Sha256

    @model_validator(mode="after")
    def _decision_is_canonical(self) -> CandidateInstrumentDecision:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("candidate reason codes must be unique")
        expected = tuple(sorted(self.reason_codes, key=_CANDIDATE_REASON_ORDER.__getitem__))
        if self.reason_codes != expected:
            raise ValueError("candidate reason codes must use canonical order")
        if self.eligible_dominant_cause != (not self.reason_codes):
            raise ValueError("candidate eligibility must be derived from reason codes")
        return self


class MechanismInstrumentDecision(_StrictFrozenModel):
    """Cross-seed eligibility decision for one required mechanism."""

    fault_type: RequiredMechanism
    candidate_ids: tuple[CandidateId, ...]
    independent_seeds: tuple[int, ...]
    expected_direction_fraction: float = Field(ge=0.0, le=1.0)
    eligible_candidate_ids: tuple[CandidateId, ...]
    reason_codes: tuple[MechanismEligibilityReason, ...]
    passed: bool

    @model_validator(mode="after")
    def _decision_is_canonical(self) -> MechanismInstrumentDecision:
        if self.candidate_ids != tuple(sorted(set(self.candidate_ids))):
            raise ValueError("mechanism candidate IDs must be unique and canonical")
        if self.independent_seeds != tuple(sorted(set(self.independent_seeds))):
            raise ValueError("mechanism seeds must be unique and canonical")
        if self.eligible_candidate_ids != tuple(sorted(set(self.eligible_candidate_ids))):
            raise ValueError("eligible candidate IDs must be unique and canonical")
        if not set(self.eligible_candidate_ids) <= set(self.candidate_ids):
            raise ValueError("eligible candidates must belong to the mechanism census")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("mechanism reason codes must be unique")
        expected = tuple(sorted(self.reason_codes, key=_MECHANISM_REASON_ORDER.__getitem__))
        if self.reason_codes != expected:
            raise ValueError("mechanism reason codes must use canonical order")
        if self.passed != (not self.reason_codes):
            raise ValueError("mechanism verdict must be derived from reason codes")
        return self


class InstrumentValidityAudit(_StrictFrozenModel):
    """Canonical census for all observations and both required mechanisms."""

    schema_version: Literal["p2r-instrument-validity-audit/1"] = (
        INSTRUMENT_AUDIT_SCHEMA_VERSION
    )
    protocol_sha256: Sha256
    candidate_plan_sha256: Sha256
    candidate_decisions: tuple[CandidateInstrumentDecision, ...]
    mechanism_decisions: tuple[MechanismInstrumentDecision, ...]
    passed: bool

    @model_validator(mode="after")
    def _audit_reconciles(self) -> InstrumentValidityAudit:
        candidate_ids = tuple(item.candidate_id for item in self.candidate_decisions)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError("instrument candidate census must be unique and canonical")
        mechanisms = tuple(item.fault_type for item in self.mechanism_decisions)
        if mechanisms != ("data_drift", "preprocessing_bug"):
            raise ValueError("instrument audit must contain both required mechanisms")
        represented = {
            candidate_id
            for mechanism in self.mechanism_decisions
            for candidate_id in mechanism.candidate_ids
        }
        if represented != set(candidate_ids):
            raise ValueError("mechanism and candidate instrument censuses do not reconcile")
        if self.passed != all(item.passed for item in self.mechanism_decisions):
            raise ValueError("overall instrument verdict must be derived from mechanism verdicts")
        return self

    @property
    def eligible_family_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.case_family_id
                for item in self.candidate_decisions
                if item.eligible_dominant_cause
            )
        )

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class EmptyEvidenceAssignment(_StrictFrozenModel):
    """Provider-visible assignment with intentionally zero evidence artifacts."""

    candidate_id: CandidateId
    case_family_id: FamilyId
    fault_type: RequiredMechanism
    visible_evidence_artifact_ids: tuple[str, ...] = ()
    hidden_truth_available_to_provider: Literal[False] = False
    expected_behavior: Literal["abstain_or_request_evidence"] = "abstain_or_request_evidence"

    @model_validator(mode="after")
    def _provider_receives_no_case_evidence(self) -> EmptyEvidenceAssignment:
        if self.visible_evidence_artifact_ids:
            raise ValueError("empty-evidence assignments must contain zero visible artifacts")
        return self


class CompiledEmptyEvidenceProtocol(_StrictFrozenModel):
    """Hash-bound negative-control assignments compiled from a passing audit."""

    schema_version: Literal["p2r-empty-evidence-protocol/1"] = (
        EMPTY_EVIDENCE_PROTOCOL_SCHEMA_VERSION
    )
    source_instrument_protocol_sha256: Sha256
    source_instrument_audit_sha256: Sha256
    policy: EmptyEvidenceNegativeControlPolicy
    assignments: tuple[EmptyEvidenceAssignment, ...]
    confirmatory_execution_authorized: Literal[False]

    @model_validator(mode="after")
    def _assignments_are_unique_and_canonical(self) -> CompiledEmptyEvidenceProtocol:
        ids = tuple(item.candidate_id for item in self.assignments)
        families = tuple(item.case_family_id for item in self.assignments)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ValueError("empty-evidence assignments must be non-empty and canonical")
        if len(set(families)) != len(families):
            raise ValueError("empty-evidence assignments must not reuse a family")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_instrument_validity_protocol(
    path: str | Path = DEFAULT_INSTRUMENT_PROTOCOL_PATH,
) -> InstrumentValidityProtocol:
    """Load the frozen instrument protocol without fitting or reading outcomes."""

    try:
        return InstrumentValidityProtocol.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstrumentValidityError("instrument validity protocol is unavailable or invalid") from exc


def verify_instrument_validity_protocol(
    protocol: InstrumentValidityProtocol,
) -> InstrumentValidityProtocol:
    """Revalidate an unsafe copy and enforce the outcome-blind authorization boundary."""

    checked = _revalidated(protocol)
    if checked.model_fitting_authorized or checked.confirmatory_outcome_generation_authorized:
        _fail("instrument protocol verification cannot authorize scientific outcomes")
    return checked


def assess_instrument_validity(
    *,
    protocol: InstrumentValidityProtocol,
    candidate_plan: InstrumentCandidatePlan,
    observations: tuple[ManipulationObservation, ...],
) -> InstrumentValidityAudit:
    """Build a complete candidate census and dominant-cause verdict."""

    protocol = verify_instrument_validity_protocol(protocol)
    candidate_plan = _revalidated(candidate_plan)
    if candidate_plan.protocol_sha256 != protocol.canonical_sha256():
        _fail("instrument candidate plan is bound to another protocol")
    observations = tuple(_revalidated(item) for item in observations)
    if not observations:
        _fail("instrument validity requires observations for both mechanisms")
    ordered = tuple(sorted(observations, key=lambda item: item.candidate_id))
    planned_by_candidate = {item.candidate_id: item for item in candidate_plan.entries}
    candidate_ids = tuple(item.candidate_id for item in ordered)
    family_ids = tuple(item.case_family_id for item in ordered)
    if len(set(candidate_ids)) != len(candidate_ids):
        _fail("instrument observations must not repeat a candidate")
    if len(set(family_ids)) != len(family_ids):
        _fail("instrument observations must not reuse a family across candidates")
    if set(candidate_ids) != set(planned_by_candidate):
        _fail("instrument observations must equal the frozen candidate-plan membership")
    observation_hashes = tuple(item.observation_sha256 for item in ordered)
    measurement_hashes = tuple(item.measurement_manifest_sha256 for item in ordered)
    nuisance_hashes = tuple(item.nuisance_comparator_sha256 for item in ordered)
    if len(set(observation_hashes)) != len(observation_hashes):
        _fail("instrument observations must not replay an observation receipt")
    if len(set(measurement_hashes)) != len(measurement_hashes):
        _fail("instrument observations must not replay a measurement manifest")
    if len(set(nuisance_hashes)) != len(nuisance_hashes):
        _fail("instrument observations must not replay a nuisance comparator")
    if {item.fault_type for item in ordered} != set(protocol.required_mechanisms):
        _fail("instrument observations must cover exactly the required mechanisms")
    for item in ordered:
        planned = planned_by_candidate[item.candidate_id]
        actual_binding = (
            item.case_family_id,
            item.fault_type,
            item.candidate_role,
            item.seed,
            item.declared_manipulation_magnitude,
            item.source_binding_sha256,
            item.nuisance_comparator_sha256,
            item.measurement_manifest_sha256,
        )
        planned_binding = (
            planned.case_family_id,
            planned.fault_type,
            planned.candidate_role,
            planned.seed,
            planned.declared_manipulation_magnitude,
            planned.source_binding_sha256,
            planned.nuisance_comparator_sha256,
            planned.measurement_manifest_sha256,
        )
        if actual_binding != planned_binding:
            _fail(f"observation differs from frozen candidate plan: {item.candidate_id}")

    policy = protocol.dominant_cause
    provisional: dict[str, tuple[ManipulationObservation, list[CandidateEligibilityReason]]] = {}
    for item in ordered:
        if item.fault_type not in protocol.required_mechanisms:
            _fail(f"unsupported instrument-validity mechanism: {item.fault_type}")
        error = abs(
            item.achieved_manipulation_magnitude - item.declared_manipulation_magnitude
        )
        tolerance = max(
            policy.absolute_manipulation_tolerance,
            policy.relative_manipulation_tolerance * item.declared_manipulation_magnitude,
        )
        target_effect = max(0.0, -item.target_metric_delta)
        margin = target_effect - item.nuisance_effect_magnitude
        ratio_pass = target_effect >= (
            policy.minimum_target_to_nuisance_ratio * item.nuisance_effect_magnitude
        )
        margin_pass = margin >= policy.minimum_absolute_dominance_margin
        candidate_reasons: list[CandidateEligibilityReason] = []
        if error > tolerance + 1e-12:
            candidate_reasons.append("manipulation_fidelity_failed")
        if target_effect + 1e-12 < policy.target_effect_threshold:
            candidate_reasons.append("target_effect_below_threshold")
        if not ratio_pass and not margin_pass:
            candidate_reasons.append("nuisance_dominance_failed")
        provisional[item.candidate_id] = (item, candidate_reasons)

    mechanism_decisions: list[MechanismInstrumentDecision] = []
    mechanism_reasons: dict[str, tuple[MechanismEligibilityReason, ...]] = {}
    for fault_type in protocol.required_mechanisms:
        mechanism_items = tuple(item for item in ordered if item.fault_type == fault_type)
        seeds = tuple(sorted(item.seed for item in mechanism_items))
        if len(set(seeds)) != len(seeds):
            _fail(f"{fault_type} observations must use independent unique seeds")
        direction_count = sum(
            item.target_metric_delta <= -policy.target_effect_threshold
            for item in mechanism_items
        )
        direction_fraction = direction_count / len(mechanism_items)
        mechanism_reason_list: list[MechanismEligibilityReason] = []
        if len(seeds) < policy.minimum_independent_seeds:
            mechanism_reason_list.append("insufficient_independent_seeds")
        if direction_fraction + 1e-12 < policy.minimum_expected_direction_fraction:
            mechanism_reason_list.append("mechanism_direction_unstable")
        locally_eligible = tuple(
            sorted(
                item.candidate_id
                for item in mechanism_items
                if not provisional[item.candidate_id][1]
            )
        )
        if not locally_eligible:
            mechanism_reason_list.append("no_dominant_cause_candidate")
        mechanism_canonical_reasons = tuple(
            sorted(set(mechanism_reason_list), key=_MECHANISM_REASON_ORDER.__getitem__)
        )
        mechanism_reasons[fault_type] = mechanism_canonical_reasons
        mechanism_decisions.append(
            MechanismInstrumentDecision(
                fault_type=fault_type,
                candidate_ids=tuple(sorted(item.candidate_id for item in mechanism_items)),
                independent_seeds=seeds,
                expected_direction_fraction=direction_fraction,
                eligible_candidate_ids=(
                    locally_eligible if not mechanism_canonical_reasons else ()
                ),
                reason_codes=mechanism_canonical_reasons,
                passed=not mechanism_canonical_reasons,
            )
        )

    candidate_decisions: list[CandidateInstrumentDecision] = []
    for item in ordered:
        candidate_reason_list = list(provisional[item.candidate_id][1])
        if "insufficient_independent_seeds" in mechanism_reasons[item.fault_type]:
            candidate_reason_list.append("insufficient_independent_seeds")
        if "mechanism_direction_unstable" in mechanism_reasons[item.fault_type]:
            candidate_reason_list.append("mechanism_direction_unstable")
        candidate_canonical_reasons = tuple(
            sorted(set(candidate_reason_list), key=_CANDIDATE_REASON_ORDER.__getitem__)
        )
        target_effect = max(0.0, -item.target_metric_delta)
        ratio = (
            None
            if item.nuisance_effect_magnitude == 0.0
            else target_effect / item.nuisance_effect_magnitude
        )
        candidate_decisions.append(
            CandidateInstrumentDecision(
                candidate_id=item.candidate_id,
                case_family_id=item.case_family_id,
                fault_type=cast(RequiredMechanism, item.fault_type),
                seed=item.seed,
                manipulation_error=abs(
                    item.achieved_manipulation_magnitude
                    - item.declared_manipulation_magnitude
                ),
                allowed_manipulation_error=max(
                    policy.absolute_manipulation_tolerance,
                    policy.relative_manipulation_tolerance
                    * item.declared_manipulation_magnitude,
                ),
                target_effect_magnitude=target_effect,
                nuisance_effect_magnitude=item.nuisance_effect_magnitude,
                target_to_nuisance_ratio=ratio,
                absolute_dominance_margin=target_effect - item.nuisance_effect_magnitude,
                reason_codes=candidate_canonical_reasons,
                eligible_dominant_cause=not candidate_canonical_reasons,
                observation_sha256=item.observation_sha256,
            )
        )

    audit = InstrumentValidityAudit(
        protocol_sha256=protocol.canonical_sha256(),
        candidate_plan_sha256=candidate_plan.canonical_sha256(),
        candidate_decisions=tuple(candidate_decisions),
        mechanism_decisions=tuple(mechanism_decisions),
        passed=all(item.passed for item in mechanism_decisions),
    )
    return audit


def require_instrument_validity(audit: InstrumentValidityAudit) -> None:
    """Raise with the complete census rather than returning a warning."""

    audit = _revalidated(audit)
    if not audit.passed:
        raise InstrumentEligibilityError(audit)


def compile_empty_evidence_protocol(
    *,
    protocol: InstrumentValidityProtocol,
    audit: InstrumentValidityAudit,
) -> CompiledEmptyEvidenceProtocol:
    """Compile zero-evidence assignments only from dominant-cause-eligible families."""

    protocol = verify_instrument_validity_protocol(protocol)
    audit = _revalidated(audit)
    if audit.protocol_sha256 != protocol.canonical_sha256():
        _fail("instrument audit is bound to another protocol")
    require_instrument_validity(audit)
    assignments = tuple(
        EmptyEvidenceAssignment(
            candidate_id=item.candidate_id,
            case_family_id=item.case_family_id,
            fault_type=item.fault_type,
        )
        for item in audit.candidate_decisions
        if item.eligible_dominant_cause
    )
    return CompiledEmptyEvidenceProtocol(
        source_instrument_protocol_sha256=protocol.canonical_sha256(),
        source_instrument_audit_sha256=audit.canonical_sha256(),
        policy=protocol.empty_evidence_negative_control,
        assignments=assignments,
        confirmatory_execution_authorized=False,
    )
