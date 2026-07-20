"""Deterministic integrity lock for the canonical P1 external result."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.diagnosis.external_pilot import validate_openai_full
from aletheia_lab.diagnosis.openai_preflight import (
    load_openai_pilot_config,
    load_openai_preflight,
    openai_preflight_sha256,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisRunRecord,
    PilotManifest,
    ProviderIdentity,
)
from aletheia_lab.evaluation.pilot import (
    EvaluationSummary,
    MatchedPilotEvaluationReport,
    evaluate_matched_pilot,
)
from aletheia_lab.evidence.store import load_bundle_store

RESULT_LOCK_SCHEMA_VERSION: Final[Literal["p1-result-lock/1"]] = "p1-result-lock/1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactDigest(_StrictFrozenModel):
    relative_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("relative_path")
    @classmethod
    def _canonical_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class OperationalTotals(_StrictFrozenModel):
    run_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    latency_ms: float = Field(ge=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _derived_counts(self) -> Self:
        if self.success_count + self.unresolved_count != self.run_count:
            raise ValueError("operational outcome counts do not match run_count")
        if self.attempt_count < self.run_count:
            raise ValueError("attempt_count cannot be smaller than run_count")
        if self.retry_count != self.attempt_count - self.run_count:
            raise ValueError("retry_count is not derived from attempts and runs")
        return self


class P1ResultLock(_StrictFrozenModel):
    """Self-contained digest census for one canonical P1 full result."""

    schema_version: Literal["p1-result-lock/1"]
    execution_commit_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    evaluation_commit_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    provider_identity: ProviderIdentity
    source_cases_tree_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_artifact_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_artifacts: tuple[ArtifactDigest, ...]
    operational_totals: OperationalTotals
    evaluation_summary: EvaluationSummary

    @model_validator(mode="after")
    def _artifact_contract(self) -> Self:
        paths = tuple(item.relative_path for item in self.pilot_artifacts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("pilot artifact paths must be unique and sorted")
        if "pilot-manifest.json" not in paths or "execution-authorization.json" not in paths:
            raise ValueError("result lock lacks required full-run control artifacts")
        return self


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("result-lock paths must be canonical relative POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("result-lock path is absolute, non-canonical or traverses parents")
    return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _real_files(root: Path) -> tuple[Path, ...]:
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"artifact root is not a real directory: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"artifact root contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            files.append(path)
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def _tree_digest(root: Path) -> str:
    rows = [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": _sha256_bytes(path.read_bytes()),
        }
        for path in _real_files(root)
    ]
    if not rows:
        raise ValueError(f"artifact root contains no files: {root}")
    return _sha256_bytes(_canonical_bytes(rows))


def _pilot_artifacts(root: Path) -> tuple[ArtifactDigest, ...]:
    return tuple(
        ArtifactDigest(
            relative_path=_safe_relative_path(path.relative_to(root).as_posix()),
            sha256=_sha256_bytes(path.read_bytes()),
        )
        for path in _real_files(root)
    )


def _artifact_set_digest(artifacts: tuple[ArtifactDigest, ...]) -> str:
    return _sha256_bytes(
        _canonical_bytes([item.model_dump(mode="json") for item in artifacts])
    )


def _load_records(pilot_root: Path, manifest: PilotManifest) -> tuple[DiagnosisRunRecord, ...]:
    records: list[DiagnosisRunRecord] = []
    for entry in manifest.entries:
        relative = _safe_relative_path(entry.relative_path)
        path = pilot_root / relative
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"result-lock run artifact missing: {relative}")
        records.append(DiagnosisRunRecord.model_validate_json(path.read_bytes()))
    return tuple(records)


def _operational_totals(
    manifest: PilotManifest, records: tuple[DiagnosisRunRecord, ...]
) -> OperationalTotals:
    attempts = tuple(attempt for record in records for attempt in record.attempts)
    usage = tuple(attempt.usage for attempt in attempts if attempt.usage is not None)
    return OperationalTotals(
        run_count=manifest.run_count,
        success_count=manifest.success_count,
        unresolved_count=manifest.unresolved_count,
        attempt_count=len(attempts),
        retry_count=len(attempts) - manifest.run_count,
        input_tokens=sum(item.input_tokens for item in usage),
        output_tokens=sum(item.output_tokens for item in usage),
        estimated_cost_usd=sum(item.estimated_cost_usd for item in usage),
        latency_ms=sum(
            attempt.latency_ms for attempt in attempts if attempt.latency_ms is not None
        ),
    )


def build_p1_result_lock(
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    config_path: str | Path,
    preflight_path: str | Path,
    evaluation_path: str | Path,
    *,
    execution_commit_sha: str,
    evaluation_commit_sha: str,
) -> P1ResultLock:
    """Validate and bind all canonical P1 inputs, outputs and derived totals."""

    pilot_root = Path(pilot_dir)
    cases_root = Path(cases_dir)
    preflight_file = Path(preflight_path)
    evaluation_file = Path(evaluation_path)
    config_file = Path(config_path)
    config = load_openai_pilot_config(config_file)
    manifest = validate_openai_full(pilot_root, evidence_store_dir, config, preflight_file)
    report = MatchedPilotEvaluationReport.model_validate_json(
        evaluation_file.read_bytes()
    )
    recomputed = evaluate_matched_pilot(
        pilot_root,
        evidence_store_dir,
        cases_root,
        openai_config_path=config_file,
        preflight_path=preflight_file,
    )
    if report != recomputed:
        raise ValueError("evaluation report differs from independent recomputation")
    artifacts = _pilot_artifacts(pilot_root)
    manifest_payload = (pilot_root / "pilot-manifest.json").read_bytes()
    records = _load_records(pilot_root, manifest)
    persisted_preflight = load_openai_preflight(preflight_file)
    store = load_bundle_store(evidence_store_dir)
    return P1ResultLock(
        schema_version=RESULT_LOCK_SCHEMA_VERSION,
        execution_commit_sha=execution_commit_sha,
        evaluation_commit_sha=evaluation_commit_sha,
        provider_identity=manifest.provider_identity,
        source_cases_tree_sha256=_tree_digest(cases_root),
        source_evidence_store_sha256=store.manifest.store_sha256,
        config_sha256=persisted_preflight.config_sha256,
        config_file_sha256=_sha256_bytes(config_file.read_bytes()),
        preflight_sha256=openai_preflight_sha256(persisted_preflight),
        preflight_file_sha256=_sha256_bytes(preflight_file.read_bytes()),
        pilot_manifest_sha256=_sha256_bytes(manifest_payload),
        evaluation_report_sha256=_sha256_bytes(evaluation_file.read_bytes()),
        pilot_artifact_set_sha256=_artifact_set_digest(artifacts),
        pilot_artifacts=artifacts,
        operational_totals=_operational_totals(manifest, records),
        evaluation_summary=report.summary,
    )


def write_p1_result_lock(lock: P1ResultLock, output_path: str | Path) -> None:
    """Write one immutable, deterministic result-lock artifact."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(_canonical_bytes(lock.model_dump(mode="json")))
        handle.flush()
        os.fsync(handle.fileno())


def validate_p1_result_lock(
    lock_path: str | Path,
    pilot_dir: str | Path,
    evidence_store_dir: str | Path,
    cases_dir: str | Path,
    config_path: str | Path,
    preflight_path: str | Path,
    evaluation_path: str | Path,
) -> P1ResultLock:
    """Recompute every result-lock field and reject any changed artifact."""

    lock = P1ResultLock.model_validate_json(Path(lock_path).read_bytes())
    expected = build_p1_result_lock(
        pilot_dir,
        evidence_store_dir,
        cases_dir,
        config_path,
        preflight_path,
        evaluation_path,
        execution_commit_sha=lock.execution_commit_sha,
        evaluation_commit_sha=lock.evaluation_commit_sha,
    )
    if lock != expected:
        raise ValueError("result lock differs from independently recomputed evidence")
    return lock
