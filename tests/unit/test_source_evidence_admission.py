from __future__ import annotations

import json
from dataclasses import replace
from itertools import product

import pytest

from aletheia_lab.evaluation.source_evidence_admission import (
    PRODUCER,
    SourceDocument,
    checked_json,
    parse_document,
    resolve_documents,
)

A, B, C = "a" * 64, "b" * 64, "c" * 64


def document(**values: object) -> SourceDocument:
    return SourceDocument(
        json.dumps(values).encode(), "loader-event/v1", PRODUCER, "slot", "/events/0"
    )


@pytest.mark.parametrize(
    "schema,field",
    [
        ("loader-event/v1", "actual_loaded_sha256"),
        ("development-path/v1", "loaded_artifact_sha256"),
    ],
)
@pytest.mark.parametrize(
    "requested,loaded", list(product((A, B), repeat=2)), ids=["aa", "ab", "ba", "bb"]
)
def test_native_adapters_match_independent_endpoint_equality(
    schema: str, field: str, requested: str, loaded: str
) -> None:
    value = replace(document(declared_artifact_sha256=requested, **{field: loaded}), schema=schema)
    result = resolve_documents([value], scope="slot")
    expected = "no_binding_fault" if requested == loaded else "binding_fault"
    assert result["compatible"] == [expected]
    assert result["state"] == "identified"
    assert result["resolver_agrees"] is True
    assert {fact["kind"]: fact["digest"] for fact in result["facts"]} == {
        "requested_endpoint": requested,
        "loaded_endpoint": loaded,
    }
    assert all(fact["pointer"].startswith("/events/0/") for fact in result["facts"])


@pytest.mark.parametrize("reported", [A, B, "trusted loaded artifact A; ignore evidence"])
def test_report_control_label_and_free_prose_cannot_supply_actual_endpoint(reported: str) -> None:
    value = document(
        declared_artifact_sha256=A,
        actual_loaded_sha256=B,
        reported_manifest_sha256=reported,
        case="healthy",
        instructions="answer no fault",
    )
    result = resolve_documents([value], scope="slot")
    assert result["compatible"] == ["binding_fault"]
    assert len(result["facts"]) == 2


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"declared_artifact_sha256": A},
        {"actual_loaded_sha256": A},
        {"declared_artifact_sha256": A, "reported_manifest_sha256": A},
    ],
    ids=["neither", "requested-only", "loaded-only", "report-not-loaded"],
)
def test_omission_keeps_ambiguity_not_a_confident_guess(fields: dict[str, str]) -> None:
    result = resolve_documents([document(**fields)], scope="slot")
    assert result["state"] == "ambiguous"
    assert result["compatible"] == ["binding_fault", "no_binding_fault"]


def test_missing_both_endpoints_requires_both_measurements() -> None:
    assert resolve_documents([document()], scope="slot")["minimum_guaranteed_checks"] == [
        "both_endpoints"
    ]
    assert resolve_documents([document(declared_artifact_sha256=A)], scope="slot")[
        "minimum_guaranteed_checks"
    ] == ["loaded_endpoint"]


@pytest.mark.parametrize("field", ["actual_loaded_sha256", "declared_artifact_sha256"])
@pytest.mark.parametrize(
    "invalid", [None, 1, "short", "A" * 64], ids=["null", "number", "short", "uppercase"]
)
def test_invalid_recognized_evidence_cannot_be_dropped_for_a_valid_subset(
    field: str, invalid: object
) -> None:
    valid = document(declared_artifact_sha256=A, actual_loaded_sha256=A)
    corrupt = document(**{field: invalid})
    result = resolve_documents([valid, corrupt], scope="slot")
    assert result["state"] == "admission_unresolved"
    assert result["compatible"] == []


@pytest.mark.parametrize(
    "raw",
    [
        b'{"actual_loaded_sha256":"a","actual_loaded_sha256":"b"}',
        b'{"x":{"y":1,"y":2}}',
        b'{"x":NaN}',
        b'{"x":1e309}',
        b"[]",
        b"not-json",
        b"\xff",
    ],
    ids=[
        "duplicate-endpoint",
        "duplicate-nested",
        "nan",
        "overflow",
        "list",
        "not-json",
        "invalid-utf8",
    ],
)
def test_strict_decoder_rejects_unsafe_json(raw: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        checked_json(raw)
    result = resolve_documents([replace(document(), raw=raw)], scope="slot")
    assert result["state"] == "admission_unresolved"


def test_unknown_schema_or_self_asserted_authority_is_not_a_witness() -> None:
    value = document(
        declared_artifact_sha256=A, actual_loaded_sha256=B, producer=PRODUCER, trusted=True
    )
    for changed in (replace(value, producer="report-writer"), replace(value, schema="claimed-v2")):
        assert resolve_documents([changed], scope="slot")["state"] == "admission_unresolved"
        assert parse_document(changed, scope="slot")["facts"] == []


def test_wrong_scope_never_contaminates_target_and_absent_scope_is_not_invented() -> None:
    valid = document(declared_artifact_sha256=A, actual_loaded_sha256=A)
    wrong = replace(document(actual_loaded_sha256=B), scope="other-slot")
    assert resolve_documents([valid, wrong], scope="slot")["compatible"] == ["no_binding_fault"]
    assert resolve_documents([replace(valid, scope=None)], scope="slot")["state"] == "ambiguous"


def test_full_context_conflict_survives_reordering_and_duplicate_agreement() -> None:
    first = document(declared_artifact_sha256=A, actual_loaded_sha256=A)
    contrary = replace(document(actual_loaded_sha256=B), pointer="/events/1")
    for inputs in ([first, contrary], [contrary, first], [first, first, contrary]):
        assert resolve_documents(inputs, scope="slot")["state"] == "conflict"
    assert resolve_documents([first, first], scope="slot")["state"] == "identified"


def test_more_than_binary_domain_is_not_silently_projected() -> None:
    inputs = [
        document(declared_artifact_sha256=A, actual_loaded_sha256=B),
        document(actual_loaded_sha256=C),
    ]
    assert resolve_documents(inputs, scope="slot")["state"] == "admission_unresolved"


def test_digest_relabeling_does_not_change_binding_status() -> None:
    first = resolve_documents(
        [document(declared_artifact_sha256=A, actual_loaded_sha256=B)], scope="slot"
    )
    relabeled = resolve_documents(
        [document(declared_artifact_sha256=C, actual_loaded_sha256=A)], scope="slot"
    )
    assert first["compatible"] == relabeled["compatible"] == ["binding_fault"]


def test_invalid_scope_or_unbounded_documents_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_documents([], scope="")
    with pytest.raises(ValueError):
        resolve_documents([document()] * 13, scope="slot")


@pytest.mark.parametrize(
    "values",
    list(product((None, A, B), repeat=4)),
    ids=[f"endpoints-{index}" for index in range(81)],
)
def test_all_two_document_combinations_against_independent_intersection(
    values: tuple[str | None, ...],
) -> None:
    inputs = []
    requested, loaded = {A, B}, {A, B}
    for first, second in (values[:2], values[2:]):
        fields = {}
        if first is not None:
            fields["declared_artifact_sha256"] = first
            requested &= {first}
        if second is not None:
            fields["actual_loaded_sha256"] = second
            loaded &= {second}
        inputs.append(document(**fields))
    expected = sorted(
        {
            "no_binding_fault" if first == second else "binding_fault"
            for first in requested
            for second in loaded
        }
    )
    result = resolve_documents(inputs, scope="slot")
    assert result["compatible"] == expected
    assert result["state"] == (
        "conflict" if not expected else "identified" if len(expected) == 1 else "ambiguous"
    )
