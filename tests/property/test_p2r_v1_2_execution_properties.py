"""Property checks for P2R v1.2 registration and attempt identities."""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_v1_2_execution import (
    P2R_V1_2_TAGGED_COMMIT,
    P2RV12Registration,
)


def _registration() -> P2RV12Registration:
    return P2RV12Registration(
        mechanism="data_drift",
        amendment_protocol_sha256="1" * 64,
        protocol_sha256="2" * 64,
        archive_readiness_sha256="3" * 64,
        tagged_protocol_commit=P2R_V1_2_TAGGED_COMMIT,
        tag_name="p2r-data-drift-confirmatory-v1.2",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2r-data-drift-confirmatory-v1.2"
        ),
        release_id=1,
        release_created_at=datetime(2026, 8, 29, tzinfo=UTC),
        release_published_at=datetime(2026, 8, 29, 0, 1, tzinfo=UTC),
        immutable=True,
        draft=False,
        prerelease=False,
    )


@given(st.integers(min_value=1, max_value=2**31 - 1))
def test_release_id_is_bound_to_v1_2_registration(release_id: int) -> None:
    registration = _registration()
    mutated = P2RV12Registration.model_validate(
        registration.model_copy(update={"release_id": release_id}).model_dump()
    )
    if release_id == registration.release_id:
        assert mutated.canonical_sha256() == registration.canonical_sha256()
    else:
        assert mutated.canonical_sha256() != registration.canonical_sha256()


@given(st.sampled_from(("tag_name", "release_url", "tagged_protocol_commit")))
def test_any_lookalike_release_identity_is_rejected(field: str) -> None:
    registration = _registration()
    payload = registration.model_dump()
    payload[field] = str(payload[field])[:-1] + "0"
    try:
        P2RV12Registration.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("a lookalike v1.2 release identity was accepted")


@given(st.binary(min_size=1, max_size=64))
def test_any_protocol_rebinding_changes_registration_identity(mutation: bytes) -> None:
    registration = _registration()
    changed = mutation.hex().ljust(64, "0")[:64]
    if changed == registration.protocol_sha256:
        changed = "0" * 64
    mutated = P2RV12Registration.model_validate(
        registration.model_copy(update={"protocol_sha256": changed}).model_dump()
    )
    assert mutated.canonical_sha256() != registration.canonical_sha256()
