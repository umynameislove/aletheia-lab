"""Append-only content-addressed storage for development pilot artifacts."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel

from aletheia_lab.diagnosis._development.contracts import (
    _RUN_ID_PATTERN,
    DEVELOPMENT_FAILURE_SCHEMA_VERSION,
    DEVELOPMENT_TERMINAL_SCHEMA_VERSION,
    DevelopmentFailureReceipt,
    DevelopmentPilotError,
    DevelopmentPilotManifest,
    DevelopmentRunRecord,
    DevelopmentTerminalReceipt,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_staged_directory
from aletheia_lab.project.identity import SHA256_PATTERN, canonical_project_json, content_sha256

_OBJECT_BUCKET = re.compile(r"^[0-9a-f]{2}$")
_OBJECT_NAME = re.compile(r"^[0-9a-f]{62}$")


class DevelopmentArtifactStore:
    """Append-only run store with content-addressed objects and atomic terminals."""

    def __init__(self, root: str | Path) -> None:
        supplied = Path(root)
        supplied.mkdir(parents=True, exist_ok=True)
        if supplied.is_symlink() or not supplied.is_dir():
            raise DevelopmentPilotError("development store root must be a real directory")
        self.root = supplied.resolve()
        self.runs_root = self.root / "runs"
        self.failures_root = self.root / "failures"
        for directory in (self.runs_root, self.failures_root):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise DevelopmentPilotError("development store-owned paths must be real")
        self.verify_integrity()

    def publish(
        self,
        manifest: DevelopmentPilotManifest,
        objects: dict[str, bytes],
    ) -> DevelopmentTerminalReceipt:
        checked = DevelopmentPilotManifest.model_validate(manifest.model_dump(mode="python"))
        if set(objects) != set(checked.object_sha256s):
            raise DevelopmentPilotError("published object census differs from manifest")
        for digest, payload in objects.items():
            if content_sha256(payload) != digest:
                raise DevelopmentPilotError("published object bytes differ from their hash")

        manifest_bytes = _canonical_model_bytes(checked)
        manifest_object_sha256 = content_sha256(manifest_bytes)
        terminal_payload = {
            "schema_version": DEVELOPMENT_TERMINAL_SCHEMA_VERSION,
            "run_id": checked.run_id,
            "manifest_sha256": checked.manifest_sha256,
            "manifest_object_sha256": manifest_object_sha256,
            "object_count": len(objects) + 1,
            "status": "development_pilot_complete",
            "protected_outcomes_opened": False,
            "live_provider_calls": 0,
            "registered_attempts_consumed": 0,
            "scientific_interpretation_permitted": False,
        }
        terminal = DevelopmentTerminalReceipt.model_validate(
            {
                **terminal_payload,
                "terminal_sha256": canonical_execution_sha256(terminal_payload),
            }
        )

        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.runs_root))
        destination = self.runs_root / checked.run_id
        try:
            object_root = stage / "objects" / "sha256"
            for digest, payload in sorted(
                {**objects, manifest_object_sha256: manifest_bytes}.items()
            ):
                target = object_root / digest[:2] / digest[2:]
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_new_file(target, payload)
            _write_new_file(stage / "terminal.json", _canonical_model_bytes(terminal))
            _fsync_tree(stage)
            try:
                publish_staged_directory(stage, destination)
            except FileExistsError as exc:
                shutil.rmtree(stage)
                existing = self.load_terminal(checked.run_id)
                if existing != terminal:
                    raise DevelopmentPilotError(
                        "conflicting development run already exists"
                    ) from exc
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        self.verify_run(checked.run_id)
        return terminal

    def record_failure(
        self,
        *,
        plan_sha256: str,
        registry_sha256: str,
        stage: str,
        exception: BaseException,
    ) -> DevelopmentFailureReceipt:
        message_sha256 = content_sha256(str(exception).encode("utf-8", errors="strict"))
        payload = {
            "schema_version": DEVELOPMENT_FAILURE_SCHEMA_VERSION,
            "plan_sha256": plan_sha256,
            "registry_sha256": registry_sha256,
            "stage": stage,
            "exception_class": type(exception).__name__,
            "message_sha256": message_sha256,
            "partial_terminal_publication": False,
            "protected_outcomes_opened": False,
            "scientific_interpretation_permitted": False,
        }
        digest = canonical_execution_sha256(payload)
        receipt = DevelopmentFailureReceipt.model_validate(
            {
                **payload,
                "failure_id": f"devfail-{digest}",
                "failure_sha256": digest,
            }
        )
        _atomic_create(
            self.failures_root / f"{receipt.failure_id}.json",
            _canonical_model_bytes(receipt),
        )
        return receipt

    def load_terminal(self, run_id: str) -> DevelopmentTerminalReceipt:
        _validate_run_id(run_id)
        path = self.runs_root / run_id / "terminal.json"
        return DevelopmentTerminalReceipt.model_validate_json(_read_regular_file(path))

    def load_manifest(self, run_id: str) -> DevelopmentPilotManifest:
        terminal = self.load_terminal(run_id)
        raw = self.read_object(run_id, terminal.manifest_object_sha256)
        manifest = DevelopmentPilotManifest.model_validate_json(raw)
        if manifest.manifest_sha256 != terminal.manifest_sha256:
            raise DevelopmentPilotError("terminal references the wrong development manifest")
        return manifest

    def read_object(self, run_id: str, digest: str) -> bytes:
        _validate_run_id(run_id)
        _validate_sha256(digest)
        path = self.runs_root / run_id / "objects" / "sha256" / digest[:2] / digest[2:]
        payload = _read_regular_file(path)
        if content_sha256(payload) != digest:
            raise DevelopmentPilotError("development object content hash mismatch")
        return payload

    def list_runs(self) -> tuple[str, ...]:
        self.verify_integrity()
        return tuple(
            sorted(
                path.name
                for path in self.runs_root.iterdir()
                if path.is_dir() and not path.name.startswith(".stage-")
            )
        )

    def verify_run(self, run_id: str) -> None:
        _validate_run_id(run_id)
        run_root = self.runs_root / run_id
        if run_root.is_symlink() or not run_root.is_dir():
            raise DevelopmentPilotError("published development run is not a real directory")
        terminal = self.load_terminal(run_id)
        manifest = self.load_manifest(run_id)
        expected_objects = set(manifest.object_sha256s) | {terminal.manifest_object_sha256}
        actual_objects: set[str] = set()
        object_root = run_root / "objects" / "sha256"
        if object_root.is_symlink() or not object_root.is_dir():
            raise DevelopmentPilotError("development object root is invalid")
        for bucket in object_root.iterdir():
            if (
                bucket.is_symlink()
                or not bucket.is_dir()
                or not _OBJECT_BUCKET.fullmatch(bucket.name)
            ):
                raise DevelopmentPilotError("development object bucket is non-canonical")
            for item in bucket.iterdir():
                if item.is_symlink() or not item.is_file() or not _OBJECT_NAME.fullmatch(item.name):
                    raise DevelopmentPilotError("development object name is non-canonical")
                digest = bucket.name + item.name
                if content_sha256(item.read_bytes()) != digest:
                    raise DevelopmentPilotError("development object failed hash verification")
                actual_objects.add(digest)
        if actual_objects != expected_objects:
            raise DevelopmentPilotError("development object membership differs from manifest")
        expected_files = {"terminal.json"} | {
            str(path.relative_to(run_root)) for path in object_root.rglob("*") if path.is_file()
        }
        actual_files = {
            str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
        }
        if actual_files != expected_files:
            raise DevelopmentPilotError("development run contains untracked files")

    def verify_integrity(self) -> None:
        for entry in self.root.iterdir():
            if entry.name not in {"runs", "failures"}:
                raise DevelopmentPilotError("development store contains an unknown root entry")
        for entry in self.runs_root.iterdir():
            if entry.name.startswith(".stage-"):
                if entry.is_symlink() or not entry.is_dir():
                    raise DevelopmentPilotError("development stage is not a real directory")
                continue
            self.verify_run(entry.name)
        for receipt_path in self.failures_root.iterdir():
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise DevelopmentPilotError("development failure entry is invalid")
            receipt = DevelopmentFailureReceipt.model_validate_json(
                _read_regular_file(receipt_path)
            )
            if receipt_path.name != f"{receipt.failure_id}.json":
                raise DevelopmentPilotError("development failure file name is non-canonical")


def load_run_record(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentRunRecord:
    return DevelopmentRunRecord.model_validate_json(store.read_object(run_id, digest))


def load_run_request(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentVariantRequest:
    return DevelopmentVariantRequest.model_validate_json(store.read_object(run_id, digest))


def load_run_response(
    store: DevelopmentArtifactStore,
    run_id: str,
    digest: str,
) -> DevelopmentVariantResponse:
    return DevelopmentVariantResponse.model_validate_json(store.read_object(run_id, digest))


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (canonical_project_json(model.model_dump(mode="json")) + "\n").encode("utf-8")


def _store_model_object(objects: dict[str, bytes], model: BaseModel) -> str:
    payload = _canonical_model_bytes(model)
    digest = content_sha256(payload)
    existing = objects.get(digest)
    if existing is not None and existing != payload:
        raise DevelopmentPilotError("development object hash collision")
    objects[digest] = payload
    return digest


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_stage = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    stage = Path(raw_stage)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(stage, path)
        except FileExistsError as exc:
            if _read_regular_file(path) != payload:
                raise DevelopmentPilotError("immutable development receipt conflict") from exc
    finally:
        stage.unlink(missing_ok=True)


def _fsync_tree(root: Path) -> None:
    # Every payload is already flushed and fsynced by ``_write_new_file``.
    # Reopening those files read-only and calling ``fsync`` again is redundant
    # and is not portable to the Windows CRT, where committing a read-only
    # descriptor may fail.  Directory fsync is retained on platforms that
    # expose the required flag so namespace publication remains durable.
    if hasattr(os, "O_DIRECTORY"):
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ) + [root]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DevelopmentPilotError("development artifact must be a regular file")
    return path.read_bytes()


def _validate_sha256(value: str) -> None:
    if re.fullmatch(SHA256_PATTERN, value) is None:
        raise DevelopmentPilotError("development object hash is invalid")


def _validate_run_id(value: str) -> None:
    if re.fullmatch(_RUN_ID_PATTERN, value) is None:
        raise DevelopmentPilotError("development run ID is invalid")
