"""Auditable candidate census and fail-closed mechanism coverage.

The persisted lifecycle deliberately separates execution, technical disposition,
classification, admission and context membership.  This module joins those
records without replacing them: every planned slot receives one terminal census
state, and mechanism coverage is derived only from admitted eligible failures
whose three evidence-condition siblings remain independently bound.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Final, Literal, NoReturn, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_json, canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    AcceptedFamilyClass,
    AdmissionRecord,
    CandidateExecution,
    CandidateId,
    CandidatePlan,
    CandidateRole,
    ClassificationRecord,
    ContextCensus,
    ContextEntry,
    EvidenceConditionName,
    FamilyCensus,
    FamilyId,
    MeasuredOutcome,
    ReserveRecoveryAuthorization,
    SlotId,
    SlotKind,
    TechnicalDisposition,
    TechnicalRejectionReason,
    ValidExclusionReason,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN, FaultTypeName

CANDIDATE_CENSUS_SCHEMA_VERSION: Final[Literal["p2-candidate-census/1"]] = "p2-candidate-census/1"
MECHANISM_COVERAGE_SCHEMA_VERSION: Final[Literal["p2-mechanism-coverage/1"]] = (
    "p2-mechanism-coverage/1"
)
_MECHANISMS: Final[tuple[FaultTypeName, ...]] = (
    "data_drift",
    "label_noise",
    "preprocessing_bug",
)
_REQUIRED_CONDITIONS: Final[frozenset[str]] = frozenset({"full", "missing_key", "noisy"})

CandidateLifecycleStatus = Literal[
    "inactive_reserve",
    "technical_rejected",
    "excluded_valid",
    "accepted",
]
CoverageReason = Literal[
    "no_eligible_failure",
    "unpromoted_reserve_family",
    "incomplete_evidence_conditions",
    "source_binding_mismatch",
    "evidence_content_reuse",
]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CoverageContractError(ValueError):
    """Raised when lifecycle records cannot support an auditable coverage claim."""


class MechanismCoverageError(CoverageContractError):
    """Structured failure raised when at least one mechanism lacks valid coverage."""

    def __init__(self, audit: MechanismCoverageAudit) -> None:
        self.audit = audit
        payload = canonical_json(audit.model_dump(mode="json"))
        super().__init__(f"mechanism coverage failed: {payload}")


def _fail(message: str) -> NoReturn:
    raise CoverageContractError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandidateCensusEntry(_StrictFrozenModel):
    """One planned slot joined to its terminal lifecycle decision and evidence."""

    slot_id: SlotId
    candidate_id: CandidateId | None = None
    fault_type: FaultTypeName
    role: CandidateRole
    slot_kind: SlotKind
    lifecycle_status: CandidateLifecycleStatus
    technical_rejection_reason: TechnicalRejectionReason | None = None
    measured_outcome: MeasuredOutcome | None = None
    admission_exclusion_reason: ValidExclusionReason | None = None
    case_family_id: FamilyId | None = None
    family_class: AcceptedFamilyClass | None = None
    evidence_conditions: tuple[EvidenceConditionName, ...] = ()
    reserve_promotion_authorized: bool = False

    @model_validator(mode="after")
    def _terminal_state_is_unambiguous(self) -> CandidateCensusEntry:
        if self.lifecycle_status == "inactive_reserve":
            if self.slot_kind != "reserve" or self.candidate_id is not None:
                raise ValueError("an inactive census entry must be an unexecuted reserve")
            if (
                any(
                    value is not None
                    for value in (
                        self.technical_rejection_reason,
                        self.measured_outcome,
                        self.admission_exclusion_reason,
                        self.case_family_id,
                        self.family_class,
                    )
                )
                or self.evidence_conditions
                or self.reserve_promotion_authorized
            ):
                raise ValueError("an inactive reserve must not claim downstream records")
            return self

        if self.candidate_id is None:
            raise ValueError("an executed census entry must carry a candidate ID")
        if self.lifecycle_status == "technical_rejected":
            if self.technical_rejection_reason is None:
                raise ValueError("a technical rejection must retain its reason code")
            if (
                any(
                    value is not None
                    for value in (
                        self.measured_outcome,
                        self.admission_exclusion_reason,
                        self.case_family_id,
                        self.family_class,
                    )
                )
                or self.evidence_conditions
                or self.reserve_promotion_authorized
            ):
                raise ValueError("a technical rejection must not enter classification or admission")
            return self

        if self.technical_rejection_reason is not None or self.measured_outcome is None:
            raise ValueError("a technically valid candidate requires one measured outcome")
        if self.lifecycle_status == "excluded_valid":
            if self.admission_exclusion_reason is None:
                raise ValueError("an excluded-valid candidate must retain its reason code")
            if self.case_family_id is not None or self.evidence_conditions:
                raise ValueError("an excluded-valid candidate must not claim family evidence")
            if self.reserve_promotion_authorized:
                raise ValueError("an excluded-valid candidate cannot claim reserve promotion")
            return self

        if (
            self.admission_exclusion_reason is not None
            or self.case_family_id is None
            or self.family_class is None
        ):
            raise ValueError("an accepted candidate must retain its family identity and class")
        expected = {
            "eligible_failure": _REQUIRED_CONDITIONS,
            "stable_control": frozenset({"full"}),
            "improvement_control": frozenset({"full"}),
            "benign_control": frozenset(),
        }[self.family_class]
        if frozenset(self.evidence_conditions) != expected or len(self.evidence_conditions) != len(
            expected
        ):
            raise ValueError("accepted family evidence conditions disagree with its class")
        if self.slot_kind == "primary" and self.reserve_promotion_authorized:
            raise ValueError("a primary candidate cannot claim reserve promotion")
        return self


class CandidateCensus(_StrictFrozenModel):
    """Canonical, hashable join over every primary and reserve slot."""

    schema_version: Literal["p2-candidate-census/1"] = CANDIDATE_CENSUS_SCHEMA_VERSION
    entries: tuple[CandidateCensusEntry, ...]

    @model_validator(mode="after")
    def _membership_is_unique_and_canonical(self) -> CandidateCensus:
        slot_ids = tuple(entry.slot_id for entry in self.entries)
        candidate_ids = tuple(
            entry.candidate_id for entry in self.entries if entry.candidate_id is not None
        )
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("candidate census must contain each slot exactly once")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate census must not repeat a candidate")
        if slot_ids != tuple(sorted(slot_ids)):
            raise ValueError("candidate census must use canonical slot order")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class MechanismCoverageFinding(_StrictFrozenModel):
    """One machine-readable reason that a mechanism cannot claim coverage."""

    reason_code: CoverageReason
    family_ids: tuple[FamilyId, ...] = ()
    evidence_condition: EvidenceConditionName | None = None
    detail: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _finding_targets_are_canonical(self) -> MechanismCoverageFinding:
        if len(set(self.family_ids)) != len(self.family_ids):
            raise ValueError("coverage finding family IDs must be unique")
        if self.family_ids != tuple(sorted(self.family_ids)):
            raise ValueError("coverage finding family IDs must use canonical order")
        if self.reason_code == "no_eligible_failure" and self.family_ids:
            raise ValueError("a zero-family finding must not name a family")
        if self.reason_code == "evidence_content_reuse" and len(self.family_ids) < 2:
            raise ValueError("evidence reuse must identify at least two families")
        return self


class MechanismCoverageEntry(_StrictFrozenModel):
    """Coverage decision for one required failure mechanism."""

    fault_type: FaultTypeName
    eligible_family_ids: tuple[FamilyId, ...]
    complete_independent_family_ids: tuple[FamilyId, ...]
    findings: tuple[MechanismCoverageFinding, ...]
    passed: bool

    @model_validator(mode="after")
    def _decision_is_derived(self) -> MechanismCoverageEntry:
        if self.eligible_family_ids != tuple(sorted(set(self.eligible_family_ids))):
            raise ValueError("eligible family IDs must be unique and canonical")
        if self.complete_independent_family_ids != tuple(
            sorted(set(self.complete_independent_family_ids))
        ):
            raise ValueError("complete family IDs must be unique and canonical")
        if not set(self.complete_independent_family_ids) <= set(self.eligible_family_ids):
            raise ValueError("complete families must be eligible families")
        expected = bool(self.complete_independent_family_ids)
        if self.passed != expected:
            raise ValueError("mechanism coverage verdict must be derived from complete families")
        return self


class MechanismCoverageAudit(_StrictFrozenModel):
    """Complete structured explanation for the cross-mechanism coverage gate."""

    schema_version: Literal["p2-mechanism-coverage/1"] = MECHANISM_COVERAGE_SCHEMA_VERSION
    mechanisms: tuple[MechanismCoverageEntry, ...]
    passed: bool

    @model_validator(mode="after")
    def _all_required_mechanisms_are_present(self) -> MechanismCoverageAudit:
        faults = tuple(entry.fault_type for entry in self.mechanisms)
        if faults != _MECHANISMS:
            raise ValueError("coverage audit must contain all mechanisms in canonical order")
        if self.passed != all(entry.passed for entry in self.mechanisms):
            raise ValueError("overall coverage must be derived from mechanism verdicts")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_candidate_census(
    *,
    plan: CandidatePlan,
    execution: CandidateExecution,
    disposition: TechnicalDisposition,
    classifications: tuple[ClassificationRecord, ...],
    admissions: tuple[AdmissionRecord, ...],
    census: FamilyCensus,
    contexts: ContextCensus,
) -> CandidateCensus:
    """Join all lifecycle ledgers and reject any candidate without one terminal reason."""

    plan = _revalidated(plan)
    execution = _revalidated(execution)
    disposition = _revalidated(disposition)
    classifications = tuple(_revalidated(item) for item in classifications)
    admissions = tuple(_revalidated(item) for item in admissions)
    census = _revalidated(census)
    contexts = _revalidated(contexts)

    executed_by_slot = {item.slot_id: item for item in execution.executed}
    dispositions = {item.candidate_id: item for item in disposition.entries}
    classified = {item.candidate_id: item for item in classifications}
    admitted = {item.candidate_id: item for item in admissions}
    families = {item.candidate_id: item for item in census.entries}
    if len(classified) != len(classifications):
        _fail("candidate census received duplicate classification records")
    if len(admitted) != len(admissions):
        _fail("candidate census received duplicate admission records")
    contexts_by_family: dict[str, list[EvidenceConditionName]] = defaultdict(list)
    for context in contexts.entries:
        contexts_by_family[context.case_family_id].append(context.evidence_condition)

    entries: list[CandidateCensusEntry] = []
    consumed_candidates: set[str] = set()
    valid_candidates: set[str] = set()
    rejected_candidates: set[str] = set()
    accepted_candidates: set[str] = set()
    for slot in sorted(plan.slots, key=lambda item: item.slot_id):
        executed = executed_by_slot.get(slot.slot_id)
        if executed is None:
            if slot.slot_id not in execution.inactive_reserve_slot_ids:
                _fail(f"candidate census cannot explain unexecuted slot {slot.slot_id}")
            entries.append(
                CandidateCensusEntry(
                    slot_id=slot.slot_id,
                    fault_type=slot.fault_type,
                    role=slot.role,
                    slot_kind=slot.slot_kind,
                    lifecycle_status="inactive_reserve",
                )
            )
            continue

        consumed_candidates.add(executed.candidate_id)
        if (
            executed.fault_type != slot.fault_type
            or executed.role != slot.role
            or executed.slot_kind != slot.slot_kind
        ):
            _fail(f"candidate census execution differs from frozen slot {slot.slot_id}")
        technical = dispositions.get(executed.candidate_id)
        if technical is None:
            _fail(f"candidate census has no technical disposition for {executed.candidate_id}")
        if technical.disposition == "technical_rejected":
            rejected_candidates.add(executed.candidate_id)
            entries.append(
                CandidateCensusEntry(
                    slot_id=slot.slot_id,
                    candidate_id=executed.candidate_id,
                    fault_type=slot.fault_type,
                    role=slot.role,
                    slot_kind=slot.slot_kind,
                    lifecycle_status="technical_rejected",
                    technical_rejection_reason=technical.rejection_reason,
                )
            )
            continue

        valid_candidates.add(executed.candidate_id)
        classification = classified.get(executed.candidate_id)
        admission = admitted.get(executed.candidate_id)
        if classification is None or admission is None:
            _fail(f"candidate census has no classification/admission for {executed.candidate_id}")
        if classification.role != slot.role:
            _fail(f"candidate census classification role differs for {executed.candidate_id}")
        if admission.admission == "excluded_valid":
            if executed.candidate_id in families:
                _fail(f"excluded-valid candidate appears in family census: {executed.candidate_id}")
            entries.append(
                CandidateCensusEntry(
                    slot_id=slot.slot_id,
                    candidate_id=executed.candidate_id,
                    fault_type=slot.fault_type,
                    role=slot.role,
                    slot_kind=slot.slot_kind,
                    lifecycle_status="excluded_valid",
                    measured_outcome=classification.measured_outcome,
                    admission_exclusion_reason=admission.exclusion_reason,
                    family_class=classification.family_class,
                )
            )
            continue

        family = families.get(executed.candidate_id)
        if family is None or admission.case_family_id != family.case_family_id:
            _fail(f"candidate census cannot bind accepted family for {executed.candidate_id}")
        if family.fault_type != slot.fault_type or family.family_class != admission.family_class:
            _fail(f"candidate census family metadata differs for {executed.candidate_id}")
        if family.proposed_family_sha256 != executed.proposed_family_sha256:
            _fail(f"candidate census family fingerprint differs for {executed.candidate_id}")
        if classification.family_class != family.family_class:
            _fail(f"candidate census classification differs for {executed.candidate_id}")
        accepted_candidates.add(executed.candidate_id)
        conditions = tuple(
            sorted(
                contexts_by_family.get(family.case_family_id, []),
                key={"full": 0, "missing_key": 1, "noisy": 2}.__getitem__,
            )
        )
        entries.append(
            CandidateCensusEntry(
                slot_id=slot.slot_id,
                candidate_id=executed.candidate_id,
                fault_type=slot.fault_type,
                role=slot.role,
                slot_kind=slot.slot_kind,
                lifecycle_status="accepted",
                measured_outcome=classification.measured_outcome,
                case_family_id=family.case_family_id,
                family_class=family.family_class,
                evidence_conditions=conditions,
                reserve_promotion_authorized=(
                    execution.reserve_recovery_authorization is not None
                    and slot.slot_id
                    == execution.reserve_recovery_authorization.promoted_reserve_slot_id
                ),
            )
        )

    expected_candidates = {item.candidate_id for item in execution.executed}
    if consumed_candidates != expected_candidates:
        _fail("candidate census contains executions outside the frozen plan")
    downstream_ids = set(dispositions) | set(classified) | set(admitted) | set(families)
    if downstream_ids - expected_candidates:
        _fail("candidate census contains downstream records for an unexecuted candidate")
    if set(dispositions) != expected_candidates:
        _fail("candidate census requires one technical disposition per execution")
    if set(classified) != valid_candidates or set(admitted) != valid_candidates:
        _fail("candidate census requires classification and admission for every valid candidate")
    if set(families) != accepted_candidates:
        _fail("candidate census family membership must equal accepted candidates")
    if rejected_candidates & (set(classified) | set(admitted) | set(families)):
        _fail("candidate census found downstream records for a technical rejection")
    represented_families = {
        entry.case_family_id for entry in entries if entry.case_family_id is not None
    }
    if set(contexts_by_family) - represented_families:
        _fail("candidate census contains contexts for a non-accepted family")
    return CandidateCensus(entries=tuple(entries))


def assess_mechanism_coverage(
    *,
    census: FamilyCensus,
    contexts: ContextCensus,
    candidate_census: CandidateCensus,
    reserve_recovery_authorization: ReserveRecoveryAuthorization | None = None,
) -> MechanismCoverageAudit:
    """Require an independent complete eligible-failure family for every mechanism."""

    census = _revalidated(census)
    contexts = _revalidated(contexts)
    candidate_census = _revalidated(candidate_census)
    if reserve_recovery_authorization is not None:
        reserve_recovery_authorization = _revalidated(reserve_recovery_authorization)
    family_by_id = {item.case_family_id: item for item in census.entries}
    candidate_by_id = {
        item.candidate_id: item
        for item in candidate_census.entries
        if item.candidate_id is not None
    }
    for family in census.entries:
        candidate = candidate_by_id.get(family.candidate_id)
        if candidate is None or candidate.lifecycle_status != "accepted":
            _fail("mechanism coverage cannot bind an accepted family to the candidate census")
        if candidate.case_family_id != family.case_family_id:
            _fail("mechanism coverage family and candidate census IDs disagree")
        if candidate.slot_kind != family.origin_slot_kind:
            _fail("mechanism coverage family origin differs from the candidate census")
        if candidate.reserve_promotion_authorized != family.reserve_promotion_authorized:
            _fail("mechanism coverage reserve promotion binding differs across censuses")
        if family.reserve_promotion_authorized:
            if reserve_recovery_authorization is None:
                _fail("mechanism coverage cannot trust a reserve promotion without authorization")
            if candidate.slot_id != reserve_recovery_authorization.promoted_reserve_slot_id:
                _fail("mechanism coverage reserve family differs from the authorized promotion")
    contexts_by_family: dict[str, list[ContextEntry]] = defaultdict(list)
    for context in contexts.entries:
        if context.case_family_id not in family_by_id:
            _fail("mechanism coverage found context for a non-accepted family")
        contexts_by_family[context.case_family_id].append(context)

    eligible_by_fault: dict[FaultTypeName, tuple[FamilyId, ...]] = {}
    findings_by_fault: dict[FaultTypeName, list[MechanismCoverageFinding]] = {
        fault_type: [] for fault_type in _MECHANISMS
    }
    candidate_complete_by_fault: dict[FaultTypeName, set[FamilyId]] = {
        fault_type: set() for fault_type in _MECHANISMS
    }
    projection_fingerprints: dict[tuple[EvidenceConditionName, str], list[FamilyId]] = defaultdict(
        list
    )
    for family_id, family_contexts in contexts_by_family.items():
        for context in family_contexts:
            projection_fingerprints[
                (context.evidence_condition, context.diagnosis_projection_sha256)
            ].append(family_id)

    for fault_type in _MECHANISMS:
        raw_eligible = tuple(
            sorted(
                (
                    entry
                    for entry in census.entries
                    if entry.fault_type == fault_type
                    and entry.family_class == "eligible_failure"
                ),
                key=lambda item: item.case_family_id,
            )
        )
        ineligible_reserves = tuple(
            entry.case_family_id
            for entry in raw_eligible
            if entry.origin_slot_kind == "reserve" and not entry.reserve_promotion_authorized
        )
        for family_id in ineligible_reserves:
            findings_by_fault[fault_type].append(
                MechanismCoverageFinding(
                    reason_code="unpromoted_reserve_family",
                    family_ids=(family_id,),
                    detail="an unpromoted reserve family cannot satisfy mechanism coverage",
                )
            )
        eligible = tuple(
            entry.case_family_id
            for entry in raw_eligible
            if entry.case_family_id not in ineligible_reserves
        )
        eligible_by_fault[fault_type] = eligible
        findings = findings_by_fault[fault_type]
        if not eligible:
            findings.append(
                MechanismCoverageFinding(
                    reason_code="no_eligible_failure",
                    detail="no admitted eligible-failure family exists for this mechanism",
                )
            )

        for family_id in eligible:
            family_contexts = contexts_by_family.get(family_id, [])
            conditions = tuple(item.evidence_condition for item in family_contexts)
            if len(conditions) != 3 or set(conditions) != _REQUIRED_CONDITIONS:
                findings.append(
                    MechanismCoverageFinding(
                        reason_code="incomplete_evidence_conditions",
                        family_ids=(family_id,),
                        detail="eligible family does not contain exactly full, missing_key and noisy",
                    )
                )
                continue
            raw_source_bindings = tuple(
                item.diagnosis_projection.get("source_binding_sha256") for item in family_contexts
            )
            valid_bindings = all(isinstance(value, str) for value in raw_source_bindings)
            source_bindings = cast(tuple[str, ...], raw_source_bindings)
            if (
                not valid_bindings
                or len(set(source_bindings)) != 1
                or re.fullmatch(SHA256_PATTERN, source_bindings[0]) is None
            ):
                findings.append(
                    MechanismCoverageFinding(
                        reason_code="source_binding_mismatch",
                        family_ids=(family_id,),
                        detail="condition siblings do not share one valid source binding",
                    )
                )
                continue
            candidate_complete_by_fault[fault_type].add(family_id)

    reused: set[FamilyId] = set()
    fault_by_family = {family.case_family_id: family.fault_type for family in census.entries}
    replay_groups: set[tuple[EvidenceConditionName, tuple[FamilyId, ...]]] = set()
    for (duplicate_condition, _), family_ids in projection_fingerprints.items():
        unique = tuple(sorted(set(family_ids)))
        if len(unique) >= 2:
            replay_groups.add((duplicate_condition, unique))
    for duplicate_condition, unique in sorted(replay_groups):
        reused.update(unique)
        affected_faults = tuple(sorted({fault_by_family[family_id] for family_id in unique}))
        for fault_type in affected_faults:
            findings_by_fault[fault_type].append(
                MechanismCoverageFinding(
                    reason_code="evidence_content_reuse",
                    family_ids=unique,
                    evidence_condition=duplicate_condition,
                    detail="multiple families replay the same bound diagnosis projection",
                )
            )

    records: list[MechanismCoverageEntry] = []
    for fault_type in _MECHANISMS:
        eligible = eligible_by_fault[fault_type]
        findings = findings_by_fault[fault_type]
        complete = tuple(sorted(candidate_complete_by_fault[fault_type] - reused))
        findings.sort(
            key=lambda item: (
                item.reason_code,
                item.evidence_condition or "",
                item.family_ids,
            )
        )
        records.append(
            MechanismCoverageEntry(
                fault_type=fault_type,
                eligible_family_ids=eligible,
                complete_independent_family_ids=complete,
                findings=tuple(findings),
                passed=bool(complete),
            )
        )
    return MechanismCoverageAudit(
        mechanisms=tuple(records),
        passed=all(record.passed for record in records),
    )


def require_mechanism_coverage(audit: MechanismCoverageAudit) -> None:
    """Raise a structured error instead of allowing incomplete coverage downstream."""

    audit = _revalidated(audit)
    if not audit.passed:
        raise MechanismCoverageError(audit)
