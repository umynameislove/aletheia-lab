"""Property checks for post-P2R denominator and mechanism-state isolation."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.downstream_disposition_v2 import (
    DownstreamDispositionPolicyV2,
    P4P5MechanismFilterManifest,
    load_downstream_disposition_policy_v2,
    load_p4_p5_filter_manifest,
)


@settings(max_examples=24, deadline=None)
@given(st.permutations(("data_drift", "preprocessing_mismatch", "label_noise")))
def test_any_inventory_reordering_fails_closed(order: tuple[str, ...]) -> None:
    canonical = ("data_drift", "preprocessing_mismatch", "label_noise")
    if tuple(order) == canonical:
        return
    payload = load_downstream_disposition_policy_v2().model_dump(mode="json")
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    denominators["mechanism_inventory"] = list(order)
    with pytest.raises(ValidationError, match="inventory"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))


@settings(max_examples=24, deadline=None)
@given(
    st.sampled_from(("data_drift", "preprocessing_mismatch", "label_noise")),
)
def test_no_non_admitted_mechanism_can_leak_into_primary_track(mechanism: str) -> None:
    payload = load_p4_p5_filter_manifest().model_dump(mode="json")
    payload["primary_causal_diagnosis_track"] = [mechanism]
    with pytest.raises(ValidationError, match="filter routing"):
        P4P5MechanismFilterManifest.model_validate_json(json.dumps(payload))


@settings(max_examples=24, deadline=None)
@given(st.sampled_from(("data_drift", "preprocessing_mismatch")))
def test_rejected_mechanism_cannot_move_to_assumption_limited_track(mechanism: str) -> None:
    payload = load_downstream_disposition_policy_v2().model_dump(mode="json")
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    denominators["assumption_limited_track"] = ["label_noise", mechanism]
    with pytest.raises(ValidationError, match="assumption-limited denominator"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))
