"""Registration and atomic evidence preservation for the P2R v1.1 recovery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_closeout import ExecutionEnvironmentReceipt
from aletheia_lab.benchmark.p2.instrument_validity import (
    InstrumentValidityAudit,
    ManipulationObservation,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import LightweightConfirmatoryProtocol
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RJointCloseout,
    P2RProtocolRegistration,
    P2RTechnicalFailure,
    load_and_verify_terminal_store,
    write_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    P2RArchiveReadinessReceipt,
    P2RV1TechnicalFailureAudit,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    P2RRecoveryProtocol,
    verify_p2r_recovery_protocol_pair,
)
from aletheia_lab.benchmark.p2.p2r_runtime import DatasetSeedMeasurement

RECOVERY_REGISTRATION_SCHEMA_VERSION: Final[
    Literal["p2r-recovery-registration/1"]
] = "p2r-recovery-registration/1"
RECOVERY_MARKER_SCHEMA_VERSION: Final[Literal["p2r-recovery-sealed-open/1"]] = (
    "p2r-recovery-sealed-open/1"
)
RECOVERY_STORE_SCHEMA_VERSION: Final[Literal["p2r-recovery-terminal-store/1"]] = (
    "p2r-recovery-terminal-store/1"
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MechanismName = Literal["data_drift", "preprocessing_bug"]
TerminalStatus = Literal["complete", "technical_failure"]


class P2RRecoveryExecutionError(ValueError):
    """Raised when recovery execution evidence is incomplete or inconsistent."""


def _fail(message: str) -> NoReturn:
    raise P2RRecoveryExecutionError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RRecoveryRegistration(_StrictFrozenModel):
    """Immutable-release evidence for a technical wrapper around frozen science."""

    schema_version: Literal["p2r-recovery-registration/1"] = (
        RECOVERY_REGISTRATION_SCHEMA_VERSION
    )
    mechanism: MechanismName
    recovery_protocol_sha256: Sha256
    predecessor_protocol_sha256: Sha256
    predecessor_terminal_store_sha256: Sha256
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
            raise ValueError("recovery release timestamps require timezone evidence")
        return value

    @model_validator(mode="after")
    def _release_identity_is_exact(self) -> P2RRecoveryRegistration:
        tag = {
            "data_drift": "p2r-data-drift-confirmatory-v1.1",
            "preprocessing_bug": "p2r-preprocessing-mismatch-confirmatory-v1.1",
        }[self.mechanism]
        url = "https://github.com/umynameislove/aletheia-lab/releases/tag/" + tag
        if self.tag_name != tag or self.release_url != url:
            raise ValueError("recovery release identity differs from its mechanism")
        if self.release_published_at < self.release_created_at:
            raise ValueError("recovery release publication precedes creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def recovery_registration_from_release(
    *,
    recovery: P2RRecoveryProtocol,
    tagged_protocol_commit: str,
    payload: object,
) -> P2RRecoveryRegistration:
    """Validate one immutable recovery release without authorizing execution."""

    checked = P2RRecoveryProtocol.model_validate(recovery.model_dump())
    if not isinstance(payload, dict):
        _fail("GitHub recovery release response must be an object")
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
        _fail("GitHub recovery release response is incomplete")
    if isinstance(payload["id"], bool) or not isinstance(payload["id"], int):
        _fail("GitHub recovery release id must be an integer")
    try:
        return P2RRecoveryRegistration(
            mechanism=checked.mechanism,
            recovery_protocol_sha256=checked.canonical_sha256(),
            predecessor_protocol_sha256=checked.artifacts.predecessor_protocol_sha256,
            predecessor_terminal_store_sha256=(
                checked.artifacts.predecessor_terminal_store_sha256
            ),
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
        raise P2RRecoveryExecutionError(
            "GitHub release is not an immutable P2R recovery registration"
        ) from exc


def verify_recovery_registration_pair(
    recoveries: Sequence[P2RRecoveryProtocol],
    registrations: Sequence[P2RRecoveryRegistration],
) -> tuple[P2RRecoveryRegistration, P2RRecoveryRegistration]:
    if len(recoveries) != 2 or len(registrations) != 2:
        _fail("recovery registration requires exactly two mechanisms")
    checked_registrations = tuple(
        P2RRecoveryRegistration.model_validate(item.model_dump()) for item in registrations
    )
    if tuple(item.mechanism for item in checked_registrations) != (
        "data_drift",
        "preprocessing_bug",
    ):
        _fail("recovery registration mechanism census is incomplete")
    for recovery, registration in zip(recoveries, checked_registrations, strict=True):
        expected = (
            recovery.mechanism,
            recovery.canonical_sha256(),
            recovery.artifacts.predecessor_protocol_sha256,
            recovery.artifacts.predecessor_terminal_store_sha256,
            recovery.governance.required_git_tag,
        )
        observed = (
            registration.mechanism,
            registration.recovery_protocol_sha256,
            registration.predecessor_protocol_sha256,
            registration.predecessor_terminal_store_sha256,
            registration.tag_name,
        )
        if observed != expected:
            _fail("recovery registration differs from the frozen recovery protocol")
    return checked_registrations  # type: ignore[return-value]


class P2RRecoverySealedMarker(_StrictFrozenModel):
    schema_version: Literal["p2r-recovery-sealed-open/1"] = RECOVERY_MARKER_SCHEMA_VERSION
    execution_commit: GitCommit
    opened_at: datetime
    recovery_protocol_sha256s: tuple[Sha256, Sha256]
    recovery_registration_sha256s: tuple[Sha256, Sha256]
    scientific_protocol_sha256s: tuple[Sha256, Sha256]
    scientific_registration_sha256s: tuple[Sha256, Sha256]
    predecessor_terminal_store_sha256: Sha256
    failure_audit_sha256: Sha256
    archive_readiness_sha256: Sha256
    maximum_paired_attempts: Literal[1]
    predecessor_attempt_retired: Literal[True]
    outcomes_released_together: Literal[True]
    rerun_forbidden: Literal[True]
    marker_sha256: Sha256

    @field_validator("opened_at")
    @classmethod
    def _opened_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("recovery marker timestamp requires timezone evidence")
        return value

    @model_validator(mode="after")
    def _marker_hash_is_derived(self) -> P2RRecoverySealedMarker:
        payload = self.model_dump(mode="json", exclude={"marker_sha256"})
        if self.marker_sha256 != canonical_sha256(payload):
            raise ValueError("recovery marker hash does not bind its evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_recovery_sealed_marker(
    *,
    execution_commit: str,
    recoveries: Sequence[P2RRecoveryProtocol],
    recovery_registrations: Sequence[P2RRecoveryRegistration],
    scientific_registrations: Sequence[P2RProtocolRegistration],
    readiness: P2RArchiveReadinessReceipt,
    failure_audit: P2RV1TechnicalFailureAudit,
    opened_at: datetime | None = None,
    repository_root: str | Path = ".",
) -> P2RRecoverySealedMarker:
    if len(recoveries) != 2:
        _fail("recovery marker requires exactly two mechanisms")
    checked_recoveries = verify_p2r_recovery_protocol_pair(
        recoveries[0], recoveries[1], root=repository_root
    )
    checked_recovery_registrations = verify_recovery_registration_pair(
        checked_recoveries, recovery_registrations
    )
    if tuple(item.mechanism for item in scientific_registrations) != (
        "data_drift",
        "preprocessing_bug",
    ):
        _fail("scientific registration mechanism census is incomplete")
    scientific_hashes = tuple(item.protocol_sha256 for item in scientific_registrations)
    expected_science = tuple(
        item.artifacts.predecessor_protocol_sha256 for item in checked_recoveries
    )
    if scientific_hashes != expected_science:
        _fail("scientific registrations differ from the frozen predecessor protocols")
    if readiness.canonical_sha256() != checked_recoveries[0].readiness.expected_receipt_sha256:
        _fail("sealed marker readiness differs from the recovery protocol")
    if failure_audit.canonical_sha256() != checked_recoveries[0].artifacts.failure_audit_sha256:
        _fail("sealed marker failure audit differs from the recovery protocol")
    timestamp = opened_at or datetime.now(UTC)
    payload: dict[str, object] = {
        "schema_version": RECOVERY_MARKER_SCHEMA_VERSION,
        "execution_commit": execution_commit,
        "opened_at": timestamp,
        "recovery_protocol_sha256s": tuple(
            item.canonical_sha256() for item in checked_recoveries
        ),
        "recovery_registration_sha256s": tuple(
            item.canonical_sha256() for item in checked_recovery_registrations
        ),
        "scientific_protocol_sha256s": scientific_hashes,
        "scientific_registration_sha256s": tuple(
            item.canonical_sha256() for item in scientific_registrations
        ),
        "predecessor_terminal_store_sha256": failure_audit.terminal_store_sha256,
        "failure_audit_sha256": failure_audit.canonical_sha256(),
        "archive_readiness_sha256": readiness.canonical_sha256(),
        "maximum_paired_attempts": 1,
        "predecessor_attempt_retired": True,
        "outcomes_released_together": True,
        "rerun_forbidden": True,
    }
    hash_payload = {
        **payload,
        "opened_at": timestamp.isoformat().replace("+00:00", "Z"),
    }
    return P2RRecoverySealedMarker.model_validate(
        {**payload, "marker_sha256": canonical_sha256(hash_payload)}
    )


def write_recovery_marker_exclusive(
    path: str | Path, marker: P2RRecoverySealedMarker
) -> None:
    checked = P2RRecoverySealedMarker.model_validate(marker.model_dump())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = checked.model_dump_json(indent=2) + "\n"
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise P2RRecoveryExecutionError(
            "P2R v1.1 sealed marker exists; rerun is forbidden"
        ) from exc


def load_recovery_marker(path: str | Path) -> P2RRecoverySealedMarker:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        _fail("P2R v1.1 sealed marker is unavailable or invalid")
    try:
        return P2RRecoverySealedMarker.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise P2RRecoveryExecutionError(
            "P2R v1.1 sealed marker is unavailable or invalid"
        ) from exc


class RecoveryStoreEntry(_StrictFrozenModel):
    relative_path: str
    sha256: Sha256
    byte_count: int = Field(gt=0)

    @field_validator("relative_path")
    @classmethod
    def _path_is_canonical_and_local(cls, value: str) -> str:
        candidate = Path(value)
        if (
            not value
            or "\\" in value
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or candidate.as_posix() != value
        ):
            raise ValueError("recovery store path must be canonical and relative")
        return value


class P2RRecoveryTerminalStore(_StrictFrozenModel):
    schema_version: Literal["p2r-recovery-terminal-store/1"] = RECOVERY_STORE_SCHEMA_VERSION
    terminal_status: TerminalStatus
    recovery_protocol_sha256s: tuple[Sha256, Sha256]
    recovery_registration_sha256s: tuple[Sha256, Sha256]
    predecessor_protocol_sha256s: tuple[Sha256, Sha256]
    predecessor_terminal_store_sha256: Sha256
    failure_audit_sha256: Sha256
    archive_readiness_sha256: Sha256
    sealed_marker_sha256: Sha256
    scientific_store_sha256: Sha256
    entries: tuple[RecoveryStoreEntry, ...]
    store_sha256: Sha256

    @model_validator(mode="after")
    def _store_hash_is_derived(self) -> P2RRecoveryTerminalStore:
        paths = tuple(item.relative_path for item in self.entries)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("recovery terminal store paths must be canonical")
        payload = self.model_dump(mode="json", exclude={"store_sha256"})
        if self.store_sha256 != canonical_sha256(payload):
            raise ValueError("recovery store hash does not bind its evidence")
        return self


def _json_bytes(value: BaseModel | Sequence[BaseModel]) -> bytes:
    payload: object
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = [item.model_dump(mode="json") for item in value]
    return (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode()


def _file_entry(root: Path, path: Path) -> RecoveryStoreEntry:
    if path.is_symlink() or not path.is_file():
        _fail("recovery terminal store contains a non-regular artifact")
    content = path.read_bytes()
    return RecoveryStoreEntry(
        relative_path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
    )


def _write_bytes_durable(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_recovery_terminal_store(
    *,
    output_dir: str | Path,
    recoveries: Sequence[P2RRecoveryProtocol],
    recovery_registrations: Sequence[P2RRecoveryRegistration],
    predecessors: Sequence[LightweightConfirmatoryProtocol],
    scientific_registrations: Sequence[P2RProtocolRegistration],
    terminal: P2RJointCloseout | P2RTechnicalFailure,
    environment: ExecutionEnvironmentReceipt,
    readiness: P2RArchiveReadinessReceipt,
    failure_audit: P2RV1TechnicalFailureAudit,
    sealed_marker: P2RRecoverySealedMarker,
    repository_root: str | Path = ".",
    measurements: Sequence[DatasetSeedMeasurement] = (),
    observations: Sequence[ManipulationObservation] = (),
    audit: InstrumentValidityAudit | None = None,
) -> P2RRecoveryTerminalStore:
    """Atomically preserve recovery authorization around the unchanged science store."""

    target = Path(output_dir)
    if target.exists() or target.is_symlink():
        _fail("P2R v1.1 terminal store already exists; rerun is forbidden")
    if len(recoveries) != 2:
        _fail("recovery store requires exactly two mechanisms")
    checked_recoveries = verify_p2r_recovery_protocol_pair(
        recoveries[0], recoveries[1], root=repository_root
    )
    checked_registrations = verify_recovery_registration_pair(
        checked_recoveries, recovery_registrations
    )
    predecessor_hashes = tuple(item.canonical_sha256() for item in predecessors)
    if predecessor_hashes != tuple(
        item.artifacts.predecessor_protocol_sha256 for item in checked_recoveries
    ):
        _fail("recovery store predecessor protocols differ from the frozen chain")
    if readiness.canonical_sha256() != checked_recoveries[0].readiness.expected_receipt_sha256:
        _fail("recovery store readiness differs from the frozen protocol")
    if failure_audit.canonical_sha256() != checked_recoveries[0].artifacts.failure_audit_sha256:
        _fail("recovery store failure audit differs from the frozen protocol")
    checked_marker = P2RRecoverySealedMarker.model_validate(sealed_marker.model_dump())
    expected_marker = (
        terminal.execution_commit,
        tuple(item.canonical_sha256() for item in checked_recoveries),
        tuple(item.canonical_sha256() for item in checked_registrations),
        predecessor_hashes,
        tuple(item.canonical_sha256() for item in scientific_registrations),
        failure_audit.terminal_store_sha256,
        failure_audit.canonical_sha256(),
        readiness.canonical_sha256(),
    )
    observed_marker = (
        checked_marker.execution_commit,
        checked_marker.recovery_protocol_sha256s,
        checked_marker.recovery_registration_sha256s,
        checked_marker.scientific_protocol_sha256s,
        checked_marker.scientific_registration_sha256s,
        checked_marker.predecessor_terminal_store_sha256,
        checked_marker.failure_audit_sha256,
        checked_marker.archive_readiness_sha256,
    )
    if observed_marker != expected_marker:
        _fail("recovery sealed marker differs from the terminal evidence")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        _write_bytes_durable(
            temporary / "recovery-protocols.json", _json_bytes(checked_recoveries)
        )
        _write_bytes_durable(
            temporary / "recovery-registrations.json", _json_bytes(checked_registrations)
        )
        _write_bytes_durable(temporary / "archive-readiness.json", _json_bytes(readiness))
        _write_bytes_durable(
            temporary / "predecessor-failure-audit.json", _json_bytes(failure_audit)
        )
        _write_bytes_durable(temporary / "sealed-open.json", _json_bytes(checked_marker))
        scientific = write_terminal_store(
            output_dir=temporary / "scientific-store",
            protocols=predecessors,
            registrations=scientific_registrations,
            terminal=terminal,
            environment=environment,
            measurements=measurements,
            observations=observations,
            audit=audit,
        )
        entries = tuple(
            _file_entry(temporary, path)
            for path in sorted(
                (item for item in temporary.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(temporary).as_posix(),
            )
        )
        payload: dict[str, object] = {
            "schema_version": RECOVERY_STORE_SCHEMA_VERSION,
            "terminal_status": scientific.terminal_status,
            "recovery_protocol_sha256s": tuple(
                item.canonical_sha256() for item in checked_recoveries
            ),
            "recovery_registration_sha256s": tuple(
                item.canonical_sha256() for item in checked_registrations
            ),
            "predecessor_protocol_sha256s": predecessor_hashes,
            "predecessor_terminal_store_sha256": failure_audit.terminal_store_sha256,
            "failure_audit_sha256": failure_audit.canonical_sha256(),
            "archive_readiness_sha256": readiness.canonical_sha256(),
            "sealed_marker_sha256": checked_marker.canonical_sha256(),
            "scientific_store_sha256": scientific.store_sha256,
            "entries": entries,
        }
        hash_payload = {
            **payload,
            "entries": tuple(item.model_dump(mode="json") for item in entries),
        }
        manifest = P2RRecoveryTerminalStore.model_validate(
            {**payload, "store_sha256": canonical_sha256(hash_payload)}
        )
        _write_bytes_durable(temporary / "manifest.json", _json_bytes(manifest))
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def load_and_verify_recovery_terminal_store(
    path: str | Path,
) -> P2RRecoveryTerminalStore:
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        _fail("P2R v1.1 terminal store is unavailable or invalid")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        _fail("P2R v1.1 terminal manifest is unavailable or invalid")
    try:
        manifest = P2RRecoveryTerminalStore.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise P2RRecoveryExecutionError(
            "P2R v1.1 terminal manifest is unavailable or invalid"
        ) from exc
    expected = {item.relative_path for item in manifest.entries}
    observed = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and item != root / "manifest.json"
    }
    if observed != expected:
        _fail("P2R v1.1 store contains missing or unmanifested artifacts")
    for entry in manifest.entries:
        artifact = root / entry.relative_path
        if artifact.is_symlink() or not artifact.is_file():
            _fail("P2R v1.1 store contains a non-regular artifact")
        content = artifact.read_bytes()
        if len(content) != entry.byte_count or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail("P2R v1.1 artifact hash or size mismatch")
    scientific = load_and_verify_terminal_store(root / "scientific-store")
    if (
        scientific.store_sha256 != manifest.scientific_store_sha256
        or scientific.terminal_status != manifest.terminal_status
        or scientific.protocol_sha256s != manifest.predecessor_protocol_sha256s
    ):
        _fail("P2R v1.1 scientific store differs from the recovery manifest")
    try:
        recoveries_raw = json.loads((root / "recovery-protocols.json").read_text())
        registrations_raw = json.loads((root / "recovery-registrations.json").read_text())
        if not isinstance(recoveries_raw, list) or not isinstance(registrations_raw, list):
            raise TypeError("recovery evidence census must contain lists")
        recoveries = tuple(
            P2RRecoveryProtocol.model_validate_json(json.dumps(item)) for item in recoveries_raw
        )
        registrations = tuple(
            P2RRecoveryRegistration.model_validate_json(json.dumps(item))
            for item in registrations_raw
        )
        readiness = P2RArchiveReadinessReceipt.model_validate_json(
            (root / "archive-readiness.json").read_text()
        )
        failure = P2RV1TechnicalFailureAudit.model_validate_json(
            (root / "predecessor-failure-audit.json").read_text()
        )
        marker = P2RRecoverySealedMarker.model_validate_json(
            (root / "sealed-open.json").read_text()
        )
        scientific_registrations_raw = json.loads(
            (root / "scientific-store" / "registrations.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(scientific_registrations_raw, list):
            raise TypeError("scientific registration census must contain a list")
        scientific_registrations = tuple(
            P2RProtocolRegistration.model_validate_json(json.dumps(item))
            for item in scientific_registrations_raw
        )
        scientific_environment = ExecutionEnvironmentReceipt.model_validate_json(
            (root / "scientific-store" / "environment.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, TypeError, ValueError) as exc:
        raise P2RRecoveryExecutionError("P2R v1.1 recovery evidence is invalid") from exc
    if (
        len(recoveries) != 2
        or tuple(item.mechanism for item in recoveries)
        != ("data_drift", "preprocessing_bug")
        or recoveries[0].readiness != recoveries[1].readiness
        or recoveries[0].technical_recovery != recoveries[1].technical_recovery
        or tuple(item.artifacts.predecessor_protocol_sha256 for item in recoveries)
        != manifest.predecessor_protocol_sha256s
        or any(
            item.artifacts.predecessor_terminal_store_sha256
            != manifest.predecessor_terminal_store_sha256
            for item in recoveries
        )
        or any(
            item.artifacts.failure_audit_sha256 != manifest.failure_audit_sha256
            for item in recoveries
        )
        or any(
            item.readiness.expected_receipt_sha256 != manifest.archive_readiness_sha256
            for item in recoveries
        )
    ):
        _fail("P2R v1.1 preserved recovery chain does not reconcile")
    if tuple(item.canonical_sha256() for item in recoveries) != manifest.recovery_protocol_sha256s:
        _fail("P2R v1.1 recovery protocol census differs from the manifest")
    verify_recovery_registration_pair(recoveries, registrations)
    if tuple(item.canonical_sha256() for item in registrations) != (
        manifest.recovery_registration_sha256s
    ):
        _fail("P2R v1.1 recovery registration census differs from the manifest")
    if readiness.canonical_sha256() != manifest.archive_readiness_sha256:
        _fail("P2R v1.1 readiness differs from the manifest")
    if marker.canonical_sha256() != manifest.sealed_marker_sha256:
        _fail("P2R v1.1 sealed marker differs from the manifest")
    if (
        marker.recovery_protocol_sha256s != manifest.recovery_protocol_sha256s
        or marker.recovery_registration_sha256s
        != manifest.recovery_registration_sha256s
        or marker.scientific_protocol_sha256s != manifest.predecessor_protocol_sha256s
        or marker.scientific_registration_sha256s
        != tuple(item.canonical_sha256() for item in scientific_registrations)
        or marker.execution_commit != scientific_environment.execution_commit
        or marker.predecessor_terminal_store_sha256
        != manifest.predecessor_terminal_store_sha256
        or marker.archive_readiness_sha256 != manifest.archive_readiness_sha256
        or marker.failure_audit_sha256 != manifest.failure_audit_sha256
    ):
        _fail("P2R v1.1 sealed marker provenance does not reconcile")
    if (
        failure.canonical_sha256() != manifest.failure_audit_sha256
        or failure.terminal_store_sha256 != manifest.predecessor_terminal_store_sha256
    ):
        _fail("P2R v1.1 predecessor failure differs from the manifest")
    return manifest
