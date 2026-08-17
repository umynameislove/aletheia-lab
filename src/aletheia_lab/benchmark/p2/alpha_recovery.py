"""Transparent recovery for a mechanism missing from the alpha sample.

The original primary run remains a failed, immutable source artifact. Recovery
executes every already-prespecified label-noise reserve so there is no optional
stopping. Before any reserve outcome is observed, R3 is the only promoted slot,
R1/R2 are fixed sensitivity probes, and F3 is superseded to preserve the
15-family alpha target. A promoted candidate that remains stable does not pass
coverage and does not unlock another tuning round.
"""

from __future__ import annotations

import math
from typing import NoReturn, cast

from aletheia_lab.benchmark.p2.alpha_execution import AlphaRuntime, execute_alpha_slot
from aletheia_lab.benchmark.p2.alpha_lifecycle import assemble_alpha_artifacts
from aletheia_lab.benchmark.p2.artifacts import P2ContractArtifacts, manifest_for_contract_store
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    ContextEntry,
    DuplicateAudit,
    ReserveRecoveryAuthorization,
    ReserveRecoveryObservation,
)
from aletheia_lab.benchmark.p2.coverage import (
    assess_mechanism_coverage,
    build_candidate_census,
)
from aletheia_lab.benchmark.p2.identity import LabelNoiseParameters
from aletheia_lab.benchmark.p2.label_noise import mutation_count


class ReserveRecoveryError(ValueError):
    """Raised when source evidence cannot authorize an outcome-blind recovery."""


def _fail(message: str) -> NoReturn:
    raise ReserveRecoveryError(message)


def _quality_audit(context: ContextEntry) -> tuple[int, int, float]:
    items = context.diagnosis_projection.get("items")
    if not isinstance(items, list):
        _fail("label-noise source context has no evidence item list")
    matches = [
        item.get("payload")
        for item in items
        if isinstance(item, dict) and item.get("id") == "target-quality-audit-summary"
    ]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        _fail("label-noise source context needs one target-quality audit summary")
    payload = matches[0]
    audited = payload.get("audited_record_count")
    disagreeing = payload.get("disagreeing_record_count")
    rate = payload.get("disagreement_rate")
    if (
        not isinstance(audited, int)
        or isinstance(audited, bool)
        or not isinstance(disagreeing, int)
        or isinstance(disagreeing, bool)
        or not isinstance(rate, int | float)
        or isinstance(rate, bool)
        or audited <= 0
    ):
        _fail("label-noise quality audit has invalid count or rate types")
    return audited, disagreeing, float(rate)


def build_reserve_recovery_authorization(
    *, source: P2ContractArtifacts, source_store_sha256: str
) -> ReserveRecoveryAuthorization:
    """Derive the only permitted recovery amendment from a failed primary store."""

    source.validate()
    expected_source_hash = manifest_for_contract_store(source).store_sha256
    if source_store_sha256 != expected_source_hash:
        _fail("declared source store hash does not bind the supplied primary artifacts")
    if source.execution.reserve_recovery_authorization is not None:
        _fail("a recovery run cannot authorize another recovery round")
    if source.report.activated_reserve != 0 or source.report.executed != source.plan.primary_planned:
        _fail("recovery source must be the untouched primary-only alpha run")
    if source.report.gate_status != "fail" or source.report.mechanism_coverage_passed:
        _fail("recovery requires a source run that failed mechanism coverage")

    candidates = build_candidate_census(
        plan=source.plan,
        execution=source.execution,
        disposition=source.disposition,
        classifications=source.classifications.entries,
        admissions=source.admissions.entries,
        census=source.census,
        contexts=source.contexts,
    )
    coverage = assess_mechanism_coverage(
        census=source.census,
        contexts=source.contexts,
        candidate_census=candidates,
    )
    failed = tuple(item for item in coverage.mechanisms if not item.passed)
    if len(failed) != 1 or failed[0].fault_type != "label_noise":
        _fail("recovery is authorized only when label noise is the sole missing mechanism")
    if {finding.reason_code for finding in failed[0].findings} != {"no_eligible_failure"}:
        _fail("label-noise recovery requires an honest zero-eligible-family finding")

    classifications = {item.candidate_id: item for item in source.classifications.entries}
    families = {item.candidate_id: item for item in source.census.entries}
    contexts = {
        (item.case_family_id, item.evidence_condition): item for item in source.contexts.entries
    }
    observations: list[ReserveRecoveryObservation] = []
    source_candidates = {item.candidate_id: item for item in candidates.entries if item.candidate_id}
    for slot in sorted(source.plan.slots, key=lambda item: item.slot_id):
        if not (
            slot.fault_type == "label_noise"
            and slot.slot_kind == "primary"
            and slot.role == "fault_directed"
        ):
            continue
        parameters = cast(LabelNoiseParameters, slot.identity.canonical_intervention_parameters)
        candidate = next(
            (item for item in source_candidates.values() if item.slot_id == slot.slot_id), None
        )
        if (
            candidate is None
            or candidate.candidate_id is None
            or candidate.lifecycle_status != "accepted"
        ):
            _fail("every source label-noise primary candidate must be accepted and auditable")
        candidate_id = candidate.candidate_id
        classification = classifications[candidate_id]
        family = families[candidate_id]
        if classification.measured_outcome != "stable" or family.family_class != "stable_control":
            _fail("source label-noise primary candidates must all be measured stable controls")
        context = contexts.get((family.case_family_id, "full"))
        if context is None:
            _fail("source label-noise stable family is missing its full evidence context")
        audited, disagreeing, achieved = _quality_audit(context)
        expected_count = mutation_count(flip_rate=parameters.flip_rate, record_count=audited)
        if disagreeing != expected_count or not math.isclose(
            achieved, disagreeing / audited, rel_tol=0.0, abs_tol=1e-12
        ):
            _fail("source label-noise intervention did not achieve its declared mutation count")
        observations.append(
            ReserveRecoveryObservation(
                slot_id=slot.slot_id,
                declared_intervention_rate=parameters.flip_rate,
                achieved_intervention_rate=achieved,
                primary_metric_delta=classification.delta,
                threshold=classification.threshold,
                measured_outcome="stable",
            )
        )

    return ReserveRecoveryAuthorization(
        schema_version="p2-reserve-recovery-authorization/1",
        protocol_version="complete-prespecified-reserve-recovery/v1",
        trigger="missing_mechanism_coverage",
        root_cause="effective_intervention_below_frozen_primary_threshold",
        fault_type="label_noise",
        source_store_sha256=source_store_sha256,
        source_candidate_plan_sha256=canonical_sha256(source.plan.model_dump(mode="json")),
        source_candidate_census_sha256=candidates.canonical_sha256(),
        source_coverage_audit_sha256=coverage.canonical_sha256(),
        source_observations=tuple(observations),
        activated_reserve_slot_ids=("M2-R1", "M2-R2", "M2-R3"),
        probe_slot_ids=("M2-R1", "M2-R2"),
        promoted_reserve_slot_id="M2-R3",
        superseded_primary_slot_id="M2-F3",
        primary_metric="accuracy",
        threshold=0.01,
        preserves_primary_measurements=True,
        executes_complete_reserve_set=True,
    )


def validate_reserve_recovery_pair(
    *,
    source: P2ContractArtifacts,
    source_store_sha256: str,
    recovered: P2ContractArtifacts,
) -> None:
    """Verify a persisted recovery against its immutable primary source store."""

    source.validate()
    recovered.validate()
    if recovered.plan != source.plan:
        _fail("recovery candidate plan differs from its primary source")
    authorization = recovered.execution.reserve_recovery_authorization
    if authorization is None:
        _fail("recovery artifacts do not contain a recovery authorization")
    expected_authorization = build_reserve_recovery_authorization(
        source=source,
        source_store_sha256=source_store_sha256,
    )
    if authorization != expected_authorization:
        _fail("recovery authorization does not match the supplied primary source")

    source_executions = source.execution.executed
    source_candidate_ids = {item.candidate_id for item in source_executions}
    recovered_primary_executions = tuple(
        item for item in recovered.execution.executed if item.slot_kind == "primary"
    )
    if recovered_primary_executions != source_executions:
        _fail("recovery changed or reordered primary executions")
    recovered_primary_dispositions = tuple(
        item
        for item in recovered.disposition.entries
        if item.candidate_id in source_candidate_ids
    )
    if recovered_primary_dispositions != source.disposition.entries:
        _fail("recovery changed or reordered primary technical dispositions")
    recovered_primary_classifications = tuple(
        item
        for item in recovered.classifications.entries
        if item.candidate_id in source_candidate_ids
    )
    if recovered_primary_classifications != source.classifications.entries:
        _fail("recovery changed or reordered primary classifications")


def execute_reserve_recovery(
    *, runtime: AlphaRuntime, source: P2ContractArtifacts, source_store_sha256: str
) -> P2ContractArtifacts:
    """Replay primaries exactly, execute all label reserves, and apply the amendment."""

    authorization = build_reserve_recovery_authorization(
        source=source, source_store_sha256=source_store_sha256
    )
    if runtime.plan != source.plan:
        _fail("recovery runtime plan differs from the immutable source plan")

    primary_results = tuple(
        execute_alpha_slot(slot, runtime)
        for slot in runtime.plan.slots
        if slot.slot_kind == "primary"
    )
    replay = assemble_alpha_artifacts(
        plan=runtime.plan,
        results=primary_results,
        duplicate_audit=source.duplicate_audit,
    )
    if replay != source:
        _fail("primary replay differs from the immutable recovery source")

    recovery_results = tuple(
        execute_alpha_slot(slot, runtime)
        for slot in runtime.plan.slots
        if slot.slot_id in authorization.activated_reserve_slot_ids
    )
    recovered = assemble_alpha_artifacts(
        plan=runtime.plan,
        results=(*primary_results, *recovery_results),
        duplicate_audit=DuplicateAudit.model_validate(source.duplicate_audit.model_dump()),
        reserve_recovery_authorization=authorization,
    )
    validate_reserve_recovery_pair(
        source=source,
        source_store_sha256=source_store_sha256,
        recovered=recovered,
    )
    return recovered
