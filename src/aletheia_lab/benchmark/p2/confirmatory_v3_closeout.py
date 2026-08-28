"""Cross-dataset closeout and immutable result storage for the v3 study."""

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
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import V3DatasetOutcome
from aletheia_lab.benchmark.p2.confirmatory_v3_inference import (
    DatasetInference,
    V3StudyDecision,
    analyze_dataset,
    decide_study,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import V3ConfirmatoryProtocol
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PROTOCOL_SHA256,
    V3RuntimeError,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import holm_adjust_all
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.filesystem import publish_staged_directory

REGISTRATION_SCHEMA_VERSION: Final[Literal["p2-v3-registration/1"]] = (
    "p2-v3-registration/1"
)
ENVIRONMENT_SCHEMA_VERSION: Final[Literal["p2-v3-environment/1"]] = (
    "p2-v3-environment/1"
)
CLOSEOUT_SCHEMA_VERSION: Final[Literal["p2-v3-closeout/1"]] = "p2-v3-closeout/1"
STORE_SCHEMA_VERSION: Final[Literal["p2-v3-result-store/1"]] = "p2-v3-result-store/1"
REQUIRED_TAG: Final[Literal["p2-label-noise-shift-factorial-v3.1"]] = (
    "p2-label-noise-shift-factorial-v3.1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_RELEASE_URL = re.compile(
    r"^https://github\.com/umynameislove/aletheia-lab/releases/tag/"
    r"p2-label-noise-shift-factorial-v3\.1$"
)


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class V3ProtocolRegistrationReceipt(_StrictFrozenModel):
    """Immutable public registration that must predate the sealed execution."""

    schema_version: Literal["p2-v3-registration/1"] = REGISTRATION_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    tag_name: Literal["p2-label-noise-shift-factorial-v3.1"] = REQUIRED_TAG
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
    def _release_is_canonical(cls, value: str) -> str:
        if _RELEASE_URL.fullmatch(value) is None:
            raise ValueError("registration must use the canonical repository release")
        return value

    @field_validator("release_created_at", "release_published_at")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("registration timestamps must include timezone evidence")
        return value

    @model_validator(mode="after")
    def _timeline_is_valid(self) -> V3ProtocolRegistrationReceipt:
        if self.release_published_at < self.release_created_at:
            raise ValueError("release publication cannot predate creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def registration_from_github_release(
    *,
    protocol: V3ConfirmatoryProtocol,
    tagged_protocol_commit: str,
    payload: object,
) -> V3ProtocolRegistrationReceipt:
    """Validate only the GitHub fields that establish immutable registration."""

    if protocol.canonical_sha256() != PROTOCOL_SHA256 or not isinstance(payload, dict):
        _fail("registration requires the immutable protocol and a GitHub release object")
    required = (
        "tag_name",
        "id",
        "html_url",
        "created_at",
        "published_at",
        "immutable",
        "draft",
        "prerelease",
    )
    if any(name not in payload for name in required):
        _fail("GitHub release response is incomplete")
    release_id = payload["id"]
    created_at = payload["created_at"]
    published_at = payload["published_at"]
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        _fail("GitHub release id must be an integer")
    if not isinstance(created_at, str) or not isinstance(published_at, str):
        _fail("GitHub release timestamps must be strings")
    try:
        return V3ProtocolRegistrationReceipt(
            tagged_protocol_commit=tagged_protocol_commit,
            tag_name=payload["tag_name"],
            release_url=payload["html_url"],
            release_id=release_id,
            release_created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
            release_published_at=datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            ),
            immutable=payload["immutable"],
            draft=payload["draft"],
            prerelease=payload["prerelease"],
        )
    except (TypeError, ValueError) as exc:
        raise V3RuntimeError(
            "GitHub release does not satisfy immutable preregistration"
        ) from exc


class V3ExecutionEnvironmentReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-v3-environment/1"] = ENVIRONMENT_SCHEMA_VERSION
    execution_commit: GitCommit
    python_version: str = Field(min_length=1)
    python_implementation: str = Field(min_length=1)
    operating_system: str = Field(min_length=1)
    machine: str = Field(min_length=1)
    package_versions: dict[str, str]

    @model_validator(mode="after")
    def _package_census_is_complete(self) -> V3ExecutionEnvironmentReceipt:
        required = {"numpy", "pandas", "pydantic", "scikit-learn", "scipy"}
        if set(self.package_versions) != required or any(
            not value for value in self.package_versions.values()
        ):
            raise ValueError("execution package census is incomplete")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def capture_execution_environment(execution_commit: str) -> V3ExecutionEnvironmentReceipt:
    packages: dict[str, str] = {}
    for name in ("numpy", "pandas", "pydantic", "scikit-learn", "scipy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError as exc:
            raise V3RuntimeError(f"required execution package is unavailable: {name}") from exc
    return V3ExecutionEnvironmentReceipt(
        execution_commit=execution_commit,
        python_version=python_version(),
        python_implementation=python_implementation(),
        operating_system=platform(),
        machine=machine(),
        package_versions=packages,
    )


class V3AssumptionFamily(_StrictFrozenModel):
    """One prospectively frozen class-by-dataset MMD family."""

    odds_multiplier: float
    raw_p_values: dict[str, float]
    holm_adjusted_p_values: dict[str, float]
    assumptions_pass: bool

    @model_validator(mode="after")
    def _decision_is_derived(self) -> V3AssumptionFamily:
        if set(self.raw_p_values) != set(self.holm_adjusted_p_values):
            raise ValueError("MMD raw and adjusted hypothesis families differ")
        expected = holm_adjust_all(self.raw_p_values)
        if any(
            abs(expected[key] - self.holm_adjusted_p_values[key]) > 1e-15
            for key in expected
        ):
            raise ValueError("MMD Holm adjustment is not reproducible")
        if self.assumptions_pass != all(value >= 0.05 for value in expected.values()):
            raise ValueError("MMD assumption disposition is not derived")
        return self


def build_assumption_families(
    primary: V3DatasetOutcome,
    replication: V3DatasetOutcome,
) -> tuple[V3AssumptionFamily, ...]:
    """Correct MMD p-values across classes and datasets, separately by environment."""

    if primary.dataset_role != "primary" or replication.dataset_role != "external_replication":
        _fail("assumption closeout requires primary then external replication")
    families: list[V3AssumptionFamily] = []
    for index, multiplier in enumerate((0.25, 1.0, 4.0)):
        raw: dict[str, float] = {}
        for outcome in (primary, replication):
            diagnostic = outcome.mmd_diagnostics[index]
            if diagnostic.dataset_id != outcome.dataset_id:
                _fail("MMD diagnostic provenance differs from its dataset outcome")
            for item in diagnostic.classes:
                raw[f"{outcome.dataset_id}/class={item.class_label}"] = (
                    item.permutation_p_value
                )
        adjusted = holm_adjust_all(raw)
        families.append(
            V3AssumptionFamily(
                odds_multiplier=multiplier,
                raw_p_values=raw,
                holm_adjusted_p_values=adjusted,
                assumptions_pass=all(value >= 0.05 for value in adjusted.values()),
            )
        )
    return tuple(families)


def _dataset_inference(
    *,
    protocol: V3ConfirmatoryProtocol,
    outcome: V3DatasetOutcome,
    assumptions_pass: bool,
) -> DatasetInference:
    admissions = sum(item.label_noise_admission for item in outcome.prior_only_controls)
    return analyze_dataset(
        protocol=protocol,
        dataset_id=outcome.dataset_id,
        dataset_role=outcome.dataset_role,
        split_membership_sha256=outcome.split_membership_sha256,
        seed_effects=outcome.seed_effects,
        prior_only_admissions={"yes_to_no": admissions, "no_to_yes": admissions},
        assumptions_pass={
            "yes_to_no": assumptions_pass,
            "no_to_yes": assumptions_pass,
        },
    )


class V3ConfirmatoryCloseout(_StrictFrozenModel):
    schema_version: Literal["p2-v3-closeout/1"] = CLOSEOUT_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    registration_sha256: Sha256
    execution_commit: GitCommit
    executed_at: datetime
    environment_sha256: Sha256
    primary_outcome_sha256: Sha256
    replication_outcome_sha256: Sha256
    primary_inference: DatasetInference
    replication_inference: DatasetInference
    assumption_families: tuple[V3AssumptionFamily, ...]
    decision: V3StudyDecision
    outcomes_released_together: Literal[True] = True

    @field_validator("executed_at")
    @classmethod
    def _execution_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("execution timestamp must include timezone evidence")
        return value

    @model_validator(mode="after")
    def _bindings_reconcile(self) -> V3ConfirmatoryCloseout:
        if tuple(item.odds_multiplier for item in self.assumption_families) != (
            0.25,
            1.0,
            4.0,
        ):
            raise ValueError("assumption families are incomplete")
        if self.decision.primary_inference_sha256 != self.primary_inference.canonical_sha256():
            raise ValueError("decision does not bind primary inference")
        if (
            self.decision.replication_inference_sha256
            != self.replication_inference.canonical_sha256()
        ):
            raise ValueError("decision does not bind replication inference")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_closeout(
    *,
    protocol: V3ConfirmatoryProtocol,
    registration: V3ProtocolRegistrationReceipt,
    environment: V3ExecutionEnvironmentReceipt,
    execution_commit: str,
    primary: V3DatasetOutcome,
    replication: V3DatasetOutcome,
    executed_at: datetime | None = None,
) -> V3ConfirmatoryCloseout:
    """Derive the first and only scientific decision after both outcomes exist."""

    if protocol.canonical_sha256() != registration.protocol_sha256:
        _fail("registration does not bind the immutable protocol")
    if environment.execution_commit != execution_commit:
        _fail("environment does not bind the execution commit")
    timestamp = executed_at or datetime.now(UTC)
    if timestamp <= registration.release_published_at:
        _fail("sealed execution must occur after public immutable registration")
    if (
        primary.execution_mode != "registered_execution"
        or replication.execution_mode != "registered_execution"
    ):
        _fail("confirmatory closeout rejects synthetic outcomes")
    families = build_assumption_families(primary, replication)
    assumptions_pass = all(item.assumptions_pass for item in families)
    primary_inference = _dataset_inference(
        protocol=protocol,
        outcome=primary,
        assumptions_pass=assumptions_pass,
    )
    replication_inference = _dataset_inference(
        protocol=protocol,
        outcome=replication,
        assumptions_pass=assumptions_pass,
    )
    decision = decide_study(
        protocol=protocol,
        primary=primary_inference,
        replication=replication_inference,
    )
    return V3ConfirmatoryCloseout(
        registration_sha256=registration.canonical_sha256(),
        execution_commit=execution_commit,
        executed_at=timestamp,
        environment_sha256=environment.canonical_sha256(),
        primary_outcome_sha256=primary.canonical_sha256(),
        replication_outcome_sha256=replication.canonical_sha256(),
        primary_inference=primary_inference,
        replication_inference=replication_inference,
        assumption_families=families,
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


class V3ResultStoreManifest(_StrictFrozenModel):
    schema_version: Literal["p2-v3-result-store/1"] = STORE_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    closeout_sha256: Sha256
    entries: tuple[StoreEntry, ...]
    store_sha256: Sha256

    @model_validator(mode="after")
    def _root_is_derived(self) -> V3ResultStoreManifest:
        required = {
            "registration.json",
            "environment.json",
            "primary-outcome.json",
            "replication-outcome.json",
            "closeout.json",
        }
        paths = tuple(item.relative_path for item in self.entries)
        if set(paths) != required or paths != tuple(sorted(paths)):
            raise ValueError("result store artifact census is incomplete")
        expected = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": self.protocol_sha256,
                "closeout_sha256": self.closeout_sha256,
                "entries": [item.model_dump(mode="json") for item in self.entries],
            }
        )
        if self.store_sha256 != expected:
            raise ValueError("result store root does not bind its entries")
        return self


def _json_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
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
        raise V3RuntimeError(f"cannot write immutable result artifact: {path}") from exc


def write_result_store(
    *,
    output_dir: str | Path,
    registration: V3ProtocolRegistrationReceipt,
    environment: V3ExecutionEnvironmentReceipt,
    primary: V3DatasetOutcome,
    replication: V3DatasetOutcome,
    closeout: V3ConfirmatoryCloseout,
) -> V3ResultStoreManifest:
    """Publish both dataset outcomes atomically without an overwrite path."""

    destination = Path(output_dir)
    if destination.exists() or destination.is_symlink():
        _fail("v3 confirmatory result store already exists")
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
        root = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": PROTOCOL_SHA256,
                "closeout_sha256": closeout.canonical_sha256(),
                "entries": [item.model_dump(mode="json") for item in entries],
            }
        )
        manifest = V3ResultStoreManifest(
            closeout_sha256=closeout.canonical_sha256(),
            entries=tuple(entries),
            store_sha256=root,
        )
        _write_exclusive(stage / "store-manifest.json", _json_bytes(manifest))
        publish_staged_directory(stage, destination)
        return manifest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def load_and_verify_result_store(path: str | Path) -> V3ResultStoreManifest:
    """Re-hash every byte and reconstruct all scientific cross-bindings."""

    root = Path(path)
    try:
        manifest = V3ResultStoreManifest.model_validate_json(
            (root / "store-manifest.json").read_text(encoding="utf-8")
        )
        registration = V3ProtocolRegistrationReceipt.model_validate_json(
            (root / "registration.json").read_text(encoding="utf-8")
        )
        environment = V3ExecutionEnvironmentReceipt.model_validate_json(
            (root / "environment.json").read_text(encoding="utf-8")
        )
        primary = V3DatasetOutcome.model_validate_json(
            (root / "primary-outcome.json").read_text(encoding="utf-8")
        )
        replication = V3DatasetOutcome.model_validate_json(
            (root / "replication-outcome.json").read_text(encoding="utf-8")
        )
        closeout = V3ConfirmatoryCloseout.model_validate_json(
            (root / "closeout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("cannot load v3 confirmatory result store") from exc
    for entry in manifest.entries:
        try:
            content = (root / entry.relative_path).read_bytes()
        except OSError as exc:
            raise V3RuntimeError("v3 result artifact is missing") from exc
        if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail("v3 result artifact checksum mismatch")
    if (
        registration.canonical_sha256() != closeout.registration_sha256
        or environment.canonical_sha256() != closeout.environment_sha256
        or primary.canonical_sha256() != closeout.primary_outcome_sha256
        or replication.canonical_sha256() != closeout.replication_outcome_sha256
        or closeout.canonical_sha256() != manifest.closeout_sha256
    ):
        _fail("v3 closeout bindings do not reconcile")
    return manifest
