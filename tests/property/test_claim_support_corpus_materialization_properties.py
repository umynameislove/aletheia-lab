from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusRequestCensus,
    VisibleEvidenceRelation,
)
from aletheia_lab.evaluation.claim_corpus_readiness import (
    build_family_inventory,
    build_request_census,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
PARENT_SHA = "b7381f9b4c855d87f21143f4fe7278c3c00cc15aa40e3e5b633fe1f79d6c5867"


@pytest.fixture(scope="module")
def request_census() -> ClaimCorpusRequestCensus:
    inventory = build_family_inventory(ROOT, PARENT_SHA)
    census = build_request_census(inventory)
    assert build_request_census(inventory) == census
    return census


# Identity invariance is not a per-example disk-I/O latency benchmark.
# Keep the default example count and all checks under the profile's own budget.
@settings(deadline=None)
@given(st.permutations(("A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL")))
def test_request_census_identity_does_not_depend_on_external_variant_order(
    request_census: ClaimCorpusRequestCensus, order: list[str],
) -> None:
    # Build the filesystem-backed frame once, then exercise each external order.
    # This remains an identity property, not an I/O-speed benchmark.
    payload = request_census.identity_payload()
    for field in ("primary_requests", "reserve_requests"):
        requests = getattr(request_census, field)
        ranks = {request.request_sha256: index for index, request in enumerate(requests)}
        external = [request for variant in order for request in requests if request.variant == variant]
        canonical = sorted(external, key=lambda request: ranks[request.request_sha256])
        assert tuple(canonical) == requests
        payload[field] = tuple(request.model_dump(mode="json") for request in canonical)
    assert canonical_execution_sha256(payload) == request_census.census_sha256


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
