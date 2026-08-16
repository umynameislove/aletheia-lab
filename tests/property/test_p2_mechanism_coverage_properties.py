"""Property checks for mechanism-complete evidence admission."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    ContextCensus,
    ContextEntry,
    FamilyCensus,
    FamilyCensusEntry,
    context_id_for,
)
from aletheia_lab.benchmark.p2.coverage import assess_mechanism_coverage

_MECHANISMS = ("data_drift", "label_noise", "preprocessing_bug")
_CONDITIONS = ("full", "missing_key", "noisy")


def _fixtures():  # type: ignore[no-untyped-def]
    families: list[FamilyCensusEntry] = []
    contexts: list[ContextEntry] = []
    for marker, mechanism in zip("abc", _MECHANISMS, strict=True):
        family = FamilyCensusEntry(
            case_family_id=f"p2-family-{marker * 64}",
            candidate_id=f"p2-candidate-{marker * 64}",
            fault_type=mechanism,  # type: ignore[arg-type]
            family_class="eligible_failure",
            proposed_family_sha256=marker * 64,
        )
        families.append(family)
        for index, condition in enumerate(_CONDITIONS):
            projection = {
                "source_binding_sha256": marker * 64,
                "items": [{"id": "comparison", "value": f"observed-{marker}-{index}"}],
            }
            contexts.append(
                ContextEntry(
                    diagnosis_context_id=context_id_for(
                        case_family_id=family.case_family_id,
                        evidence_condition=condition,
                    ),
                    case_family_id=family.case_family_id,
                    evidence_condition=condition,  # type: ignore[arg-type]
                    diagnosis_projection=projection,
                    diagnosis_projection_sha256=canonical_sha256(projection),
                )
            )
    return (
        FamilyCensus(schema_version="p2-family-census/1", entries=tuple(families)),
        ContextCensus(schema_version="p2-context-census/1", entries=tuple(contexts)),
    )


@given(
    mechanism=st.sampled_from(_MECHANISMS),
    missing_condition=st.sampled_from(_CONDITIONS),
)
def test_removing_any_required_sibling_blocks_only_its_mechanism(
    mechanism: str,
    missing_condition: str,
) -> None:
    census, contexts = _fixtures()
    family = next(entry for entry in census.entries if entry.fault_type == mechanism)
    incomplete = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for context in contexts.entries
            if not (
                context.case_family_id == family.case_family_id
                and context.evidence_condition == missing_condition
            )
        ),
    )

    audit = assess_mechanism_coverage(census=census, contexts=incomplete)
    failed = {entry.fault_type for entry in audit.mechanisms if not entry.passed}
    finding = next(entry for entry in audit.mechanisms if entry.fault_type == mechanism)

    assert failed == {mechanism}
    assert {item.reason_code for item in finding.findings} == {"incomplete_evidence_conditions"}


@given(mechanism=st.sampled_from(_MECHANISMS))
def test_reclassifying_an_eligible_family_as_stable_never_preserves_coverage(
    mechanism: str,
) -> None:
    census, contexts = _fixtures()
    family = next(entry for entry in census.entries if entry.fault_type == mechanism)
    changed = family.model_copy(update={"family_class": "stable_control"})
    stable_census = FamilyCensus(
        schema_version="p2-family-census/1",
        entries=tuple(changed if entry == family else entry for entry in census.entries),
    )
    control_contexts = ContextCensus(
        schema_version="p2-context-census/1",
        entries=tuple(
            context
            for context in contexts.entries
            if context.case_family_id != family.case_family_id
            or context.evidence_condition == "full"
        ),
    )

    audit = assess_mechanism_coverage(census=stable_census, contexts=control_contexts)
    failed = next(entry for entry in audit.mechanisms if entry.fault_type == mechanism)

    assert not failed.passed
    assert failed.eligible_family_ids == ()
    assert {item.reason_code for item in failed.findings} == {"no_eligible_failure"}
