"""Property checks for P2R v1.2 outcome-blind amendment boundaries."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    P2RV12MethodologicalAmendmentProtocol,
    load_p2r_v1_2_protocol,
)


def _protocol() -> P2RV12MethodologicalAmendmentProtocol:
    return load_p2r_v1_2_protocol(DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH)


@given(st.integers(min_value=0, max_value=5000).filter(lambda value: value != 493))
def test_any_replication_dose_mutation_is_rejected(value: int) -> None:
    payload = _protocol().model_dump()
    payload["datasets"][1]["target_row_count"] = value
    try:
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a non-derived registered dose was accepted")


@given(st.integers(min_value=0, max_value=3000).filter(lambda value: value != 1140))
def test_any_selected_minimum_capacity_mutation_is_rejected(value: int) -> None:
    payload = _protocol().model_dump()
    payload["datasets"][1]["minimum_capacity_count"] = value
    try:
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a mutated outcome-blind capacity was accepted")


@given(st.permutations((0, 1, 2, 3)))
def test_allowed_change_order_is_part_of_protocol_identity(order: list[int]) -> None:
    protocol = _protocol()
    allowed = protocol.scientific_invariants.allowed_changes
    payload = protocol.model_dump()
    payload["scientific_invariants"]["allowed_changes"] = tuple(allowed[index] for index in order)
    if order == [0, 1, 2, 3]:
        assert P2RV12MethodologicalAmendmentProtocol.model_validate(payload) == protocol
        return
    try:
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a reordered methodological delta was accepted")


@given(st.binary(min_size=1, max_size=64))
def test_any_feasibility_receipt_hash_mutation_is_rejected(mutation: bytes) -> None:
    protocol = _protocol()
    changed = mutation.hex().ljust(64, "0")[:64]
    if changed == protocol.artifacts.feasibility_receipt_sha256:
        changed = "0" * 64
    payload = protocol.model_dump()
    payload["artifacts"]["feasibility_receipt_sha256"] = changed
    try:
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a mutated feasibility identity was accepted")
