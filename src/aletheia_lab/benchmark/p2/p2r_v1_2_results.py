"""Preserve and publish bounded facts from the completed P2R v1.2 study."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.instrument_validity import (
    InstrumentValidityAudit,
    ManipulationObservation,
)
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RJointCloseout,
    P2RTerminalStore,
    load_and_verify_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_execution import P2RV12Registration
from aletheia_lab.filesystem import publish_staged_directory

PUBLICATION_SUMMARY_SCHEMA_VERSION: Final[Literal["p2r-v1-2-publication-summary/1"]] = (
    "p2r-v1-2-publication-summary/1"
)
PRESERVATION_RECEIPT_SCHEMA_VERSION: Final[Literal["p2r-v1-2-preservation-receipt/1"]] = (
    "p2r-v1-2-preservation-receipt/1"
)
STUDY_ID: Final[Literal["p2r-confirmatory-v1.2"]] = "p2r-confirmatory-v1.2"
EXECUTION_COMMIT: Final[str] = "4765f5811f9e7cc025db0bebf65b4fdc602d0c3a"
TERMINAL_STORE_SHA256: Final[str] = (
    "7b920ef15cc5683965652a3dc02cef06bf514d8c85e1954c3094c1c441919956"
)
TERMINAL_ARTIFACT_SHA256: Final[str] = (
    "da6f8a5d49b2b9352dbaacdc1803aa5c9ae94a7225f547b1ca5ce38a155199ce"
)
SEALED_MARKER_SHA256: Final[str] = (
    "c8c16f56146031d136e6256359e34a1a555acdfacc96ed0f6340bad4656e5638"
)
DEFAULT_TERMINAL_STORE_PATH = Path("experiments/p2/outputs/p2r-confirmatory-v1-2")
DEFAULT_PUBLICATION_SUMMARY_PATH = Path(
    "configs/benchmark/provenance/p2r_v1_2_publication_summary.json"
)
DEFAULT_PRESERVATION_ROOT = Path("../preserved-artifacts")
_CONTENT_ADDRESS: Final[str] = f"sha256-{TERMINAL_STORE_SHA256}"
_STORE_FILES: Final[tuple[str, ...]] = (
    "closeout.json",
    "environment.json",
    "instrument-audit.json",
    "manifest.json",
    "measurements.json",
    "paired-observations.json",
    "registrations.json",
    "sealed-open.json",
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MechanismName = Literal["data_drift", "preprocessing_bug"]
DatasetId = Literal[
    "uci_default_of_credit_card_clients",
    "uci_online_shoppers_purchasing_intention",
]
DatasetRole = Literal["primary", "external_replication"]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RV12DatasetResultSummary(_StrictFrozenModel):
    """Compact dataset-level decision facts reproduced from the terminal closeout."""

    dataset_id: DatasetId
    dataset_role: DatasetRole
    n_seeds: Literal[5]
    median_target_effect: float
    median_nuisance_effect: float
    expected_direction_fraction: float = Field(ge=0.0, le=1.0)
    manipulation_fidelity_pass: bool
    target_effect_pass: bool
    direction_pass: bool
    dominance_pass: bool
    passed: bool
    decision_sha256: Sha256


class P2RV12MechanismResultSummary(_StrictFrozenModel):
    """One mechanism's complete registered disposition and instrument reasons."""

    mechanism: MechanismName
    amendment_protocol_sha256: Sha256
    execution_protocol_sha256: Sha256
    registration_sha256: Sha256
    mechanism_closeout_sha256: Sha256
    disposition: Literal["rejected"]
    admitted: Literal[False]
    paired_instrument_pass: Literal[False]
    cross_dataset_claim_allowed: Literal[False]
    declared_manipulation_magnitude: float
    achieved_manipulation_minimum: float
    achieved_manipulation_maximum: float
    minimum_practical_effect: float
    minimum_expected_direction_fraction: float
    instrument_reason_codes: tuple[
        Literal["mechanism_direction_unstable", "no_dominant_cause_candidate"], ...
    ]
    eligible_candidate_count: Literal[0]
    dataset_results: tuple[P2RV12DatasetResultSummary, P2RV12DatasetResultSummary]

    @model_validator(mode="after")
    def _mechanism_result_is_canonical(self) -> P2RV12MechanismResultSummary:
        if self.instrument_reason_codes != (
            "mechanism_direction_unstable",
            "no_dominant_cause_candidate",
        ):
            raise ValueError("P2R v1.2 reason census must remain complete and canonical")
        if tuple(item.dataset_role for item in self.dataset_results) != (
            "primary",
            "external_replication",
        ):
            raise ValueError("P2R v1.2 dataset roles must remain canonical")
        if any(item.passed for item in self.dataset_results):
            raise ValueError("rejected P2R mechanisms cannot contain a passing dataset result")
        if (
            self.declared_manipulation_magnitude,
            self.achieved_manipulation_minimum,
            self.achieved_manipulation_maximum,
            self.minimum_practical_effect,
            self.minimum_expected_direction_fraction,
        ) != (0.2, 0.2, 0.2, 0.01, 0.8):
            raise ValueError("P2R v1.2 registered thresholds and achieved bounds are immutable")
        return self


class P2RV12PublicationSummary(_StrictFrozenModel):
    """Small tracked summary; it never substitutes for the ignored terminal store."""

    schema_version: Literal["p2r-v1-2-publication-summary/1"] = PUBLICATION_SUMMARY_SCHEMA_VERSION
    study_id: Literal["p2r-confirmatory-v1.2"] = STUDY_ID
    execution_commit: GitCommit
    terminal_store_sha256: Sha256
    terminal_artifact_sha256: Sha256
    sealed_marker_sha256: Sha256
    measurement_census_sha256: Sha256
    paired_observation_census_sha256: Sha256
    instrument_audit_sha256: Sha256
    n_mechanisms: Literal[2]
    n_admitted: Literal[0]
    n_datasets: Literal[2]
    n_registered_seeds_per_mechanism_dataset: Literal[5]
    n_measurements: Literal[20]
    n_paired_observations: Literal[10]
    outcomes_released_together: Literal[True]
    rerun_forbidden: Literal[True]
    independent_new_dataset_replication: Literal[False]
    mechanisms: tuple[P2RV12MechanismResultSummary, P2RV12MechanismResultSummary]
    scientific_interpretation: Literal[
        "manipulation_fidelity_passed_but_target_direction_and_paired_instrument_gates_failed"
    ]
    claim_boundary: Literal[
        "both_mechanisms_rejected_zero_admitted_no_cross_dataset_or_diagnostic_ground_truth_claim"
    ]
    external_content_address: str
    terminal_store_excluded_from_git: Literal[True]

    @model_validator(mode="after")
    def _summary_is_bound_and_non_inflating(self) -> P2RV12PublicationSummary:
        if (
            self.execution_commit != EXECUTION_COMMIT
            or self.terminal_store_sha256 != TERMINAL_STORE_SHA256
            or self.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
            or self.sealed_marker_sha256 != SEALED_MARKER_SHA256
            or self.external_content_address != _CONTENT_ADDRESS
        ):
            raise ValueError("publication summary is bound to another P2R terminal study")
        if tuple(item.mechanism for item in self.mechanisms) != (
            "data_drift",
            "preprocessing_bug",
        ):
            raise ValueError("publication summary must contain both P2R mechanisms once")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class P2RV12PreservationReceipt(_StrictFrozenModel):
    """Byte-level receipt for the external content-addressed evidence copy."""

    schema_version: Literal["p2r-v1-2-preservation-receipt/1"] = PRESERVATION_RECEIPT_SCHEMA_VERSION
    preserved_at: datetime
    study_id: Literal["p2r-confirmatory-v1.2"] = STUDY_ID
    content_address: str
    result_store_relative_path: Literal["result-store"]
    terminal_store_sha256: Sha256
    terminal_artifact_sha256: Sha256
    manifest_file_sha256: Sha256
    artifact_count_with_manifest: Literal[8]
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
    def _receipt_is_content_addressed(self) -> P2RV12PreservationReceipt:
        if (
            self.content_address != _CONTENT_ADDRESS
            or self.terminal_store_sha256 != TERMINAL_STORE_SHA256
            or self.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
        ):
            raise ValueError("preservation receipt is bound to another P2R terminal study")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail(f"P2R preservation requires a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3RuntimeError(f"cannot hash P2R preservation artifact: {path}") from exc
    return digest.hexdigest()


def _exact_store_files(store: Path) -> tuple[Path, ...]:
    if store.is_symlink() or not store.is_dir():
        _fail("P2R v1.2 terminal store must be a regular directory")
    try:
        observed = tuple(sorted(path.name for path in store.iterdir()))
    except OSError as exc:
        raise V3RuntimeError("cannot inspect P2R v1.2 terminal store") from exc
    if observed != _STORE_FILES:
        _fail("P2R v1.2 terminal store has a missing or unexpected artifact")
    files = tuple(store / name for name in _STORE_FILES)
    if any(path.is_symlink() or not path.is_file() for path in files):
        _fail("P2R v1.2 terminal store contains a non-regular artifact")
    return files


def _load_closeout(store: Path) -> P2RJointCloseout:
    try:
        return P2RJointCloseout.model_validate_json(
            (store / "closeout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 closeout is unavailable or invalid") from exc


def _load_audit(store: Path) -> InstrumentValidityAudit:
    try:
        return InstrumentValidityAudit.model_validate_json(
            (store / "instrument-audit.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 instrument audit is unavailable or invalid") from exc


def _load_registrations(store: Path) -> tuple[P2RV12Registration, P2RV12Registration]:
    try:
        raw = json.loads((store / "registrations.json").read_text(encoding="utf-8"))
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("registration census must contain exactly two entries")
        parsed = tuple(P2RV12Registration.model_validate_json(json.dumps(item)) for item in raw)
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 registration census is unavailable or invalid") from exc
    return parsed  # type: ignore[return-value]


def _load_observations(store: Path) -> tuple[ManipulationObservation, ...]:
    try:
        raw = json.loads((store / "paired-observations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 observation census is unavailable or invalid") from exc
    if (
        not isinstance(raw, list)
        or len(raw) != 10
        or not all(isinstance(item, dict) for item in raw)
    ):
        _fail("P2R v1.2 observation census must contain ten objects")
    try:
        return tuple(ManipulationObservation.model_validate_json(json.dumps(item)) for item in raw)
    except ValueError as exc:
        raise V3RuntimeError("P2R v1.2 observation census is invalid") from exc


def _load_terminal_manifest(store: Path) -> P2RTerminalStore:
    try:
        return load_and_verify_terminal_store(store)
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 terminal store is unavailable or invalid") from exc


def load_p2r_v1_2_publication_summary(
    path: str | Path = DEFAULT_PUBLICATION_SUMMARY_PATH,
) -> P2RV12PublicationSummary:
    try:
        return P2RV12PublicationSummary.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 publication summary is unavailable or invalid") from exc


def verify_p2r_v1_2_publication_summary(
    summary: P2RV12PublicationSummary,
    *,
    terminal_store_path: str | Path = DEFAULT_TERMINAL_STORE_PATH,
) -> P2RV12PublicationSummary:
    """Reproduce every compact result from the frozen terminal evidence."""

    checked = P2RV12PublicationSummary.model_validate(summary.model_dump())
    store = Path(terminal_store_path)
    manifest = _load_terminal_manifest(store)
    closeout = _load_closeout(store)
    audit = _load_audit(store)
    registrations = _load_registrations(store)
    observations = _load_observations(store)
    if (
        manifest.store_sha256 != checked.terminal_store_sha256
        or manifest.terminal_artifact_sha256 != checked.terminal_artifact_sha256
        or closeout.execution_commit != checked.execution_commit
        or closeout.measurement_census_sha256 != checked.measurement_census_sha256
        or closeout.paired_observation_census_sha256 != checked.paired_observation_census_sha256
        or closeout.instrument_audit_sha256 != checked.instrument_audit_sha256
        or closeout.n_mechanisms != checked.n_mechanisms
        or closeout.n_admitted != checked.n_admitted
        or closeout.outcomes_released_together != checked.outcomes_released_together
        or closeout.rerun_forbidden != checked.rerun_forbidden
        or len(observations) != checked.n_paired_observations
    ):
        _fail("P2R v1.2 publication summary differs from terminal evidence")
    by_registration = {item.mechanism: item for item in registrations}
    by_audit = {item.fault_type: item for item in audit.mechanism_decisions}
    by_summary = {item.mechanism: item for item in checked.mechanisms}
    for mechanism_closeout in closeout.mechanism_closeouts:
        result = by_summary[mechanism_closeout.mechanism]
        registration = by_registration[mechanism_closeout.mechanism]
        decision = by_audit[mechanism_closeout.mechanism]
        observed_dataset_results = tuple(
            P2RV12DatasetResultSummary(
                dataset_id=cast(DatasetId, item.dataset_id),
                dataset_role=item.dataset_role,
                n_seeds=5,
                median_target_effect=item.median_target_effect,
                median_nuisance_effect=item.median_nuisance_effect,
                expected_direction_fraction=item.expected_direction_fraction,
                manipulation_fidelity_pass=item.manipulation_fidelity_pass,
                target_effect_pass=item.target_effect_pass,
                direction_pass=item.direction_pass,
                dominance_pass=item.dominance_pass,
                passed=item.passed,
                decision_sha256=item.decision_sha256,
            )
            for item in (
                mechanism_closeout.primary_decision,
                mechanism_closeout.replication_decision,
            )
        )
        mechanism_observations = tuple(
            item for item in observations if item.fault_type == mechanism_closeout.mechanism
        )
        achieved = tuple(item.achieved_manipulation_magnitude for item in mechanism_observations)
        observed = (
            registration.amendment_protocol_sha256,
            mechanism_closeout.protocol_sha256,
            mechanism_closeout.registration_sha256,
            mechanism_closeout.closeout_sha256,
            mechanism_closeout.disposition,
            mechanism_closeout.admitted,
            mechanism_closeout.paired_instrument_pass,
            mechanism_closeout.cross_dataset_claim_allowed,
            min(achieved),
            max(achieved),
            decision.reason_codes,
            len(decision.eligible_candidate_ids),
            observed_dataset_results,
        )
        expected = (
            result.amendment_protocol_sha256,
            result.execution_protocol_sha256,
            result.registration_sha256,
            result.mechanism_closeout_sha256,
            result.disposition,
            result.admitted,
            result.paired_instrument_pass,
            result.cross_dataset_claim_allowed,
            result.achieved_manipulation_minimum,
            result.achieved_manipulation_maximum,
            result.instrument_reason_codes,
            result.eligible_candidate_count,
            result.dataset_results,
        )
        if observed != expected:
            _fail(f"P2R v1.2 {mechanism_closeout.mechanism} summary does not reproduce")
    return checked


def preservation_destination(preservation_root: str | Path) -> Path:
    return Path(preservation_root) / STUDY_ID / _CONTENT_ADDRESS


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
        raise V3RuntimeError(f"cannot create P2R preservation artifact: {path}") from exc


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_writable_for_cleanup(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _require_read_only_tree(root: Path) -> None:
    if any(path.stat().st_mode & 0o222 for path in (root, *root.rglob("*"))):
        _fail("P2R v1.2 preservation tree must remain read-only")


def verify_preserved_p2r_v1_2(path: str | Path) -> P2RV12PreservationReceipt:
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        _fail("P2R v1.2 preservation directory is unavailable")
    try:
        entries = tuple(sorted(item.name for item in root.iterdir()))
        receipt = P2RV12PreservationReceipt.model_validate_json(
            (root / "preservation-receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("P2R v1.2 preservation receipt is unavailable or invalid") from exc
    if entries != ("preservation-receipt.json", "result-store"):
        _fail("P2R v1.2 preservation directory contains unexpected content")
    store = root / receipt.result_store_relative_path
    files = _exact_store_files(store)
    manifest = _load_terminal_manifest(store)
    if (
        manifest.store_sha256 != receipt.terminal_store_sha256
        or manifest.terminal_artifact_sha256 != receipt.terminal_artifact_sha256
        or _file_sha256(store / "manifest.json") != receipt.manifest_file_sha256
        or len(files) != receipt.artifact_count_with_manifest
        or sum(item.stat().st_size for item in files) != receipt.result_store_bytes_with_manifest
    ):
        _fail("P2R v1.2 preserved evidence does not reconcile with its receipt")
    _require_read_only_tree(root)
    return receipt


def preserve_p2r_v1_2_evidence(
    *,
    root: str | Path,
    preservation_root: str | Path = DEFAULT_PRESERVATION_ROOT,
    terminal_store_path: str | Path = DEFAULT_TERMINAL_STORE_PATH,
    preserved_at: datetime | None = None,
) -> P2RV12PreservationReceipt:
    """Copy the completed study once without rewriting or deleting the source store."""

    base = Path(root).resolve()
    source = Path(terminal_store_path)
    store = source if source.is_absolute() else base / source
    archive = Path(preservation_root)
    archive_root = archive if archive.is_absolute() else base / archive
    destination = preservation_destination(archive_root)
    if destination.exists() or destination.is_symlink():
        return verify_preserved_p2r_v1_2(destination)
    source_files = _exact_store_files(store)
    manifest = _load_terminal_manifest(store)
    if (
        manifest.store_sha256 != TERMINAL_STORE_SHA256
        or manifest.terminal_artifact_sha256 != TERMINAL_ARTIFACT_SHA256
    ):
        _fail("source P2R v1.2 evidence differs from the registered terminal study")
    source_hashes = {path.name: _file_sha256(path) for path in source_files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{_CONTENT_ADDRESS}-", dir=destination.parent))
    try:
        staged_store = stage / "result-store"
        staged_store.mkdir()
        for path in source_files:
            shutil.copyfile(path, staged_store / path.name)
        receipt = P2RV12PreservationReceipt(
            preserved_at=preserved_at or datetime.now(UTC),
            content_address=_CONTENT_ADDRESS,
            result_store_relative_path="result-store",
            terminal_store_sha256=manifest.store_sha256,
            terminal_artifact_sha256=manifest.terminal_artifact_sha256,
            manifest_file_sha256=source_hashes["manifest.json"],
            artifact_count_with_manifest=8,
            result_store_bytes_with_manifest=sum(path.stat().st_size for path in source_files),
            copy_is_byte_identical=True,
            original_result_store_modified=False,
            read_only_after_verification=True,
        )
        _write_exclusive(stage / "preservation-receipt.json", _json_bytes(receipt))
        _make_read_only(stage)
        verify_preserved_p2r_v1_2(stage)
        if {path.name: _file_sha256(path) for path in source_files} != source_hashes:
            _fail("source P2R v1.2 evidence changed during preservation")
        publish_staged_directory(stage, destination)
        return verify_preserved_p2r_v1_2(destination)
    except BaseException:
        if stage.exists():
            _make_writable_for_cleanup(stage)
            shutil.rmtree(stage)
        raise
