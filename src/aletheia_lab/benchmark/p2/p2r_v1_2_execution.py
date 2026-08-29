"""Registration and single-attempt boundaries for the P2R v1.2 amendment.

The public v1.2 artifact is a methodological wrapper, not an executable copy of
the v1 protocol.  This module deterministically compiles that wrapper and its
verified predecessor into the exact execution protocol used by the existing
P2R runtime.  Registration binds both identities so neither the amendment nor
the executable interpretation can drift after release.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
    StudyDatasetBinding,
    verify_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_recovery import P2RArchiveReadinessReceipt
from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    P2RV12MethodologicalAmendmentProtocol,
    verify_p2r_v1_2_protocol,
    verify_p2r_v1_2_protocol_pair,
)

REGISTRATION_SCHEMA_VERSION: Final[Literal["p2r-v1-2-registration/1"]] = (
    "p2r-v1-2-registration/1"
)
MARKER_SCHEMA_VERSION: Final[Literal["p2r-v1-2-sealed-open/1"]] = (
    "p2r-v1-2-sealed-open/1"
)
P2R_V1_2_TAGGED_COMMIT: Final[str] = "19f600e7f4a04b42c4f32d5d1c33ae2dbac3c1c3"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MechanismName = Literal["data_drift", "preprocessing_bug"]


class P2RV12ExecutionError(ValueError):
    """Raised when v1.2 registration or execution evidence is inconsistent."""


def _fail(message: str) -> NoReturn:
    raise P2RV12ExecutionError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def compile_execution_protocol(
    amendment: P2RV12MethodologicalAmendmentProtocol,
    *,
    root: str | Path = ".",
) -> LightweightConfirmatoryProtocol:
    """Compile the registered amendment without introducing a free parameter."""

    checked, predecessor, _, _, _ = verify_p2r_v1_2_protocol(amendment, root=root)
    prior_by_dataset = {item.dataset_id: item for item in predecessor.datasets}
    datasets = tuple(
        StudyDatasetBinding(
            dataset_id=item.dataset_id,
            role=item.role,
            split_membership_sha256=item.split_membership_sha256,
            sealed_membership_sha256=item.sealed_membership_sha256,
            target_feature=item.selected_target_feature,
            intervention_rule=item.intervention_rule,
            nuisance_comparator=item.nuisance_comparator,
        )
        for item in checked.datasets
    )
    for item in datasets:
        prior = prior_by_dataset[item.dataset_id]
        if (
            item.role != prior.role
            or item.split_membership_sha256 != prior.split_membership_sha256
            or item.sealed_membership_sha256 != prior.sealed_membership_sha256
        ):
            _fail("compiled v1.2 execution protocol changes a frozen dataset identity")
    payload = predecessor.model_dump()
    payload.update(
        protocol_version=checked.protocol_version,
        required_git_tag=checked.governance.required_git_tag,
        datasets=tuple(item.model_dump() for item in datasets),
    )
    try:
        return verify_lightweight_confirmatory_protocol(
            LightweightConfirmatoryProtocol.model_validate(payload)
        )
    except ValueError as exc:
        raise P2RV12ExecutionError(
            "v1.2 amendment cannot compile to the frozen execution contract"
        ) from exc


class P2RV12Registration(_StrictFrozenModel):
    """Immutable release evidence binding amendment and compiled execution."""

    schema_version: Literal["p2r-v1-2-registration/1"] = REGISTRATION_SCHEMA_VERSION
    mechanism: MechanismName
    amendment_protocol_sha256: Sha256
    protocol_sha256: Sha256
    archive_readiness_sha256: Sha256
    tagged_protocol_commit: GitCommit
    tag_name: str
    release_url: str
    release_id: int = Field(gt=0)
    release_created_at: datetime
    release_published_at: datetime
    immutable: Literal[True]
    draft: Literal[False]
    prerelease: Literal[False]

    @field_validator("release_created_at", "release_published_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v1.2 release timestamps require timezone evidence")
        return value

    @model_validator(mode="after")
    def _identity_is_exact(self) -> P2RV12Registration:
        tag = {
            "data_drift": "p2r-data-drift-confirmatory-v1.2",
            "preprocessing_bug": "p2r-preprocessing-mismatch-confirmatory-v1.2",
        }[self.mechanism]
        url = "https://github.com/umynameislove/aletheia-lab/releases/tag/" + tag
        if self.tag_name != tag or self.release_url != url:
            raise ValueError("v1.2 release identity differs from its mechanism")
        if self.tagged_protocol_commit != P2R_V1_2_TAGGED_COMMIT:
            raise ValueError("v1.2 release is bound to another protocol commit")
        if self.release_published_at < self.release_created_at:
            raise ValueError("v1.2 release publication precedes creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def registration_from_release(
    *,
    amendment: P2RV12MethodologicalAmendmentProtocol,
    execution_protocol: LightweightConfirmatoryProtocol,
    archive_readiness: P2RArchiveReadinessReceipt,
    tagged_protocol_commit: str,
    payload: object,
) -> P2RV12Registration:
    """Validate an immutable GitHub release without trusting missing fields."""

    if not isinstance(payload, dict):
        _fail("GitHub v1.2 release response must be an object")
    required = {
        "tag_name",
        "id",
        "html_url",
        "created_at",
        "published_at",
        "immutable",
        "draft",
        "prerelease",
    }
    if not required <= set(payload):
        _fail("GitHub v1.2 release response is incomplete")
    if isinstance(payload["id"], bool) or not isinstance(payload["id"], int):
        _fail("GitHub v1.2 release id must be an integer")
    try:
        return P2RV12Registration(
            mechanism=amendment.mechanism,
            amendment_protocol_sha256=amendment.canonical_sha256(),
            protocol_sha256=execution_protocol.canonical_sha256(),
            archive_readiness_sha256=archive_readiness.canonical_sha256(),
            tagged_protocol_commit=tagged_protocol_commit,
            tag_name=payload["tag_name"],
            release_url=payload["html_url"],
            release_id=payload["id"],
            release_created_at=datetime.fromisoformat(
                str(payload["created_at"]).replace("Z", "+00:00")
            ),
            release_published_at=datetime.fromisoformat(
                str(payload["published_at"]).replace("Z", "+00:00")
            ),
            immutable=payload["immutable"],
            draft=payload["draft"],
            prerelease=payload["prerelease"],
        )
    except (TypeError, ValueError) as exc:
        raise P2RV12ExecutionError(
            "GitHub release is not an immutable P2R v1.2 registration"
        ) from exc


def verify_registration_pair(
    amendments: Sequence[P2RV12MethodologicalAmendmentProtocol],
    protocols: Sequence[LightweightConfirmatoryProtocol],
    registrations: Sequence[P2RV12Registration],
    archive_readiness: P2RArchiveReadinessReceipt,
    *,
    root: str | Path = ".",
) -> tuple[P2RV12Registration, P2RV12Registration]:
    if len(amendments) != 2 or len(protocols) != 2 or len(registrations) != 2:
        _fail("v1.2 registration requires exactly two mechanisms")
    checked_amendments = verify_p2r_v1_2_protocol_pair(
        amendments[0], amendments[1], root=root
    )
    checked_registrations = tuple(
        P2RV12Registration.model_validate(item.model_dump()) for item in registrations
    )
    if tuple(item.mechanism for item in checked_registrations) != (
        "data_drift",
        "preprocessing_bug",
    ):
        _fail("v1.2 registration mechanism census is incomplete")
    for amendment, protocol, registration in zip(
        checked_amendments, protocols, checked_registrations, strict=True
    ):
        compiled = compile_execution_protocol(amendment, root=root)
        expected = (
            amendment.mechanism,
            amendment.canonical_sha256(),
            compiled.canonical_sha256(),
            archive_readiness.canonical_sha256(),
            amendment.governance.required_git_tag,
        )
        observed = (
            registration.mechanism,
            registration.amendment_protocol_sha256,
            registration.protocol_sha256,
            registration.archive_readiness_sha256,
            registration.tag_name,
        )
        if protocol != compiled or observed != expected:
            _fail("v1.2 registration differs from the frozen amendment chain")
    return checked_registrations  # type: ignore[return-value]


class P2RV12SealedMarker(_StrictFrozenModel):
    schema_version: Literal["p2r-v1-2-sealed-open/1"] = MARKER_SCHEMA_VERSION
    execution_commit: GitCommit
    opened_at: datetime
    amendment_protocol_sha256s: tuple[Sha256, Sha256]
    execution_protocol_sha256s: tuple[Sha256, Sha256]
    registration_sha256s: tuple[Sha256, Sha256]
    predecessor_terminal_store_sha256: Sha256
    maximum_paired_attempts: Literal[1]
    outcomes_released_together: Literal[True]
    rerun_forbidden: Literal[True]
    marker_sha256: Sha256

    @field_validator("opened_at")
    @classmethod
    def _opened_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v1.2 marker timestamp requires timezone evidence")
        return value

    @model_validator(mode="after")
    def _hash_is_derived(self) -> P2RV12SealedMarker:
        payload = self.model_dump(mode="json", exclude={"marker_sha256"})
        if self.marker_sha256 != canonical_sha256(payload):
            raise ValueError("v1.2 marker hash does not bind its evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_sealed_marker(
    *,
    execution_commit: str,
    amendments: Sequence[P2RV12MethodologicalAmendmentProtocol],
    protocols: Sequence[LightweightConfirmatoryProtocol],
    registrations: Sequence[P2RV12Registration],
    archive_readiness: P2RArchiveReadinessReceipt,
    root: str | Path = ".",
    opened_at: datetime | None = None,
) -> P2RV12SealedMarker:
    checked = verify_registration_pair(
        amendments, protocols, registrations, archive_readiness, root=root
    )
    timestamp = opened_at or datetime.now(UTC)
    payload: dict[str, object] = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "execution_commit": execution_commit,
        "opened_at": timestamp,
        "amendment_protocol_sha256s": tuple(
            item.canonical_sha256() for item in amendments
        ),
        "execution_protocol_sha256s": tuple(
            item.canonical_sha256() for item in protocols
        ),
        "registration_sha256s": tuple(item.canonical_sha256() for item in checked),
        "predecessor_terminal_store_sha256": (
            amendments[0].artifacts.predecessor_terminal_store_sha256
        ),
        "maximum_paired_attempts": 1,
        "outcomes_released_together": True,
        "rerun_forbidden": True,
    }
    hash_payload = {
        **payload,
        "opened_at": timestamp.isoformat().replace("+00:00", "Z"),
    }
    return P2RV12SealedMarker.model_validate(
        {**payload, "marker_sha256": canonical_sha256(hash_payload)}
    )


def write_marker_exclusive(path: str | Path, marker: P2RV12SealedMarker) -> None:
    checked = P2RV12SealedMarker.model_validate(marker.model_dump())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(checked.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise P2RV12ExecutionError(
            "P2R v1.2 sealed marker exists; rerun is forbidden"
        ) from exc
