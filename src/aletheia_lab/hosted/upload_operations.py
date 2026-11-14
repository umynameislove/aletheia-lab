"""Internal authorized preview/confirm orchestration; no durable job or HTTP route."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, RootModel, SecretStr

from aletheia_lab.hosted_contract.imports import PreviewImport
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.view import AnyProductView
from aletheia_lab.project.identity import content_hasher

from .async_work import run_blocking
from .postgres_sources import SourceNotFound
from .preview_leases import PreviewLeases
from .request_boundary import decode_json_model
from .upload_project import _native_path, _plain_directory
from .upload_snapshot import AnyUploadSnapshot
from .worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_candidates: dict[str, object]
    excluded_metrics: list[dict[str, object]] = Field(default_factory=list, max_length=1000)
    excluded_metrics_total: int = Field(default=0, ge=0)


class _UploadSnapshotOutput(RootModel[Annotated[AnyUploadSnapshot, Field(discriminator="schema_version")]]):
    pass


class _ProductViewOutput(RootModel[Annotated[AnyProductView, Field(discriminator="schema_version")]]):
    pass


class Sources(Protocol):
    async def read(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str) -> bytes: ...
    async def publish_snapshot(self, *, credential_digest: SecretStr, snapshot: AnyUploadSnapshot) -> AnyUploadSnapshot: ...
    async def snapshot(self, *, credential_digest: SecretStr, workspace_id: str, snapshot_id: str) -> AnyUploadSnapshot: ...


class Worker(Protocol):
    async def run_upload(self, payload: bytes) -> bytes: ...


class UploadOperations:
    """Re-read authorized bytes around worker calls and never trust worker scope."""

    def __init__(self, sources: Sources, worker: Worker, *, leases: PreviewLeases | None = None,
                 private_root: Path | None = None) -> None:
        self._sources, self._worker = sources, worker
        self._leases, self._private_root = leases, private_root

    def _worker_input_limit(self) -> int:
        value = getattr(self._worker, 'maximum_payload_bytes', MAX_INPUT_BYTES)
        if type(value) is not int or not MAX_INPUT_BYTES <= value <= MAX_UPLOAD_OUTPUT_BYTES:
            raise ValueError('Invalid private worker input bound.')
        return value

    @staticmethod
    def _worker_result(raw: bytes) -> bytes:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            return raw
        if isinstance(value, dict) and value.get("schema_version") == "hosted-upload-error/v1":
            if value.get("code") == "source_changed" and set(value) == {"schema_version", "code"}:
                from .preview_leases import PreviewChanged
                raise PreviewChanged()
            from .import_errors import MappingInvalid
            raise MappingInvalid(value)
        return raw

    def _owned_input(self, path: Path) -> Path:
        root = self._private_root
        if (root is None or not root.is_absolute() or path.parent != root / "inputs"
                or re.fullmatch(r"[0-9a-f]{32}\.zip", path.name) is None):
            raise ValueError("An exact server-owned input path is required.")
        _plain_directory(_native_path(root))
        return _native_path(path)

    def _write_input(self, data: bytes, path: Path) -> None:
        path = self._owned_input(path)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        _plain_directory(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())

    def _cleanup_input(self, path: Path) -> None:
        path = self._owned_input(path)
        if path.parent.exists():
            _plain_directory(path.parent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 1024 or info.st_nlink != 1):
                raise ValueError("Invalid owned input.")
            path.unlink()
        parent = path.parent.parent / "materializations"
        owned = parent / path.stem
        if owned.exists():
            _plain_directory(owned)
            if (owned.resolve(strict=True).parent != parent.resolve(strict=True)
                    or not owned.resolve(strict=True).is_relative_to(path.parent.parent.resolve(strict=True))):
                raise ValueError("Invalid owned materialization.")
            shutil.rmtree(owned)

    async def _write_input_ready(self, data: bytes, path: Path) -> None:
        await run_blocking(self._write_input, data, path)

    async def analyze(self, *, credential_digest: SecretStr, workspace_id: str,
                      snapshot_id: str, question: str,
                      analysis_model: str = 'upload-snapshot-observation/v1') -> AnyProductView:
        question = checked_product_question(question)
        snapshot = await self._sources.snapshot(credential_digest=credential_digest, workspace_id=workspace_id,
                                                snapshot_id=snapshot_id)
        payload: dict[str, object] = {"operation": "analyze", "question": question}
        if analysis_model != 'upload-snapshot-observation/v1':
            if analysis_model != 'upload-snapshot-metric-observation/v2':
                raise ValueError('Unsupported analysis version.')
            payload['analysis_model'] = analysis_model
        def encode() -> bytes:
            return json.dumps(dict(payload, snapshot=snapshot.model_dump(mode='json')),
                              ensure_ascii=True, allow_nan=False).encode()
        request = await run_blocking(encode)
        if len(request) > self._worker_input_limit():
            raise ValueError("Snapshot exceeds worker input budget")
        raw = await self._worker.run_upload(request)
        current = await self._sources.snapshot(credential_digest=credential_digest, workspace_id=workspace_id,
                                               snapshot_id=snapshot_id)
        if current != snapshot:
            raise SourceNotFound()
        view = decode_json_model(raw, _ProductViewOutput, maximum_bytes=MAX_OUTPUT_BYTES).root
        from .upload_analysis import analyze_initial_upload, analyze_upload_metrics

        analyzer = analyze_upload_metrics if analysis_model == 'upload-snapshot-metric-observation/v2' else analyze_initial_upload
        if view != await run_blocking(analyzer, snapshot, question):
            raise ValueError("Worker analysis mismatch")
        return view

    async def _execute(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str,
                       mapping: dict[str, object] | None,
                       expected_source_state_sha256: str | None = None) -> tuple[bytes, bytes]:
        data = await self._sources.read(credential_digest=credential_digest, workspace_id=workspace_id, source_id=source_id)
        request: dict[str, object] = {"operation": "preview" if mapping is None else "confirm",
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
        input_path = None
        if self._private_root is None:
            request["source_base64"] = base64.b64encode(data).decode("ascii")
        else:
            input_path = self._private_root / "inputs" / (uuid4().hex + ".zip")
            request["source_path"] = str(input_path)
        if mapping is not None:
            request.update(workspace_id=workspace_id, source_id=source_id, mapping=mapping)
            if expected_source_state_sha256 is not None:
                request["expected_source_state_sha256"] = expected_source_state_sha256
        try:
            if input_path is not None:
                await self._write_input_ready(data, input_path)
            payload = json.dumps(request, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
            if len(payload) > MAX_INPUT_BYTES:
                raise ValueError("Source exceeds worker input budget")
            result = await run_blocking(self._worker_result, await self._worker.run_upload(payload))
        finally:
            if input_path is not None:
                self._cleanup_input(input_path)
        # A previous read is not a grant after a long-running worker operation.
        current = await self._sources.read(credential_digest=credential_digest, workspace_id=workspace_id, source_id=source_id)
        if not content_hasher(data).digest() == content_hasher(current).digest():
            raise SourceNotFound()
        return data, result

    async def preview(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str) -> Preview | PreviewImport:
        data, raw = await self._execute(credential_digest=credential_digest, workspace_id=workspace_id,
                                        source_id=source_id, mapping=None)
        preview = decode_json_model(raw, Preview, maximum_bytes=MAX_OUTPUT_BYTES)
        if preview.source_sha256 != content_hasher(data).hexdigest():
            raise ValueError("Worker source mismatch")
        if self._leases is not None:
            return await self._leases.create(credential_digest=credential_digest, workspace_id=workspace_id,
                source_id=source_id, preview_payload=preview.model_dump(mode="json"))
        return preview

    async def confirm(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str,
                      mapping: dict[str, object]) -> AnyUploadSnapshot:
        snapshot = await self.prepare_confirm(credential_digest=credential_digest, workspace_id=workspace_id,
                                              source_id=source_id, mapping=mapping)
        return await self._sources.publish_snapshot(credential_digest=credential_digest, snapshot=snapshot)

    async def prepare_confirm(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str,
                              mapping: dict[str, object], preview_id: str | None = None) -> AnyUploadSnapshot:
        preview = None
        if self._leases is not None:
            if preview_id is None:
                from .import_errors import PreviewRequired
                raise PreviewRequired()
            preview = await self._leases.validate(credential_digest=credential_digest, workspace_id=workspace_id,
                source_id=source_id, preview_id=preview_id)
        data, raw = await self._execute(credential_digest=credential_digest, workspace_id=workspace_id,
                                        source_id=source_id, mapping=mapping,
                                        expected_source_state_sha256=None if preview is None else preview.source_state_sha256)
        snapshot = (await run_blocking(decode_json_model, raw, _UploadSnapshotOutput,
                    maximum_bytes=MAX_UPLOAD_OUTPUT_BYTES)).root
        if (snapshot.workspace_id != workspace_id or snapshot.source_id != source_id
                or snapshot.source_sha256 != content_hasher(data).hexdigest()):
            raise ValueError("Worker source scope mismatch")
        if preview is not None:
            if self._leases is None or preview_id is None:
                raise ValueError("Missing preview binding.")
            current = await self._leases.validate(credential_digest=credential_digest, workspace_id=workspace_id,
                source_id=source_id, preview_id=preview_id)
            if current.source_sha256 != snapshot.source_sha256 or current != preview:
                raise SourceNotFound()
        return snapshot
