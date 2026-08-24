"""Property invariants preventing outcome-aware drift in the v3.2 recovery."""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    DEFAULT_V3_2_PROTOCOL_PATH,
    V32ConfirmatoryProtocol,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_technical_delta,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    V3ProtocolError,
    load_v3_confirmatory_protocol,
)


@given(
    dataset_index=st.integers(min_value=0, max_value=1),
    replacement=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
)
def test_any_split_membership_rebinding_is_detected(dataset_index: int, replacement: str) -> None:
    current = load_v3_2_confirmatory_protocol()
    assume(replacement != current.dataset_splits[dataset_index].membership_sha256)
    splits = list(current.dataset_splits)
    splits[dataset_index] = splits[dataset_index].model_copy(
        update={"membership_sha256": replacement}
    )
    changed = current.model_copy(update={"dataset_splits": tuple(splits)})
    with pytest.raises(V3ProtocolError, match="dataset_splits"):
        verify_v3_2_technical_delta(changed, load_v3_confirmatory_protocol())


@given(
    threshold=st.floats(
        min_value=0.0001,
        max_value=0.2,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_any_changed_practical_threshold_is_rejected(threshold: float) -> None:
    assume(threshold != 0.05)
    payload = json.loads(DEFAULT_V3_2_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["inference"]["minimum_practical_effect"] = threshold
    with pytest.raises(ValidationError):
        V32ConfirmatoryProtocol.model_validate(payload)


@given(extra_change=st.text(min_size=1).filter(lambda value: value.strip() != ""))
def test_allowed_recovery_delta_cannot_expand(extra_change: str) -> None:
    payload = json.loads(DEFAULT_V3_2_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["technical_recovery"]["allowed_changes"].append(extra_change)
    with pytest.raises(ValidationError):
        V32ConfirmatoryProtocol.model_validate(payload)
