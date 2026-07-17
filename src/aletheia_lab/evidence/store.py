"""Immutable, deterministic filesystem persistence for evidence bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.evidence.rubric import EvidenceCondition
from aletheia_lab.evidence.schema import (
    EvidenceBundle,
    canonical_json,
    project_diagnosis_evidence,
)

EVIDENCE_STORE_SCHEMA_VERSION: Final[Literal["evidence-store/2"]] = "evidence-store/2"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not value or value != value.strip() or "\\" in value or ":" in value:
        raise ValueError("store paths must be non-empty canonical POSIX paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("store paths must be normalized, relative and non-traversing")
    return value


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceStoreEntry(_StrictFrozenModel):
    """Integrity metadata for one canonical bundle file."""

    evidence_bundle_id: str
    case_id: str
    case_family_id: str = Field(pattern=r"^p1-family-[0-9a-f]{64}$")
    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    evidence_condition: EvidenceCondition
    relative_path: str
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_view_sha256: str = Field(pattern=_SHA256_PATTERN)
    file_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("relative_path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class EvidenceStoreArtifact(_StrictFrozenModel):
    """Integrity metadata for a non-bundle audit artifact."""

    artifact_type: str
    relative_path: str
    file_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("artifact_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact_type must not be blank")
        return value

    @field_validator("relative_path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        return _safe_relative_path(value)


def _store_digest(
    entries: tuple[EvidenceStoreEntry, ...], artifacts: tuple[EvidenceStoreArtifact, ...]
) -> str:
    payload = {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class EvidenceStoreManifest(_StrictFrozenModel):
    """Self-validating index for one immutable evidence-store generation."""

    schema_version: Literal["evidence-store/2"]
    bundle_count: int
    entries: tuple[EvidenceStoreEntry, ...]
    artifacts: tuple[EvidenceStoreArtifact, ...] = ()
    store_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("entries")
    @classmethod
    def _ordered_entries(
        cls, value: tuple[EvidenceStoreEntry, ...]
    ) -> tuple[EvidenceStoreEntry, ...]:
        return tuple(sorted(value, key=lambda entry: entry.evidence_bundle_id))

    @field_validator("artifacts")
    @classmethod
    def _ordered_artifacts(
        cls, value: tuple[EvidenceStoreArtifact, ...]
    ) -> tuple[EvidenceStoreArtifact, ...]:
        return tuple(sorted(value, key=lambda artifact: artifact.relative_path))

    @model_validator(mode="after")
    def _integrity(self) -> EvidenceStoreManifest:
        if self.bundle_count != len(self.entries):
            raise ValueError("bundle_count does not match entries")
        for name, values in (
            ("evidence_bundle_id", [entry.evidence_bundle_id for entry in self.entries]),
            ("case_id", [entry.case_id for entry in self.entries]),
            ("diagnosis_context_id", [entry.diagnosis_context_id for entry in self.entries]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name} in evidence store")
        paths = [entry.relative_path for entry in self.entries] + [
            artifact.relative_path for artifact in self.artifacts
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate relative_path in evidence store")
        if "store-manifest.json" in paths:
            raise ValueError("payload path collides with store manifest")
        if self.store_sha256 != _store_digest(self.entries, self.artifacts):
            raise ValueError("store_sha256 does not match canonical store index")
        return self


@dataclass(frozen=True)
class LoadedEvidenceStore:
    manifest: EvidenceStoreManifest
    bundles: tuple[EvidenceBundle, ...]
    artifact_payloads: dict[str, object]


def save_bundle(bundle: EvidenceBundle, path: str | Path) -> None:
    """Atomically save one bundle; retained for the narrow single-file API."""

    bundle_path = Path(path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(bundle.model_dump(mode="json"))
    fd, temp_name = tempfile.mkstemp(dir=bundle_path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, bundle_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Load an evidence bundle from JSON."""

    bundle_path = Path(path)
    return EvidenceBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))


def _build_store_files(
    bundles: tuple[EvidenceBundle, ...],
    artifacts: Mapping[str, tuple[str, object]],
) -> tuple[EvidenceStoreManifest, dict[str, bytes]]:
    if not bundles:
        raise ValueError("cannot persist an empty evidence store")
    entries: list[EvidenceStoreEntry] = []
    files: dict[str, bytes] = {}
    for bundle in sorted(bundles, key=lambda item: item.evidence_bundle_id):
        bundle_sha = bundle.canonical_sha256()
        relative_path = f"bundles/{bundle_sha}.json"
        payload = _json_bytes(bundle.model_dump(mode="json"))
        files[relative_path] = payload
        entries.append(
            EvidenceStoreEntry(
                evidence_bundle_id=bundle.evidence_bundle_id,
                case_id=bundle.case_id,
                case_family_id=bundle.case_family_id,
                diagnosis_context_id=bundle.diagnosis_context_id,
                evidence_condition=bundle.evidence_condition,
                relative_path=relative_path,
                bundle_sha256=bundle_sha,
                diagnosis_view_sha256=project_diagnosis_evidence(bundle).canonical_sha256(),
                file_sha256=_sha256_bytes(payload),
            )
        )

    artifact_entries: list[EvidenceStoreArtifact] = []
    for relative_path, (artifact_type, artifact_payload) in sorted(artifacts.items()):
        safe_path = _safe_relative_path(relative_path)
        payload = _json_bytes(artifact_payload)
        if safe_path in files or safe_path == "store-manifest.json":
            raise ValueError(f"duplicate or reserved artifact path: {safe_path}")
        files[safe_path] = payload
        artifact_entries.append(
            EvidenceStoreArtifact(
                artifact_type=artifact_type,
                relative_path=safe_path,
                file_sha256=_sha256_bytes(payload),
            )
        )

    entry_tuple = tuple(entries)
    artifact_tuple = tuple(artifact_entries)
    manifest = EvidenceStoreManifest(
        schema_version=EVIDENCE_STORE_SCHEMA_VERSION,
        bundle_count=len(entry_tuple),
        entries=entry_tuple,
        artifacts=artifact_tuple,
        store_sha256=_store_digest(entry_tuple, artifact_tuple),
    )
    files["store-manifest.json"] = _json_bytes(manifest.model_dump(mode="json"))
    return manifest, files


def save_bundle_store(
    bundles: tuple[EvidenceBundle, ...],
    output_dir: str | Path,
    *,
    artifacts: Mapping[str, tuple[str, object]] | None = None,
) -> EvidenceStoreManifest:
    """Persist a complete store atomically and refuse conflicting replacement.

    Repeating the same generation is idempotent.  If any byte differs, callers
    must choose a new output directory instead of mutating an existing run.
    """

    manifest, files = _build_store_files(bundles, artifacts or {})
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise FileExistsError(f"evidence store path is not a real directory: {output}")
        existing = {
            path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if existing == files and not any(path.is_symlink() for path in output.rglob("*")):
            load_bundle_store(output)
            return manifest
        raise FileExistsError(f"refusing to replace non-identical evidence store: {output}")

    stage = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}.stage-"))
    try:
        for relative_path, payload in files.items():
            destination = stage / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stage, output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    load_bundle_store(output)
    return manifest


def _confined_file(root: Path, relative_path: str) -> Path:
    candidate = root / _safe_relative_path(relative_path)
    if candidate.is_symlink():
        raise ValueError(f"store payload must not be a symlink: {relative_path}")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"store payload escapes root: {relative_path}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"store payload missing: {relative_path}")
    return candidate


def load_bundle_store(output_dir: str | Path) -> LoadedEvidenceStore:
    """Load a store and recompute every path, file, bundle and projection hash."""

    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"evidence store is not a real directory: {root}")
    manifest_path = _confined_file(root, "store-manifest.json")
    manifest = EvidenceStoreManifest.model_validate_json(manifest_path.read_text("utf-8"))
    expected_paths = {"store-manifest.json"}
    bundles: list[EvidenceBundle] = []
    for entry in manifest.entries:
        path = _confined_file(root, entry.relative_path)
        expected_paths.add(entry.relative_path)
        payload = path.read_bytes()
        if _sha256_bytes(payload) != entry.file_sha256:
            raise ValueError(f"bundle file hash mismatch: {entry.relative_path}")
        bundle = EvidenceBundle.model_validate_json(payload)
        if (
            bundle.evidence_bundle_id != entry.evidence_bundle_id
            or bundle.case_id != entry.case_id
            or bundle.case_family_id != entry.case_family_id
            or bundle.diagnosis_context_id != entry.diagnosis_context_id
            or bundle.evidence_condition != entry.evidence_condition
        ):
            raise ValueError(f"bundle identity differs from store entry: {entry.relative_path}")
        if bundle.canonical_sha256() != entry.bundle_sha256:
            raise ValueError(f"bundle canonical hash mismatch: {entry.relative_path}")
        if project_diagnosis_evidence(bundle).canonical_sha256() != entry.diagnosis_view_sha256:
            raise ValueError(f"diagnosis-view hash mismatch: {entry.relative_path}")
        bundles.append(bundle)

    artifact_payloads: dict[str, object] = {}
    for artifact in manifest.artifacts:
        path = _confined_file(root, artifact.relative_path)
        expected_paths.add(artifact.relative_path)
        payload = path.read_bytes()
        if _sha256_bytes(payload) != artifact.file_sha256:
            raise ValueError(f"artifact file hash mismatch: {artifact.relative_path}")
        artifact_payloads[artifact.relative_path] = json.loads(payload)

    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"evidence store contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise ValueError(
            "evidence store file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return LoadedEvidenceStore(
        manifest=manifest,
        bundles=tuple(sorted(bundles, key=lambda item: item.evidence_bundle_id)),
        artifact_payloads=artifact_payloads,
    )
