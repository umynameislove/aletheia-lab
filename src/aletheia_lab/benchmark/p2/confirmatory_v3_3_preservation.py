"""Content-addressed preservation and compact publication facts for v3.3."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_3_closeout import (
    V33ConfirmatoryCloseout,
    V33ProtocolRegistrationReceipt,
    load_and_verify_terminal_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.filesystem import publish_staged_directory

PRESERVATION_SCHEMA_VERSION: Final[Literal["p2-v3-3-preservation-receipt/1"]] = (
    "p2-v3-3-preservation-receipt/1"
)
PUBLICATION_SUMMARY_SCHEMA_VERSION: Final[Literal["p2-v3-3-publication-summary/1"]] = (
    "p2-v3-3-publication-summary/1"
)
STUDY_ID: Final[Literal["p2-label-noise-shift-factorial-v3.3"]] = (
    "p2-label-noise-shift-factorial-v3.3"
)
PROTOCOL_SHA256: Final[str] = "5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456"
TERMINAL_STORE_SHA256: Final[str] = (
    "d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152"
)
TERMINAL_ARTIFACT_SHA256: Final[str] = (
    "9b8d87cbd3e52dc5c6da50066c6816d5620457a3d8ac8094fafc8136560339c4"
)
DEFAULT_TERMINAL_STORE_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.3"
)
DEFAULT_PREFLIGHT_REGISTRATION_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.3-registration.json"
)
DEFAULT_SEALED_OPEN_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.3-sealed-open.json"
)
DEFAULT_PRESERVATION_ROOT = Path("../preserved-artifacts")
DEFAULT_PUBLICATION_SUMMARY_PATH = Path(
    "configs/benchmark/provenance/p2_label_noise_shift_v3_3_publication_summary.json"
)
_CONTENT_ADDRESS = f"sha256-{TERMINAL_STORE_SHA256}"
_STORE_FILES: Final[tuple[str, ...]] = (
    "closeout.json",
    "environment.json",
    "primary-attempt.json",
    "registration.json",
    "replication-attempt.json",
    "store-manifest.json",
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class V33SealedOpenReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-v3-3-sealed-open/1"]
    protocol_sha256: Sha256
    registration_sha256: Sha256
    execution_commit: GitCommit
    opened_at: datetime
    rerun_forbidden: Literal[True]

    @field_validator("opened_at")
    @classmethod
    def _opened_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("sealed-open timestamp requires timezone evidence")
        return value


class V33PreservationReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-v3-3-preservation-receipt/1"] = PRESERVATION_SCHEMA_VERSION
    preserved_at: datetime
    study_id: Literal["p2-label-noise-shift-factorial-v3.3"] = STUDY_ID
    content_address: str
    result_store_relative_path: Literal["result-store"]
    preflight_registration_relative_path: Literal["preflight-registration.json"]
    sealed_open_relative_path: Literal["sealed-open-receipt.json"]
    protocol_sha256: Sha256
    terminal_store_sha256: Sha256
    terminal_artifact_sha256: Sha256
    store_manifest_file_sha256: Sha256
    preflight_registration_file_sha256: Sha256
    sealed_open_file_sha256: Sha256
    artifact_count: int = Field(gt=0)
    artifact_bytes: int = Field(gt=0)
    result_store_bytes_with_manifest: int = Field(gt=0)
    copy_is_byte_identical: Literal[True]
    original_result_store_modified: Literal[False]
    read_only_after_verification: Literal[True]

    @field_validator("preserved_at")
    @classmethod
    def _preserved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("preservation timestamp requires timezone evidence")
        return value

    @model_validator(mode="after")
    def _identity_is_content_addressed(self) -> V33PreservationReceipt:
        if (
            self.content_address != _CONTENT_ADDRESS
            or self.protocol_sha256 != PROTOCOL_SHA256
            or self.terminal_store_sha256 != TERMINAL_STORE_SHA256
            or self.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
        ):
            raise ValueError("preservation receipt is bound to another terminal study")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class AssumptionEnvironmentSummary(_StrictFrozenModel):
    odds_multiplier: float = Field(gt=0)
    assumptions_pass: bool


class V33PublicationSummary(_StrictFrozenModel):
    """Compact tracked facts; never a replacement for the preserved terminal store."""

    schema_version: Literal["p2-v3-3-publication-summary/1"] = (
        PUBLICATION_SUMMARY_SCHEMA_VERSION
    )
    study_id: Literal["p2-label-noise-shift-factorial-v3.3"] = STUDY_ID
    protocol_sha256: Sha256
    registration_sha256: Sha256
    execution_commit: GitCommit
    terminal_store_sha256: Sha256
    terminal_artifact_sha256: Sha256
    disposition: Literal["abstain"]
    cross_dataset_claim_allowed: Literal[False]
    outcomes_released_together: Literal[True]
    datasets: tuple[
        Literal[
            "uci_default_of_credit_card_clients",
            "uci_online_shoppers_purchasing_intention",
        ],
        ...,
    ]
    assumption_environments: tuple[AssumptionEnvironmentSummary, ...]
    directional_holm_adjusted_p_values: dict[Literal["no_to_yes", "yes_to_no"], float]
    scientific_interpretation: Literal[
        "strong_registered_directional_signal_but_extreme_prior_assumptions_failed"
    ]
    claim_boundary: Literal[
        "assumption_limited_not_admitted_no_cross_dataset_generalization"
    ]
    rerun_forbidden: Literal[True]
    external_content_address: str
    large_artifacts_excluded_from_git: Literal[True]

    @model_validator(mode="after")
    def _summary_is_bounded(self) -> V33PublicationSummary:
        expected_environments = ((0.25, False), (1.0, True), (4.0, False))
        observed = tuple(
            (item.odds_multiplier, item.assumptions_pass)
            for item in self.assumption_environments
        )
        if (
            self.protocol_sha256 != PROTOCOL_SHA256
            or self.terminal_store_sha256 != TERMINAL_STORE_SHA256
            or self.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
            or self.external_content_address != _CONTENT_ADDRESS
            or observed != expected_environments
        ):
            raise ValueError("publication summary overstates or rebinds v3.3 evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise V3RuntimeError(f"preservation requires a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3RuntimeError(f"cannot hash preservation artifact: {path}") from exc
    return digest.hexdigest()


def _load_registration(path: Path) -> V33ProtocolRegistrationReceipt:
    try:
        return V33ProtocolRegistrationReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.3 preflight registration is invalid") from exc


def _load_marker(path: Path) -> V33SealedOpenReceipt:
    try:
        return V33SealedOpenReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.3 sealed-open receipt is invalid") from exc


def _resolve(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def _exact_store_files(store: Path) -> tuple[Path, ...]:
    if store.is_symlink() or not store.is_dir():
        raise V3RuntimeError("v3.3 terminal store must be a regular directory")
    try:
        observed = tuple(sorted(path.name for path in store.iterdir()))
    except OSError as exc:
        raise V3RuntimeError("cannot inspect v3.3 terminal store") from exc
    if observed != _STORE_FILES:
        raise V3RuntimeError("v3.3 terminal store has a missing or unexpected artifact")
    files = tuple(store / name for name in _STORE_FILES)
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise V3RuntimeError("v3.3 terminal store contains a non-regular artifact")
    return files


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
    ).encode()


def _write_exclusive(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise V3RuntimeError(f"cannot create immutable preservation artifact: {path}") from exc


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_writable_for_cleanup(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _require_read_only_tree(root: Path) -> None:
    paths = (root, *root.rglob("*"))
    if any(path.stat().st_mode & 0o222 for path in paths):
        raise V3RuntimeError("v3.3 preservation tree must remain read-only")


def preservation_destination(preservation_root: str | Path) -> Path:
    return Path(preservation_root) / STUDY_ID / _CONTENT_ADDRESS


def verify_preserved_v3_3(path: str | Path) -> V33PreservationReceipt:
    """Verify every preserved byte, receipt binding, census, and terminal cross-link."""

    root = Path(path)
    expected_root_entries = (
        "preflight-registration.json",
        "preservation-receipt.json",
        "result-store",
        "sealed-open-receipt.json",
    )
    if root.is_symlink() or not root.is_dir():
        raise V3RuntimeError("v3.3 preservation directory is unavailable")
    try:
        observed_root_entries = tuple(sorted(item.name for item in root.iterdir()))
        receipt = V33PreservationReceipt.model_validate_json(
            (root / "preservation-receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.3 preservation receipt is unavailable or invalid") from exc
    if observed_root_entries != expected_root_entries:
        raise V3RuntimeError("v3.3 preservation directory has unexpected content")
    store = root / receipt.result_store_relative_path
    files = _exact_store_files(store)
    manifest = load_and_verify_terminal_store(store)
    registration_path = root / receipt.preflight_registration_relative_path
    marker_path = root / receipt.sealed_open_relative_path
    registration = _load_registration(registration_path)
    marker = _load_marker(marker_path)
    store_registration = _load_registration(store / "registration.json")
    if (
        manifest.store_sha256 != receipt.terminal_store_sha256
        or manifest.terminal_artifact_sha256 != receipt.terminal_artifact_sha256
        or registration.canonical_sha256() != store_registration.canonical_sha256()
        or marker.protocol_sha256 != receipt.protocol_sha256
        or marker.registration_sha256 != registration.canonical_sha256()
        or marker.rerun_forbidden is not True
        or _file_sha256(store / "store-manifest.json") != receipt.store_manifest_file_sha256
        or _file_sha256(registration_path) != receipt.preflight_registration_file_sha256
        or _file_sha256(marker_path) != receipt.sealed_open_file_sha256
        or receipt.artifact_count != len(manifest.entries)
        or receipt.artifact_bytes != sum(item.size_bytes for item in manifest.entries)
        or receipt.result_store_bytes_with_manifest != sum(path.stat().st_size for path in files)
    ):
        raise V3RuntimeError("v3.3 preserved evidence does not reconcile with its receipt")
    _require_read_only_tree(root)
    return receipt


def preserve_v3_3_evidence(
    *,
    root: str | Path,
    preservation_root: str | Path = DEFAULT_PRESERVATION_ROOT,
    terminal_store_path: str | Path = DEFAULT_TERMINAL_STORE_PATH,
    preflight_registration_path: str | Path = DEFAULT_PREFLIGHT_REGISTRATION_PATH,
    sealed_open_path: str | Path = DEFAULT_SEALED_OPEN_PATH,
    preserved_at: datetime | None = None,
) -> V33PreservationReceipt:
    """Copy frozen evidence once without deleting, rewriting, or rerunning the source study."""

    base = Path(root).resolve()
    store = _resolve(base, terminal_store_path)
    preflight = _resolve(base, preflight_registration_path)
    marker_path = _resolve(base, sealed_open_path)
    archive_root = _resolve(base, preservation_root)
    destination = preservation_destination(archive_root)
    if destination.exists() or destination.is_symlink():
        return verify_preserved_v3_3(destination)
    source_files = _exact_store_files(store)
    manifest = load_and_verify_terminal_store(store)
    registration = _load_registration(preflight)
    marker = _load_marker(marker_path)
    store_registration = _load_registration(store / "registration.json")
    if (
        manifest.store_sha256 != TERMINAL_STORE_SHA256
        or manifest.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
        or registration.canonical_sha256() != store_registration.canonical_sha256()
        or marker.protocol_sha256 != PROTOCOL_SHA256
        or marker.registration_sha256 != registration.canonical_sha256()
        or marker.rerun_forbidden is not True
    ):
        raise V3RuntimeError("source v3.3 evidence does not match the registered terminal study")
    source_hashes = {path.name: _file_sha256(path) for path in source_files}
    source_hashes["preflight-registration.json"] = _file_sha256(preflight)
    source_hashes["sealed-open-receipt.json"] = _file_sha256(marker_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{_CONTENT_ADDRESS}-", dir=destination.parent))
    try:
        staged_store = stage / "result-store"
        staged_store.mkdir()
        for source in source_files:
            shutil.copyfile(source, staged_store / source.name)
        shutil.copyfile(preflight, stage / "preflight-registration.json")
        shutil.copyfile(marker_path, stage / "sealed-open-receipt.json")
        receipt = V33PreservationReceipt(
            preserved_at=preserved_at or datetime.now(UTC),
            content_address=_CONTENT_ADDRESS,
            result_store_relative_path="result-store",
            preflight_registration_relative_path="preflight-registration.json",
            sealed_open_relative_path="sealed-open-receipt.json",
            protocol_sha256=PROTOCOL_SHA256,
            terminal_store_sha256=manifest.store_sha256,
            terminal_artifact_sha256=manifest.terminal_artifact_sha256,
            store_manifest_file_sha256=source_hashes["store-manifest.json"],
            preflight_registration_file_sha256=source_hashes["preflight-registration.json"],
            sealed_open_file_sha256=source_hashes["sealed-open-receipt.json"],
            artifact_count=len(manifest.entries),
            artifact_bytes=sum(item.size_bytes for item in manifest.entries),
            result_store_bytes_with_manifest=sum(path.stat().st_size for path in source_files),
            copy_is_byte_identical=True,
            original_result_store_modified=False,
            read_only_after_verification=True,
        )
        _write_exclusive(stage / "preservation-receipt.json", _json_bytes(receipt))
        _make_read_only(stage)
        verify_preserved_v3_3(stage)
        observed_source_hashes = {path.name: _file_sha256(path) for path in source_files}
        observed_source_hashes["preflight-registration.json"] = _file_sha256(preflight)
        observed_source_hashes["sealed-open-receipt.json"] = _file_sha256(marker_path)
        if observed_source_hashes != source_hashes:
            raise V3RuntimeError("source v3.3 evidence changed during preservation")
        publish_staged_directory(stage, destination)
        return verify_preserved_v3_3(destination)
    except BaseException:
        if stage.exists():
            _make_writable_for_cleanup(stage)
            shutil.rmtree(stage)
        raise


def load_v3_3_publication_summary(
    path: str | Path = DEFAULT_PUBLICATION_SUMMARY_PATH,
) -> V33PublicationSummary:
    try:
        return V33PublicationSummary.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.3 publication summary is unavailable or invalid") from exc


def verify_v3_3_publication_summary(
    summary: V33PublicationSummary,
    *,
    terminal_store_path: str | Path,
) -> V33PublicationSummary:
    """Reproduce every compact fact from the frozen terminal store."""

    checked = V33PublicationSummary.model_validate(summary.model_dump())
    store = Path(terminal_store_path)
    manifest = load_and_verify_terminal_store(store)
    try:
        closeout = V33ConfirmatoryCloseout.model_validate_json(
            (store / "closeout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.3 closeout is unavailable or invalid") from exc
    if closeout.decision is None:
        raise V3RuntimeError("v3.3 publication summary requires complete scientific inference")
    observed_environments = tuple(
        AssumptionEnvironmentSummary(
            odds_multiplier=family.odds_multiplier,
            assumptions_pass=family.assumptions_pass,
        )
        for family in closeout.assumption_families
    )
    observed = (
        closeout.protocol_sha256,
        closeout.registration_sha256,
        closeout.execution_commit,
        manifest.store_sha256,
        manifest.terminal_artifact_sha256,
        closeout.disposition,
        closeout.cross_dataset_claim_allowed,
        closeout.outcomes_released_together,
        (closeout.primary_inference.dataset_id, closeout.replication_inference.dataset_id)
        if closeout.primary_inference is not None and closeout.replication_inference is not None
        else (),
        observed_environments,
        closeout.decision.holm_adjusted_p_values,
    )
    expected = (
        checked.protocol_sha256,
        checked.registration_sha256,
        checked.execution_commit,
        checked.terminal_store_sha256,
        checked.terminal_artifact_sha256,
        checked.disposition,
        checked.cross_dataset_claim_allowed,
        checked.outcomes_released_together,
        checked.datasets,
        checked.assumption_environments,
        checked.directional_holm_adjusted_p_values,
    )
    if observed != expected:
        raise V3RuntimeError("v3.3 publication summary does not reproduce from terminal evidence")
    return checked
