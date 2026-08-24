"""Tracked, outcome-blind preservation of the terminal v3.2 failure."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_2_closeout import (
    V32TechnicalFailureReceipt,
    load_and_verify_terminal_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    V3_2_PROTOCOL_SHA256,
    V3RuntimeError,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

FAILURE_AUDIT_SCHEMA_VERSION: Final[Literal["p2-v3-2-failure-audit/1"]] = (
    "p2-v3-2-failure-audit/1"
)
DEFAULT_V3_2_FAILURE_AUDIT_PATH = Path(
    "configs/benchmark/provenance/p2_v3_2_technical_failure_audit.json"
)
DEFAULT_V3_2_TERMINAL_STORE_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.2"
)
V3_2_TERMINAL_STORE_SHA256: Final[str] = (
    "1ce2b827d027cdb0685ad22c520d1ff11b6fcb45e2af5894ad2f4f964c97d029"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class V32TechnicalFailureAudit(_StrictFrozenModel):
    """Public facts and bounded diagnosis; never a substitute for lost outcomes."""

    schema_version: Literal["p2-v3-2-failure-audit/1"] = FAILURE_AUDIT_SCHEMA_VERSION
    study_tag: Literal["p2-label-noise-shift-factorial-v3.2"]
    protocol_sha256: Sha256 = V3_2_PROTOCOL_SHA256
    registration_sha256: Sha256
    execution_commit: GitCommit
    failure_stage: Literal["build_closeout"]
    exception_class: Literal["ValidationError"]
    exception_message_sha256: Sha256
    terminal_artifact_sha256: Sha256
    terminal_store_sha256: Sha256
    terminal_store_manifest_file_sha256: Sha256
    terminal_failure_file_sha256: Sha256
    partial_outcome_published: Literal[False]
    scientific_disposition_generated: Literal[False]
    rerun_forbidden: Literal[True]
    outcome_artifacts_available: Literal[False]
    diagnosis_scope: Literal["failure_receipt_code_path_and_synthetic_reproduction_only"]
    root_cause_classification: Literal["implementation_contract_defect"]
    reproduced_conflict: Literal[
        "scientific_abstention_requires_complete_inference_but_closeout_rejected_any_abstention_with_inference"
    ]
    causal_attribution: Literal["high_confidence_not_exception_preimage_verified"]
    scientific_semantics_changed_by_repair: Literal[False]
    original_attempt_may_be_overwritten: Literal[False]
    recovery_scope: Literal[
        "new_protocol_tag_release_registration_and_single_prospective_execution_required"
    ]

    @model_validator(mode="after")
    def _identity_is_frozen(self) -> V32TechnicalFailureAudit:
        if self.terminal_store_sha256 != V3_2_TERMINAL_STORE_SHA256:
            raise ValueError("v3.2 audit is bound to another terminal store")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise V3RuntimeError(f"required v3.2 failure artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3RuntimeError(f"cannot read required v3.2 failure artifact: {path}") from exc
    return digest.hexdigest()


def load_v3_2_failure_audit(
    path: str | Path = DEFAULT_V3_2_FAILURE_AUDIT_PATH,
) -> V32TechnicalFailureAudit:
    try:
        return V32TechnicalFailureAudit.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.2 technical-failure audit is unavailable or invalid") from exc


def verify_v3_2_failure_audit(
    audit: V32TechnicalFailureAudit,
    *,
    root: str | Path,
    terminal_store_path: str | Path = DEFAULT_V3_2_TERMINAL_STORE_PATH,
) -> V32TechnicalFailureAudit:
    """Reconcile the tracked audit with the immutable local terminal store."""

    checked = V32TechnicalFailureAudit.model_validate(audit.model_dump())
    base = Path(root).resolve()
    store = Path(terminal_store_path)
    store = store if store.is_absolute() else base / store
    manifest = load_and_verify_terminal_store(store)
    try:
        failure = V32TechnicalFailureReceipt.model_validate_json(
            (store / "technical-failure.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.2 terminal failure receipt is invalid") from exc
    observed = (
        failure.protocol_sha256,
        failure.registration_sha256,
        failure.execution_commit,
        failure.failure_stage,
        failure.exception_class,
        failure.exception_message_sha256,
        manifest.terminal_artifact_sha256,
        manifest.store_sha256,
    )
    expected = (
        checked.protocol_sha256,
        checked.registration_sha256,
        checked.execution_commit,
        checked.failure_stage,
        checked.exception_class,
        checked.exception_message_sha256,
        checked.terminal_artifact_sha256,
        checked.terminal_store_sha256,
    )
    if observed != expected:
        raise V3RuntimeError("v3.2 failure audit does not reconcile with terminal evidence")
    if _file_sha256(store / "store-manifest.json") != checked.terminal_store_manifest_file_sha256:
        raise V3RuntimeError("v3.2 terminal manifest file hash differs from the audit")
    if _file_sha256(store / "technical-failure.json") != checked.terminal_failure_file_sha256:
        raise V3RuntimeError("v3.2 terminal failure file hash differs from the audit")
    return checked
