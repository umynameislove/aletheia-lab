"""Fail-closed coverage tests for admitted Phase 2 evidence families."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    ContextCensus,
    ContextEntry,
    FamilyCensus,
    FamilyCensusEntry,
    context_id_for,
)
from aletheia_lab.benchmark.p2.coverage import (
    CandidateCensus,
    CandidateCensusEntry,
    MechanismCoverageError,
    MechanismCoverageFinding,
    assess_mechanism_coverage,
    require_mechanism_coverage,
)

_MECHANISMS = ("data_drift", "label_noise", "preprocessing_bug")
_CONDITIONS = ("full", "missing_key", "noisy")


def _family(marker: str, mechanism: str, *, family_class: str = "eligible_failure"):
    return FamilyCensusEntry(
        case_family_id=f"p2-family-{marker * 64}",
        candidate_id=f"p2-candidate-{marker * 64}",
        fault_type=mechanism,  # type: ignore[arg-type]
        family_class=family_class,  # type: ignore[arg-type]
        proposed_family_sha256=marker * 64,
    )


def _context(
    family: FamilyCensusEntry,
    condition: str,
    *,
    source_marker: str | None = None,
    content_marker: str | None = None,
) -> ContextEntry:
    source = (source_marker or family.proposed_family_sha256[0]) * 64
    content = content_marker or family.proposed_family_sha256[0]
    projection = {
        "source_binding_sha256": source,
        "items": [{"id": "comparison", "value": f"observed-{content}-{condition[0]}"}],
    }
    return ContextEntry(
        diagnosis_context_id=context_id_for(
            case_family_id=family.case_family_id,
            evidence_condition=condition,
        ),
        case_family_id=family.case_family_id,
        evidence_condition=condition,  # type: ignore[arg-type]
        diagnosis_projection=projection,
        diagnosis_projection_sha256=canonical_sha256(projection),
    )


def _complete_store():  # type: ignore[no-untyped-def]
    families = tuple(
        _family(marker, mechanism) for marker, mechanism in zip("abc", _MECHANISMS, strict=True)
    )
    contexts = tuple(
        _context(family, condition) for family in families for condition in _CONDITIONS
    )
    return (
        FamilyCensus(schema_version="p2-family-census/1", entries=families),
        ContextCensus(schema_version="p2-context-census/1", entries=contexts),
    )


def test_one_complete_independent_failure_per_mechanism_passes() -> None:
    census, contexts = _complete_store()
    audit = assess_mechanism_coverage(census=census, contexts=contexts)

    assert audit.passed
    assert all(entry.passed for entry in audit.mechanisms)
    assert all(len(entry.complete_independent_family_ids) == 1 for entry in audit.mechanisms)
    require_mechanism_coverage(audit)


def test_stable_controls_do_not_substitute_for_an_eligible_failure() -> None:
    census, contexts = _complete_store()
    label = next(entry for entry in census.entries if entry.fault_type == "label_noise")
    stable = label.model_copy(update={"family_class": "stable_control"})
    changed_census = FamilyCensus(
        schema_version="p2-family-census/1",
        entries=tuple(stable if entry == label else entry for entry in census.entries),
    )
    changed_contexts = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for context in contexts.entries
            if context.case_family_id != label.case_family_id
            or context.evidence_condition == "full"
        ),
    )

    audit = assess_mechanism_coverage(census=changed_census, contexts=changed_contexts)
    record = next(entry for entry in audit.mechanisms if entry.fault_type == "label_noise")
    assert not record.passed
    assert [finding.reason_code for finding in record.findings] == ["no_eligible_failure"]


def test_missing_sibling_fails_with_family_bound_reason() -> None:
    census, contexts = _complete_store()
    label = next(entry for entry in census.entries if entry.fault_type == "label_noise")
    incomplete = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for context in contexts.entries
            if not (
                context.case_family_id == label.case_family_id
                and context.evidence_condition == "noisy"
            )
        ),
    )

    audit = assess_mechanism_coverage(census=census, contexts=incomplete)
    record = next(entry for entry in audit.mechanisms if entry.fault_type == "label_noise")
    assert record.complete_independent_family_ids == ()
    assert record.findings[0].reason_code == "incomplete_evidence_conditions"
    assert record.findings[0].family_ids == (label.case_family_id,)


def test_cross_family_projection_replay_is_not_independent_coverage() -> None:
    first = _family("a", "data_drift")
    second = _family("b", "data_drift")
    label = _family("c", "label_noise")
    preprocessing = _family("d", "preprocessing_bug")
    families = (first, second, label, preprocessing)
    contexts: list[ContextEntry] = []
    for family in families:
        for condition in _CONDITIONS:
            replay = family in {first, second}
            contexts.append(
                _context(
                    family,
                    condition,
                    source_marker="f" if replay else None,
                    content_marker="shared" if replay else None,
                )
            )
    census = FamilyCensus(schema_version="p2-family-census/1", entries=families)
    context_census = ContextCensus(schema_version="p2-context-census/1", entries=tuple(contexts))

    audit = assess_mechanism_coverage(census=census, contexts=context_census)
    drift = next(entry for entry in audit.mechanisms if entry.fault_type == "data_drift")
    finding = next(item for item in drift.findings if item.reason_code == "evidence_content_reuse")
    assert finding.family_ids == tuple(sorted((first.case_family_id, second.case_family_id)))
    assert finding.evidence_condition == "full"
    assert not drift.passed


def test_cross_mechanism_projection_replay_invalidates_both_sources() -> None:
    drift = _family("a", "data_drift")
    label = _family("b", "label_noise")
    preprocessing = _family("c", "preprocessing_bug")
    families = (drift, label, preprocessing)
    contexts = tuple(
        _context(
            family,
            condition,
            source_marker="f" if family in {drift, label} else None,
            content_marker="shared" if family in {drift, label} else None,
        )
        for family in families
        for condition in _CONDITIONS
    )

    audit = assess_mechanism_coverage(
        census=FamilyCensus(schema_version="p2-family-census/1", entries=families),
        contexts=ContextCensus(schema_version="p2-context-census/1", entries=contexts),
    )
    by_fault = {entry.fault_type: entry for entry in audit.mechanisms}

    assert not by_fault["data_drift"].passed
    assert not by_fault["label_noise"].passed
    assert by_fault["preprocessing_bug"].passed
    assert {finding.reason_code for finding in by_fault["label_noise"].findings} == {
        "evidence_content_reuse"
    }


def test_projection_replay_does_not_hide_an_independent_family() -> None:
    first = _family("a", "data_drift")
    replay = _family("b", "data_drift")
    independent = _family("c", "data_drift")
    label = _family("d", "label_noise")
    preprocessing = _family("e", "preprocessing_bug")
    families = (first, replay, independent, label, preprocessing)
    contexts = tuple(
        _context(
            family,
            condition,
            source_marker="f" if family in {first, replay} else None,
            content_marker="shared" if family in {first, replay} else None,
        )
        for family in families
        for condition in _CONDITIONS
    )

    audit = assess_mechanism_coverage(
        census=FamilyCensus(schema_version="p2-family-census/1", entries=families),
        contexts=ContextCensus(schema_version="p2-context-census/1", entries=contexts),
    )
    drift = next(entry for entry in audit.mechanisms if entry.fault_type == "data_drift")

    assert drift.passed
    assert drift.complete_independent_family_ids == (independent.case_family_id,)
    assert {finding.reason_code for finding in drift.findings} == {"evidence_content_reuse"}


def test_replay_from_a_stable_control_cannot_supply_an_eligible_sibling() -> None:
    census, contexts = _complete_store()
    label = next(entry for entry in census.entries if entry.fault_type == "label_noise")
    stable = _family("d", "data_drift", family_class="stable_control")
    stable_full = _context(stable, "full", source_marker="d", content_marker="shared")
    label_contexts = tuple(
        _context(
            label,
            condition,
            source_marker="d",
            content_marker="shared" if condition == "full" else None,
        )
        for condition in _CONDITIONS
    )
    changed_contexts = tuple(
        context for context in contexts.entries if context.case_family_id != label.case_family_id
    ) + (*label_contexts, stable_full)

    audit = assess_mechanism_coverage(
        census=FamilyCensus(schema_version="p2-family-census/1", entries=(*census.entries, stable)),
        contexts=ContextCensus(schema_version="p2-context-census/1", entries=changed_contexts),
    )
    label_coverage = next(entry for entry in audit.mechanisms if entry.fault_type == "label_noise")

    assert not label_coverage.passed
    assert label_coverage.complete_independent_family_ids == ()
    assert {finding.reason_code for finding in label_coverage.findings} == {
        "evidence_content_reuse"
    }


def test_source_binding_mismatch_blocks_a_complete_family() -> None:
    census, contexts = _complete_store()
    label = next(entry for entry in census.entries if entry.fault_type == "label_noise")
    changed = tuple(
        _context(
            label,
            context.evidence_condition,
            source_marker="f" if context.evidence_condition == "noisy" else "c",
        )
        if context.case_family_id == label.case_family_id
        else context
        for context in contexts.entries
    )

    audit = assess_mechanism_coverage(
        census=census,
        contexts=ContextCensus(schema_version="p2-context-census/1", entries=changed),
    )
    record = next(entry for entry in audit.mechanisms if entry.fault_type == "label_noise")
    assert record.findings[0].reason_code == "source_binding_mismatch"
    assert not record.passed


def test_structured_error_retains_machine_readable_audit() -> None:
    census, contexts = _complete_store()
    label = next(entry for entry in census.entries if entry.fault_type == "label_noise")
    incomplete = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for context in contexts.entries
            if context.case_family_id != label.case_family_id
        ),
    )
    audit = assess_mechanism_coverage(census=census, contexts=incomplete)

    with pytest.raises(MechanismCoverageError) as raised:
        require_mechanism_coverage(audit)
    assert raised.value.audit == audit
    assert "no_eligible_failure" not in str(raised.value)
    assert "incomplete_evidence_conditions" in str(raised.value)


def test_projection_hash_tamper_fails_before_coverage_is_counted() -> None:
    family = _family("a", "data_drift")
    context = _context(family, "full")
    payload = context.model_dump(mode="json")
    payload["diagnosis_projection_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match="does not match projection bytes"):
        ContextEntry.model_validate(payload)


def _inactive(slot_id: str = "M1-R1") -> CandidateCensusEntry:
    return CandidateCensusEntry(
        slot_id=slot_id,
        fault_type="data_drift",
        role="fault_directed",
        slot_kind="reserve",
        lifecycle_status="inactive_reserve",
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"candidate_id": f"p2-candidate-{'a' * 64}"}, "unexecuted reserve"),
        ({"measured_outcome": "stable"}, "must not claim downstream"),
        (
            {"lifecycle_status": "technical_rejected", "candidate_id": None},
            "must carry a candidate ID",
        ),
        (
            {
                "lifecycle_status": "technical_rejected",
                "candidate_id": f"p2-candidate-{'a' * 64}",
            },
            "retain its reason code",
        ),
        (
            {
                "lifecycle_status": "technical_rejected",
                "candidate_id": f"p2-candidate-{'a' * 64}",
                "technical_rejection_reason": "one_factor_violation",
                "measured_outcome": "stable",
            },
            "must not enter classification",
        ),
        (
            {
                "lifecycle_status": "excluded_valid",
                "candidate_id": f"p2-candidate-{'a' * 64}",
            },
            "requires one measured outcome",
        ),
        (
            {
                "lifecycle_status": "excluded_valid",
                "candidate_id": f"p2-candidate-{'a' * 64}",
                "measured_outcome": "stable",
            },
            "retain its reason code",
        ),
        (
            {
                "lifecycle_status": "excluded_valid",
                "candidate_id": f"p2-candidate-{'a' * 64}",
                "measured_outcome": "stable",
                "admission_exclusion_reason": "evidence_leakage",
                "case_family_id": f"p2-family-{'a' * 64}",
            },
            "must not claim family evidence",
        ),
        (
            {
                "lifecycle_status": "accepted",
                "candidate_id": f"p2-candidate-{'a' * 64}",
                "measured_outcome": "regression",
            },
            "retain its family identity",
        ),
        (
            {
                "lifecycle_status": "accepted",
                "candidate_id": f"p2-candidate-{'a' * 64}",
                "measured_outcome": "regression",
                "case_family_id": f"p2-family-{'a' * 64}",
                "family_class": "eligible_failure",
                "evidence_conditions": ("full",),
            },
            "evidence conditions disagree",
        ),
    ],
)
def test_candidate_census_entry_rejects_ambiguous_terminal_states(
    updates: dict[str, object], message: str
) -> None:
    payload = _inactive().model_dump()
    payload.update(updates)
    with pytest.raises(ValidationError, match=message):
        CandidateCensusEntry.model_validate(payload)


def test_candidate_census_requires_unique_canonical_membership() -> None:
    first = _inactive("M1-R1")
    second = _inactive("M1-R2")
    with pytest.raises(ValidationError, match="each slot exactly once"):
        CandidateCensus(entries=(first, first))
    with pytest.raises(ValidationError, match="canonical slot order"):
        CandidateCensus(entries=(second, first))

    candidate = f"p2-candidate-{'a' * 64}"
    rejected = first.model_copy(
        update={
            "candidate_id": candidate,
            "lifecycle_status": "technical_rejected",
            "technical_rejection_reason": "one_factor_violation",
        }
    )
    repeated = second.model_copy(
        update={
            "candidate_id": candidate,
            "lifecycle_status": "technical_rejected",
            "technical_rejection_reason": "one_factor_violation",
        }
    )
    with pytest.raises(ValidationError, match="must not repeat a candidate"):
        CandidateCensus(entries=(rejected, repeated))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "reason_code": "incomplete_evidence_conditions",
            "family_ids": (f"p2-family-{'a' * 64}", f"p2-family-{'a' * 64}"),
            "detail": "duplicate IDs",
        },
        {
            "reason_code": "incomplete_evidence_conditions",
            "family_ids": (f"p2-family-{'b' * 64}", f"p2-family-{'a' * 64}"),
            "detail": "noncanonical IDs",
        },
        {
            "reason_code": "no_eligible_failure",
            "family_ids": (f"p2-family-{'a' * 64}",),
            "detail": "zero-family finding names a family",
        },
        {
            "reason_code": "evidence_content_reuse",
            "family_ids": (f"p2-family-{'a' * 64}",),
            "detail": "reuse needs a pair",
        },
    ],
)
def test_coverage_findings_reject_ambiguous_targets(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MechanismCoverageFinding.model_validate(payload)
