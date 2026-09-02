from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.claim_corpus_protocol import (
    ClaimSupportCorpusProtocol,
    load_claim_support_corpus_protocol,
)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "configs/evaluation/claim_support_corpus_protocol.json"


def _payload() -> dict[str, object]:
    return load_claim_support_corpus_protocol(PROTOCOL_PATH).model_dump(mode="python")


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["protocol_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "protocol_sha256"}
    )
    return payload


@given(st.permutations(("data_drift", "preprocessing_mismatch", "label_noise")))
def test_mechanism_permutation_cannot_change_the_registered_census_order(
    order: list[str],
) -> None:
    if tuple(order) == ("data_drift", "preprocessing_mismatch", "label_noise"):
        return
    payload = _payload()
    source = payload["source_boundary"]
    assert isinstance(source, dict)
    source["mechanism_inventory"] = tuple(order)

    with pytest.raises(ValidationError):
        ClaimSupportCorpusProtocol.model_validate(_rehash(payload))


@given(st.permutations(("A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL")))
def test_variant_permutation_cannot_change_the_registered_eligibility_order(
    order: list[str],
) -> None:
    canonical = ("A1", "A2", "A3", "B0", "B1", "B2", "CodeGraph", "FULL")
    if tuple(order) == canonical:
        return
    payload = _payload()
    policy = payload["variant_eligibility"]
    assert isinstance(policy, dict)
    policy["eligible_variants"] = tuple(order)

    with pytest.raises(ValidationError):
        ClaimSupportCorpusProtocol.model_validate(_rehash(payload))


@given(st.integers(min_value=0, max_value=49).filter(lambda value: value != 5))
def test_per_family_cap_cannot_be_adapted_to_observed_label_scarcity(cap: int) -> None:
    payload = _payload()
    census = payload["family_census"]
    assert isinstance(census, dict)
    census["maximum_claims_per_family_per_label"] = cap

    with pytest.raises(ValidationError):
        ClaimSupportCorpusProtocol.model_validate(_rehash(payload))
