"""Immutable, self-validating storage for the Phase 2 contract artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    AdmissionLedger,
    AlphaValidityReport,
    CandidateExecution,
    CandidatePlan,
    ClassificationLedger,
    ContextCensus,
    DuplicateAudit,
    FamilyCensus,
    TechnicalDisposition,
)
from aletheia_lab.benchmark.p2.validation import validate_contract_bundle

ArtifactKind = Literal[
    "candidate-plan",
    "candidate-execution",
    "technical-disposition",
    "classification-ledger",
    "admission-ledger",
    "family-census",
    "context-census",
    "duplicate-audit",
    "alpha-validity-report",
]

STORE_SCHEMA_VERSION: Final[Literal["p2-contract-store/1"]] = "p2-contract-store/1"

_ARTIFACT_PATHS: Final[dict[str, str]] = {
    "candidate-plan": "candidate-plan.json",
    "candidate-execution": "candidate-execution.json",
    "technical-disposition": "technical-disposition.json",
    "classification-ledger": "classification-ledger.json",
    "admission-ledger": "admission-ledger.json",
    "family-census": "family-census.json",
    "context-census": "context-census.json",
    "duplicate-audit": "duplicate-audit.json",
    "alpha-validity-report": "alpha-validity-report.json",
}

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SECRET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*=)"
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ContractStoreEntry(_StrictFrozenModel):
    artifact_kind: ArtifactKind
    relative_path: str
    file_sha256: str

    @model_validator(mode="after")
    def _path_and_hash_are_valid(self) -> ContractStoreEntry:
        expected = _ARTIFACT_PATHS[self.artifact_kind]
        if self.relative_path != expected:
            raise ValueError(f"{self.artifact_kind} must use fixed path {expected}")
        if re.fullmatch(r"[0-9a-f]{64}", self.file_sha256) is None:
            raise ValueError("file_sha256 must be a lowercase SHA-256 digest")
        return self


class ContractStoreManifest(_StrictFrozenModel):
    schema_version: Literal["p2-contract-store/1"]
    artifact_count: int
    entries: tuple[ContractStoreEntry, ...]
    store_sha256: str

    @model_validator(mode="after")
    def _manifest_is_complete(self) -> ContractStoreManifest:
        if self.artifact_count != len(_ARTIFACT_PATHS):
            raise ValueError("artifact_count must equal the complete contract artifact set")
        kinds = tuple(entry.artifact_kind for entry in self.entries)
        paths = tuple(entry.relative_path for entry in self.entries)
        if len(set(kinds)) != len(kinds) or len(set(paths)) != len(paths):
            raise ValueError("manifest artifact kinds and paths must be unique")
        if set(kinds) != set(_ARTIFACT_PATHS):
            raise ValueError("manifest must contain exactly the nine contract artifacts")
        if list(paths) != sorted(paths):
            raise ValueError("manifest entries must be sorted by relative_path")
        expected_store_hash = canonical_sha256(
            [entry.model_dump(mode="json") for entry in self.entries]
        )
        if self.store_sha256 != expected_store_hash:
            raise ValueError("store_sha256 does not bind the manifest entries")
        return self


@dataclass(frozen=True)
class P2ContractArtifacts:
    plan: CandidatePlan
    execution: CandidateExecution
    disposition: TechnicalDisposition
    classifications: ClassificationLedger
    admissions: AdmissionLedger
    census: FamilyCensus
    contexts: ContextCensus
    duplicate_audit: DuplicateAudit
    report: AlphaValidityReport

    def validate(self) -> None:
        validate_contract_bundle(
            plan=self.plan,
            execution=self.execution,
            disposition=self.disposition,
            classifications=self.classifications.entries,
            admissions=self.admissions.entries,
            census=self.census,
            contexts=self.contexts,
            duplicate_audit=self.duplicate_audit,
            report=self.report,
        )


@dataclass(frozen=True)
class LoadedContractStore:
    manifest: ContractStoreManifest
    artifacts: P2ContractArtifacts


def _artifact_models(artifacts: P2ContractArtifacts) -> dict[str, BaseModel]:
    return {
        "candidate-plan": artifacts.plan,
        "candidate-execution": artifacts.execution,
        "technical-disposition": artifacts.disposition,
        "classification-ledger": artifacts.classifications,
        "admission-ledger": artifacts.admissions,
        "family-census": artifacts.census,
        "context-census": artifacts.contexts,
        "duplicate-audit": artifacts.duplicate_audit,
        "alpha-validity-report": artifacts.report,
    }


def _assert_safe_payload(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            _assert_safe_payload(nested, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _assert_safe_payload(nested, f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"contract artifact text must be Unicode NFC at {path}")
    if value.startswith(("/", "~")) or _WINDOWS_ABSOLUTE_PATH.match(value):
        raise ValueError(f"contract artifact contains a local absolute path at {path}")
    if value.startswith("file://"):
        raise ValueError(f"contract artifact contains a local file URI at {path}")
    if _SECRET_PATTERN.search(value):
        raise ValueError(f"contract artifact contains a secret-like value at {path}")


def _artifact_bytes(payload: object) -> bytes:
    """Canonical artifact JSON preserving JSON number types.

    Identity canonicalization intentionally represents numbers as normalized
    decimal strings. Persisted strict schemas must retain JSON integer/number
    types, so artifacts use a separate, versioned serialization domain.
    """

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _build_store(
    artifacts: P2ContractArtifacts,
) -> tuple[ContractStoreManifest, dict[str, bytes]]:
    artifacts.validate()
    files: dict[str, bytes] = {}
    entries: list[ContractStoreEntry] = []
    for kind, model in _artifact_models(artifacts).items():
        payload = model.model_dump(mode="json")
        _assert_safe_payload(payload)
        encoded = _artifact_bytes(payload)
        relative_path = _ARTIFACT_PATHS[kind]
        files[relative_path] = encoded
        entries.append(
            ContractStoreEntry(
                artifact_kind=kind,  # type: ignore[arg-type]
                relative_path=relative_path,
                file_sha256=_sha256_bytes(encoded),
            )
        )
    ordered_entries = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    manifest = ContractStoreManifest(
        schema_version=STORE_SCHEMA_VERSION,
        artifact_count=len(ordered_entries),
        entries=ordered_entries,
        store_sha256=canonical_sha256([entry.model_dump(mode="json") for entry in ordered_entries]),
    )
    files["store-manifest.json"] = _artifact_bytes(manifest.model_dump(mode="json"))
    return manifest, files


def _read_real_file(root: Path, relative_path: str) -> bytes:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative_path:
        raise ValueError(f"contract store path is unsafe: {relative_path}")
    candidate = root.joinpath(*pure.parts)
    if candidate.is_symlink():
        raise ValueError(f"contract store payload must not be a symlink: {relative_path}")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"contract store payload escapes root: {relative_path}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"contract store payload is missing: {relative_path}")
    return candidate.read_bytes()


def save_contract_store(
    artifacts: P2ContractArtifacts,
    output_dir: str | Path,
) -> ContractStoreManifest:
    """Atomically persist all nine artifacts and refuse non-identical overwrite."""

    manifest, files = _build_store(artifacts)
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_dir():
            raise FileExistsError(f"contract store path is not a real directory: {output}")
        existing_paths = {
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if any(path.is_symlink() for path in output.rglob("*")):
            raise FileExistsError("refusing a contract store containing symlinks")
        existing = {path: _read_real_file(output, path) for path in existing_paths}
        if existing == files:
            load_contract_store(output)
            return manifest
        raise FileExistsError("refusing to replace a non-identical contract store")

    stage = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}.stage-"))
    try:
        for relative_path, payload in files.items():
            destination = stage / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stage, output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    load_contract_store(output)
    return manifest


def load_contract_store(output_dir: str | Path) -> LoadedContractStore:
    """Load, hash-check, parse and cross-validate a complete contract store."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"contract store is not a real directory: {root}")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("contract store must not contain symlinks")

    manifest_bytes = _read_real_file(root, "store-manifest.json")
    manifest = ContractStoreManifest.model_validate_json(manifest_bytes)
    if manifest_bytes != _artifact_bytes(manifest.model_dump(mode="json")):
        raise ValueError("contract store manifest is not canonically encoded")

    expected_paths = {"store-manifest.json", *(_ARTIFACT_PATHS.values())}
    observed_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if observed_paths != expected_paths:
        raise ValueError(
            "contract store file set differs from the manifest; "
            f"missing={sorted(expected_paths - observed_paths)}; "
            f"extra={sorted(observed_paths - expected_paths)}"
        )

    payloads: dict[str, bytes] = {}
    for entry in manifest.entries:
        payload = _read_real_file(root, entry.relative_path)
        if _sha256_bytes(payload) != entry.file_sha256:
            raise ValueError(f"artifact hash mismatch: {entry.relative_path}")
        parsed = _model_for_kind(entry.artifact_kind).model_validate_json(payload)
        if payload != _artifact_bytes(parsed.model_dump(mode="json")):
            raise ValueError(f"artifact is not canonically encoded: {entry.relative_path}")
        payloads[entry.artifact_kind] = payload

    artifacts = P2ContractArtifacts(
        plan=CandidatePlan.model_validate_json(payloads["candidate-plan"]),
        execution=CandidateExecution.model_validate_json(payloads["candidate-execution"]),
        disposition=TechnicalDisposition.model_validate_json(payloads["technical-disposition"]),
        classifications=ClassificationLedger.model_validate_json(payloads["classification-ledger"]),
        admissions=AdmissionLedger.model_validate_json(payloads["admission-ledger"]),
        census=FamilyCensus.model_validate_json(payloads["family-census"]),
        contexts=ContextCensus.model_validate_json(payloads["context-census"]),
        duplicate_audit=DuplicateAudit.model_validate_json(payloads["duplicate-audit"]),
        report=AlphaValidityReport.model_validate_json(payloads["alpha-validity-report"]),
    )
    artifacts.validate()
    return LoadedContractStore(manifest=manifest, artifacts=artifacts)


def _model_for_kind(kind: str) -> type[BaseModel]:
    models: dict[str, type[BaseModel]] = {
        "candidate-plan": CandidatePlan,
        "candidate-execution": CandidateExecution,
        "technical-disposition": TechnicalDisposition,
        "classification-ledger": ClassificationLedger,
        "admission-ledger": AdmissionLedger,
        "family-census": FamilyCensus,
        "context-census": ContextCensus,
        "duplicate-audit": DuplicateAudit,
        "alpha-validity-report": AlphaValidityReport,
    }
    try:
        return models[kind]
    except KeyError as exc:  # pragma: no cover - manifest Literal guards this
        raise ValueError(f"unknown contract artifact kind: {kind}") from exc
