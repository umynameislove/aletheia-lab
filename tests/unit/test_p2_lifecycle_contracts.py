"""Regression tests for the Phase 2 candidate lifecycle records and validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2 import (
    AdmissionLedger,
    AdmissionRecord,
    AlphaValidityReport,
    CandidateExecution,
    CandidatePlan,
    CandidateSlot,
    ClassificationLedger,
    ClassificationRecord,
    ContextCensus,
    ContextEntry,
    ContractViolation,
    DataDriftParameters,
    DuplicateAudit,
    DuplicateFinding,
    ExecutedCandidate,
    FamilyCensus,
    FamilyCensusEntry,
    FamilyIdentity,
    LabelNoiseParameters,
    P2ContractArtifacts,
    PreprocessingBugParameters,
    TechnicalDisposition,
    TechnicalDispositionEntry,
    candidate_id_for,
    canonical_sha256,
    context_id_for,
    load_contract_store,
    proposed_family_sha256,
    save_contract_store,
    validate_alpha_report,
    validate_candidate_flow,
    validate_context_cardinality,
    validate_contract_bundle,
    validate_duplicate_audit,
    validate_frozen_alpha_plan,
    validate_reserve_activation,
)

_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64

_MECHANISMS = ("data_drift", "label_noise", "preprocessing_bug")

_SLOT_SPECS: dict[str, tuple[str, str, str, int, int | None]] = {
    "M1-F1": ("data_drift", "primary", "fault_directed", 1, None),
    "M1-F2": ("data_drift", "primary", "fault_directed", 3, None),
    "M1-S1": ("data_drift", "primary", "fault_directed", 5, None),
    "M1-I1": ("data_drift", "primary", "fault_directed", 4, None),
    "M1-B1": ("data_drift", "primary", "designed_benign_control", 105, None),
    "M1-R1": ("data_drift", "reserve", "fault_directed", 2, 1),
    "M1-R2": ("data_drift", "reserve", "fault_directed", 106, 2),
    "M1-R3": ("data_drift", "reserve", "fault_directed", 107, 3),
    "M2-F1": ("label_noise", "primary", "fault_directed", 201, None),
    "M2-F2": ("label_noise", "primary", "fault_directed", 202, None),
    "M2-F3": ("label_noise", "primary", "fault_directed", 203, None),
    "M2-I1": (
        "label_noise",
        "primary",
        "designed_improvement_control",
        204,
        None,
    ),
    "M2-B1": ("label_noise", "primary", "designed_benign_control", 205, None),
    "M2-R1": ("label_noise", "reserve", "fault_directed", 206, 1),
    "M2-R2": ("label_noise", "reserve", "fault_directed", 207, 2),
    "M2-R3": ("label_noise", "reserve", "fault_directed", 208, 3),
    "M3-F1": ("preprocessing_bug", "primary", "fault_directed", 301, None),
    "M3-F2": ("preprocessing_bug", "primary", "fault_directed", 302, None),
    "M3-F3": ("preprocessing_bug", "primary", "fault_directed", 303, None),
    "M3-I1": (
        "preprocessing_bug",
        "primary",
        "designed_improvement_control",
        304,
        None,
    ),
    "M3-B1": (
        "preprocessing_bug",
        "primary",
        "designed_benign_control",
        305,
        None,
    ),
    "M3-R1": ("preprocessing_bug", "reserve", "fault_directed", 306, 1),
    "M3-R2": ("preprocessing_bug", "reserve", "fault_directed", 307, 2),
    "M3-R3": ("preprocessing_bug", "reserve", "fault_directed", 308, 3),
}

_M1_TARGETS = {
    "M1-F1": {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
    "M1-F2": {"Month-to-month": 0.90, "One year": 0.06, "Two year": 0.04},
    "M1-S1": {"Month-to-month": 0.60, "One year": 0.20, "Two year": 0.20},
    "M1-I1": {"Month-to-month": 0.40, "One year": 0.30, "Two year": 0.30},
    "M1-B1": {"Month-to-month": 0.55, "One year": 0.25, "Two year": 0.20},
    "M1-R1": {"Month-to-month": 0.70, "One year": 0.18, "Two year": 0.12},
    "M1-R2": {"Month-to-month": 0.75, "One year": 0.15, "Two year": 0.10},
    "M1-R3": {"Month-to-month": 0.50, "One year": 0.25, "Two year": 0.25},
}

_M2_RATES = {
    "M2-F1": 0.01,
    "M2-F2": 0.05,
    "M2-F3": 0.20,
    "M2-I1": 0.20,
    "M2-B1": 0.0,
    "M2-R1": 0.025,
    "M2-R2": 0.10,
    "M2-R3": 0.30,
}

_M3_RANKS = {
    "M3-F1": (3, 2),
    "M3-F2": (2, 1),
    "M3-F3": (1, 3),
    "M3-I1": (1, 3),
    "M3-B1": (None, None),
    "M3-R1": (3, 1),
    "M3-R2": (2, 3),
    "M3-R3": (1, 2),
}


def _slot(slot_id: str) -> CandidateSlot:
    fault_type, kind, role, seed, reserve_order = _SLOT_SPECS[slot_id]
    if fault_type == "data_drift":
        parameters: object = DataDriftParameters(
            feature="Contract",
            target_distribution=_M1_TARGETS[slot_id],
            output_size=1409,
        )
        intervention_type = (
            "empirical_distribution_resampling_control"
            if slot_id == "M1-B1"
            else "categorical_distribution_shift"
        )
    elif fault_type == "label_noise":
        parameters = LabelNoiseParameters(
            flip_rate=_M2_RATES[slot_id],
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )
        intervention_type = {
            "M2-I1": "training_target_label_repair",
            "M2-B1": "target_label_serialization_roundtrip",
        }.get(slot_id, "training_target_label_corruption")
    else:
        source_rank, mapped_rank = _M3_RANKS[slot_id]
        parameters = PreprocessingBugParameters(
            target_feature="Contract",
            source_rank=source_rank,
            mapped_rank=mapped_rank,
            mode="inference_only",
            transform_name="one_hot_encoder",
        )
        intervention_type = {
            "M3-I1": "inference_encoder_mapping_repair",
            "M3-B1": "name_bound_column_order_permutation",
        }.get(slot_id, "inference_encoder_mapping_mismatch")
    identity = FamilyIdentity(
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=_HEX_A,
        model_data_split_manifest_sha256=_HEX_B,
        fault_type=fault_type,  # type: ignore[arg-type]
        intervention_type=intervention_type,
        canonical_intervention_parameters=parameters,  # type: ignore[arg-type]
        seed=seed,
        reference_construction_id="clean-test-reference/v1",
        injector_contract_version="kernel/v1",
        model_specification_sha256=_HEX_C,
        preprocessing_specification_sha256=_HEX_D,
        identity_schema_version="p2-family-identity/v1",
    )
    return CandidateSlot(
        slot_id=slot_id,
        fault_type=fault_type,  # type: ignore[arg-type]
        slot_kind=kind,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        reserve_order=reserve_order,
        identity=identity,
    )


def _build_plan() -> CandidatePlan:
    return CandidatePlan(
        schema_version="p2-candidate-plan/1",
        primary_planned=15,
        reserve_planned=9,
        slots=tuple(_slot(slot_id) for slot_id in _SLOT_SPECS),
    )


def _executed_for(slot: CandidateSlot) -> ExecutedCandidate:
    fingerprint = proposed_family_sha256(slot.identity)
    return ExecutedCandidate(
        candidate_id=candidate_id_for(slot_id=slot.slot_id, family_fingerprint=fingerprint),
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        role=slot.role,
        slot_kind=slot.slot_kind,
        proposed_family_sha256=fingerprint,
        dataset_sha256=_HEX_A,
        model_data_split_manifest_sha256=_HEX_B,
    )


_OUTCOME_FOR_ROLE = {
    "fault_directed": ("regression", "eligible_failure"),
    "designed_improvement_control": ("improvement", "improvement_control"),
    "designed_benign_control": ("benign", "benign_control"),
}


def _classification(executed: ExecutedCandidate) -> ClassificationRecord:
    outcome, family_class = _OUTCOME_FOR_ROLE[executed.role]
    if outcome == "regression":
        reference, observed = 0.80, 0.75
    elif outcome == "improvement":
        reference, observed = 0.70, 0.75
    else:
        reference, observed = 0.80, 0.80
    policy = {
        "data_drift": "accuracy-regression/v1",
        "label_noise": "label-noise-impact/alpha-v1",
        "preprocessing_bug": "preprocessing-bug-impact/alpha-v1",
    }[executed.fault_type]
    return ClassificationRecord(
        schema_version="p2-classification-record/1",
        candidate_id=executed.candidate_id,
        role=executed.role,
        eligibility_policy_version=policy,  # type: ignore[arg-type]
        primary_metric="accuracy",
        reference_value=reference,
        observed_value=observed,
        delta=observed - reference,
        threshold=0.01,
        measured_outcome=outcome,  # type: ignore[arg-type]
        family_class=family_class,  # type: ignore[arg-type]
        equivalence_checks_passed=True if outcome == "benign" else None,
    )


def _context_entry(
    family_id: str,
    condition: str,
    *,
    projection: dict[str, object] | None = None,
) -> ContextEntry:
    payload = projection or {"items": [{"id": "primary-comparison", "value": "observable"}]}
    return ContextEntry(
        diagnosis_context_id=context_id_for(
            case_family_id=family_id,
            evidence_condition=condition,
        ),
        case_family_id=family_id,
        evidence_condition=condition,  # type: ignore[arg-type]
        diagnosis_projection=payload,
        diagnosis_projection_sha256=canonical_sha256(payload),
    )


class _Bundle:
    """A minimal, internally consistent alpha run used as the test baseline."""

    def __init__(self) -> None:
        self.plan = _build_plan()
        primary = tuple(slot for slot in self.plan.slots if slot.slot_kind == "primary")
        self.executed = tuple(_executed_for(slot) for slot in primary)
        self.execution = CandidateExecution(
            schema_version="p2-candidate-execution/1",
            executed=self.executed,
            inactive_reserve_slot_ids=tuple(
                slot.slot_id for slot in self.plan.slots if slot.slot_kind == "reserve"
            ),
        )
        self.disposition = TechnicalDisposition(
            schema_version="p2-technical-disposition/1",
            entries=tuple(
                TechnicalDispositionEntry(
                    candidate_id=item.candidate_id, disposition="technically_valid"
                )
                for item in self.executed
            ),
        )
        self.classifications = tuple(_classification(item) for item in self.executed)
        self.admissions = tuple(
            AdmissionRecord(
                schema_version="p2-admission-record/1",
                candidate_id=item.candidate_id,
                admission="accepted",
                case_family_id=f"p2-family-{item.proposed_family_sha256}",
                family_class=_OUTCOME_FOR_ROLE[item.role][1],  # type: ignore[arg-type]
            )
            for item in self.executed
        )
        self.census = FamilyCensus(
            schema_version="p2-family-census/1",
            entries=tuple(
                FamilyCensusEntry(
                    case_family_id=f"p2-family-{item.proposed_family_sha256}",
                    candidate_id=item.candidate_id,
                    fault_type=item.fault_type,
                    family_class=_OUTCOME_FOR_ROLE[item.role][1],  # type: ignore[arg-type]
                    proposed_family_sha256=item.proposed_family_sha256,
                )
                for item in self.executed
            ),
        )
        self.contexts = ContextCensus(
            schema_version="p2-context-census/1",
            entries=tuple(self._contexts()),
        )

    def _contexts(self) -> list[ContextEntry]:
        entries: list[ContextEntry] = []
        for item in self.executed:
            family_class = _OUTCOME_FOR_ROLE[item.role][1]
            family_id = f"p2-family-{item.proposed_family_sha256}"
            if family_class == "eligible_failure":
                conditions = ("full", "missing_key", "noisy")
            elif family_class == "benign_control":
                conditions = ()
            else:
                conditions = ("full",)
            for position, condition in enumerate(conditions):
                entries.append(
                    _context_entry(
                        family_id,
                        condition,
                        projection={
                            "items": [
                                {
                                    "id": "primary-comparison",
                                    "value": f"observable-{position}",
                                }
                            ]
                        },
                    )
                )
        return entries

    def flow_kwargs(self) -> dict[str, object]:
        return {
            "plan": self.plan,
            "execution": self.execution,
            "disposition": self.disposition,
            "classifications": self.classifications,
            "admissions": self.admissions,
            "census": self.census,
        }


@pytest.fixture
def bundle() -> _Bundle:
    return _Bundle()


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #


def test_consistent_run_passes_every_validator(bundle: _Bundle) -> None:
    validate_candidate_flow(**bundle.flow_kwargs())  # type: ignore[arg-type]
    validate_reserve_activation(
        plan=bundle.plan, execution=bundle.execution, disposition=bundle.disposition
    )
    validate_context_cardinality(census=bundle.census, contexts=bundle.contexts)


def test_plan_declares_fifteen_primary_and_nine_reserve_slots(bundle: _Bundle) -> None:
    assert bundle.plan.primary_planned == 15
    assert bundle.plan.reserve_planned == 9
    for fault_type in _MECHANISMS:
        reserve = [
            slot
            for slot in bundle.plan.slots
            if slot.slot_kind == "reserve" and slot.fault_type == fault_type
        ]
        assert sorted(slot.reserve_order for slot in reserve) == [1, 2, 3]


def test_frozen_alpha_plan_rejects_single_mechanism_substitution(bundle: _Bundle) -> None:
    template = next(slot for slot in bundle.plan.slots if slot.slot_id == "M1-F1")
    slots = tuple(
        CandidateSlot(
            slot_id=f"M1-F{index}",
            fault_type="data_drift",
            slot_kind="primary",
            role="fault_directed",
            identity=template.identity,
        )
        for index in range(1, 16)
    ) + tuple(
        CandidateSlot(
            slot_id=f"M1-R{index}",
            fault_type="data_drift",
            slot_kind="reserve",
            role="fault_directed",
            reserve_order=index,
            identity=template.identity,
        )
        for index in range(1, 10)
    )
    wrong_plan = CandidatePlan(
        schema_version="p2-candidate-plan/1",
        primary_planned=15,
        reserve_planned=9,
        slots=slots,
    )
    with pytest.raises(ContractViolation, match="frozen alpha grid"):
        validate_frozen_alpha_plan(wrong_plan)


def test_frozen_alpha_plan_rejects_seed_or_parameter_drift(bundle: _Bundle) -> None:
    slots = list(bundle.plan.slots)
    target = slots[0]
    identity_payload = target.identity.model_dump()
    identity_payload["seed"] = 999
    slots[0] = CandidateSlot(
        slot_id=target.slot_id,
        fault_type=target.fault_type,
        slot_kind=target.slot_kind,
        role=target.role,
        reserve_order=target.reserve_order,
        identity=FamilyIdentity(**identity_payload),  # type: ignore[arg-type]
    )
    tampered = CandidatePlan(
        schema_version="p2-candidate-plan/1",
        primary_planned=15,
        reserve_planned=9,
        slots=tuple(slots),
    )
    with pytest.raises(ContractViolation, match="role/seed contract"):
        validate_frozen_alpha_plan(tampered)


# --------------------------------------------------------------------------- #
# Technical rejection vs valid exclusion
# --------------------------------------------------------------------------- #


def test_technical_rejection_requires_a_reason_code() -> None:
    with pytest.raises(ValidationError, match="machine-readable reason"):
        TechnicalDispositionEntry(
            candidate_id=f"p2-candidate-{_HEX_A}", disposition="technical_rejected"
        )


def test_valid_candidate_must_not_carry_a_rejection_reason() -> None:
    with pytest.raises(ValidationError, match="must not carry"):
        TechnicalDispositionEntry(
            candidate_id=f"p2-candidate-{_HEX_A}",
            disposition="technically_valid",
            rejection_reason="invalid_parameter",
        )


def test_exclusion_requires_a_reason_and_forbids_family_membership() -> None:
    with pytest.raises(ValidationError, match="machine-readable reason"):
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            admission="excluded_valid",
        )
    with pytest.raises(ValidationError, match="must not claim family membership"):
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            admission="excluded_valid",
            exclusion_reason="evidence_leakage",
            case_family_id=f"p2-family-{_HEX_B}",
        )


def test_rejected_candidate_cannot_be_classified(bundle: _Bundle) -> None:
    entries = list(bundle.disposition.entries)
    entries[0] = TechnicalDispositionEntry(
        candidate_id=entries[0].candidate_id,
        disposition="technical_rejected",
        rejection_reason="provenance_hash_mismatch",
    )
    kwargs = bundle.flow_kwargs()
    kwargs["disposition"] = TechnicalDisposition(
        schema_version="p2-technical-disposition/1", entries=tuple(entries)
    )
    with pytest.raises(ContractViolation, match="only technically valid candidates are classified"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_rejected_candidate_cannot_appear_in_the_census(bundle: _Bundle) -> None:
    entries = list(bundle.disposition.entries)
    target = entries[0].candidate_id
    entries[0] = TechnicalDispositionEntry(
        candidate_id=target,
        disposition="technical_rejected",
        rejection_reason="metric_missing_or_non_finite",
    )
    kwargs = bundle.flow_kwargs()
    kwargs["disposition"] = TechnicalDisposition(
        schema_version="p2-technical-disposition/1", entries=tuple(entries)
    )
    kwargs["classifications"] = tuple(
        record for record in bundle.classifications if record.candidate_id != target
    )
    kwargs["admissions"] = tuple(
        record for record in bundle.admissions if record.candidate_id != target
    )
    with pytest.raises(ContractViolation, match="census must match the accepted set"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Control boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "expected", "reference", "observed", "outcome", "equivalence"),
    [
        (
            "designed_benign_control",
            "classified as benign_control",
            0.8,
            0.8,
            "benign",
            True,
        ),
        (
            "designed_improvement_control",
            "must not claim a family class",
            0.8,
            0.7,
            "regression",
            None,
        ),
    ],
)
def test_control_role_cannot_produce_an_eligible_failure(
    role: str,
    expected: str,
    reference: float,
    observed: float,
    outcome: str,
    equivalence: bool | None,
) -> None:
    with pytest.raises(ValidationError, match=expected):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role=role,  # type: ignore[arg-type]
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=reference,
            observed_value=observed,
            delta=observed - reference,
            threshold=0.01,
            measured_outcome=outcome,  # type: ignore[arg-type]
            family_class="eligible_failure",
            equivalence_checks_passed=equivalence,
        )


def _direction_violation(
    candidate_id: str,
    policy: str = "accuracy-regression/v1",
) -> ClassificationRecord:
    """An improvement control that made the metric worse: honest, but not a family."""

    return ClassificationRecord(
        schema_version="p2-classification-record/1",
        candidate_id=candidate_id,
        role="designed_improvement_control",
        eligibility_policy_version=policy,  # type: ignore[arg-type]
        primary_metric="accuracy",
        reference_value=0.8,
        observed_value=0.7,
        delta=-0.1,
        threshold=0.01,
        measured_outcome="regression",
        family_class=None,
        deviation_note="repair control moved the metric the wrong way",
    )


def test_direction_violation_is_recordable_without_a_family_class() -> None:
    record = _direction_violation(f"p2-candidate-{_HEX_A}")
    assert record.family_class is None
    assert record.measured_outcome == "regression"


def test_only_a_direction_violation_may_omit_the_family_class() -> None:
    with pytest.raises(ValidationError, match="may omit a family class"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.8,
            observed_value=0.7,
            delta=-0.1,
            threshold=0.01,
            measured_outcome="regression",
            family_class=None,
        )


def test_direction_violation_must_be_excluded_for_that_reason(bundle: _Bundle) -> None:
    target = next(
        item.candidate_id for item in bundle.executed if item.role == "designed_improvement_control"
    )
    classifications = tuple(
        _direction_violation(target, record.eligibility_policy_version)
        if record.candidate_id == target
        else record
        for record in bundle.classifications
    )

    accepted_kwargs = bundle.flow_kwargs()
    accepted_kwargs["classifications"] = classifications
    with pytest.raises(ContractViolation, match="cannot be accepted"):
        validate_candidate_flow(**accepted_kwargs)  # type: ignore[arg-type]

    wrong_reason = tuple(
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id=record.candidate_id,
            admission="excluded_valid",
            exclusion_reason="evidence_leakage",
        )
        if record.candidate_id == target
        else record
        for record in bundle.admissions
    )
    census = FamilyCensus(
        schema_version="p2-family-census/1",
        entries=tuple(entry for entry in bundle.census.entries if entry.candidate_id != target),
    )
    kwargs = bundle.flow_kwargs()
    kwargs["classifications"] = classifications
    kwargs["admissions"] = wrong_reason
    kwargs["census"] = census
    with pytest.raises(ContractViolation, match="control_direction_violation reason"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_benign_outcome_requires_a_declared_benign_control() -> None:
    with pytest.raises(ValidationError, match="only a declared benign control"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.8,
            observed_value=0.8,
            delta=0.0,
            threshold=0.01,
            measured_outcome="benign",
            family_class="benign_control",
        )


def test_benign_control_must_record_passing_equivalence_checks() -> None:
    with pytest.raises(ValidationError, match="equivalence checks"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="designed_benign_control",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.8,
            observed_value=0.8,
            delta=0.0,
            threshold=0.01,
            measured_outcome="benign",
            family_class="benign_control",
            equivalence_checks_passed=False,
        )


def test_outcome_and_family_class_must_agree() -> None:
    with pytest.raises(ValidationError, match="implies"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.8,
            observed_value=0.7,
            delta=-0.1,
            threshold=0.01,
            measured_outcome="regression",
            family_class="stable_control",
        )


def test_delta_must_equal_observed_minus_reference() -> None:
    with pytest.raises(ValidationError, match="delta must equal"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.8,
            observed_value=0.7,
            delta=-0.2,
            threshold=0.01,
            measured_outcome="regression",
            family_class="eligible_failure",
        )


@pytest.mark.parametrize(
    ("reference", "observed", "claimed_outcome", "claimed_class"),
    [
        (0.70, 0.90, "regression", "eligible_failure"),
        (0.90, 0.70, "improvement", "improvement_control"),
        (0.80, 0.805, "regression", "eligible_failure"),
    ],
)
def test_outcome_must_be_derived_from_delta_and_threshold(
    reference: float,
    observed: float,
    claimed_outcome: str,
    claimed_class: str,
) -> None:
    with pytest.raises(ValidationError, match="derived from delta"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=reference,
            observed_value=observed,
            delta=observed - reference,
            threshold=0.01,
            measured_outcome=claimed_outcome,  # type: ignore[arg-type]
            family_class=claimed_class,  # type: ignore[arg-type]
        )


def test_benign_control_cannot_hide_primary_metric_harm() -> None:
    with pytest.raises(ValidationError, match="stable band"):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="designed_benign_control",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=0.90,
            observed_value=0.10,
            delta=-0.80,
            threshold=0.01,
            measured_outcome="benign",
            family_class="benign_control",
            equivalence_checks_passed=True,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_classification_rejects_non_finite_metrics(value: float) -> None:
    with pytest.raises(ValidationError):
        ClassificationRecord(
            schema_version="p2-classification-record/1",
            candidate_id=f"p2-candidate-{_HEX_A}",
            role="fault_directed",
            eligibility_policy_version="accuracy-regression/v1",
            primary_metric="accuracy",
            reference_value=value,
            observed_value=0.7,
            delta=-0.1,
            threshold=0.01,
            measured_outcome="regression",
            family_class="eligible_failure",
        )


# --------------------------------------------------------------------------- #
# Membership swaps that preserve counts
# --------------------------------------------------------------------------- #


def test_swapped_census_membership_is_caught_even_though_counts_match(
    bundle: _Bundle,
) -> None:
    entries = list(bundle.census.entries)
    first_candidate = entries[0].candidate_id
    second_candidate = entries[1].candidate_id
    entries[0] = entries[0].model_copy(update={"candidate_id": second_candidate})
    entries[1] = entries[1].model_copy(update={"candidate_id": first_candidate})
    kwargs = bundle.flow_kwargs()
    kwargs["census"] = FamilyCensus(schema_version="p2-family-census/1", entries=tuple(entries))
    with pytest.raises(ContractViolation, match="family ID"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_missing_census_entry_is_caught(bundle: _Bundle) -> None:
    kwargs = bundle.flow_kwargs()
    kwargs["census"] = FamilyCensus(
        schema_version="p2-family-census/1", entries=bundle.census.entries[1:]
    )
    with pytest.raises(ContractViolation, match="census must match the accepted set"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_duplicate_candidate_id_in_census_is_rejected(bundle: _Bundle) -> None:
    first = bundle.census.entries[0]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        FamilyCensus(schema_version="p2-family-census/1", entries=(first, first))


def test_census_family_id_must_match_its_fingerprint() -> None:
    with pytest.raises(ValidationError, match="namespaced family fingerprint"):
        FamilyCensusEntry(
            case_family_id=f"p2-family-{_HEX_A}",
            candidate_id=f"p2-candidate-{_HEX_B}",
            fault_type="data_drift",
            family_class="eligible_failure",
            proposed_family_sha256=_HEX_C,
        )


def test_disposition_must_cover_exactly_the_executed_set(bundle: _Bundle) -> None:
    kwargs = bundle.flow_kwargs()
    kwargs["disposition"] = TechnicalDisposition(
        schema_version="p2-technical-disposition/1",
        entries=bundle.disposition.entries[1:],
    )
    with pytest.raises(ContractViolation, match="exactly the executed set"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_id", f"p2-candidate-{'e' * 64}", "candidate ID"),
        ("proposed_family_sha256", "e" * 64, "fingerprint"),
        ("dataset_sha256", "e" * 64, "dataset_sha256"),
        ("model_data_split_manifest_sha256", "e" * 64, "split manifest"),
    ],
)
def test_execution_must_bind_back_to_the_frozen_slot(
    bundle: _Bundle,
    field: str,
    value: str,
    message: str,
) -> None:
    first = bundle.executed[0]
    payload = first.model_dump()
    payload[field] = value
    tampered = ExecutedCandidate(**payload)  # type: ignore[arg-type]
    kwargs = bundle.flow_kwargs()
    kwargs["execution"] = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=(tampered, *bundle.executed[1:]),
        inactive_reserve_slot_ids=bundle.execution.inactive_reserve_slot_ids,
    )
    with pytest.raises(ContractViolation, match=message):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_classification_role_must_match_executed_role(bundle: _Bundle) -> None:
    index = next(
        index
        for index, item in enumerate(bundle.executed)
        if item.role == "designed_improvement_control"
    )
    original = bundle.classifications[index]
    payload = original.model_dump()
    payload["role"] = "fault_directed"
    classifications = list(bundle.classifications)
    classifications[index] = ClassificationRecord(**payload)  # type: ignore[arg-type]
    kwargs = bundle.flow_kwargs()
    kwargs["classifications"] = tuple(classifications)
    with pytest.raises(ContractViolation, match="role disagrees"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_classification_policy_must_match_mechanism(bundle: _Bundle) -> None:
    original = bundle.classifications[0]
    payload = original.model_dump()
    payload["eligibility_policy_version"] = "label-noise-impact/alpha-v1"
    classifications = list(bundle.classifications)
    classifications[0] = ClassificationRecord(**payload)  # type: ignore[arg-type]
    kwargs = bundle.flow_kwargs()
    kwargs["classifications"] = tuple(classifications)
    with pytest.raises(ContractViolation, match="policy disagrees"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_validator_revalidates_unsafe_model_copy_updates(bundle: _Bundle) -> None:
    classifications = list(bundle.classifications)
    classifications[0] = classifications[0].model_copy(
        update={
            "measured_outcome": "stable",
            "family_class": "stable_control",
        }
    )
    kwargs = bundle.flow_kwargs()
    kwargs["classifications"] = tuple(classifications)
    with pytest.raises(ValidationError, match="derived from delta"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Reserve activation
# --------------------------------------------------------------------------- #


def _activate(bundle: _Bundle, slot_id: str, *, reject_primary: str | None) -> dict[str, object]:
    slot = next(item for item in bundle.plan.slots if item.slot_id == slot_id)
    executed = (*bundle.executed, _executed_for(slot))
    inactive = tuple(
        item.slot_id
        for item in bundle.plan.slots
        if item.slot_kind == "reserve" and item.slot_id != slot_id
    )
    entries = []
    for entry in bundle.disposition.entries:
        if reject_primary is not None and entry.candidate_id == reject_primary:
            entries.append(
                TechnicalDispositionEntry(
                    candidate_id=entry.candidate_id,
                    disposition="technical_rejected",
                    rejection_reason="one_factor_violation",
                )
            )
        else:
            entries.append(entry)
    entries.append(
        TechnicalDispositionEntry(
            candidate_id=_executed_for(slot).candidate_id, disposition="technically_valid"
        )
    )
    return {
        "plan": bundle.plan,
        "execution": CandidateExecution(
            schema_version="p2-candidate-execution/1",
            executed=executed,
            inactive_reserve_slot_ids=inactive,
        ),
        "disposition": TechnicalDisposition(
            schema_version="p2-technical-disposition/1", entries=tuple(entries)
        ),
    }


def test_reserve_activation_requires_a_technical_rejection(bundle: _Bundle) -> None:
    kwargs = _activate(bundle, "M1-R1", reject_primary=None)
    with pytest.raises(ContractViolation, match="exceeds the technical-rejection budget"):
        validate_reserve_activation(**kwargs)  # type: ignore[arg-type]


def test_reserve_activation_is_allowed_after_a_technical_rejection(bundle: _Bundle) -> None:
    rejected = next(
        item.candidate_id for item in bundle.executed if item.fault_type == "data_drift"
    )
    kwargs = _activate(bundle, "M1-R1", reject_primary=rejected)
    validate_reserve_activation(**kwargs)  # type: ignore[arg-type]


def test_reserve_activation_must_follow_the_declared_order(bundle: _Bundle) -> None:
    rejected = next(
        item.candidate_id for item in bundle.executed if item.fault_type == "data_drift"
    )
    kwargs = _activate(bundle, "M1-R2", reject_primary=rejected)
    with pytest.raises(ContractViolation, match="without gaps"):
        validate_reserve_activation(**kwargs)  # type: ignore[arg-type]


def test_reserve_activation_must_stay_within_its_mechanism(bundle: _Bundle) -> None:
    rejected = next(
        item.candidate_id for item in bundle.executed if item.fault_type == "data_drift"
    )
    kwargs = _activate(bundle, "M2-R1", reject_primary=rejected)
    with pytest.raises(ContractViolation, match="exceeds the technical-rejection budget"):
        validate_reserve_activation(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Context cardinality
# --------------------------------------------------------------------------- #


def test_eligible_failure_requires_all_three_siblings(bundle: _Bundle) -> None:
    failure_family = next(
        entry.case_family_id
        for entry in bundle.census.entries
        if entry.family_class == "eligible_failure"
    )
    kept = tuple(
        entry
        for entry in bundle.contexts.entries
        if not (entry.case_family_id == failure_family and entry.evidence_condition == "noisy")
    )
    with pytest.raises(ContractViolation, match="must have contexts"):
        validate_context_cardinality(
            census=bundle.census,
            contexts=ContextCensus(schema_version="p2-context-census/1", entries=kept),
        )


def test_benign_control_must_have_no_context(bundle: _Bundle) -> None:
    benign_family = next(
        entry.case_family_id
        for entry in bundle.census.entries
        if entry.family_class == "benign_control"
    )
    extra = _context_entry(benign_family, "full")
    with pytest.raises(ContractViolation, match="must have contexts"):
        validate_context_cardinality(
            census=bundle.census,
            contexts=ContextCensus(
                schema_version="p2-context-census/1",
                entries=(*bundle.contexts.entries, extra),
            ),
        )


def test_stable_and_improvement_controls_have_exactly_one_context(bundle: _Bundle) -> None:
    counts: dict[str, int] = {}
    classes = {entry.case_family_id: entry.family_class for entry in bundle.census.entries}
    for entry in bundle.contexts.entries:
        counts[entry.case_family_id] = counts.get(entry.case_family_id, 0) + 1
    for family_id, family_class in classes.items():
        if family_class == "improvement_control":
            assert counts.get(family_id, 0) == 1
        if family_class == "eligible_failure":
            assert counts.get(family_id, 0) == 3
        if family_class == "benign_control":
            assert counts.get(family_id, 0) == 0


def test_context_referencing_an_unknown_family_is_rejected(bundle: _Bundle) -> None:
    stray = _context_entry(f"p2-family-{_HEX_A}", "full")
    with pytest.raises(ContractViolation, match="unknown family"):
        validate_context_cardinality(
            census=bundle.census,
            contexts=ContextCensus(
                schema_version="p2-context-census/1",
                entries=(*bundle.contexts.entries, stray),
            ),
        )


@pytest.mark.parametrize(
    "leaky_id",
    [
        "ctx-full-001",
        "ctx-noisy-001",
        "ctx-missing-key-001",
        "ctx-distractor-1",
        "ctx-control-1",
        "ctx-failure-1",
    ],
)
def test_context_id_must_be_an_opaque_namespaced_hash(leaky_id: str) -> None:
    payload = {"items": []}
    with pytest.raises(ValidationError, match="String should match pattern"):
        ContextEntry(
            diagnosis_context_id=leaky_id,
            case_family_id=f"p2-family-{_HEX_A}",
            evidence_condition="full",
            diagnosis_projection=payload,
            diagnosis_projection_sha256=canonical_sha256(payload),
        )


def test_duplicate_family_condition_pair_is_rejected() -> None:
    first = _context_entry(f"p2-family-{_HEX_A}", "full")
    with pytest.raises(ValidationError, match="diagnosis_context_id"):
        ContextCensus(schema_version="p2-context-census/1", entries=(first, first))


@pytest.mark.parametrize(
    "projection",
    [
        {"evidence_condition": "redacted"},
        {"items": [{"id": "distractor-comparison"}]},
        {"expected_behavior": "abstain"},
        {"metadata": {"cause_label": "label_noise"}},
        {"items": [{"id": "comparison", "value": "missing_key"}]},
    ],
)
def test_diagnosis_projection_rejects_evaluator_metadata(
    projection: dict[str, object],
) -> None:
    family_id = f"p2-family-{_HEX_A}"
    with pytest.raises(ValidationError, match="projection"):
        _context_entry(family_id, "full", projection=projection)


def test_diagnosis_projection_hash_must_bind_exact_content() -> None:
    family_id = f"p2-family-{_HEX_A}"
    projection = {"items": [{"id": "secondary-comparison", "value": "observable"}]}
    with pytest.raises(ValidationError, match="does not match"):
        ContextEntry(
            diagnosis_context_id=context_id_for(
                case_family_id=family_id,
                evidence_condition="full",
            ),
            case_family_id=family_id,
            evidence_condition="full",
            diagnosis_projection=projection,
            diagnosis_projection_sha256=_HEX_B,
        )


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #


def _duplicate_inputs(
    bundle: _Bundle,
    kind: str,
    *,
    first_admission: str = "excluded_valid",
    reason: str | None = None,
    measure_value: float | None = None,
) -> tuple[DuplicateAudit, tuple[AdmissionRecord, ...], CandidateExecution]:
    originals = tuple(item for item in bundle.executed if item.fault_type == "data_drift")[:2]
    first, second = originals
    if kind == "exact_identity":
        second = ExecutedCandidate(
            candidate_id=candidate_id_for(
                slot_id=second.slot_id,
                family_fingerprint=first.proposed_family_sha256,
            ),
            slot_id=second.slot_id,
            fault_type=second.fault_type,
            role=second.role,
            slot_kind=second.slot_kind,
            proposed_family_sha256=first.proposed_family_sha256,
            dataset_sha256=second.dataset_sha256,
            model_data_split_manifest_sha256=second.model_data_split_manifest_sha256,
        )
        measure = "identity_equality"
        first_basis = second_basis = first.proposed_family_sha256
    elif kind == "effective_intervention":
        measure = "effective_fingerprint_equality"
        first_basis = second_basis = _HEX_D
    else:
        measure = "total_variation_distance"
        first_basis, second_basis = _HEX_C, _HEX_D

    audit = DuplicateAudit(
        schema_version="p2-duplicate-audit/1",
        findings=(
            DuplicateFinding(
                kind=kind,  # type: ignore[arg-type]
                fault_type="data_drift",
                measure=measure,  # type: ignore[arg-type]
                candidate_id=first.candidate_id,
                duplicate_of_candidate_id=second.candidate_id,
                candidate_basis_sha256=first_basis,
                duplicate_of_basis_sha256=second_basis,
                measure_value=measure_value,
            ),
        ),
    )
    admissions = (
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id=first.candidate_id,
            admission=first_admission,  # type: ignore[arg-type]
            exclusion_reason=reason,  # type: ignore[arg-type]
            case_family_id=(
                None
                if first_admission == "excluded_valid"
                else f"p2-family-{first.proposed_family_sha256}"
            ),
            family_class=(None if first_admission == "excluded_valid" else "eligible_failure"),
        ),
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id=second.candidate_id,
            admission="accepted",
            case_family_id=f"p2-family-{second.proposed_family_sha256}",
            family_class="eligible_failure",
        ),
    )
    execution = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=(first, second),
        inactive_reserve_slot_ids=(),
    )
    return audit, admissions, execution


def test_exact_duplicate_must_be_excluded_with_the_matching_reason(
    bundle: _Bundle,
) -> None:
    audit, admissions, execution = _duplicate_inputs(
        bundle,
        "exact_identity",
        reason="exact_identity_duplicate",
    )
    validate_duplicate_audit(audit=audit, admissions=admissions, execution=execution)

    _, accepted, _ = _duplicate_inputs(
        bundle,
        "exact_identity",
        first_admission="accepted",
    )
    with pytest.raises(ContractViolation, match="must be excluded"):
        validate_duplicate_audit(audit=audit, admissions=accepted, execution=execution)


def test_effective_duplicate_must_be_excluded_with_the_matching_reason(
    bundle: _Bundle,
) -> None:
    audit, admissions, execution = _duplicate_inputs(
        bundle,
        "effective_intervention",
        reason="evidence_leakage",
    )
    with pytest.raises(ContractViolation, match="matching reason code"):
        validate_duplicate_audit(audit=audit, admissions=admissions, execution=execution)


def test_near_duplicate_is_disclosed_but_not_silently_excluded(
    bundle: _Bundle,
) -> None:
    audit, admissions, execution = _duplicate_inputs(
        bundle,
        "near_duplicate",
        first_admission="accepted",
        measure_value=0.01,
    )
    validate_duplicate_audit(audit=audit, admissions=admissions, execution=execution)

    _, excluded, _ = _duplicate_inputs(
        bundle,
        "near_duplicate",
        reason="effective_intervention_duplicate",
        measure_value=0.01,
    )
    with pytest.raises(ContractViolation, match="disclosed, not silently excluded"):
        validate_duplicate_audit(audit=audit, admissions=excluded, execution=execution)


def test_near_duplicate_finding_requires_the_frozen_threshold() -> None:
    with pytest.raises(ValidationError, match="below 0.02"):
        DuplicateFinding(
            kind="near_duplicate",
            fault_type="data_drift",
            measure="total_variation_distance",
            candidate_id=f"p2-candidate-{_HEX_A}",
            duplicate_of_candidate_id=f"p2-candidate-{_HEX_B}",
            candidate_basis_sha256=_HEX_C,
            duplicate_of_basis_sha256=_HEX_D,
            measure_value=0.02,
        )


def test_duplicate_finding_rejects_self_reference() -> None:
    with pytest.raises(ValidationError, match="cannot duplicate itself"):
        DuplicateFinding(
            kind="exact_identity",
            fault_type="data_drift",
            measure="identity_equality",
            candidate_id=f"p2-candidate-{_HEX_A}",
            duplicate_of_candidate_id=f"p2-candidate-{_HEX_A}",
            candidate_basis_sha256=_HEX_C,
            duplicate_of_basis_sha256=_HEX_C,
        )


def test_duplicate_exclusion_requires_a_matching_audit_finding(
    bundle: _Bundle,
) -> None:
    _, admissions, execution = _duplicate_inputs(
        bundle,
        "exact_identity",
        reason="exact_identity_duplicate",
    )
    with pytest.raises(ContractViolation, match="requires exactly one"):
        validate_duplicate_audit(
            audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
            admissions=admissions,
            execution=execution,
        )


def test_duplicate_audit_rejects_unknown_candidates(bundle: _Bundle) -> None:
    audit, _, execution = _duplicate_inputs(
        bundle,
        "effective_intervention",
        reason="effective_intervention_duplicate",
    )
    with pytest.raises(ContractViolation, match="unknown candidate"):
        validate_duplicate_audit(audit=audit, admissions=(), execution=execution)


# --------------------------------------------------------------------------- #
# Alpha validity report
# --------------------------------------------------------------------------- #


def _report(bundle: _Bundle, **overrides: object) -> AlphaValidityReport:
    classes = [entry.family_class for entry in bundle.census.entries]
    payload: dict[str, object] = {
        "schema_version": "p2-alpha-validity-report/1",
        "primary_planned": 15,
        "reserve_planned": 9,
        "planned_total": 24,
        "executed": len(bundle.executed),
        "activated_reserve": 0,
        "inactive_reserve": 9,
        "technically_valid": len(bundle.executed),
        "technical_rejected": 0,
        "accepted": len(bundle.census.entries),
        "excluded_valid": 0,
        "eligible_failure": classes.count("eligible_failure"),
        "stable_control": classes.count("stable_control"),
        "improvement_control": classes.count("improvement_control"),
        "benign_control": classes.count("benign_control"),
        "context_count": len(bundle.contexts.entries),
        "mechanism_coverage_passed": True,
        "gate_status": "pass",
        "deviation_note": None,
    }
    payload.update(overrides)
    return AlphaValidityReport(**payload)  # type: ignore[arg-type]


def test_report_reconciles_with_the_underlying_entries(bundle: _Bundle) -> None:
    validate_alpha_report(
        report=_report(bundle),
        plan=bundle.plan,
        execution=bundle.execution,
        disposition=bundle.disposition,
        admissions=bundle.admissions,
        census=bundle.census,
        contexts=bundle.contexts,
    )


def test_report_rejects_an_inflated_accepted_count(bundle: _Bundle) -> None:
    with pytest.raises(ValidationError, match="technically_valid must equal"):
        _report(bundle, accepted=99)


def test_report_rejects_a_context_count_chosen_independently(bundle: _Bundle) -> None:
    with pytest.raises(ValidationError, match="context_count must be derived"):
        _report(bundle, context_count=60)


def test_report_rejects_unreconciled_inactive_reserve(bundle: _Bundle) -> None:
    with pytest.raises(ValidationError, match="inactive_reserve"):
        _report(bundle, inactive_reserve=4)


def test_validator_recomputes_counts_from_entries(bundle: _Bundle) -> None:
    """A report can be arithmetically self-consistent yet disagree with reality."""

    shrunk = FamilyCensus(schema_version="p2-family-census/1", entries=bundle.census.entries[:-1])
    with pytest.raises(ContractViolation, match="recomputes to"):
        validate_alpha_report(
            report=_report(bundle),
            plan=bundle.plan,
            execution=bundle.execution,
            disposition=bundle.disposition,
            admissions=bundle.admissions,
            census=shrunk,
            contexts=bundle.contexts,
        )


def test_validator_recomputes_mechanism_coverage_and_gate_status(bundle: _Bundle) -> None:
    false_report = _report(
        bundle,
        mechanism_coverage_passed=False,
        gate_status="fail",
        deviation_note="claimed mechanism coverage failure",
    )
    with pytest.raises(ContractViolation, match="mechanism_coverage_passed"):
        validate_alpha_report(
            report=false_report,
            plan=bundle.plan,
            execution=bundle.execution,
            disposition=bundle.disposition,
            admissions=bundle.admissions,
            census=bundle.census,
            contexts=bundle.contexts,
        )


# --------------------------------------------------------------------------- #
# Strictness
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CandidatePlan(
            schema_version="p2-candidate-plan/1",
            primary_planned=0,
            reserve_planned=0,
            slots=(),
            extra="x",  # type: ignore[call-arg]
        ),
        lambda: TechnicalDisposition(
            schema_version="p2-technical-disposition/1",
            entries=(),
            extra="x",  # type: ignore[call-arg]
        ),
        lambda: FamilyCensus(
            schema_version="p2-family-census/1",
            entries=(),
            extra="x",  # type: ignore[call-arg]
        ),
        lambda: ContextCensus(
            schema_version="p2-context-census/1",
            entries=(),
            extra="x",  # type: ignore[call-arg]
        ),
        lambda: DuplicateAudit(
            schema_version="p2-duplicate-audit/1",
            findings=(),
            extra="x",  # type: ignore[call-arg]
        ),
    ],
)
def test_every_record_rejects_extra_fields(factory: object) -> None:
    with pytest.raises(ValidationError):
        factory()  # type: ignore[operator]


def test_plan_schema_version_is_pinned() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        CandidatePlan(
            schema_version="p2-candidate-plan/2",  # type: ignore[arg-type]
            primary_planned=0,
            reserve_planned=0,
            slots=(),
        )


def test_disposition_schema_version_is_pinned() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        TechnicalDisposition(
            schema_version="p2-technical-disposition/2",  # type: ignore[arg-type]
            entries=(),
        )


def test_census_schema_version_is_pinned() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        FamilyCensus(
            schema_version="p2-family-census/2",  # type: ignore[arg-type]
            entries=(),
        )


def test_plan_rejects_a_declared_count_that_disagrees_with_its_slots() -> None:
    with pytest.raises(ValidationError, match="primary_planned does not match"):
        CandidatePlan(
            schema_version="p2-candidate-plan/1",
            primary_planned=3,
            reserve_planned=0,
            slots=(),
        )


def test_alpha_report_requires_the_frozen_grid_size(bundle: _Bundle) -> None:
    shrunk = CandidatePlan(
        schema_version="p2-candidate-plan/1",
        primary_planned=14,
        reserve_planned=9,
        slots=tuple(slot for slot in bundle.plan.slots if slot.slot_id != "M3-B1"),
    )
    with pytest.raises(ContractViolation, match="frozen alpha grid"):
        validate_alpha_report(
            report=_report(bundle),
            plan=shrunk,
            execution=bundle.execution,
            disposition=bundle.disposition,
            admissions=bundle.admissions,
            census=bundle.census,
            contexts=bundle.contexts,
        )


def _artifacts(bundle: _Bundle) -> P2ContractArtifacts:
    return P2ContractArtifacts(
        plan=bundle.plan,
        execution=bundle.execution,
        disposition=bundle.disposition,
        classifications=ClassificationLedger(
            schema_version="p2-classification-ledger/1",
            entries=bundle.classifications,
        ),
        admissions=AdmissionLedger(
            schema_version="p2-admission-ledger/1",
            entries=bundle.admissions,
        ),
        census=bundle.census,
        contexts=bundle.contexts,
        duplicate_audit=DuplicateAudit(
            schema_version="p2-duplicate-audit/1",
            findings=(),
        ),
        report=_report(bundle),
    )


def test_authoritative_bundle_validator_runs_every_contract(bundle: _Bundle) -> None:
    artifacts = _artifacts(bundle)
    validate_contract_bundle(
        plan=artifacts.plan,
        execution=artifacts.execution,
        disposition=artifacts.disposition,
        classifications=artifacts.classifications.entries,
        admissions=artifacts.admissions.entries,
        census=artifacts.census,
        contexts=artifacts.contexts,
        duplicate_audit=artifacts.duplicate_audit,
        report=artifacts.report,
    )


def test_contract_store_is_complete_idempotent_and_byte_reproducible(
    bundle: _Bundle,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(bundle)
    first = tmp_path / "store-a"
    second = tmp_path / "store-b"
    manifest_a = save_contract_store(artifacts, first)
    manifest_b = save_contract_store(artifacts, second)
    assert save_contract_store(artifacts, first) == manifest_a
    assert manifest_a == manifest_b
    assert load_contract_store(first).artifacts == artifacts
    assert {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }


def test_contract_store_rejects_tamper_extra_file_and_conflicting_overwrite(
    bundle: _Bundle,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(bundle)
    tampered = tmp_path / "tampered"
    save_contract_store(artifacts, tampered)
    plan_path = tampered / "candidate-plan.json"
    plan_path.write_bytes(plan_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch|canonically encoded"):
        load_contract_store(tampered)

    extra = tmp_path / "extra"
    save_contract_store(artifacts, extra)
    (extra / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="file set differs"):
        load_contract_store(extra)

    conflict = tmp_path / "conflict"
    save_contract_store(artifacts, conflict)
    changed_entries = list(bundle.disposition.entries)
    first = changed_entries[0]
    changed_entries[0] = TechnicalDispositionEntry(
        candidate_id=first.candidate_id,
        disposition=first.disposition,
        rejection_reason=first.rejection_reason,
        detail="different but valid detail",
    )
    changed = P2ContractArtifacts(
        **{
            **artifacts.__dict__,
            "disposition": TechnicalDisposition(
                schema_version="p2-technical-disposition/1",
                entries=tuple(changed_entries),
            ),
        }
    )
    with pytest.raises(FileExistsError, match="non-identical"):
        save_contract_store(changed, conflict)


def test_contract_store_rejects_symlink_and_manifest_path_traversal(
    bundle: _Bundle,
    tmp_path: Path,
    make_symlink,
) -> None:
    artifacts = _artifacts(bundle)
    linked = tmp_path / "linked"
    save_contract_store(artifacts, linked)
    real = linked / "candidate-plan.json"
    moved = linked / "candidate-plan.real.json"
    real.rename(moved)
    make_symlink(real, moved.name)
    with pytest.raises(ValueError, match="symlink"):
        load_contract_store(linked)

    traversal = tmp_path / "traversal"
    save_contract_store(artifacts, traversal)
    manifest_path = traversal / "store-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][0]["relative_path"] = "../escape.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="fixed path"):
        load_contract_store(traversal)


def test_contract_store_rejects_local_paths_in_artifact_details(
    bundle: _Bundle,
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(bundle)
    entries = list(bundle.disposition.entries)
    first = entries[0]
    entries[0] = TechnicalDispositionEntry(
        candidate_id=first.candidate_id,
        disposition=first.disposition,
        detail="/srv/private/output.json",
    )
    unsafe = P2ContractArtifacts(
        **{
            **artifacts.__dict__,
            "disposition": TechnicalDisposition(
                schema_version="p2-technical-disposition/1",
                entries=tuple(entries),
            ),
        }
    )
    with pytest.raises(ValueError, match="local absolute path"):
        save_contract_store(unsafe, tmp_path / "unsafe")


def test_ids_must_use_the_phase_two_namespace() -> None:
    with pytest.raises(ValidationError):
        TechnicalDispositionEntry(
            candidate_id=f"p1-family-{_HEX_A}", disposition="technically_valid"
        )


# --------------------------------------------------------------------------- #
# Fail-closed lifecycle branch coverage
# --------------------------------------------------------------------------- #


def _flow_without_candidate(bundle: _Bundle, candidate_id: str) -> dict[str, object]:
    """Remove one candidate consistently so a later cross-artifact gate is isolated."""

    executed = tuple(item for item in bundle.executed if item.candidate_id != candidate_id)
    return {
        "plan": bundle.plan,
        "execution": CandidateExecution(
            schema_version="p2-candidate-execution/1",
            executed=executed,
            inactive_reserve_slot_ids=bundle.execution.inactive_reserve_slot_ids,
        ),
        "disposition": TechnicalDisposition(
            schema_version="p2-technical-disposition/1",
            entries=tuple(
                item for item in bundle.disposition.entries if item.candidate_id != candidate_id
            ),
        ),
        "classifications": tuple(
            item for item in bundle.classifications if item.candidate_id != candidate_id
        ),
        "admissions": tuple(item for item in bundle.admissions if item.candidate_id != candidate_id),
        "census": FamilyCensus(
            schema_version="p2-family-census/1",
            entries=tuple(item for item in bundle.census.entries if item.candidate_id != candidate_id),
        ),
    }


@pytest.mark.parametrize(
    ("slot_id", "identity_update", "expected"),
    [
        ("M1-F1", {"intervention_type": "empirical_distribution_resampling_control"}, "categorical_distribution_shift"),
        ("M1-B1", {"intervention_type": "categorical_distribution_shift"}, "empirical resampling"),
        ("M2-F1", {"intervention_type": "training_target_label_repair"}, "intervention_type"),
        ("M3-F1", {"intervention_type": "inference_encoder_mapping_repair"}, "intervention_type"),
    ],
)
def test_frozen_slot_rejects_role_specific_intervention_substitution(
    slot_id: str,
    identity_update: dict[str, object],
    expected: str,
) -> None:
    slot = _slot(slot_id)
    forged = slot.model_copy(update={"identity": slot.identity.model_copy(update=identity_update)})
    with pytest.raises(ContractViolation, match=expected):
        validate_frozen_alpha_plan(
            CandidatePlan(
                schema_version="p2-candidate-plan/1",
                primary_planned=15,
                reserve_planned=9,
                slots=tuple(forged if item.slot_id == slot_id else item for item in _build_plan().slots),
            )
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("fault_type", "label_noise", "changed fault_type"),
        ("role", "designed_benign_control", "changed role"),
        ("slot_kind", "reserve", "changed slot_kind"),
    ],
)
def test_candidate_flow_rejects_execution_metadata_rebinding(
    bundle: _Bundle,
    field: str,
    value: str,
    expected: str,
) -> None:
    executed = list(bundle.executed)
    executed[0] = executed[0].model_copy(update={field: value})
    kwargs = bundle.flow_kwargs()
    kwargs["execution"] = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=tuple(executed),
        inactive_reserve_slot_ids=bundle.execution.inactive_reserve_slot_ids,
    )
    with pytest.raises(ContractViolation, match=expected):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_candidate_flow_rejects_unknown_execution_slot(bundle: _Bundle) -> None:
    executed = list(bundle.executed)
    executed[0] = executed[0].model_copy(update={"slot_id": "M1-F9"})
    kwargs = bundle.flow_kwargs()
    kwargs["execution"] = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=tuple(executed),
        inactive_reserve_slot_ids=bundle.execution.inactive_reserve_slot_ids,
    )
    with pytest.raises(ContractViolation, match="not in the frozen plan"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_candidate_flow_requires_every_primary_even_if_ledgers_reconcile(bundle: _Bundle) -> None:
    target = bundle.executed[0].candidate_id
    with pytest.raises(ContractViolation, match="every primary slot must execute"):
        validate_candidate_flow(**_flow_without_candidate(bundle, target))  # type: ignore[arg-type]


def test_candidate_flow_rejects_primary_declared_as_inactive(bundle: _Bundle) -> None:
    target = bundle.executed[0].candidate_id
    kwargs = _flow_without_candidate(bundle, target)
    execution = kwargs["execution"]
    assert isinstance(execution, CandidateExecution)
    kwargs["execution"] = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=execution.executed,
        inactive_reserve_slot_ids=(*execution.inactive_reserve_slot_ids, "M1-F1"),
    )
    with pytest.raises(ContractViolation, match="only reserve slots may be inactive"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_candidate_flow_rejects_duplicate_classification_and_admission_records(
    bundle: _Bundle,
) -> None:
    kwargs = bundle.flow_kwargs()
    kwargs["classifications"] = (*bundle.classifications, bundle.classifications[0])
    with pytest.raises(ContractViolation, match="at most one classification"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]

    kwargs = bundle.flow_kwargs()
    kwargs["admissions"] = (*bundle.admissions, bundle.admissions[0])
    with pytest.raises(ContractViolation, match="at most one admission"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_candidate_flow_rejects_admission_class_disagreement(bundle: _Bundle) -> None:
    admissions = list(bundle.admissions)
    admissions[0] = admissions[0].model_copy(update={"family_class": "stable_control"})
    kwargs = bundle.flow_kwargs()
    kwargs["admissions"] = tuple(admissions)
    with pytest.raises(ContractViolation, match="admission family class disagrees"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_candidate_flow_rejects_direction_reason_on_classified_family(bundle: _Bundle) -> None:
    admissions = list(bundle.admissions)
    admissions[0] = AdmissionRecord(
        schema_version="p2-admission-record/1",
        candidate_id=admissions[0].candidate_id,
        admission="excluded_valid",
        exclusion_reason="control_direction_violation",
    )
    kwargs = bundle.flow_kwargs()
    kwargs["admissions"] = tuple(admissions)
    kwargs["census"] = FamilyCensus(
        schema_version="p2-family-census/1",
        entries=tuple(
            item for item in bundle.census.entries if item.candidate_id != admissions[0].candidate_id
        ),
    )
    with pytest.raises(ContractViolation, match="only valid when classification has no"):
        validate_candidate_flow(**kwargs)  # type: ignore[arg-type]


def test_duplicate_audit_rejects_duplicate_admissions(bundle: _Bundle) -> None:
    execution = CandidateExecution(
        schema_version="p2-candidate-execution/1",
        executed=bundle.executed[:2],
        inactive_reserve_slot_ids=(),
    )
    admissions = (bundle.admissions[0], bundle.admissions[0])
    with pytest.raises(ContractViolation, match="unique admission candidate IDs"):
        validate_duplicate_audit(
            audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
            admissions=admissions,
            execution=execution,
        )


def test_duplicate_audit_rejects_admission_for_unexecuted_candidate(bundle: _Bundle) -> None:
    with pytest.raises(ContractViolation, match="admissions for unexecuted"):
        validate_duplicate_audit(
            audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
            admissions=(bundle.admissions[0],),
            execution=CandidateExecution(
                schema_version="p2-candidate-execution/1",
                executed=(),
                inactive_reserve_slot_ids=(),
            ),
        )


@pytest.mark.parametrize(
    ("family_id", "condition", "expected"),
    [
        ("p1-family-" + _HEX_A, "full", "Phase 2 family namespace"),
        ("p2-family-" + _HEX_A, "hidden", "unknown evidence condition"),
    ],
)
def test_context_identity_rejects_foreign_namespace_and_condition(
    family_id: str, condition: str, expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        context_id_for(case_family_id=family_id, evidence_condition=condition)


def test_candidate_slot_reserve_order_is_bound_to_slot_kind() -> None:
    primary = _slot("M1-F1")
    reserve = _slot("M1-R1")
    with pytest.raises(ValidationError, match="primary slots must not"):
        CandidateSlot(**{**primary.model_dump(), "reserve_order": 1})
    with pytest.raises(ValidationError, match="reserve slots must declare"):
        CandidateSlot(**{**reserve.model_dump(), "reserve_order": None})


def test_candidate_plan_rejects_reserve_count_and_order_drift() -> None:
    plan = _build_plan()
    with pytest.raises(ValidationError, match="reserve_planned"):
        CandidatePlan(
            schema_version="p2-candidate-plan/1",
            primary_planned=plan.primary_planned,
            reserve_planned=8,
            slots=plan.slots,
        )
    reserves = [slot for slot in plan.slots if slot.slot_kind == "reserve"]
    target = reserves[1]
    forged = target.model_copy(update={"reserve_order": 4})
    with pytest.raises(ValidationError, match="gapless sequence"):
        CandidatePlan(
            schema_version="p2-candidate-plan/1",
            primary_planned=15,
            reserve_planned=9,
            slots=tuple(forged if slot.slot_id == target.slot_id else slot for slot in plan.slots),
        )


def test_candidate_execution_rejects_executed_inactive_overlap(bundle: _Bundle) -> None:
    with pytest.raises(ValidationError, match="both executed and inactive"):
        CandidateExecution(
            schema_version="p2-candidate-execution/1",
            executed=bundle.executed,
            inactive_reserve_slot_ids=(bundle.executed[0].slot_id,),
        )


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"reference_value": 1.1}, "reference_value must lie"),
        ({"observed_value": -0.1}, "observed_value must lie"),
        ({"delta": 1.1}, "delta must lie"),
        ({"threshold": 0.02}, "threshold 0.01"),
        ({"equivalence_checks_passed": True}, "reserved for benign controls"),
    ],
)
def test_classification_rejects_invalid_metric_and_control_fields(
    bundle: _Bundle, updates: dict[str, object], expected: str
) -> None:
    record = bundle.classifications[0]
    with pytest.raises(ValidationError, match=expected):
        ClassificationRecord(**{**record.model_dump(), **updates})


def test_accepted_admission_requires_family_and_forbids_exclusion_reason() -> None:
    with pytest.raises(ValidationError, match="family ID and class"):
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id="p2-candidate-" + _HEX_A,
            admission="accepted",
        )
    with pytest.raises(ValidationError, match="must not carry an exclusion"):
        AdmissionRecord(
            schema_version="p2-admission-record/1",
            candidate_id="p2-candidate-" + _HEX_A,
            admission="accepted",
            case_family_id="p2-family-" + _HEX_A,
            family_class="eligible_failure",
            exclusion_reason="evidence_leakage",
        )


def test_context_entry_rejects_unbound_id_and_noncanonical_json() -> None:
    family_id = "p2-family-" + _HEX_A
    projection = {1: "not-a-string-key"}
    with pytest.raises(ValidationError, match="valid string"):
        ContextEntry(
            diagnosis_context_id=context_id_for(
                case_family_id=family_id, evidence_condition="full"
            ),
            case_family_id=family_id,
            evidence_condition="full",
            diagnosis_projection=projection,  # type: ignore[arg-type]
            diagnosis_projection_sha256=_HEX_A,
        )
    valid_projection = {"items": []}
    with pytest.raises(ValidationError, match="must bind family"):
        ContextEntry(
            diagnosis_context_id="p2-context-" + _HEX_B,
            case_family_id=family_id,
            evidence_condition="full",
            diagnosis_projection=valid_projection,
            diagnosis_projection_sha256=canonical_sha256(valid_projection),
        )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "kind": "exact_identity",
                "measure": "jaccard_similarity",
                "candidate_basis_sha256": _HEX_C,
                "duplicate_of_basis_sha256": _HEX_C,
                "measure_value": None,
            },
            "identity_equality",
        ),
        (
            {
                "kind": "effective_intervention",
                "measure": "effective_fingerprint_equality",
                "candidate_basis_sha256": _HEX_C,
                "duplicate_of_basis_sha256": _HEX_D,
                "measure_value": None,
            },
            "equal intervention fingerprints",
        ),
        (
            {
                "kind": "near_duplicate",
                "measure": "total_variation_distance",
                "candidate_basis_sha256": _HEX_C,
                "duplicate_of_basis_sha256": _HEX_D,
                "measure_value": None,
            },
            "record its measure_value",
        ),
        (
            {
                "kind": "near_duplicate",
                "measure": "jaccard_similarity",
                "candidate_basis_sha256": _HEX_C,
                "duplicate_of_basis_sha256": _HEX_D,
                "measure_value": 0.89,
            },
            "at least 0.90",
        ),
    ],
)
def test_duplicate_finding_enforces_kind_specific_measure_contract(
    payload: dict[str, object], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        DuplicateFinding(
            fault_type="label_noise",
            candidate_id="p2-candidate-" + _HEX_A,
            duplicate_of_candidate_id="p2-candidate-" + _HEX_B,
            **payload,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"planned_total": 25}, "primary_planned"),
        ({"executed": 14}, "primary_planned.*activated_reserve"),
        ({"technically_valid": 14}, "technically_valid.*technical_rejected"),
        ({"accepted": 14}, "accepted.*excluded_valid"),
        ({"gate_status": "fail", "deviation_note": None}, "must be 'pass'"),
        ({"gate_status": "pass", "deviation_note": "unexpected"}, "must not carry"),
    ],
)
def test_alpha_report_rejects_arithmetic_and_status_forgery(
    bundle: _Bundle, updates: dict[str, object], expected: str
) -> None:
    payload = _report(bundle).model_dump()
    payload.update(updates)
    with pytest.raises(ValidationError, match=expected):
        AlphaValidityReport(**payload)  # type: ignore[arg-type]
