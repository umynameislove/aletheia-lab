"""Citation aliases never gain endpoint facts, source authority or guard exceptions."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from aletheia_lab.evaluation.native_cache_citation import (
    PROMPT,
    citation_schema,
    diagnose_citations,
    normalize_citations,
)
from aletheia_lab.evaluation.native_cache_extraction import parser_facts
from aletheia_lab.model_gateway.openai import _openai_response_format
from aletheia_lab.project.identity import content_sha256

A, B = "a" * 64, "b" * 64


def document(identifier, kind, authority, value):
    text = json.dumps(value) if isinstance(value, dict) else value
    return {
        "id": identifier,
        "kind": kind,
        "authority": authority,
        "scope": "attempt-0",
        "text": text,
        "sha256": content_sha256(text.encode()),
    }


def documents(consumed=B):
    result = [
        document(
            "native-out",
            "joblib-stdout",
            "native-message",
            "[Memory] Loading cached_value from cache/joblib/fn/" + "1" * 32,
        ),
        document(
            "native-query",
            "joblib-query",
            "native-message",
            "Querying cached_value (argument hash " + "1" * 32 + ")",
        ),
        document(
            "intent",
            "caller-intent",
            "caller-pin",
            {"schema_version": "cache-intent/v1", "requested_sha256": A},
        ),
    ]
    if consumed is not None:
        result.append(
            document(
                "consumer",
                "consumer-witness",
                "same-buffer-consumer",
                {"schema_version": "cache-consumer/v1", "consumed_sha256": consumed},
            )
        )
    return result


def proposal(facts):
    return json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}).encode()


@pytest.mark.parametrize(
    "selector,suffix,expansion,substitution",
    [
        ("intent", "requested_sha256", 0, 0),
        ("2", "requested_sha256", 0, 1),
        ("intent", "text", 1, 0),
        ("2", "text", 1, 0),
    ],
    ids=["canonical", "index-field", "id-text", "index-text"],
)
def test_alias_resolution_preserves_all_proposed_content(selector, suffix, expansion, substitution):
    inputs = documents()
    expected = parser_facts(inputs)
    actual = deepcopy(expected)
    actual[0]["pointer"] = f"/documents/{selector}/{suffix}"
    before = deepcopy(actual)
    normalized, counts = normalize_citations(inputs, actual)
    assert actual == before and normalized == expected
    assert counts == {
        "citation_repaired_fact_count": expansion + substitution,
        "document_text_expansion_count": expansion,
        "explicit_field_locator_substitution_count": substitution,
    }
    result = diagnose_citations(inputs, proposal(actual))
    assert result["role_digest_frame_equal"] and result["role_digest_document_frame_equal"]
    assert result["raw_resolution_matches_reference"]
    assert result["normalized_assessment"]["status"] == "admitted"
    assert result["strict_assessment"]["status"] == (
        "rejected" if expansion + substitution else "admitted"
    )


@pytest.mark.parametrize(
    "pointer",
    [
        "/documents/intent/consumed_sha256",
        "/documents/2/consumed_sha256",
        "/documents/consumer/requested_sha256",
        "/documents/native-out/text",
        "/documents/0/text",
        "/documents/missing/text",
        "/documents/02/text",
        "/documents/-1/text",
        "/documents/99/text",
        "/documents/intent/text/requested_sha256",
        "/documents/intent/unknown",
        "/documents/intent/~1requested_sha256",
    ],
    ids=[
        "wrong-id-field",
        "wrong-index-field",
        "wrong-source",
        "native-id",
        "native-index",
        "missing",
        "leading-zero",
        "negative",
        "out-of-range",
        "extra-level",
        "unknown-field",
        "escape",
    ],
)
def test_failed_locators_cannot_be_repaired_using_a_matching_digest(pointer):
    inputs = documents()
    facts = parser_facts(inputs)
    facts[0]["pointer"] = pointer
    result = diagnose_citations(inputs, proposal(facts))
    assert result["strict_assessment"]["status"] == "rejected"
    assert result["normalized_assessment"] is None
    assert not result["citation_only_rejection"]


@pytest.mark.parametrize(
    "field,value",
    [("kind", "loaded_endpoint"), ("digest", B), ("document_sha256", "c" * 64)],
    ids=["wrong-role", "wrong-digest", "wrong-hash"],
)
def test_text_expansion_never_changes_proposed_role_digest_or_document_hash(field, value):
    inputs = documents(consumed=A)
    facts = parser_facts(inputs)
    facts[0]["pointer"] = "/documents/intent/text"
    facts[0][field] = value
    assert diagnose_citations(inputs, proposal(facts))["normalized_assessment"] is None


def test_reversed_roles_with_matching_fault_status_still_fail():
    inputs = documents()
    facts = parser_facts(inputs)
    facts[0]["kind"], facts[1]["kind"] = facts[1]["kind"], facts[0]["kind"]
    facts[0]["pointer"], facts[1]["pointer"] = "/documents/2/text", "/documents/3/text"
    result = diagnose_citations(inputs, proposal(facts))
    assert result["raw_resolution_matches_reference"]
    assert not result["role_digest_frame_equal"] and result["normalized_assessment"] is None


def test_aliases_cannot_invent_missing_loaded_endpoint():
    inputs = documents(consumed=None)
    facts = parser_facts(inputs)
    actual = [*facts, {**facts[0], "kind": "loaded_endpoint", "pointer": "/documents/2/text"}]
    result = diagnose_citations(inputs, proposal(actual))
    assert result["strict_assessment"]["unsafe_raw_commit"]
    assert result["normalized_assessment"] is None


def test_normalization_preserves_omissions_and_contrary_witness_denominator():
    inputs = documents(consumed=A)
    contrary = documents(consumed=B)[-1]
    contrary["id"] = "contrary"
    inputs = [*inputs[2:], contrary]
    facts = parser_facts(inputs[:2])
    facts[0]["pointer"] = "/documents/intent/text"
    result = diagnose_citations(inputs, proposal(facts))
    assert result["normalized_assessment"]["status"] == "rejected"
    assert result["normalized_assessment"]["omitted_eligible_fact_count"] == 1
    assert result["normalized_assessment"]["unsafe_raw_commit"]
    assert not result["citation_only_rejection"]


def test_alias_duplicates_remain_invalid_not_deduplicated():
    inputs = documents()
    facts = parser_facts(inputs)
    facts.append({**facts[0], "pointer": "/documents/2/text"})
    result = diagnose_citations(inputs, proposal(facts))
    assert not result["role_digest_frame_equal"] and result["normalized_assessment"] is None


@pytest.mark.parametrize(
    "change",
    ["authority", "schema", "extra-field", "duplicate-id", "scope"],
    ids=["authority", "schema", "not-closed", "ambiguous-document", "scope"],
)
def test_source_metadata_and_closed_grammar_are_required(change):
    inputs = documents()
    facts = parser_facts(inputs)
    if change == "authority":
        inputs[2]["authority"] = "native-message"
    elif change == "scope":
        inputs[2]["scope"] = "other"
    elif change == "duplicate-id":
        inputs[3]["id"] = "intent"
    else:
        value = json.loads(inputs[2]["text"])
        if change == "schema":
            value["schema_version"] = "unknown"
        else:
            value["consumed_sha256"] = B
        inputs[2]["text"] = json.dumps(value)
        inputs[2]["sha256"] = content_sha256(inputs[2]["text"].encode())
    with pytest.raises(ValueError):
        normalize_citations(inputs, facts)


@pytest.mark.parametrize(
    "raw",
    [b"{}", b'{"schema_version":"source-evidence-proposal/v1","facts":[],"facts":[]}'],
    ids=["shape", "duplicate-key"],
)
def test_invalid_raw_proposals_never_gain_a_normalized_assessment(raw):
    result = diagnose_citations(documents(), raw)
    assert result["strict_assessment"]["status"] == "invalid_proposal"
    assert result["normalized_assessment"] is None


def test_canonical_schema_does_not_encode_eligible_pairs_or_fact_values():
    inputs = documents(consumed=None)
    schema = citation_schema(inputs)
    choices = schema["properties"]["facts"]["items"]["properties"]["pointer"]["enum"]
    assert len(choices) == 2 * len(inputs)
    assert "/documents/native-out/consumed_sha256" in choices
    assert "/documents/intent/consumed_sha256" in choices
    assert all("/consumer/" not in choice for choice in choices)
    assert _openai_response_format(json.dumps(schema))["json_schema"]["strict"] is True
    properties = schema["properties"]["facts"]["items"]["properties"]
    assert "enum" not in properties["digest"] and "enum" not in properties["document_sha256"]
    assert "SOURCE LOCATOR" in PROMPT and "not a JSON Pointer" in PROMPT
    bad = parser_facts(inputs)
    bad[0]["pointer"] = "/documents/native-out/consumed_sha256"
    assert diagnose_citations(inputs, proposal(bad))["strict_assessment"]["status"] == "rejected"


def test_live_diagnostics_do_not_expand_citations():
    inputs = documents()
    facts = parser_facts(inputs)
    facts[0]["pointer"] = "/documents/intent/text"
    report = diagnose_citations(inputs, proposal(facts), normalize=False)
    assert report["role_digest_frame_equal"]
    assert report["normalized_assessment"] is None and report["citation_repaired_fact_count"] == 0
