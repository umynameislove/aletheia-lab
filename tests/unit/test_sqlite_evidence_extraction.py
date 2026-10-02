"""Independent frame, semantic role, scope, grounding and omission falsifiers."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from aletheia_lab.evaluation.native_cache_extraction import resolve_facts
from aletheia_lab.evaluation.sqlite_evidence_extraction import (
    PATHS,
    PROMPT,
    assess_proposal,
    citation_schema,
    parser_facts,
    provider_payload,
)
from aletheia_lab.evaluation.sqlite_evidence_source import (
    VIEWS,
    document,
    generate_source,
    visible_reference,
)
from aletheia_lab.model_gateway.openai import _openai_response_format
from aletheia_lab.project.identity import content_sha256


def proposal(facts):
    return json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": facts}).encode()


@pytest.fixture(scope="module")
def cases():
    return generate_source(namespace="offline-extractor-fixture")["cases"]


@pytest.mark.parametrize(
    "index,view",
    [(i, v) for i in range(5) for v in VIEWS],
    ids=[f"control-{i}-{v}" for i in range(5) for v in VIEWS],
)
def test_parser_and_fixed_resolver_match_independent_full_frame(cases, index, view):
    case = cases[index]
    docs = [
        doc
        for doc in case["documents"]
        if view == "with_consumer" or doc["kind"] != "sqlite-blob-witness"
    ]
    reference = visible_reference(case, view)
    assert parser_facts(docs) == reference["facts"]
    result = assess_proposal(docs, proposal(reference["facts"]))
    assert result["exact_fact_frame"] and result["content_frame_equal"]
    assert result["status"] == "admitted"
    assert result["raw_resolution"] == result["guarded_resolution"] == reference["resolution"]
    assert not result["unwarranted_singleton"]


def test_schema_is_cartesian_not_an_answer_key(cases):
    docs = cases[0]["documents"]
    enum = citation_schema(docs)["properties"]["facts"]["items"]["properties"]["pointer"]["enum"]
    assert set(enum) == {f"/documents/{doc['id']}/{path}" for doc in docs for path in PATHS}
    assert len(enum) == 16
    assert "/documents/query/cell/sha256" in enum
    assert "/documents/manifest/manifest/declared_sha256" in enum
    wire = _openai_response_format(json.dumps(citation_schema(docs)))
    assert wire["json_schema"]["strict"]
    payload = provider_payload(docs)
    assert payload["documents"] == docs
    assert set(payload) == {"producer", "scope", "semantics", "documents"}
    assert "SOURCE LOCATOR" in PROMPT.upper() or "custom source" in PROMPT


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "loaded_endpoint"),
        ("digest", "f" * 64),
        ("document_sha256", "f" * 64),
        ("pointer", "/documents/selection/cell/sha256"),
        ("pointer", "/documents/0/selection/expected/sha256"),
        ("pointer", "/documents/selection/text"),
    ],
    ids=["role", "digest", "source-hash", "nested-field", "index", "text"],
)
def test_same_digest_healthy_case_cannot_hide_wrong_role_or_citation(cases, field, value):
    docs = cases[0]["documents"]
    facts = visible_reference(cases[0], "with_consumer")["facts"]
    facts[0][field] = value
    result = assess_proposal(docs, proposal(facts))
    assert result["status"] == "rejected"
    assert not result["exact_fact_frame"]
    assert result["guarded_resolution"] is None


def test_grounded_row_declaration_is_not_loaded_authority(cases):
    docs = cases[1]["documents"][:3]
    facts = parser_facts(docs)
    manifest = docs[1]
    value = json.loads(manifest["text"])
    facts.append(
        {
            "kind": "loaded_endpoint",
            "digest": value["manifest"]["declared_sha256"],
            "pointer": "/documents/manifest/manifest/declared_sha256",
            "document_sha256": manifest["sha256"],
        }
    )
    result = assess_proposal(docs, proposal(facts))
    assert result["grounding_error_count"] == 0
    assert result["unsupported_fact_count"] == 1
    assert result["unwarranted_singleton"] and result["wrong_visible_status"]
    assert result["baseline_resolution"]["state"] == "ambiguous"
    assert result["guarded_resolution"] is None


def test_other_attempt_observation_cannot_rebind_scope(cases):
    docs = deepcopy(cases[1]["documents"])
    docs[-1]["scope"] = "attempt-1"
    facts = parser_facts(docs)
    assert len(facts) == 1
    assert resolve_facts(facts)["state"] == "ambiguous"
    candidate = visible_reference(cases[1], "with_consumer")["facts"]
    result = assess_proposal(docs, proposal(candidate))
    assert result["grounding_error_count"] == 0
    assert result["unsupported_fact_count"] == 1
    assert result["guarded_resolution"] is None


def test_contrary_same_scope_witness_cannot_be_omitted(cases):
    docs = deepcopy(cases[0]["documents"])
    other = deepcopy(cases[1]["documents"][-1])
    other["id"] = "other-cell"
    docs.append(other)
    all_facts = parser_facts(docs)
    assert resolve_facts(all_facts)["state"] == "conflict"
    kept = [fact for fact in all_facts if "other-cell" not in fact["pointer"]]
    result = assess_proposal(docs, proposal(kept))
    assert result["omitted_eligible_fact_count"] == 1
    assert result["unwarranted_singleton"]
    assert result["guarded_resolution"] is None
    assert assess_proposal(docs, proposal(all_facts))["guarded_resolution"]["state"] == "conflict"


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "authority",
        "unknown-kind",
        "id",
        "extra",
        "private-path",
        "json-duplicate",
        "json-nonfinite",
        "grammar",
        "trace",
        "malformed-digest",
    ],
    ids=lambda value: value,
)
def test_invalid_source_contract_is_not_silently_admitted(cases, mutation):
    docs = deepcopy(cases[0]["documents"])
    if mutation == "hash":
        docs[-1]["sha256"] = "f" * 64
    elif mutation == "authority":
        docs[-1]["authority"] = "native-statement"
    elif mutation == "unknown-kind":
        docs[-1]["kind"] = "self-authenticated"
    elif mutation == "id":
        docs[-1]["id"] = docs[0]["id"]
    elif mutation == "extra":
        docs[-1]["host_attested"] = "true"
    else:
        value = json.loads(docs[-1]["text"])
        if mutation == "private-path":
            raw = '{"path":"/Users/private/account"}'
        elif mutation == "json-duplicate":
            raw = '{"schema_version":"sqlite-blob-witness/v1","cell":{},"cell":{}}'
        elif mutation == "json-nonfinite":
            raw = '{"value":NaN}'
        elif mutation == "grammar":
            value["authority"] = "same-returned-buffer"
            raw = json.dumps(value)
        elif mutation == "trace":
            docs[0]["text"] = "SELECT payload FROM unbound_table"
            docs[0]["sha256"] = content_sha256(docs[0]["text"].encode())
            raw = docs[-1]["text"]
        else:
            value["cell"]["sha256"] = "invalid"
            raw = json.dumps(value)
        docs[-1]["text"] = raw
        docs[-1]["sha256"] = content_sha256(raw.encode())
    with pytest.raises(ValueError):
        parser_facts(docs)


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b'{"facts":[],"facts":[]}',
        b'{"schema_version":"other","facts":[]}',
        b'{"schema_version":"source-evidence-proposal/v1","facts":[{}]}',
    ],
    ids=["empty", "array", "duplicate-key", "wrong-schema", "wrong-fact"],
)
def test_invalid_proposal_retains_failure(cases, raw):
    result = assess_proposal(cases[0]["documents"], raw)
    assert result["status"] == "invalid_proposal"
    assert not result["exact_fact_frame"]
    assert result["guarded_resolution"] is None


def test_duplicate_proposal_and_benign_omission_are_rejected(cases):
    docs = cases[0]["documents"]
    facts = parser_facts(docs)
    assert assess_proposal(docs, proposal([*facts, facts[0]]))["status"] == "invalid_proposal"
    result = assess_proposal(docs, proposal(facts[:1]))
    assert result["status"] == "rejected" and result["omitted_eligible_fact_count"] == 1
    assert not result["unwarranted_singleton"]


def test_provenance_only_rejection_is_separate_from_wrong_visible_status(cases):
    docs = cases[1]["documents"]
    facts = parser_facts(docs)
    facts[1]["pointer"] = "/documents/cell/text"
    result = assess_proposal(docs, proposal(facts))
    assert result["citation_only_rejection"]
    assert result["content_frame_equal"]
    assert result["unwarranted_singleton"]
    assert not result["wrong_visible_status"]


def test_no_documents_and_excess_documents_fail(cases):
    with pytest.raises(ValueError):
        parser_facts([])
    with pytest.raises(ValueError):
        parser_facts(cases[0]["documents"] * 3)
    changed = document("other", "sqlite-row-metadata", "row-declaration", {})
    with pytest.raises(ValueError):
        parser_facts([changed])
