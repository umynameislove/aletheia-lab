"""Property checks for P2R v1.1 recovery authorization identities."""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    P2RRecoveryRegistration,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    load_p2r_recovery_protocol,
)


def _registration() -> P2RRecoveryRegistration:
    recovery = load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH)
    return P2RRecoveryRegistration(
        mechanism="data_drift",
        recovery_protocol_sha256=recovery.canonical_sha256(),
        predecessor_protocol_sha256=recovery.artifacts.predecessor_protocol_sha256,
        predecessor_terminal_store_sha256=(
            recovery.artifacts.predecessor_terminal_store_sha256
        ),
        tagged_protocol_commit="1" * 40,
        tag_name="p2r-data-drift-confirmatory-v1.1",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2r-data-drift-confirmatory-v1.1"
        ),
        release_id=1,
        release_created_at=datetime(2026, 8, 28, tzinfo=UTC),
        release_published_at=datetime(2026, 8, 28, 0, 1, tzinfo=UTC),
        immutable=True,
        draft=False,
        prerelease=False,
    )


@given(st.binary(min_size=1, max_size=64))
def test_arbitrary_recovery_hash_rebinding_changes_registration_identity(
    mutation: bytes,
) -> None:
    registration = _registration()
    changed = mutation.hex().ljust(64, "0")[:64]
    if changed == registration.recovery_protocol_sha256:
        changed = "0" * 64
    mutated = P2RRecoveryRegistration.model_validate(
        registration.model_copy(update={"recovery_protocol_sha256": changed}).model_dump()
    )
    assert mutated.canonical_sha256() != registration.canonical_sha256()


@given(st.integers(min_value=1, max_value=2**31 - 1))
def test_release_id_is_cryptographically_bound_to_registration(release_id: int) -> None:
    registration = _registration()
    mutated = P2RRecoveryRegistration.model_validate(
        registration.model_copy(update={"release_id": release_id}).model_dump()
    )
    if release_id == registration.release_id:
        assert mutated.canonical_sha256() == registration.canonical_sha256()
    else:
        assert mutated.canonical_sha256() != registration.canonical_sha256()


@given(st.sampled_from(("tag_name", "release_url")))
def test_lookalike_release_identifiers_are_always_rejected(field: str) -> None:
    registration = _registration()
    payload = registration.model_dump()
    payload[field] = str(payload[field]) + "-lookalike"
    try:
        P2RRecoveryRegistration.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a lookalike recovery release identity was accepted")
