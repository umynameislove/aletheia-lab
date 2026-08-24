"""Auditable technical recovery contracts for the interrupted v3.1 attempt."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3ProtocolRegistrationReceipt,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

FAILURE_RECEIPT_SCHEMA_VERSION: Final[
    Literal["p2-v3-technical-failure-receipt/1"]
] = "p2-v3-technical-failure-receipt/1"
DEFAULT_V3_1_FAILURE_RECEIPT_PATH = Path(
    "configs/benchmark/provenance/p2_v3_1_technical_failure_receipt.json"
)
DEFAULT_V3_1_REGISTRATION_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.1-registration.json"
)
DEFAULT_V3_1_SEALED_MARKER_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.1-sealed-open.json"
)
DEFAULT_V3_1_RESULT_STORE_PATH = Path(
    "experiments/p2/outputs/label-noise-shift-factorial-v3.1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class CalibrationFailureCell(_StrictFrozenModel):
    direction: Literal["yes_to_no", "no_to_yes"]
    conditional_rate: float
    corruption_seed: Literal[6103, 6111, 6112, 6118]
    training_role: str
    original_disposition: Literal[
        "line_search_failed", "iteration_budget_exhausted"
    ]
    development_record_count: Literal[6000]
    sum_gradient_infinity_norm: float = Field(gt=0.0)
    mean_gradient_infinity_norm: float = Field(gt=0.0)
    newton_step_infinity_norm: float | None = Field(default=None, ge=0.0)
    hessian_condition_number: float = Field(gt=0.0)
    objective_delta_at_best_rejected_step: float | None = Field(default=None, ge=0.0)
    perfect_or_quasi_separation_detected: Literal[False]

    @model_validator(mode="after")
    def _mean_gradient_reconciles(self) -> CalibrationFailureCell:
        if self.conditional_rate not in {0.1, 0.3}:
            raise ValueError("audited calibration cell has an unexpected rate")
        expected = self.sum_gradient_infinity_norm / self.development_record_count
        if abs(expected - self.mean_gradient_infinity_norm) > 1e-18:
            raise ValueError("mean calibration gradient does not reconcile")
        if self.original_disposition == "line_search_failed":
            if self.newton_step_infinity_norm is None:
                raise ValueError("line-search evidence requires the rejected Newton step")
            if self.objective_delta_at_best_rejected_step is None:
                raise ValueError("line-search evidence requires the objective delta")
        elif (
            self.newton_step_infinity_norm is not None
            or self.objective_delta_at_best_rejected_step is not None
        ):
            raise ValueError("iteration-budget evidence must not invent a rejected step")
        return self


class V3TechnicalFailureReceipt(_StrictFrozenModel):
    """Immutable facts establishing why the original attempt cannot be replayed."""

    schema_version: Literal["p2-v3-technical-failure-receipt/1"] = (
        FAILURE_RECEIPT_SCHEMA_VERSION
    )
    study_tag: Literal["p2-label-noise-shift-factorial-v3.1"]
    protocol_sha256: Sha256
    registration_sha256: Sha256
    registration_file_sha256: Sha256
    sealed_marker_file_sha256: Sha256
    execution_commit: GitCommit
    tagged_protocol_commit: GitCommit
    sealed_opened_at: datetime
    sealed_partition_opened: Literal[True]
    result_store_published: Literal[False]
    confirmatory_closeout_generated: Literal[False]
    scientific_disposition_generated: Literal[False]
    rerun_forbidden: Literal[True]
    observed_exception_type: Literal["V3RuntimeError"]
    observed_exception_message: Literal["calibration line search failed"]
    observed_failure_cell: CalibrationFailureCell
    outcome_blind_root_cause_classification: Literal["implementation_defect"]
    root_cause: Literal[
        "summed_gradient_tolerance_depended_on_development_size_and_exact_objective_comparison_rejected_roundoff_scale_steps"
    ]
    affected_cell_census: tuple[CalibrationFailureCell, ...]
    recovery_scope: Literal[
        "new_protocol_tag_release_and_single_prospective_execution_required"
    ]
    original_attempt_may_be_overwritten: Literal[False]

    @model_validator(mode="after")
    def _census_is_exact_and_unique(self) -> V3TechnicalFailureReceipt:
        if self.sealed_opened_at.tzinfo is None:
            raise ValueError("sealed-open timestamp must include timezone evidence")
        expected = {
            ("yes_to_no", 0.1, 6103, "line_search_failed"),
            ("yes_to_no", 0.1, 6111, "iteration_budget_exhausted"),
            ("yes_to_no", 0.1, 6112, "line_search_failed"),
            ("no_to_yes", 0.3, 6118, "line_search_failed"),
        }
        observed = {
            (
                item.direction,
                item.conditional_rate,
                item.corruption_seed,
                item.original_disposition,
            )
            for item in self.affected_cell_census
        }
        if observed != expected or len(self.affected_cell_census) != len(expected):
            raise ValueError("technical failure cell census must contain the four audited cells")
        first = self.observed_failure_cell
        if (first.direction, first.conditional_rate, first.corruption_seed) != (
            "yes_to_no",
            0.1,
            6103,
        ):
            raise ValueError("observed execution failure must identify the first failed cell")
        if first not in self.affected_cell_census:
            raise ValueError("observed execution failure is absent from the audited census")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink() or not path.is_file():
        raise V3RuntimeError(f"required recovery artifact is not a regular file: {path}")
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3RuntimeError(f"cannot read required recovery artifact: {path}") from exc
    return digest.hexdigest()


def load_v3_technical_failure_receipt(
    path: str | Path = DEFAULT_V3_1_FAILURE_RECEIPT_PATH,
) -> V3TechnicalFailureReceipt:
    try:
        return V3TechnicalFailureReceipt.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("v3.1 technical failure receipt is unavailable or invalid") from exc


def refuse_v3_1_reexecution(*, root: str | Path) -> None:
    """Unconditionally retire v3.1 once its tracked failure receipt exists."""

    base = Path(root).resolve()
    receipt = load_v3_technical_failure_receipt(
        base / DEFAULT_V3_1_FAILURE_RECEIPT_PATH
    )
    if not receipt.rerun_forbidden or receipt.original_attempt_may_be_overwritten:
        raise V3RuntimeError("v3.1 retirement receipt does not fail closed")
    raise V3RuntimeError(
        "v3.1 execution is permanently retired after its sealed technical failure; "
        "a new protocol, tag, release, and registration are required"
    )


def verify_v3_technical_failure_receipt(
    receipt: V3TechnicalFailureReceipt,
    *,
    root: str | Path,
    registration_path: str | Path = DEFAULT_V3_1_REGISTRATION_PATH,
    marker_path: str | Path = DEFAULT_V3_1_SEALED_MARKER_PATH,
    result_store_path: str | Path = DEFAULT_V3_1_RESULT_STORE_PATH,
) -> V3TechnicalFailureReceipt:
    """Bind the tracked receipt to ignored local evidence without changing it."""

    checked = V3TechnicalFailureReceipt.model_validate(receipt.model_dump())
    base = Path(root).resolve()

    def resolve(path: str | Path) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else base / candidate

    registration_file = resolve(registration_path)
    marker_file = resolve(marker_path)
    result_store = resolve(result_store_path)
    if result_store.exists() or result_store.is_symlink():
        raise V3RuntimeError("v3.1 failure receipt conflicts with an existing result store")
    if _file_sha256(registration_file) != checked.registration_file_sha256:
        raise V3RuntimeError("v3.1 registration file hash differs from the failure receipt")
    if _file_sha256(marker_file) != checked.sealed_marker_file_sha256:
        raise V3RuntimeError("v3.1 sealed marker hash differs from the failure receipt")
    try:
        registration = V3ProtocolRegistrationReceipt.model_validate_json(
            registration_file.read_text(encoding="utf-8")
        )
        marker = json.loads(marker_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise V3RuntimeError("v3.1 local recovery evidence is invalid") from exc
    expected_marker = {
        "schema_version": "p2-v3-sealed-open/1",
        "protocol_sha256": checked.protocol_sha256,
        "registration_sha256": checked.registration_sha256,
        "execution_commit": checked.execution_commit,
        "opened_at": checked.sealed_opened_at.isoformat(),
        "rerun_forbidden": True,
    }
    if marker != expected_marker:
        raise V3RuntimeError("v3.1 sealed marker content differs from the failure receipt")
    if (
        registration.canonical_sha256() != checked.registration_sha256
        or registration.protocol_sha256 != checked.protocol_sha256
        or registration.tagged_protocol_commit != checked.tagged_protocol_commit
        or registration.tag_name != checked.study_tag
    ):
        raise V3RuntimeError("v3.1 registration semantics differ from the failure receipt")
    return checked
