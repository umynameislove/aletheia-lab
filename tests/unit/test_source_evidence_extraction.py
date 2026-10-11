from __future__ import annotations

import json
from dataclasses import replace
from itertools import product
from typing import Any

import pytest

from aletheia_lab.evaluation.source_evidence_admission import (
    PRODUCER,
    SourceDocument,
    resolve_documents,
)
from aletheia_lab.evaluation.source_evidence_extraction import assess_extraction, combine_readings
from aletheia_lab.project.identity import content_sha256

A, B = "a" * 64, "b" * 64


def document(**values: object) -> SourceDocument:
    return SourceDocument(
        json.dumps(values).encode(), "loader-event/v1", PRODUCER, "slot", "/record"
    )


def proposal(facts: object) -> bytes:
    return json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}).encode()


@pytest.mark.parametrize(
    "requested,loaded", list(product((A, B), repeat=2)), ids=["aa", "ab", "ba", "bb"]
)
def test_exact_fact_proposal_uses_same_input_and_fixed_resolver(
    requested: str, loaded: str
) -> None:
    inputs = [document(declared_artifact_sha256=requested, actual_loaded_sha256=loaded)]
    baseline = resolve_documents(inputs, scope="slot")
    result = assess_extraction(inputs, scope="slot", proposal=proposal(baseline["facts"]))
    assert result["status"] == "admitted"
    assert result["candidate_resolution"]["compatible"] == [
        "no_binding_fault" if requested == loaded else "binding_fault"
    ]
    assert result["grounding_error_count"] == result["semantic_role_error_count"] == 0


def test_manifest_is_grounded_but_not_authorized_consumed_buffer() -> None:
    value = document(declared_artifact_sha256=A, actual_loaded_sha256=B, reported_manifest_sha256=A)
    facts = resolve_documents([value], scope="slot")["facts"]
    facts[1] = {**facts[1], "digest": A, "pointer": "/record/reported_manifest_sha256"}
    result = assess_extraction([value], scope="slot", proposal=proposal(facts))
    assert result["grounding_error_count"] == 0
    assert result["semantic_role_error_count"] == 1
    assert result["omitted_eligible_fact_count"] == 1
    assert result["candidate_resolution"] is None


def test_same_status_does_not_excuse_swapped_roles() -> None:
    value = document(declared_artifact_sha256=A, actual_loaded_sha256=B)
    facts = resolve_documents([value], scope="slot")["facts"]
    facts[0]["kind"], facts[1]["kind"] = facts[1]["kind"], facts[0]["kind"]
    result = assess_extraction([value], scope="slot", proposal=proposal(facts))
    assert result["grounding_error_count"] == 0
    assert result["semantic_role_error_count"] == 2
    assert result["status"] == "rejected"


def test_omitted_same_scope_conflict_cannot_obtain_subset_commitment() -> None:
    first = document(declared_artifact_sha256=A, actual_loaded_sha256=A)
    contrary = replace(document(actual_loaded_sha256=B), pointer="/other")
    subset = resolve_documents([first], scope="slot")["facts"]
    result = assess_extraction([first, contrary], scope="slot", proposal=proposal(subset))
    assert result["baseline_state"] == "conflict"
    assert result["grounding_error_count"] == 0
    assert result["omitted_eligible_fact_count"] == 1
    assert result["candidate_resolution"] is None
    all_facts = resolve_documents([first, contrary], scope="slot")["facts"]
    assert (
        assess_extraction([first, contrary], scope="slot", proposal=proposal(all_facts))[
            "candidate_resolution"
        ]["state"]
        == "conflict"
    )


@pytest.mark.parametrize("field", ["digest", "pointer", "document_sha256", "kind"])
def test_identity_or_fact_corruption_is_never_promoted(field: str) -> None:
    value = document(declared_artifact_sha256=A, actual_loaded_sha256=B)
    facts = resolve_documents([value], scope="slot")["facts"]
    facts[0][field] = "invented"
    assert (
        assess_extraction([value], scope="slot", proposal=proposal(facts))["status"] == "rejected"
    )


@pytest.mark.parametrize("change", ["scope", "producer", "schema"])
def test_output_cannot_grant_source_authority_or_scope(change: str) -> None:
    value = document(declared_artifact_sha256=A, actual_loaded_sha256=B)
    facts = resolve_documents([value], scope="slot")["facts"]
    unverified = replace(value, **{change: "unverified"})
    assert (
        assess_extraction([unverified], scope="slot", proposal=proposal(facts))["status"]
        == "rejected"
    )


@pytest.mark.parametrize(
    "raw",
    [b"{}", b"not-json", b"[]", b'{"schema_version":"a","schema_version":"b"}', b'{"x":NaN}'],
    ids=["empty", "prose", "list", "duplicate", "nan"],
)
def test_invalid_proposal_fails_without_source_dump(raw: bytes) -> None:
    result = assess_extraction([document()], scope="slot", proposal=raw)
    assert result["status"] == "invalid_proposal"
    assert result["candidate_resolution"] is None
    assert "not-json" not in json.dumps(result)


def test_duplicate_facts_and_unknown_grammar_are_rejected() -> None:
    value = document(declared_artifact_sha256=A)
    facts = resolve_documents([value], scope="slot")["facts"]
    assert (
        assess_extraction([value], scope="slot", proposal=proposal(facts * 2))["status"]
        == "invalid_proposal"
    )
    facts[0]["trusted"] = "true"
    assert (
        assess_extraction([value], scope="slot", proposal=proposal(facts))["status"]
        == "invalid_proposal"
    )


def test_malformed_recognized_source_is_not_replaced_by_empty_output() -> None:
    result = assess_extraction(
        [document(actual_loaded_sha256=None)], scope="slot", proposal=proposal([])
    )
    assert result["baseline_state"] == "admission_unresolved"
    assert result["status"] == "rejected"


def test_missing_native_endpoint_remains_ambiguous_even_after_valid_extraction() -> None:
    value = document(actual_loaded_sha256=B)
    facts = [
        {
            "kind": "loaded_endpoint",
            "digest": B,
            "pointer": "/record/actual_loaded_sha256",
            "document_sha256": content_sha256(value.raw),
        }
    ]
    result = assess_extraction([value], scope="slot", proposal=proposal(facts))
    assert result["candidate_resolution"]["state"] == "ambiguous"


READINGS = [
    {"state": "identified", "compatible": ["no_binding_fault"]},
    {"state": "identified", "compatible": ["binding_fault"]},
    {"state": "ambiguous", "compatible": ["binding_fault", "no_binding_fault"]},
    {"state": "conflict", "compatible": []},
    {"state": "admission_unresolved", "compatible": []},
]


@pytest.mark.parametrize(
    "first,second", list(product(range(5), repeat=2)), ids=[f"readings-{i}" for i in range(25)]
)
def test_all_two_reading_combinations_preserve_conflict_and_unknown(
    first: int, second: int
) -> None:
    readings = [READINGS[first], READINGS[second]]
    result = combine_readings(readings, coverage_verified=True)
    if 4 in (first, second):
        expected = "admission_unresolved"
    elif first == second == 3:
        expected = "conflict"
    elif 3 in (first, second):
        expected = "reading_reconciliation_required"
    elif first == second and first < 2:
        expected = "identified"
    else:
        expected = "ambiguous"
    assert result["state"] == expected
    assert result["commit_permitted"] is (expected == "identified")


@pytest.mark.parametrize("coverage,unknown", [(False, False), (False, True), (True, True)])
def test_singleton_is_not_safe_when_true_reading_coverage_is_unknown(
    coverage: bool, unknown: bool
) -> None:
    result = combine_readings([READINGS[0]], coverage_verified=coverage, unknown_reading=unknown)
    assert result["state"] == "admission_unresolved"
    assert result["commit_permitted"] is False
    assert result["compatible"] == []
    assert result["listed_reading_compatible"] == ["no_binding_fault"]


@pytest.mark.parametrize(
    "reading",
    [
        {"state": []},
        {"state": "identified", "compatible": []},
        {"state": "conflict", "compatible": ["binding_fault"]},
        {"state": "identified", "compatible": ["binding_fault", "binding_fault"]},
        {"state": "ambiguous", "compatible": [0]},
    ],
)
def test_malformed_reading_is_not_a_semantic_branch(reading: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        combine_readings([reading], coverage_verified=True)


def test_reading_frame_and_caller_flags_are_bounded() -> None:
    for readings in ([], [READINGS[0]] * 13):
        with pytest.raises(ValueError):
            combine_readings(readings, coverage_verified=True)
    with pytest.raises(ValueError):
        combine_readings([READINGS[0]], coverage_verified="true")  # type: ignore[arg-type]
