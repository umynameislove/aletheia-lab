"""Frozen deterministic relation instrument for visible claim evidence.

The instrument accepts only the three fields authorized by the corpus
protocol: claim text, claim type, and visible evidence.  Mechanism, variant,
condition, hidden truth, human judgment, and protected outcomes are absent from
the API rather than merely ignored.
"""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimType,
    SupportLabel,
    VisibleEvidenceRelation,
)
from aletheia_lab.project.identity import normalize_text

INSTRUMENT_VERSION: Final = "claim-support-visible-relation/1"


def classify_visible_support(
    *,
    claim_text: str,
    claim_type: ClaimType,
    visible_evidence: tuple[VisibleEvidenceRelation, ...],
) -> SupportLabel:
    """Classify support using frozen contradiction-first precedence.

    ``claim_type`` is validated and retained as an explicit input boundary.  It
    does not alter precedence in v1; allowing type-specific thresholds would
    make the validation target adaptive.
    """

    del claim_type
    normalize_text(claim_text, label="automatic instrument claim", max_length=2048)
    if not visible_evidence:
        raise ClaimCorpusContractError("automatic instrument requires visible evidence")
    try:
        checked = tuple(
            VisibleEvidenceRelation.model_validate(item.model_dump(mode="python"))
            for item in visible_evidence
        )
    except ValidationError as exc:
        raise ClaimCorpusContractError("visible evidence relation is invalid") from exc

    if any(item.relation_polarity == "contradicts" for item in checked):
        return "contradicted"
    supporting = tuple(item for item in checked if item.relation_polarity == "supports")
    if not supporting:
        return "unsupported"
    if any(item.relation_scope == "entire" for item in supporting):
        return "fully_supported"
    return "partially_supported"


__all__ = ["INSTRUMENT_VERSION", "classify_visible_support"]
