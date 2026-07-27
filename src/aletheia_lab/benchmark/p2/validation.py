"""Cross-artifact validation for the Phase 2 candidate lifecycle.

Every check here recomputes its answer from entry identifiers. Comparing the
declared counts against each other would pass for a report whose totals are
arithmetically consistent but whose membership has been swapped, and that is
precisely the tampering this layer exists to catch.

The validators are fail-closed: an unknown identifier, a candidate appearing in
two buckets, or a family class that its role cannot produce raises rather than
being reported as a warning.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Final, NoReturn, TypeVar

from pydantic import BaseModel

from aletheia_lab.benchmark.p2.contracts import (
    CONTEXT_CARDINALITY,
    AdmissionRecord,
    AlphaValidityReport,
    CandidateExecution,
    CandidatePlan,
    ClassificationRecord,
    ContextCensus,
    DuplicateAudit,
    FamilyCensus,
    TechnicalDisposition,
)
from aletheia_lab.benchmark.p2.identity import (
    DataDriftParameters,
    LabelNoiseParameters,
    PreprocessingBugParameters,
    candidate_id_for,
    proposed_family_sha256,
)

PRIMARY_SLOT_COUNT: int = 15
RESERVE_SLOT_COUNT: int = 9
PLANNED_SLOT_TOTAL: int = PRIMARY_SLOT_COUNT + RESERVE_SLOT_COUNT

_POLICY_BY_FAULT: Final[dict[str, str]] = {
    "data_drift": "accuracy-regression/v1",
    "label_noise": "label-noise-impact/alpha-v1",
    "preprocessing_bug": "preprocessing-bug-impact/alpha-v1",
}


@dataclass(frozen=True)
class _FrozenSlotSpec:
    fault_type: str
    slot_kind: str
    role: str
    seed: int
    reserve_order: int | None = None


_FROZEN_ALPHA_SLOTS: Final[dict[str, _FrozenSlotSpec]] = {
    "M1-F1": _FrozenSlotSpec("data_drift", "primary", "fault_directed", 1),
    "M1-F2": _FrozenSlotSpec("data_drift", "primary", "fault_directed", 3),
    "M1-S1": _FrozenSlotSpec("data_drift", "primary", "fault_directed", 5),
    "M1-I1": _FrozenSlotSpec("data_drift", "primary", "fault_directed", 4),
    "M1-B1": _FrozenSlotSpec("data_drift", "primary", "designed_benign_control", 105),
    "M1-R1": _FrozenSlotSpec("data_drift", "reserve", "fault_directed", 2, 1),
    "M1-R2": _FrozenSlotSpec("data_drift", "reserve", "fault_directed", 106, 2),
    "M1-R3": _FrozenSlotSpec("data_drift", "reserve", "fault_directed", 107, 3),
    "M2-F1": _FrozenSlotSpec("label_noise", "primary", "fault_directed", 201),
    "M2-F2": _FrozenSlotSpec("label_noise", "primary", "fault_directed", 202),
    "M2-F3": _FrozenSlotSpec("label_noise", "primary", "fault_directed", 203),
    "M2-I1": _FrozenSlotSpec("label_noise", "primary", "designed_improvement_control", 204),
    "M2-B1": _FrozenSlotSpec("label_noise", "primary", "designed_benign_control", 205),
    "M2-R1": _FrozenSlotSpec("label_noise", "reserve", "fault_directed", 206, 1),
    "M2-R2": _FrozenSlotSpec("label_noise", "reserve", "fault_directed", 207, 2),
    "M2-R3": _FrozenSlotSpec("label_noise", "reserve", "fault_directed", 208, 3),
    "M3-F1": _FrozenSlotSpec("preprocessing_bug", "primary", "fault_directed", 301),
    "M3-F2": _FrozenSlotSpec("preprocessing_bug", "primary", "fault_directed", 302),
    "M3-F3": _FrozenSlotSpec("preprocessing_bug", "primary", "fault_directed", 303),
    "M3-I1": _FrozenSlotSpec("preprocessing_bug", "primary", "designed_improvement_control", 304),
    "M3-B1": _FrozenSlotSpec("preprocessing_bug", "primary", "designed_benign_control", 305),
    "M3-R1": _FrozenSlotSpec("preprocessing_bug", "reserve", "fault_directed", 306, 1),
    "M3-R2": _FrozenSlotSpec("preprocessing_bug", "reserve", "fault_directed", 307, 2),
    "M3-R3": _FrozenSlotSpec("preprocessing_bug", "reserve", "fault_directed", 308, 3),
}

_M1_TARGETS: Final[dict[str, dict[str, float]]] = {
    "M1-F1": {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
    "M1-F2": {"Month-to-month": 0.90, "One year": 0.06, "Two year": 0.04},
    "M1-S1": {"Month-to-month": 0.60, "One year": 0.20, "Two year": 0.20},
    "M1-I1": {"Month-to-month": 0.40, "One year": 0.30, "Two year": 0.30},
    "M1-R1": {"Month-to-month": 0.70, "One year": 0.18, "Two year": 0.12},
    "M1-R2": {"Month-to-month": 0.75, "One year": 0.15, "Two year": 0.10},
    "M1-R3": {"Month-to-month": 0.50, "One year": 0.25, "Two year": 0.25},
}

_M2_RATES: Final[dict[str, float]] = {
    "M2-F1": 0.01,
    "M2-F2": 0.05,
    "M2-F3": 0.20,
    "M2-I1": 0.20,
    "M2-B1": 0.0,
    "M2-R1": 0.025,
    "M2-R2": 0.10,
    "M2-R3": 0.30,
}

_M3_RANKS: Final[dict[str, tuple[int | None, int | None]]] = {
    "M3-F1": (3, 2),
    "M3-F2": (2, 1),
    "M3-F3": (1, 3),
    "M3-I1": (1, 3),
    "M3-B1": (None, None),
    "M3-R1": (3, 1),
    "M3-R2": (2, 3),
    "M3-R3": (1, 2),
}


class ContractViolation(ValueError):
    """Raised when Phase 2 artifacts disagree with one another."""


def _fail(message: str) -> NoReturn:
    raise ContractViolation(message)


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run schema validators even when callers used unsafe ``model_copy``."""

    return type(model).model_validate(model.model_dump())


def validate_frozen_alpha_plan(plan: CandidatePlan) -> None:
    """Reject any outcome-driven drift from the frozen alpha grid."""

    plan = _revalidated(plan)
    by_id = {slot.slot_id: slot for slot in plan.slots}
    expected_ids = set(_FROZEN_ALPHA_SLOTS)
    actual_ids = set(by_id)
    if actual_ids != expected_ids:
        _fail(
            "alpha slot IDs differ from the frozen alpha grid; "
            f"missing={sorted(expected_ids - actual_ids)}; "
            f"extra={sorted(actual_ids - expected_ids)}"
        )
    if plan.primary_planned != PRIMARY_SLOT_COUNT or plan.reserve_planned != RESERVE_SLOT_COUNT:
        _fail("the frozen alpha plan must contain exactly 15 primary and 9 reserve slots")

    for slot_id, expected in _FROZEN_ALPHA_SLOTS.items():
        slot = by_id[slot_id]
        actual = (
            slot.fault_type,
            slot.slot_kind,
            slot.role,
            slot.identity.seed,
            slot.reserve_order,
        )
        wanted = (
            expected.fault_type,
            expected.slot_kind,
            expected.role,
            expected.seed,
            expected.reserve_order,
        )
        if actual != wanted:
            _fail(f"slot {slot_id} differs from the frozen alpha role/seed contract")

        parameters = slot.identity.canonical_intervention_parameters
        if slot_id.startswith("M1-"):
            if not isinstance(parameters, DataDriftParameters):
                _fail(f"slot {slot_id} must use data-drift parameters")
            if parameters.feature != "Contract":
                _fail(f"slot {slot_id} must target Contract")
            if slot_id == "M1-B1":
                if slot.identity.intervention_type != "empirical_distribution_resampling_control":
                    _fail("M1-B1 must use the empirical resampling control")
            else:
                if slot.identity.intervention_type != "categorical_distribution_shift":
                    _fail(f"slot {slot_id} must use categorical_distribution_shift")
                if parameters.target_distribution != _M1_TARGETS[slot_id]:
                    _fail(f"slot {slot_id} target distribution differs from the alpha contract")
        elif slot_id.startswith("M2-"):
            if not isinstance(parameters, LabelNoiseParameters):
                _fail(f"slot {slot_id} must use label-noise parameters")
            if not math.isclose(
                parameters.flip_rate, _M2_RATES[slot_id], rel_tol=0.0, abs_tol=1e-12
            ):
                _fail(f"slot {slot_id} flip_rate differs from the alpha contract")
            if (
                parameters.flip_direction != "symmetric"
                or parameters.selection_policy != "seeded_record_hash"
                or parameters.scope != "train"
            ):
                _fail(f"slot {slot_id} label-noise semantics differ from the alpha contract")
            expected_intervention = {
                "M2-I1": "training_target_label_repair",
                "M2-B1": "target_label_serialization_roundtrip",
            }.get(slot_id, "training_target_label_corruption")
            if slot.identity.intervention_type != expected_intervention:
                _fail(f"slot {slot_id} intervention_type differs from the alpha contract")
        else:
            if not isinstance(parameters, PreprocessingBugParameters):
                _fail(f"slot {slot_id} must use preprocessing-bug parameters")
            if parameters.target_feature != "Contract" or parameters.mode != "inference_only":
                _fail(f"slot {slot_id} preprocessing target/mode differs from the alpha contract")
            if (parameters.source_rank, parameters.mapped_rank) != _M3_RANKS[slot_id]:
                _fail(f"slot {slot_id} category-rank mapping differs from the alpha contract")
            expected_intervention = {
                "M3-I1": "inference_encoder_mapping_repair",
                "M3-B1": "name_bound_column_order_permutation",
            }.get(slot_id, "inference_encoder_mapping_mismatch")
            if slot.identity.intervention_type != expected_intervention:
                _fail(f"slot {slot_id} intervention_type differs from the alpha contract")


def validate_candidate_flow(
    *,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
    classifications: tuple[ClassificationRecord, ...],
    admissions: tuple[AdmissionRecord, ...],
    census: FamilyCensus,
) -> None:
    """Recompute the candidate flow from identifiers and reject any mismatch."""

    plan = _revalidated(plan)
    execution = _revalidated(execution)
    disposition = _revalidated(disposition)
    classifications = tuple(_revalidated(record) for record in classifications)
    admissions = tuple(_revalidated(record) for record in admissions)
    census = _revalidated(census)
    plan_slots = {slot.slot_id: slot for slot in plan.slots}

    executed_slots = {item.slot_id for item in execution.executed}
    unknown_slots = executed_slots - set(plan_slots)
    if unknown_slots:
        _fail(f"executed slots are not in the frozen plan: {sorted(unknown_slots)}")

    inactive = set(execution.inactive_reserve_slot_ids)
    unknown_inactive = inactive - set(plan_slots)
    if unknown_inactive:
        _fail(f"inactive reserve slots are not in the plan: {sorted(unknown_inactive)}")
    non_reserve_inactive = {
        slot_id for slot_id in inactive if plan_slots[slot_id].slot_kind != "reserve"
    }
    if non_reserve_inactive:
        _fail(f"only reserve slots may be inactive: {sorted(non_reserve_inactive)}")

    primary_slots = {slot_id for slot_id, slot in plan_slots.items() if slot.slot_kind == "primary"}
    missing_primary = primary_slots - executed_slots
    if missing_primary:
        _fail(f"every primary slot must execute; missing: {sorted(missing_primary)}")

    reserve_slots = {slot_id for slot_id, slot in plan_slots.items() if slot.slot_kind == "reserve"}
    if (reserve_slots & executed_slots) | inactive != reserve_slots:
        _fail("activated and inactive reserve slots must partition the reserve pool")

    for item in execution.executed:
        slot = plan_slots[item.slot_id]
        if item.fault_type != slot.fault_type:
            _fail(f"candidate {item.candidate_id} changed fault_type from its slot")
        if item.role != slot.role:
            _fail(f"candidate {item.candidate_id} changed role from its slot")
        if item.slot_kind != slot.slot_kind:
            _fail(f"candidate {item.candidate_id} changed slot_kind from its slot")
        expected_fingerprint = proposed_family_sha256(slot.identity)
        if item.proposed_family_sha256 != expected_fingerprint:
            _fail(f"candidate {item.candidate_id} fingerprint differs from its frozen identity")
        expected_candidate_id = candidate_id_for(
            slot_id=slot.slot_id,
            family_fingerprint=expected_fingerprint,
        )
        if item.candidate_id != expected_candidate_id:
            _fail(f"candidate ID does not bind the frozen slot identity: {item.slot_id}")
        if item.dataset_sha256 != slot.identity.dataset_sha256:
            _fail(f"candidate {item.candidate_id} changed dataset_sha256 from its slot")
        if item.model_data_split_manifest_sha256 != slot.identity.model_data_split_manifest_sha256:
            _fail(f"candidate {item.candidate_id} changed split manifest from its slot")

    executed_ids = {item.candidate_id for item in execution.executed}

    disposition_ids = {entry.candidate_id for entry in disposition.entries}
    if disposition_ids != executed_ids:
        missing = sorted(executed_ids - disposition_ids)
        extra = sorted(disposition_ids - executed_ids)
        _fail(
            f"technical disposition must cover exactly the executed set; missing={missing}; extra={extra}"
        )

    valid_ids = {
        entry.candidate_id
        for entry in disposition.entries
        if entry.disposition == "technically_valid"
    }
    rejected_ids = {
        entry.candidate_id
        for entry in disposition.entries
        if entry.disposition == "technical_rejected"
    }

    classification_ids = [record.candidate_id for record in classifications]
    duplicates = [item for item, count in Counter(classification_ids).items() if count > 1]
    if duplicates:
        _fail(f"a candidate may hold at most one classification: {sorted(duplicates)}")
    if set(classification_ids) != valid_ids:
        missing = sorted(valid_ids - set(classification_ids))
        extra = sorted(set(classification_ids) - valid_ids)
        _fail(f"only technically valid candidates are classified; missing={missing}; extra={extra}")

    rejected_with_classification = rejected_ids & set(classification_ids)
    if rejected_with_classification:
        _fail(f"rejected candidates must not be classified: {sorted(rejected_with_classification)}")

    admission_ids = [record.candidate_id for record in admissions]
    admission_duplicates = [item for item, count in Counter(admission_ids).items() if count > 1]
    if admission_duplicates:
        _fail(f"a candidate may hold at most one admission record: {sorted(admission_duplicates)}")
    if set(admission_ids) != valid_ids:
        missing = sorted(valid_ids - set(admission_ids))
        extra = sorted(set(admission_ids) - valid_ids)
        _fail(
            f"only technically valid candidates reach admission; missing={missing}; extra={extra}"
        )

    classification_by_id = {record.candidate_id: record for record in classifications}
    execution_by_id = {item.candidate_id: item for item in execution.executed}
    for candidate_id, classification in classification_by_id.items():
        executed = execution_by_id[candidate_id]
        if classification.role != executed.role:
            _fail(f"classification role disagrees with execution for {candidate_id}")
        expected_policy = _POLICY_BY_FAULT[executed.fault_type]
        if classification.eligibility_policy_version != expected_policy:
            _fail(
                f"classification policy disagrees with mechanism for {candidate_id}; "
                f"expected {expected_policy}"
            )
    accepted_records = tuple(record for record in admissions if record.admission == "accepted")

    for record in accepted_records:
        classification = classification_by_id[record.candidate_id]
        if classification.family_class is None:
            _fail(
                "a control whose measurement contradicts its declared direction cannot be "
                f"accepted: {record.candidate_id}"
            )
        if record.family_class != classification.family_class:
            _fail(f"admission family class disagrees with classification for {record.candidate_id}")

    # A direction violation must be excluded for that exact reason, so the
    # ledger distinguishes it from a duplicate or a leakage exclusion.
    for record in admissions:
        classification = classification_by_id[record.candidate_id]
        if classification.family_class is None:
            if record.admission != "excluded_valid":
                _fail(f"a direction violation must be excluded: {record.candidate_id}")
            if record.exclusion_reason != "control_direction_violation":
                _fail(
                    "a direction violation must use the control_direction_violation reason: "
                    f"{record.candidate_id}"
                )
        elif record.exclusion_reason == "control_direction_violation":
            _fail(
                "control_direction_violation is only valid when classification has no "
                f"family class: {record.candidate_id}"
            )

    census_by_candidate = {entry.candidate_id: entry for entry in census.entries}
    accepted_ids = {record.candidate_id for record in accepted_records}
    if set(census_by_candidate) != accepted_ids:
        missing = sorted(accepted_ids - set(census_by_candidate))
        extra = sorted(set(census_by_candidate) - accepted_ids)
        _fail(f"family census must match the accepted set; missing={missing}; extra={extra}")

    excluded_ids = {
        record.candidate_id for record in admissions if record.admission == "excluded_valid"
    }
    excluded_in_census = excluded_ids & set(census_by_candidate)
    if excluded_in_census:
        _fail(
            f"excluded-valid candidates must not appear in the census: {sorted(excluded_in_census)}"
        )
    rejected_in_census = rejected_ids & set(census_by_candidate)
    if rejected_in_census:
        _fail(
            f"technically rejected candidates must not appear in the census: {sorted(rejected_in_census)}"
        )

    for record in accepted_records:
        entry = census_by_candidate[record.candidate_id]
        if record.case_family_id != entry.case_family_id:
            _fail(f"admission and census disagree on the family ID for {record.candidate_id}")
        if entry.family_class != record.family_class:
            _fail(f"census family class disagrees with admission for {record.candidate_id}")
        executed = execution_by_id[record.candidate_id]
        expected_family_id = f"p2-family-{executed.proposed_family_sha256}"
        if record.case_family_id != expected_family_id:
            _fail(f"admission family ID does not bind execution for {record.candidate_id}")
        if entry.proposed_family_sha256 != executed.proposed_family_sha256:
            _fail(f"census fingerprint disagrees with execution for {record.candidate_id}")
        if entry.fault_type != executed.fault_type:
            _fail(f"census fault_type disagrees with execution for {record.candidate_id}")


def validate_reserve_activation(
    *,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
) -> None:
    """Reserve slots may only replace primary slots rejected on technical grounds."""

    plan = _revalidated(plan)
    execution = _revalidated(execution)
    disposition = _revalidated(disposition)
    plan_slots = {slot.slot_id: slot for slot in plan.slots}
    disposition_by_id = {entry.candidate_id: entry for entry in disposition.entries}
    execution_ids = {item.candidate_id for item in execution.executed}
    if set(disposition_by_id) != execution_ids:
        _fail("reserve validation requires disposition for exactly every executed candidate")
    unknown_slots = {item.slot_id for item in execution.executed} - set(plan_slots)
    if unknown_slots:
        _fail(f"reserve validation found unknown executed slots: {sorted(unknown_slots)}")

    activated = tuple(item for item in execution.executed if item.slot_kind == "reserve")
    rejected_primary_by_fault: Counter[str] = Counter()
    for item in execution.executed:
        if item.slot_kind != "primary":
            continue
        entry = disposition_by_id.get(item.candidate_id)
        if entry is not None and entry.disposition == "technical_rejected":
            rejected_primary_by_fault[item.fault_type] += 1

    activated_by_fault: dict[str, list[int]] = {}
    for item in activated:
        slot = plan_slots[item.slot_id]
        if slot.fault_type != item.fault_type or slot.slot_kind != "reserve":
            _fail(f"activated reserve {item.slot_id} disagrees with its frozen slot")
        if slot.reserve_order is None:
            _fail(f"reserve slot {slot.slot_id} has no reserve_order")
        activated_by_fault.setdefault(item.fault_type, []).append(slot.reserve_order)

    for fault_type, orders in activated_by_fault.items():
        ordered = sorted(orders)
        if ordered != list(range(1, len(ordered) + 1)):
            _fail(
                f"reserve activation for {fault_type} must follow R1, R2, R3 without gaps; "
                f"got {ordered}"
            )
        budget = rejected_primary_by_fault.get(fault_type, 0)
        if len(ordered) > budget:
            _fail(
                f"reserve activation for {fault_type} exceeds the technical-rejection budget: "
                f"{len(ordered)} activated, {budget} rejected"
            )


def validate_context_cardinality(
    *,
    census: FamilyCensus,
    contexts: ContextCensus,
) -> None:
    """Each family class permits exactly one set of evidence conditions."""

    census = _revalidated(census)
    contexts = _revalidated(contexts)
    family_class = {entry.case_family_id: entry.family_class for entry in census.entries}

    observed: dict[str, set[str]] = {}
    for entry in contexts.entries:
        if entry.case_family_id not in family_class:
            _fail(f"context references an unknown family: {entry.case_family_id}")
        observed.setdefault(entry.case_family_id, set()).add(entry.evidence_condition)

    for family_id, klass in family_class.items():
        expected = CONTEXT_CARDINALITY[klass]
        actual = observed.get(family_id, set())
        if actual != set(expected):
            _fail(
                f"family {family_id} of class {klass} must have contexts "
                f"{sorted(expected)}, got {sorted(actual)}"
            )


def validate_duplicate_audit(
    *,
    audit: DuplicateAudit,
    admissions: tuple[AdmissionRecord, ...],
    execution: CandidateExecution,
) -> None:
    """Exact and effective duplicates must be excluded; near duplicates must not."""

    audit = _revalidated(audit)
    admissions = tuple(_revalidated(record) for record in admissions)
    execution = _revalidated(execution)
    admission_ids = [record.candidate_id for record in admissions]
    if len(set(admission_ids)) != len(admission_ids):
        _fail("duplicate validation requires unique admission candidate IDs")
    admission_by_id = {record.candidate_id: record for record in admissions}
    execution_by_id = {record.candidate_id: record for record in execution.executed}
    if set(admission_by_id) - set(execution_by_id):
        _fail("duplicate validation received admissions for unexecuted candidates")

    finding_by_excluded: dict[str, list[str]] = {}

    for finding in audit.findings:
        for candidate_id in (finding.candidate_id, finding.duplicate_of_candidate_id):
            if candidate_id not in admission_by_id:
                _fail(f"duplicate audit references an unknown candidate: {candidate_id}")
            if candidate_id not in execution_by_id:
                _fail(f"duplicate audit references an unexecuted candidate: {candidate_id}")

        candidate = execution_by_id[finding.candidate_id]
        duplicate_of = execution_by_id[finding.duplicate_of_candidate_id]
        if candidate.fault_type != finding.fault_type:
            _fail(f"duplicate finding fault_type disagrees with {finding.candidate_id}")
        if duplicate_of.fault_type != finding.fault_type:
            _fail("duplicate findings must compare candidates from the same mechanism")

        record = admission_by_id[finding.candidate_id]
        if finding.kind == "exact_identity":
            if (
                finding.candidate_basis_sha256 != candidate.proposed_family_sha256
                or finding.duplicate_of_basis_sha256 != duplicate_of.proposed_family_sha256
            ):
                _fail("exact duplicate basis hashes must match execution fingerprints")
            if record.admission != "excluded_valid":
                _fail(f"exact identity duplicate must be excluded: {finding.candidate_id}")
            if record.exclusion_reason != "exact_identity_duplicate":
                _fail(
                    f"exact identity duplicate must use the matching reason code: "
                    f"{finding.candidate_id}"
                )
            finding_by_excluded.setdefault(finding.candidate_id, []).append(
                "exact_identity_duplicate"
            )
        elif finding.kind == "effective_intervention":
            if candidate.proposed_family_sha256 == duplicate_of.proposed_family_sha256:
                _fail("equal identity fingerprints must be reported as exact duplicates")
            if record.admission != "excluded_valid":
                _fail(f"effective intervention duplicate must be excluded: {finding.candidate_id}")
            if record.exclusion_reason != "effective_intervention_duplicate":
                _fail(
                    f"effective intervention duplicate must use the matching reason code: "
                    f"{finding.candidate_id}"
                )
            finding_by_excluded.setdefault(finding.candidate_id, []).append(
                "effective_intervention_duplicate"
            )
        else:
            if record.admission == "excluded_valid" and record.exclusion_reason in {
                "exact_identity_duplicate",
                "effective_intervention_duplicate",
            }:
                _fail(
                    "a near duplicate must be disclosed, not silently excluded as a duplicate: "
                    f"{finding.candidate_id}"
                )

        if finding.kind in {"exact_identity", "effective_intervention"}:
            representative = admission_by_id[finding.duplicate_of_candidate_id]
            if representative.admission != "accepted":
                _fail("duplicate findings must reference an accepted representative")

    for record in admissions:
        if record.exclusion_reason not in {
            "exact_identity_duplicate",
            "effective_intervention_duplicate",
        }:
            continue
        supporting = finding_by_excluded.get(record.candidate_id, [])
        if supporting != [record.exclusion_reason]:
            _fail(
                f"duplicate exclusion for {record.candidate_id} requires exactly one "
                "matching audit finding"
            )


def validate_alpha_report(
    *,
    report: AlphaValidityReport,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
    admissions: tuple[AdmissionRecord, ...],
    census: FamilyCensus,
    contexts: ContextCensus,
) -> None:
    """Recompute every reported total from the underlying entry lists."""

    report = _revalidated(report)
    plan = _revalidated(plan)
    execution = _revalidated(execution)
    disposition = _revalidated(disposition)
    admissions = tuple(_revalidated(record) for record in admissions)
    census = _revalidated(census)
    contexts = _revalidated(contexts)
    validate_frozen_alpha_plan(plan)
    if plan.primary_planned != PRIMARY_SLOT_COUNT:
        _fail(f"the alpha grid must declare {PRIMARY_SLOT_COUNT} primary slots")
    if plan.reserve_planned != RESERVE_SLOT_COUNT:
        _fail(f"the alpha grid must declare {RESERVE_SLOT_COUNT} reserve slots")

    activated_reserve = sum(1 for item in execution.executed if item.slot_kind == "reserve")
    accepted_ids = {record.candidate_id for record in admissions if record.admission == "accepted"}
    accepted_execution = [item for item in execution.executed if item.candidate_id in accepted_ids]
    mechanism_coverage_passed = all(
        sum(
            1
            for item in accepted_execution
            if item.fault_type == fault_type and item.role == "fault_directed"
        )
        >= 2
        and sum(
            1
            for item in accepted_execution
            if item.fault_type == fault_type and item.role != "fault_directed"
        )
        >= 1
        for fault_type in _POLICY_BY_FAULT
    )
    recomputed = {
        "primary_planned": plan.primary_planned,
        "reserve_planned": plan.reserve_planned,
        "planned_total": plan.primary_planned + plan.reserve_planned,
        "executed": len(execution.executed),
        "activated_reserve": activated_reserve,
        "inactive_reserve": len(execution.inactive_reserve_slot_ids),
        "technically_valid": sum(
            1 for entry in disposition.entries if entry.disposition == "technically_valid"
        ),
        "technical_rejected": sum(
            1 for entry in disposition.entries if entry.disposition == "technical_rejected"
        ),
        "accepted": sum(1 for record in admissions if record.admission == "accepted"),
        "excluded_valid": sum(1 for record in admissions if record.admission == "excluded_valid"),
        "eligible_failure": sum(
            1 for entry in census.entries if entry.family_class == "eligible_failure"
        ),
        "stable_control": sum(
            1 for entry in census.entries if entry.family_class == "stable_control"
        ),
        "improvement_control": sum(
            1 for entry in census.entries if entry.family_class == "improvement_control"
        ),
        "benign_control": sum(
            1 for entry in census.entries if entry.family_class == "benign_control"
        ),
        "context_count": len(contexts.entries),
        "mechanism_coverage_passed": mechanism_coverage_passed,
    }

    for field, expected in recomputed.items():
        declared = getattr(report, field)
        if declared != expected:
            _fail(f"{field} is declared as {declared} but recomputes to {expected}")

    if recomputed["planned_total"] != PLANNED_SLOT_TOTAL:
        _fail(f"the alpha grid must plan exactly {PLANNED_SLOT_TOTAL} slots")


def validate_contract_bundle(
    *,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
    classifications: tuple[ClassificationRecord, ...],
    admissions: tuple[AdmissionRecord, ...],
    census: FamilyCensus,
    contexts: ContextCensus,
    duplicate_audit: DuplicateAudit,
    report: AlphaValidityReport,
) -> None:
    """Run every contract invariant through one authoritative fail-closed entry point."""

    validate_frozen_alpha_plan(plan)
    validate_candidate_flow(
        plan=plan,
        execution=execution,
        disposition=disposition,
        classifications=classifications,
        admissions=admissions,
        census=census,
    )
    validate_reserve_activation(
        plan=plan,
        execution=execution,
        disposition=disposition,
    )
    validate_context_cardinality(census=census, contexts=contexts)
    validate_duplicate_audit(
        audit=duplicate_audit,
        admissions=admissions,
        execution=execution,
    )
    validate_alpha_report(
        report=report,
        plan=plan,
        execution=execution,
        disposition=disposition,
        admissions=admissions,
        census=census,
        contexts=contexts,
    )
