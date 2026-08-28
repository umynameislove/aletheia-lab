"""Property checks for immutable P2R recovery identities."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    P2RRecoveryProtocol,
    load_p2r_recovery_protocol,
)


@given(st.binary(min_size=1, max_size=64))
def test_any_predecessor_protocol_hash_mutation_is_rejected(mutation: bytes) -> None:
    protocol = load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH)
    payload = protocol.model_dump()
    changed = mutation.hex().ljust(64, "0")[:64]
    if changed == protocol.artifacts.predecessor_protocol_sha256:
        changed = "0" * 64
    payload["artifacts"]["predecessor_protocol_sha256"] = changed
    try:
        P2RRecoveryProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a mutated predecessor identity was accepted")


@given(st.permutations((0, 1, 2, 3)))
def test_allowed_change_order_is_part_of_the_frozen_contract(order: list[int]) -> None:
    protocol = load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH)
    allowed = protocol.technical_recovery.allowed_changes
    payload = protocol.model_dump()
    payload["technical_recovery"]["allowed_changes"] = tuple(allowed[index] for index in order)
    if order == [0, 1, 2, 3]:
        assert P2RRecoveryProtocol.model_validate(payload) == protocol
        return
    try:
        P2RRecoveryProtocol.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a reordered technical delta was accepted")
