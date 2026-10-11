"""Offline assessment of proposed facts, not an authenticated semantic oracle.

The known-schema producer adapter is the matched-input comparator and the
semantic admission contract. A candidate cannot gain authority by citing bytes
or silently removing a same-scope witness. Unknown producer semantics remain
unresolved; a language-model list of readings does not prove true-reading coverage.
"""

from __future__ import annotations

from typing import Any

from aletheia_lab.evaluation.source_evidence_admission import (
    SourceDocument,
    checked_json,
    resolve_documents,
)
from aletheia_lab.project.identity import content_sha256

_FIELDS = {"kind", "digest", "pointer", "document_sha256"}


def _key(fact: dict[str, str]) -> tuple[str, ...]:
    return tuple(fact[field] for field in sorted(_FIELDS))


def _proposed_facts(raw: bytes) -> list[dict[str, str]]:
    value = checked_json(raw)
    if set(value) != {"schema_version", "facts"} or value["schema_version"] != (
        "source-evidence-proposal/v1"
    ):
        raise ValueError("unsupported extraction proposal")
    facts = value["facts"]
    if not isinstance(facts, list) or len(facts) > 24:
        raise ValueError("invalid proposed fact frame")
    result = []
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != _FIELDS:
            raise ValueError("invalid proposed fact grammar")
        if not all(isinstance(item, str) for item in fact.values()):
            raise ValueError("proposed facts must contain strings only")
        result.append(dict(fact))
    if len({_key(fact) for fact in result}) != len(result):
        raise ValueError("duplicate proposed fact")
    return result


def _grounded(fact: dict[str, str], documents: list[SourceDocument]) -> bool:
    """Byte/field correspondence is deliberately weaker than semantic warrant."""
    for document in documents:
        if content_sha256(document.raw) != fact["document_sha256"]:
            continue
        prefix = document.pointer + "/"
        if not fact["pointer"].startswith(prefix):
            continue
        field = fact["pointer"].removeprefix(prefix)
        try:
            value = checked_json(document.raw)
        except (ValueError, UnicodeError):
            continue
        if "/" not in field and value.get(field) == fact["digest"]:
            return True
    return False


def assess_extraction(
    documents: list[SourceDocument], *, scope: str, proposal: bytes
) -> dict[str, Any]:
    """Separate grounding, semantic role and omissions before fixed resolution.

    Completeness here means all eligible facts in the provided known-schema
    documents, not the entire runtime. This conservative filter intentionally
    rejects even benign omissions; it is not a theorem requiring complete logs.
    D and S must receive these same document bytes and caller-bound metadata.
    No new source semantics or extractor efficacy is inferred from this check.
    """
    baseline = resolve_documents(documents, scope=scope)
    expected = {_key(fact) for fact in baseline["facts"]}
    base = {
        "baseline_state": baseline["state"],
        "baseline_compatible": baseline["compatible"],
        "eligible_fact_count": len(expected),
        "semantic_reference": "known-schema producer adapter; not independent language gold",
    }
    try:
        facts = _proposed_facts(proposal)
    except (ValueError, UnicodeError):
        return {**base, "status": "invalid_proposal", "candidate_resolution": None}
    observed = {_key(fact) for fact in facts}
    ungrounded = sum(not _grounded(fact, documents) for fact in facts)
    unsupported = len(observed - expected)
    omitted = len(expected - observed)
    accepted = (
        baseline["state"] != "admission_unresolved" and ungrounded == unsupported == omitted == 0
    )
    # Only an exact authorized fact frame is promoted. Never resolve a subset
    # which can suppress a conflicting witness, or let S supply caller metadata.
    return {
        **base,
        "status": "admitted" if accepted else "rejected",
        "grounding_error_count": ungrounded,
        "semantic_role_error_count": unsupported,
        "omitted_eligible_fact_count": omitted,
        "exact_fact_frame": observed == expected,
        "candidate_resolution": (
            {"state": baseline["state"], "compatible": baseline["compatible"]} if accepted else None
        ),
        "resolver_origin": "fixed deterministic resolver" if accepted else None,
    }


def combine_readings(
    readings: list[dict[str, Any]], *, coverage_verified: bool, unknown_reading: bool = False
) -> dict[str, Any]:
    """Conservative finite aggregation; coverage is caller evidence, not model prose.

    Conditional soundness additionally requires the true world to be in the
    model and satisfy a covered reading. These premises are NOT established by
    this function. A conflicting reading cannot vanish as an empty union member.
    """
    if not readings or len(readings) > 12:
        raise ValueError("invalid reading frame")
    if type(coverage_verified) is not bool or type(unknown_reading) is not bool:
        raise ValueError("reading coverage flags must be caller booleans")
    allowed = {
        "identified": ({"binding_fault"}, {"no_binding_fault"}),
        "ambiguous": ({"binding_fault", "no_binding_fault"},),
        "conflict": (set(),),
        "admission_unresolved": (set(),),
    }
    for reading in readings:
        if not isinstance(reading, dict):
            raise ValueError("reading must be an object")
        state, compatible = reading.get("state"), reading.get("compatible")
        if (
            not isinstance(state, str)
            or state not in allowed
            or not isinstance(compatible, list)
            or not all(isinstance(item, str) for item in compatible)
            or len(set(compatible)) != len(compatible)
            or set(compatible) not in allowed[state]
        ):
            raise ValueError("reading does not match finite resolver semantics")
    states = [reading["state"] for reading in readings]
    statuses = sorted({item for reading in readings for item in reading["compatible"]})
    if not coverage_verified or unknown_reading or "admission_unresolved" in states:
        state = "admission_unresolved"
    elif all(item == "conflict" for item in states):
        state = "conflict"
    elif "conflict" in states:
        state = "reading_reconciliation_required"
    else:
        state = "identified" if len(statuses) == 1 else "ambiguous"
    return {
        "state": state,
        # A listed-reading singleton is not an exhaustive answer when coverage
        # is unknown or a conflicting branch needs reconciliation.
        "compatible": statuses if state in {"identified", "ambiguous"} else [],
        "listed_reading_compatible": statuses,
        "commit_permitted": state == "identified",
        "coverage_verified": coverage_verified,
        "unknown_reading": unknown_reading,
    }
