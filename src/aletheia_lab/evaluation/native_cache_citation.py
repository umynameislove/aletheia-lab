"""Citation-only development correction; frozen strict extraction stays unchanged."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from aletheia_lab.evaluation.native_cache_extraction import (
    RESPONSE_SCHEMA,
    SEMANTICS,
    assess_native_proposal,
    parser_facts,
)
from aletheia_lab.evaluation.source_evidence_extraction import _proposed_facts

PROMPT = (
    "Extract every eligible artifact endpoint fact and no other facts. Preserve its "
    "role, exact lowercase SHA256 value and exact source document sha256. The pointer "
    "is a SOURCE LOCATOR, not a JSON Pointer into the envelope: locate the document "
    "by its id, decode that document.text as JSON, then name its endpoint field. "
    "Use /documents/{document.id}/requested_sha256 or "
    "/documents/{document.id}/consumed_sha256, as appropriate to the actual evidence. "
    "For example the requested field in document id intent is cited as "
    "/documents/intent/requested_sha256. Never cite /text or a numeric array index. "
    "The schema lists all visible document IDs crossed with both possible field "
    "names; a listed pointer is NOT evidence that the field exists, has authority, "
    "or warrants any role. Verify these from the supplied documents and semantics. "
    "Return schema_version source-evidence-proposal/v1 and facts only. Do not "
    "diagnose, fill missing facts, execute code or decide which case is faulty. " + SEMANTICS
)
_FIELDS = ("requested_sha256", "consumed_sha256")
_SELECTOR = re.compile(r"/documents/([^/]+)/([^/]+)\Z")


def citation_schema(documents: list[dict[str, str]]) -> dict[str, Any]:
    """Constrain syntax, not eligible facts, roles, values or source authority."""
    parser_facts(documents)  # Validate visible inputs; never use facts to select enums.
    result = deepcopy(RESPONSE_SCHEMA)
    result["properties"]["facts"]["items"]["properties"]["pointer"] = {
        "type": "string",
        "enum": [
            f"/documents/{identifier}/{field}"
            for identifier in sorted(document["id"] for document in documents)
            for field in _FIELDS
        ],
    }
    return result


def _locator_document(documents: list[dict[str, str]], pointer: str) -> tuple[dict[str, str], str]:
    match = _SELECTOR.fullmatch(pointer)
    if match is None:
        raise ValueError("unsupported source locator")
    selector, suffix = match.groups()
    if re.fullmatch(r"0|[1-9][0-9]*", selector):
        index = int(selector)
        if index >= len(documents):
            raise ValueError("document index is outside the visible input")
        return documents[index], suffix
    matches = [document for document in documents if document["id"] == selector]
    if len(matches) != 1:
        raise ValueError("locator does not identify one visible document")
    return matches[0], suffix


def _canonical_locator(documents: list[dict[str, str]], fact: dict[str, str]) -> tuple[str, str]:
    document, suffix = _locator_document(documents, fact["pointer"])
    if fact["document_sha256"] != document["sha256"]:
        raise ValueError("locator and document hash disagree")
    eligible = parser_facts([document])
    if len(eligible) != 1:
        raise ValueError("document does not warrant one unique endpoint")
    reference = eligible[0]
    field = reference["pointer"].rsplit("/", 1)[1]
    if suffix not in {"text", field} or any(
        fact[key] != reference[key] for key in ("kind", "digest", "document_sha256")
    ):
        raise ValueError("citation normalization cannot repair a semantic mismatch")
    return reference["pointer"], suffix


def normalize_citations(
    documents: list[dict[str, str]], proposed: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Supplementary cached replay only: never edit facts or select a doc by its hash.

    Whole-text citations can be promoted only for the existing closed producer
    grammar with exactly one eligible endpoint field. Missing/extra endpoints,
    reversed roles, wrong hashes or digests, and semantic duplicates fail closed.
    """
    parser_facts(documents)
    result = []
    counts = {
        "citation_repaired_fact_count": 0,
        "document_text_expansion_count": 0,
        "explicit_field_locator_substitution_count": 0,
    }
    for fact in proposed:
        pointer, suffix = _canonical_locator(documents, fact)
        if fact["pointer"] != pointer:
            counts["citation_repaired_fact_count"] += 1
            counts[
                "document_text_expansion_count"
                if suffix == "text"
                else "explicit_field_locator_substitution_count"
            ] += 1
        result.append({**fact, "pointer": pointer})
    keys = {tuple(sorted(fact.items())) for fact in result}
    if len(keys) != len(result):
        raise ValueError("citation aliases create duplicate semantic facts")
    return result, counts


def diagnose_citations(
    documents: list[dict[str, str]], raw: bytes, *, normalize: bool = True
) -> dict[str, Any]:
    """Explain strict rejection without replacing any primary metric or response."""
    expected = parser_facts(documents)
    strict = assess_native_proposal(documents, raw)
    report = {
        "strict_assessment": strict,
        "role_digest_frame_equal": False,
        "role_digest_document_frame_equal": False,
        "raw_resolution_matches_reference": False,
        "citation_only_rejection": False,
        "citation_repaired_fact_count": 0,
        "document_text_expansion_count": 0,
        "explicit_field_locator_substitution_count": 0,
        "normalized_assessment": None,
    }
    try:
        proposed = _proposed_facts(raw)
    except (ValueError, UnicodeError):
        return report
    for name, fields in (
        ("role_digest_frame_equal", ("kind", "digest")),
        ("role_digest_document_frame_equal", ("kind", "digest", "document_sha256")),
    ):
        report[name] = sorted(
            tuple(fact[field] for field in fields) for fact in proposed
        ) == sorted(tuple(fact[field] for field in fields) for fact in expected)
    report["raw_resolution_matches_reference"] = (
        strict["raw_resolution"] == strict["baseline_resolution"]
    )
    if not normalize:
        return report
    try:
        normalized, counts = normalize_citations(documents, proposed)
    except (ValueError, UnicodeError):
        return report
    assessment = assess_native_proposal(
        documents,
        json.dumps({"schema_version": "source-evidence-proposal/v1", "facts": normalized}).encode(),
    )
    report.update(
        **counts,
        normalized_assessment=assessment,
        citation_only_rejection=(
            strict["status"] == "rejected" and assessment["status"] == "admitted"
        ),
    )
    return report
