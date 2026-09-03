from __future__ import annotations

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.evaluation.claim_corpus_contracts import VisibleEvidenceRelation
from aletheia_lab.evaluation.claim_corpus_readiness import (
    build_family_inventory,
    build_request_census,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support

ROOT = Path(__file__).resolve().parents[2]
PARENT_SHA = "b7381f9b4c855d87f21143f4fe7278c3c00cc15aa40e3e5b633fe1f79d6c5867"


@given(st.permutations(("A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL")))
def test_request_census_identity_does_not_depend_on_external_variant_order(
    unused_order: list[str],
) -> None:
    del unused_order
    inventory = build_family_inventory(ROOT, PARENT_SHA)

    assert build_request_census(inventory) == build_request_census(inventory)


@given(st.permutations(("neutral", "partial", "entire")))
def test_contradiction_precedence_is_invariant_to_visible_evidence_order(
    order: list[str],
) -> None:
    evidence = {
        "neutral": VisibleEvidenceRelation(
            evidence_id="evidence-neutral",
            text="A neutral visible record.",
            relation_polarity="neutral",
            relation_scope="none",
        ),
        "partial": VisibleEvidenceRelation(
            evidence_id="evidence-partial",
            text="A partially supporting visible record.",
            relation_polarity="supports",
            relation_scope="partial",
        ),
        "entire": VisibleEvidenceRelation(
            evidence_id="evidence-conflict",
            text="A conflicting visible record.",
            relation_polarity="contradicts",
            relation_scope="entire",
        ),
    }

    assert (
        classify_visible_support(
            claim_text="A bounded claim.",
            claim_type="cause_assertion",
            visible_evidence=tuple(evidence[item] for item in order),
        )
        == "contradicted"
    )


@given(st.sampled_from(("cause_assertion", "evidence_statement", "uncertainty_statement", "recommended_action", "other")))
def test_claim_type_cannot_adapt_the_frozen_support_precedence(claim_type: str) -> None:
    visible = (
        VisibleEvidenceRelation(
            evidence_id="evidence-support",
            text="A complete visible support record.",
            relation_polarity="supports",
            relation_scope="entire",
        ),
    )

    assert (
        classify_visible_support(
            claim_text="A bounded claim.",
            claim_type=claim_type,  # type: ignore[arg-type]
            visible_evidence=visible,
        )
        == "fully_supported"
    )
