"""Conservative structural witnesses for authentic quantitative challenge frames.

A shared numeric token is not a contradiction. Eligibility requires a complete
material part in the unambiguous form ``observed.macro_f1 = 0.7`` and the SAME
measurement path in authenticated JSON evidence. Arbitrary prose is ineligible;
frame intent remains independent of subsequent semantic and human judgments.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from aletheia_lab.evaluation.claim_corpus_contracts import AtomicClaimV2
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceItem

_ASSERTION = re.compile(
    r"([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\s*=\s*"
    r"(-?\d+(?:\.\d+)?)\.?"
)


def measurements(item: ModelVisibleEvidenceItem) -> dict[str, Decimal]:
    """Read numeric leaves only from JSON measurements; never from IDs or hashes."""
    if item.evidence_id == "ev-source-provenance":
        return {}
    try:
        payload = json.loads(item.content, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, InvalidOperation):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, Decimal] = {}

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key):
                    visit(child, f"{path}.{key}" if path else key)
        elif isinstance(value, Decimal) and value.is_finite():
            result[path] = value

    visit(payload, "")
    return result


def material_measurements(claim: AtomicClaimV2) -> tuple[tuple[str, Decimal], ...]:
    """Require every material part to declare exactly one scoped numeric equality."""
    # Do not let numeric material parts silently erase a subject, qualifier or
    # additional assertion from the actual claim that the relation model sees.
    if claim.claim_text != "; ".join(part.text for part in claim.material_parts):
        return ()
    result = []
    for part in claim.material_parts:
        match = _ASSERTION.fullmatch(part.text)
        if match is None:
            return ()
        result.append((match[1], Decimal(match[2])))
    return tuple(result)


def covered_parts(
    parts: Sequence[tuple[str, Decimal]], items: Sequence[ModelVisibleEvidenceItem]
) -> set[int]:
    facts = tuple(measurements(item) for item in items)
    return {
        index for index, (path, value) in enumerate(parts)
        if any(record.get(path) == value for record in facts)
    }


def conflicting_measurement_ids(
    source: Sequence[ModelVisibleEvidenceItem],
    counter: Sequence[ModelVisibleEvidenceItem],
    parts: Sequence[tuple[str, Decimal]] | None = None,
) -> tuple[str, ...]:
    """Match the same evidence ID and field path, excluding irrelevant numbers."""
    counter_by_id = {item.evidence_id: item for item in counter}
    conflicts = []
    for item in source:
        candidate = counter_by_id.get(item.evidence_id)
        if candidate is None or candidate.title != item.title or candidate.kind != item.kind:
            continue
        left, right = measurements(item), measurements(candidate)
        candidates = tuple(left.items()) if parts is None else parts
        if any(
            left.get(path) == value and path in right and right[path] != value
            for path, value in candidates
        ):
            conflicts.append(item.evidence_id)
    return tuple(sorted(conflicts))
