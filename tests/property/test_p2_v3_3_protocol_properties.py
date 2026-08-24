"""Property invariants preventing outcome-aware drift in v3.3."""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    load_v3_2_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    DEFAULT_V3_3_PROTOCOL_PATH,
    V33ConfirmatoryProtocol,
    load_v3_3_confirmatory_protocol,
    verify_v3_3_technical_delta,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import V3ProtocolError


@given(
    dataset_index=st.integers(min_value=0, max_value=1),
    replacement=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
)
def test_any_split_membership_rebinding_is_detected(
    dataset_index: int,
    replacement: str,
) -> None:
    current = load_v3_3_confirmatory_protocol()
    assume(replacement != current.dataset_splits[dataset_index].membership_sha256)
    splits = list(current.dataset_splits)
    splits[dataset_index] = splits[dataset_index].model_copy(
        update={"membership_sha256": replacement}
    )
    changed = current.model_copy(update={"dataset_splits": tuple(splits)})
    with pytest.raises(V3ProtocolError, match="dataset_splits"):
        verify_v3_3_technical_delta(changed, load_v3_2_confirmatory_protocol())


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
    payload = json.loads(DEFAULT_V3_3_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["inference"]["minimum_practical_effect"] = threshold
    with pytest.raises((ValidationError, V3ProtocolError)):
        changed = V33ConfirmatoryProtocol.model_validate_json(json.dumps(payload))
        verify_v3_3_technical_delta(changed, load_v3_2_confirmatory_protocol())


@given(extra_change=st.text(min_size=1).filter(lambda value: value.strip() != ""))
def test_allowed_recovery_delta_cannot_expand(extra_change: str) -> None:
    payload = json.loads(DEFAULT_V3_3_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["technical_recovery"]["allowed_changes"].append(extra_change)
    with pytest.raises(ValidationError):
        V33ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


@given(replacement=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))
def test_any_failure_store_rebinding_is_rejected(replacement: str) -> None:
    current = load_v3_3_confirmatory_protocol()
    assume(replacement != current.technical_recovery.predecessor_terminal_store_sha256)
    payload = json.loads(DEFAULT_V3_3_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["technical_recovery"]["predecessor_terminal_store_sha256"] = replacement
    with pytest.raises(ValidationError):
        V33ConfirmatoryProtocol.model_validate_json(json.dumps(payload))
