"""Outcome-free archive readiness and immutable P2R v1 failure preservation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DatasetBindingAudit,
    V3DatasetBinding,
    V3DatasetBindingManifest,
    V3DatasetBindingReceipt,
    inspect_v3_dataset_archive,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RTechnicalFailure,
    load_and_verify_terminal_store,
)

ARCHIVE_READINESS_SCHEMA_VERSION: Final[Literal["p2r-archive-readiness/1"]] = (
    "p2r-archive-readiness/1"
)
ARCHIVE_ITEM_SCHEMA_VERSION: Final[Literal["p2r-archive-readiness-item/1"]] = (
    "p2r-archive-readiness-item/1"
)
FAILURE_AUDIT_SCHEMA_VERSION: Final[Literal["p2r-v1-failure-audit/1"]] = "p2r-v1-failure-audit/1"

DEFAULT_P2R_READINESS_PATH = Path("experiments/p2/outputs/p2r-archive-readiness.json")
DEFAULT_P2R_FAILURE_AUDIT_PATH = Path(
    "configs/benchmark/provenance/p2r_v1_technical_failure_audit.json"
)
DEFAULT_P2R_V1_REGISTRATION_PATH = Path("experiments/p2/outputs/p2r-registration.json")
DEFAULT_P2R_V1_MARKER_PATH = Path("experiments/p2/outputs/p2r-sealed-open.json")
DEFAULT_P2R_V1_STORE_PATH = Path("experiments/p2/outputs/p2r-confirmatory-v1")
P2R_V1_TERMINAL_STORE_SHA256: Final[str] = (
    "28fab91bf7a24994f93a7a145e3786ae200dab8062d06f7501e114df0ce7e28d"
)
P2R_V1_EXCEPTION_MESSAGE: Final[str] = "cannot inspect the bound dataset archive"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
DatasetRole = Literal["primary", "external_replication"]


class P2RRecoveryError(ValueError):
    """Raised when recovery evidence does not reconcile exactly."""


def _fail(message: str) -> NoReturn:
    raise P2RRecoveryError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RArchiveReadinessItem(_StrictFrozenModel):
    schema_version: Literal["p2r-archive-readiness-item/1"] = ARCHIVE_ITEM_SCHEMA_VERSION
    dataset_id: str
    role: DatasetRole
    archive_file_name: str
    archive_sha256: Sha256
    archive_byte_count: int = Field(gt=0)
    member_path: str
    member_sha256: Sha256
    member_byte_count: int = Field(gt=0)
    source_schema_sha256: Sha256
    record_identity_sha256: Sha256
    target_binding_sha256: Sha256
    row_count: int = Field(gt=0)
    parser_format: Literal["csv", "xls"]
    parser_engine: Literal["pandas_c", "xlrd"]
    archive_regular_file: Literal[True]
    pinned_audit_reproduced: Literal[True]
    item_sha256: Sha256

    @model_validator(mode="after")
    def _identity_is_derived(self) -> P2RArchiveReadinessItem:
        payload = self.model_dump(mode="json", exclude={"item_sha256"})
        if self.item_sha256 != canonical_sha256(payload):
            raise ValueError("archive readiness item hash does not bind its evidence")
        return self


class P2RArchiveReadinessReceipt(_StrictFrozenModel):
    schema_version: Literal["p2r-archive-readiness/1"] = ARCHIVE_READINESS_SCHEMA_VERSION
    dataset_manifest_sha256: Sha256
    dataset_receipt_sha256: Sha256
    items: tuple[P2RArchiveReadinessItem, ...]
    dataset_ids: tuple[str, str]
    all_pinned_archives_reproduced: Literal[True]
    target_inspection_scope: Literal["encoding_and_class_eligibility_only"]
    split_membership_compiled: Literal[False]
    sealed_partition_opened: Literal[False]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    execution_attempt_consumed: Literal[False]
    ready_for_registered_preflight: Literal[True]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_complete_and_derived(self) -> P2RArchiveReadinessReceipt:
        expected_ids = (
            "uci_default_of_credit_card_clients",
            "uci_online_shoppers_purchasing_intention",
        )
        if self.dataset_ids != expected_ids:
            raise ValueError("archive readiness has an unexpected dataset census")
        if tuple(item.dataset_id for item in self.items) != expected_ids:
            raise ValueError("archive readiness items are not in canonical dataset order")
        if tuple(item.role for item in self.items) != (
            "primary",
            "external_replication",
        ):
            raise ValueError("archive readiness requires both registered dataset roles")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("archive readiness receipt hash does not bind its evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _readiness_item(
    *,
    dataset: V3DatasetBinding,
    audit: DatasetBindingAudit,
) -> P2RArchiveReadinessItem:
    checked = V3DatasetBinding.model_validate(dataset.model_dump())
    payload: dict[str, object] = {
        "schema_version": ARCHIVE_ITEM_SCHEMA_VERSION,
        "dataset_id": checked.dataset_id,
        "role": checked.role,
        "archive_file_name": checked.archive.file_name,
        "archive_sha256": audit.archive_sha256,
        "archive_byte_count": audit.archive_byte_count,
        "member_path": checked.archive.member_path,
        "member_sha256": audit.member_sha256,
        "member_byte_count": audit.member_byte_count,
        "source_schema_sha256": audit.source_schema_sha256,
        "record_identity_sha256": audit.record_identity_sha256,
        "target_binding_sha256": audit.target_binding_sha256,
        "row_count": audit.row_count,
        "parser_format": checked.parser.format,
        "parser_engine": checked.parser.engine,
        "archive_regular_file": True,
        "pinned_audit_reproduced": True,
    }
    return P2RArchiveReadinessItem.model_validate(
        {**payload, "item_sha256": canonical_sha256(payload)}
    )


def build_p2r_archive_readiness(
    *,
    manifest: V3DatasetBindingManifest,
    pinned_receipt: V3DatasetBindingReceipt,
    archive_directory: str | Path,
) -> P2RArchiveReadinessReceipt:
    """Reproduce archive/schema audits without compiling splits or fitting a model."""

    checked_manifest = V3DatasetBindingManifest.model_validate(manifest.model_dump())
    checked_receipt = V3DatasetBindingReceipt.model_validate(pinned_receipt.model_dump())
    if checked_receipt.manifest_sha256 != checked_manifest.canonical_sha256():
        _fail("pinned dataset receipt is bound to another manifest")
    directory = Path(archive_directory)
    reproduced: list[DatasetBindingAudit] = []
    for dataset in checked_manifest.datasets:
        observed = inspect_v3_dataset_archive(
            manifest_sha256=checked_manifest.canonical_sha256(),
            dataset=dataset,
            archive_path=directory / dataset.archive.file_name,
        )
        expected = next(
            (item for item in checked_receipt.datasets if item.dataset_id == dataset.dataset_id),
            None,
        )
        if expected is None or observed != expected:
            _fail("archive readiness does not reproduce the pinned dataset audit")
        reproduced.append(observed)
    items = tuple(
        _readiness_item(dataset=dataset, audit=audit)
        for dataset, audit in zip(checked_manifest.datasets, reproduced, strict=True)
    )
    payload: dict[str, object] = {
        "schema_version": ARCHIVE_READINESS_SCHEMA_VERSION,
        "dataset_manifest_sha256": checked_manifest.canonical_sha256(),
        "dataset_receipt_sha256": checked_receipt.canonical_sha256(),
        "items": items,
        "dataset_ids": tuple(item.dataset_id for item in items),
        "all_pinned_archives_reproduced": True,
        "target_inspection_scope": "encoding_and_class_eligibility_only",
        "split_membership_compiled": False,
        "sealed_partition_opened": False,
        "model_fitted": False,
        "predictive_metrics_generated": False,
        "execution_attempt_consumed": False,
        "ready_for_registered_preflight": True,
    }
    hash_payload = {
        **payload,
        "items": tuple(item.model_dump(mode="json") for item in items),
    }
    return P2RArchiveReadinessReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_sha256(hash_payload)}
    )


def write_archive_readiness_exclusive(
    path: str | Path, receipt: P2RArchiveReadinessReceipt
) -> None:
    """Write an idempotent receipt without replacing different evidence."""

    checked = P2RArchiveReadinessReceipt.model_validate(receipt.model_dump())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = checked.model_dump_json(indent=2) + "\n"
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        try:
            existing = load_archive_readiness(target)
        except (OSError, ValueError) as exc:
            raise P2RRecoveryError("existing archive readiness receipt is invalid") from exc
        if existing != checked:
            _fail("existing archive readiness receipt contains different evidence")


def load_archive_readiness(
    path: str | Path = DEFAULT_P2R_READINESS_PATH,
) -> P2RArchiveReadinessReceipt:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise P2RRecoveryError("archive readiness receipt is unavailable or invalid")
    try:
        return P2RArchiveReadinessReceipt.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise P2RRecoveryError("archive readiness receipt is unavailable or invalid") from exc


def verify_p2r_archive_readiness(
    receipt: P2RArchiveReadinessReceipt,
    *,
    manifest: V3DatasetBindingManifest,
    pinned_receipt: V3DatasetBindingReceipt,
    archive_directory: str | Path,
) -> P2RArchiveReadinessReceipt:
    checked = P2RArchiveReadinessReceipt.model_validate(receipt.model_dump())
    reproduced = build_p2r_archive_readiness(
        manifest=manifest,
        pinned_receipt=pinned_receipt,
        archive_directory=archive_directory,
    )
    if checked != reproduced:
        _fail("archive readiness receipt does not match current pinned archives")
    return checked


class P2RV1TechnicalFailureAudit(_StrictFrozenModel):
    schema_version: Literal["p2r-v1-failure-audit/1"] = FAILURE_AUDIT_SCHEMA_VERSION
    study_tags: tuple[
        Literal["p2r-data-drift-confirmatory-v1"],
        Literal["p2r-preprocessing-mismatch-confirmatory-v1"],
    ]
    protocol_sha256s: tuple[Sha256, Sha256]
    registration_sha256s: tuple[Sha256, Sha256]
    execution_commit: GitCommit
    failure_stage: Literal["load_primary"]
    exception_class: Literal["V3DatasetBindingError"]
    exception_message_sha256: Sha256
    exception_message_preimage: Literal["cannot inspect the bound dataset archive"]
    terminal_artifact_sha256: Sha256
    terminal_store_sha256: Sha256
    registration_file_sha256: Sha256
    sealed_marker_file_sha256: Sha256
    terminal_manifest_file_sha256: Sha256
    terminal_failure_file_sha256: Sha256
    terminal_environment_file_sha256: Sha256
    required_archive_file_names: tuple[str, str]
    required_archive_sha256s: tuple[Sha256, Sha256]
    archives_absent_in_execution_worktree_after_failure: tuple[Literal[True], Literal[True]]
    valid_archive_copies_verified_elsewhere_after_failure: Literal[True]
    partial_outcome_published: Literal[False]
    scientific_disposition_generated: Literal[False]
    model_fitted: Literal[False]
    rerun_forbidden: Literal[True]
    root_cause_classification: Literal["preflight_archive_readiness_defect"]
    causal_attribution: Literal["exception_preimage_and_post_failure_filesystem_state_verified"]
    scientific_semantics_changed_by_repair: Literal[False]
    original_attempt_may_be_overwritten: Literal[False]
    recovery_scope: Literal[
        "new_protocol_tag_release_registration_and_single_prospective_execution_required"
    ]

    @model_validator(mode="after")
    def _failure_identity_is_frozen(self) -> P2RV1TechnicalFailureAudit:
        if self.terminal_store_sha256 != P2R_V1_TERMINAL_STORE_SHA256:
            raise ValueError("P2R v1 audit is bound to another terminal store")
        expected = hashlib.sha256(P2R_V1_EXCEPTION_MESSAGE.encode()).hexdigest()
        if self.exception_message_sha256 != expected:
            raise ValueError("P2R v1 exception preimage does not match its digest")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail(f"required P2R failure artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RRecoveryError(f"cannot read P2R failure artifact: {path}") from exc
    return digest.hexdigest()


def load_p2r_v1_failure_audit(
    path: str | Path = DEFAULT_P2R_FAILURE_AUDIT_PATH,
) -> P2RV1TechnicalFailureAudit:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise P2RRecoveryError("P2R v1 failure audit is unavailable or invalid")
    try:
        return P2RV1TechnicalFailureAudit.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise P2RRecoveryError("P2R v1 failure audit is unavailable or invalid") from exc


def verify_p2r_v1_failure_audit(
    audit: P2RV1TechnicalFailureAudit,
    *,
    root: str | Path,
    registration_path: str | Path = DEFAULT_P2R_V1_REGISTRATION_PATH,
    marker_path: str | Path = DEFAULT_P2R_V1_MARKER_PATH,
    terminal_store_path: str | Path = DEFAULT_P2R_V1_STORE_PATH,
) -> P2RV1TechnicalFailureAudit:
    """Bind the tracked diagnosis to the ignored, immutable local v1 evidence."""

    checked = P2RV1TechnicalFailureAudit.model_validate(audit.model_dump())
    base = Path(root).resolve()

    def resolve(path: str | Path) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else base / candidate

    registration = resolve(registration_path)
    marker = resolve(marker_path)
    store = resolve(terminal_store_path)
    manifest = load_and_verify_terminal_store(store)
    try:
        failure = P2RTechnicalFailure.model_validate_json(
            (store / "technical-failure.json").read_text(encoding="utf-8")
        )
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise P2RRecoveryError("P2R v1 failure evidence is invalid") from exc
    observed = (
        failure.protocol_sha256s,
        failure.registration_sha256s,
        failure.execution_commit,
        failure.failure_stage,
        failure.exception_class,
        failure.exception_message_sha256,
        manifest.terminal_artifact_sha256,
        manifest.store_sha256,
    )
    expected = (
        checked.protocol_sha256s,
        checked.registration_sha256s,
        checked.execution_commit,
        checked.failure_stage,
        checked.exception_class,
        checked.exception_message_sha256,
        checked.terminal_artifact_sha256,
        checked.terminal_store_sha256,
    )
    if observed != expected:
        _fail("P2R v1 failure audit does not reconcile with terminal evidence")
    if (
        marker_payload.get("execution_commit") != checked.execution_commit
        or tuple(marker_payload.get("registration_sha256s", ())) != checked.registration_sha256s
        or marker_payload.get("rerun_forbidden") is not True
    ):
        _fail("P2R v1 sealed marker does not reconcile with the failure audit")
    file_evidence = (
        (_file_sha256(registration), checked.registration_file_sha256),
        (_file_sha256(marker), checked.sealed_marker_file_sha256),
        (_file_sha256(store / "manifest.json"), checked.terminal_manifest_file_sha256),
        (
            _file_sha256(store / "technical-failure.json"),
            checked.terminal_failure_file_sha256,
        ),
        (
            _file_sha256(store / "environment.json"),
            checked.terminal_environment_file_sha256,
        ),
    )
    if any(observed_hash != expected_hash for observed_hash, expected_hash in file_evidence):
        _fail("P2R v1 failure artifact bytes differ from the tracked audit")
    return checked
