"""Fail-closed registration and atomic publication for confirmatory outcomes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from platform import machine, platform, python_implementation, python_version
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_inference import StudyDecision, decide_study
from aletheia_lab.benchmark.p2.confirmatory_protocol import ConfirmatoryProtocol
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    DatasetOutcome,
    validate_dose_monotonicity,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

REGISTRATION_SCHEMA_VERSION: Final[Literal["p2-confirmatory-registration/1"]] = (
    "p2-confirmatory-registration/1"
)
CLOSEOUT_SCHEMA_VERSION: Final[Literal["p2-confirmatory-closeout/1"]] = "p2-confirmatory-closeout/1"
STORE_SCHEMA_VERSION: Final[Literal["p2-confirmatory-result-store/1"]] = (
    "p2-confirmatory-result-store/1"
)
ENVIRONMENT_SCHEMA_VERSION: Final[Literal["p2-confirmatory-environment/1"]] = (
    "p2-confirmatory-environment/1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_RELEASE_URL = re.compile(
    r"^https://github\.com/umynameislove/aletheia-lab/releases/tag/"
    r"p2-label-noise-confirmatory-v2$"
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProtocolRegistrationReceipt(_StrictFrozenModel):
    """Auditable external registration that must predate sealed execution."""

    schema_version: Literal["p2-confirmatory-registration/1"] = REGISTRATION_SCHEMA_VERSION
    protocol_sha256: Sha256
    tag_name: Literal["p2-label-noise-confirmatory-v2"]
    tagged_protocol_commit: GitCommit
    release_url: str
    release_id: int = Field(gt=0)
    release_created_at: datetime
    release_published_at: datetime
    immutable: Literal[True]
    draft: Literal[False]
    prerelease: Literal[False]

    @field_validator("release_url")
    @classmethod
    def _release_is_the_registered_repository(cls, value: str) -> str:
        if _RELEASE_URL.fullmatch(value) is None:
            raise ValueError("registration must use the frozen repository release URL")
        return value

    @field_validator("release_created_at", "release_published_at")
    @classmethod
    def _timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("registration timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _release_timeline_is_valid(self) -> ProtocolRegistrationReceipt:
        if self.release_published_at < self.release_created_at:
            raise ValueError("release publication cannot predate release creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def registration_from_github_release(
    *,
    protocol: ConfirmatoryProtocol,
    tagged_protocol_commit: str,
    payload: object,
) -> ProtocolRegistrationReceipt:
    """Validate the minimal GitHub API payload needed for preregistration."""

    if not isinstance(payload, dict):
        raise ConfirmatoryExecutionError("GitHub release response must be an object")
    try:
        tag_name = payload["tag_name"]
        release_id = payload["id"]
        html_url = payload["html_url"]
        created_at = payload["created_at"]
        published_at = payload["published_at"]
        immutable = payload["immutable"]
        draft = payload["draft"]
        prerelease = payload["prerelease"]
    except KeyError as exc:
        raise ConfirmatoryExecutionError("GitHub release response is incomplete") from exc
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        raise ConfirmatoryExecutionError("GitHub release id must be an integer")
    if not isinstance(created_at, str) or not isinstance(published_at, str):
        raise ConfirmatoryExecutionError("GitHub release timestamps must be strings")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        parsed_published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfirmatoryExecutionError("GitHub release timestamps are invalid") from exc
    try:
        return ProtocolRegistrationReceipt(
            protocol_sha256=protocol.canonical_sha256(),
            tag_name=tag_name,
            tagged_protocol_commit=tagged_protocol_commit,
            release_url=html_url,
            release_id=release_id,
            release_created_at=parsed_created_at,
            release_published_at=parsed_published_at,
            immutable=immutable,
            draft=draft,
            prerelease=prerelease,
        )
    except ValueError as exc:
        raise ConfirmatoryExecutionError(
            "GitHub release does not satisfy immutable preregistration"
        ) from exc


class ConfirmatoryCloseout(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-closeout/1"] = CLOSEOUT_SCHEMA_VERSION
    protocol_sha256: Sha256
    registration_sha256: Sha256
    execution_commit: GitCommit
    executed_at: datetime
    environment_sha256: Sha256
    primary_outcome_sha256: Sha256
    replication_outcome_sha256: Sha256
    primary_replicate_count: Literal[180]
    replication_replicate_count: Literal[180]
    primary_dose_monotonicity: dict[str, bool]
    replication_dose_monotonicity: dict[str, bool]
    decision: StudyDecision
    outcomes_released_together: Literal[True] = True

    @field_validator("executed_at")
    @classmethod
    def _execution_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("execution timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def _decision_hashes_reconcile(self) -> ConfirmatoryCloseout:
        if self.decision.protocol_sha256 != self.protocol_sha256:
            raise ValueError("closeout decision uses another protocol")
        if set(self.primary_dose_monotonicity) != {"yes_to_no", "no_to_yes"}:
            raise ValueError("primary dose-response report is incomplete")
        if set(self.replication_dose_monotonicity) != {"yes_to_no", "no_to_yes"}:
            raise ValueError("replication dose-response report is incomplete")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ExecutionEnvironmentReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-environment/1"] = ENVIRONMENT_SCHEMA_VERSION
    execution_commit: GitCommit
    python_version: str = Field(min_length=1)
    python_implementation: str = Field(min_length=1)
    operating_system: str = Field(min_length=1)
    machine: str = Field(min_length=1)
    package_versions: dict[str, str]

    @model_validator(mode="after")
    def _required_packages_are_present(self) -> ExecutionEnvironmentReceipt:
        required = {"numpy", "pandas", "pydantic", "scikit-learn", "scipy"}
        if set(self.package_versions) != required:
            raise ValueError("execution environment package census is incomplete")
        if any(not value for value in self.package_versions.values()):
            raise ValueError("execution environment package versions must be non-empty")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def capture_execution_environment(execution_commit: str) -> ExecutionEnvironmentReceipt:
    """Capture the exact interpreter and scientific package versions."""

    packages: dict[str, str] = {}
    for name in ("numpy", "pandas", "pydantic", "scikit-learn", "scipy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError as exc:
            raise ConfirmatoryExecutionError(
                f"required execution package is unavailable: {name}"
            ) from exc
    return ExecutionEnvironmentReceipt(
        execution_commit=execution_commit,
        python_version=python_version(),
        python_implementation=python_implementation(),
        operating_system=platform(),
        machine=machine(),
        package_versions=packages,
    )


def build_closeout(
    *,
    protocol: ConfirmatoryProtocol,
    registration: ProtocolRegistrationReceipt,
    environment: ExecutionEnvironmentReceipt,
    execution_commit: str,
    primary: DatasetOutcome,
    replication: DatasetOutcome,
    executed_at: datetime | None = None,
) -> ConfirmatoryCloseout:
    """Derive the only scientific decision after both outcomes are complete."""

    if registration.protocol_sha256 != protocol.canonical_sha256():
        raise ConfirmatoryExecutionError("registration does not bind the frozen protocol")
    if environment.execution_commit != execution_commit:
        raise ConfirmatoryExecutionError("environment does not bind the execution commit")
    timestamp = executed_at or datetime.now(UTC)
    if timestamp <= registration.release_published_at:
        raise ConfirmatoryExecutionError("execution must occur after immutable registration")
    if (
        primary.receipt.dataset_role != "primary"
        or replication.receipt.dataset_role != "external_replication"
    ):
        raise ConfirmatoryExecutionError("closeout requires primary then external replication")
    decision = decide_study(
        protocol=protocol,
        primary=primary.analysis,
        replication=replication.analysis,
    )
    return ConfirmatoryCloseout(
        protocol_sha256=protocol.canonical_sha256(),
        registration_sha256=registration.canonical_sha256(),
        execution_commit=execution_commit,
        executed_at=timestamp,
        environment_sha256=environment.canonical_sha256(),
        primary_outcome_sha256=primary.canonical_sha256(),
        replication_outcome_sha256=replication.canonical_sha256(),
        primary_replicate_count=180,
        replication_replicate_count=180,
        primary_dose_monotonicity=validate_dose_monotonicity(primary),
        replication_dose_monotonicity=validate_dose_monotonicity(replication),
        decision=decision,
    )


class StoreEntry(_StrictFrozenModel):
    relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)

    @field_validator("relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("store paths must be normalized relative POSIX paths")
        if path.as_posix() != value:
            raise ValueError("store paths must use POSIX syntax")
        return value


class ResultStoreManifest(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-result-store/1"] = STORE_SCHEMA_VERSION
    protocol_sha256: Sha256
    closeout_sha256: Sha256
    entries: tuple[StoreEntry, ...]
    store_sha256: Sha256

    @model_validator(mode="after")
    def _store_is_complete(self) -> ResultStoreManifest:
        required = {
            "registration.json",
            "primary-outcome.json",
            "replication-outcome.json",
            "closeout.json",
            "environment.json",
        }
        paths = tuple(item.relative_path for item in self.entries)
        if set(paths) != required or paths != tuple(sorted(paths)):
            raise ValueError("result store must contain the complete canonical artifact set")
        expected = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": self.protocol_sha256,
                "closeout_sha256": self.closeout_sha256,
                "entries": [item.model_dump(mode="json") for item in self.entries],
            }
        )
        if self.store_sha256 != expected:
            raise ValueError("result store root does not match its entries")
        return self


def _json_bytes(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json")
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ConfirmatoryExecutionError(f"cannot write immutable result artifact: {path}") from exc


def write_result_store(
    *,
    output_dir: str | Path,
    registration: ProtocolRegistrationReceipt,
    environment: ExecutionEnvironmentReceipt,
    primary: DatasetOutcome,
    replication: DatasetOutcome,
    closeout: ConfirmatoryCloseout,
) -> ResultStoreManifest:
    """Publish both outcomes atomically; existing stores are never overwritten."""

    destination = Path(output_dir)
    if destination.exists() or destination.is_symlink():
        raise ConfirmatoryExecutionError("confirmatory result store already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        artifacts: tuple[tuple[str, BaseModel], ...] = (
            ("registration.json", registration),
            ("environment.json", environment),
            ("primary-outcome.json", primary),
            ("replication-outcome.json", replication),
            ("closeout.json", closeout),
        )
        entries: list[StoreEntry] = []
        for relative_path, model in artifacts:
            content = _json_bytes(model)
            _write_exclusive(stage / relative_path, content)
            entries.append(
                StoreEntry(
                    relative_path=relative_path,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
        entries.sort(key=lambda item: item.relative_path)
        store_sha256 = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": closeout.protocol_sha256,
                "closeout_sha256": closeout.canonical_sha256(),
                "entries": [item.model_dump(mode="json") for item in entries],
            }
        )
        manifest = ResultStoreManifest(
            protocol_sha256=closeout.protocol_sha256,
            closeout_sha256=closeout.canonical_sha256(),
            entries=tuple(entries),
            store_sha256=store_sha256,
        )
        _write_exclusive(stage / "store-manifest.json", _json_bytes(manifest))
        os.replace(stage, destination)
        return manifest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def load_and_verify_result_store(path: str | Path) -> ResultStoreManifest:
    """Re-hash every published byte and reconstruct all cross-artifact contracts."""

    root = Path(path)
    try:
        manifest = ResultStoreManifest.model_validate_json(
            (root / "store-manifest.json").read_text(encoding="utf-8")
        )
        registration = ProtocolRegistrationReceipt.model_validate_json(
            (root / "registration.json").read_text(encoding="utf-8")
        )
        environment = ExecutionEnvironmentReceipt.model_validate_json(
            (root / "environment.json").read_text(encoding="utf-8")
        )
        primary = DatasetOutcome.model_validate_json(
            (root / "primary-outcome.json").read_text(encoding="utf-8")
        )
        replication = DatasetOutcome.model_validate_json(
            (root / "replication-outcome.json").read_text(encoding="utf-8")
        )
        closeout = ConfirmatoryCloseout.model_validate_json(
            (root / "closeout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ConfirmatoryExecutionError("cannot load confirmatory result store") from exc
    for entry in manifest.entries:
        artifact = root / entry.relative_path
        try:
            content = artifact.read_bytes()
        except OSError as exc:
            raise ConfirmatoryExecutionError("confirmatory result artifact is missing") from exc
        if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
            raise ConfirmatoryExecutionError("confirmatory result artifact checksum mismatch")
    if (
        registration.canonical_sha256() != closeout.registration_sha256
        or environment.canonical_sha256() != closeout.environment_sha256
        or primary.canonical_sha256() != closeout.primary_outcome_sha256
        or replication.canonical_sha256() != closeout.replication_outcome_sha256
        or closeout.canonical_sha256() != manifest.closeout_sha256
    ):
        raise ConfirmatoryExecutionError("confirmatory closeout bindings do not reconcile")
    return manifest
