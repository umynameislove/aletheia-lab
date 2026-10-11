"""Producer-scoped admission of retained development loader evidence.

This adapter does not authenticate an arbitrary JSON document. Its caller must
bind producer and scope independently of document prose. It preserves missing
endpoints and contradictions and reuses the existing finite lineage resolver.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from aletheia_lab.evaluation.compositional_lineage import reachable_statuses, visible_reference
from aletheia_lab.project.identity import content_sha256

PRODUCER = "local-m4-buffer-loader"
SCHEMAS = {
    "development-path/v1": "loaded_artifact_sha256",
    "loader-event/v1": "actual_loaded_sha256",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def checked_json(raw: bytes) -> dict[str, Any]:
    """Reject duplicate keys and nonfinite constants, including nested objects."""

    def reject_constant(value: str) -> None:
        raise ValueError("nonfinite JSON constant")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    if len(raw) > 50_000_000:
        raise ValueError("source JSON is too large")
    value = json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )
    if not isinstance(value, dict):
        raise ValueError("source JSON must be an object")
    return value


@dataclass(frozen=True)
class SourceDocument:
    """Caller-bound evidence; producer/scope cannot come from LLM output."""

    raw: bytes
    schema: str
    producer: str
    scope: str | None
    pointer: str


def parse_document(document: SourceDocument, *, scope: str) -> dict[str, Any]:
    """Extract only declared and actually loaded endpoints, never report text."""
    identity = content_sha256(document.raw)
    base = {"document_sha256": identity, "facts": [], "issues": []}
    if document.scope != scope:
        return {**base, "disposition": "outside_scope"}
    if document.producer != PRODUCER or document.schema not in SCHEMAS:
        return {**base, "disposition": "unresolved", "issues": ["unverified_producer_or_schema"]}
    try:
        value = checked_json(document.raw)
    except (ValueError, UnicodeError):
        return {**base, "disposition": "unresolved", "issues": ["invalid_json"]}
    facts: list[dict[str, str]] = []
    issues: list[str] = []
    for endpoint, field in (
        ("requested_endpoint", "declared_artifact_sha256"),
        ("loaded_endpoint", SCHEMAS[document.schema]),
    ):
        if field not in value:
            issues.append(f"missing:{field}")
        elif not isinstance(value[field], str) or not _DIGEST.fullmatch(value[field]):
            issues.append(f"invalid:{field}")
        else:
            facts.append(
                {
                    "kind": endpoint,
                    "digest": value[field],
                    "pointer": f"{document.pointer}/{field}",
                    "document_sha256": identity,
                }
            )
    # A malformed recognized endpoint is NOT a benign omission: do not commit
    # from a remaining valid subset while silently dropping invalid evidence.
    disposition = (
        "unresolved" if any(issue.startswith("invalid:") for issue in issues) else "parsed"
    )
    return {**base, "facts": facts, "issues": issues, "disposition": disposition}


def resolve_documents(documents: list[SourceDocument], *, scope: str) -> dict[str, Any]:
    """Resolve same-input evidence under a binary, receipt-local artifact scope."""
    if not scope or len(documents) > 12:
        raise ValueError("invalid receipt-local evidence scope")
    parsed = [parse_document(document, scope=scope) for document in documents]
    facts = [fact for item in parsed for fact in item["facts"]]
    digests = sorted({fact["digest"] for fact in facts})
    if len(digests) > 2 or any(item["disposition"] == "unresolved" for item in parsed):
        return {
            "state": "admission_unresolved",
            "compatible": [],
            "facts": facts,
            "documents": parsed,
            "resolver_agrees": None,
        }
    aliases = {digest: f"artifact-{index}" for index, digest in enumerate(digests)}
    records = [
        {
            "id": f"r{index:02d}",
            "kind": fact["kind"],
            "request": "request-0",
            "attempt": "attempt-0",
            "subject": "request-0" if fact["kind"] == "requested_endpoint" else "attempt-0",
            "value": aliases[fact["digest"]],
        }
        for index, fact in enumerate(facts)
    ]
    # The aliases below are solver-local variables, NOT recovered native IDs.
    context = {
        "schema_version": "compositional-artifact-lineage/v1",
        "request": "request-0",
        "attempt": "attempt-0",
        "records": records,
    }
    reference = visible_reference(context)
    agreement = reachable_statuses(context) == reference["compatible"]
    if not agreement:
        raise ValueError("finite resolvers disagree")
    return {**reference, "facts": facts, "documents": parsed, "resolver_agrees": agreement}
