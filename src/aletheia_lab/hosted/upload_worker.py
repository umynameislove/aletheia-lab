"""One-shot upload/project worker; caller supplies trusted private root."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .request_boundary import decode_json_model
from .source_bundle import MAXIMUM_ENTRIES, MAXIMUM_EXPANDED_BYTES, MAXIMUM_UPLOAD_BYTES
from .upload_project import (
    UploadMappingRejected,
    UploadProject,
    UploadSourceStateChanged,
    _native_path,
    _plain_directory,
)
from .upload_snapshot import parse_upload_snapshot
from .worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES
from .worker_payloads import PayloadReference, is_reference, read_input, write_output


class WorkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["preview", "confirm", "analyze", "follow_up", "refresh"]
    source_base64: str | None = Field(default=None, min_length=1, max_length=MAX_INPUT_BYTES)
    source_path: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_source_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    captured_at: str | None = Field(default=None, min_length=1, max_length=32)
    workspace_id: str | None = None
    source_id: str | None = None
    mapping: dict[str, object] | None = None
    snapshot: dict[str, object] | None = None
    question: str | None = None
    analysis_model: Literal['upload-snapshot-observation/v1', 'upload-snapshot-metric-observation/v2'] | None = None
    parent: dict[str, object] | None = None
    selection_kind: Literal["claim", "node"] | None = None
    selection_id: str | None = None
    origin_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    before_source_path: str | None = Field(default=None, min_length=1, max_length=4096)
    new_source_path: str | None = Field(default=None, min_length=1, max_length=4096)


def _read_source_path(root: Path, value: str) -> bytes:
    path = Path(value)
    if not path.is_absolute() or path.parent != root / "inputs" or path.name in {"", ".", ".."}:
        raise ValueError("invalid source input")
    path = _native_path(path)
    _plain_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        named = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named.st_mode)
                or stat.S_ISLNK(named.st_mode) or getattr(named, "st_file_attributes", 0) & 1024
                or before.st_nlink != 1 or not 0 < before.st_size <= MAXIMUM_UPLOAD_BYTES):
            raise ValueError("invalid source input")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(MAXIMUM_UPLOAD_BYTES + 1)
        after = os.fstat(descriptor)
        current = path.lstat()
        def signature(info: os.stat_result) -> tuple[int, int, int, int]:
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if (len(data) != before.st_size or signature(before) != signature(after)
                or signature(before) != signature(named) or signature(before) != signature(current)):
            raise ValueError("source input changed")
        return data
    finally:
        os.close(descriptor)


@contextmanager
def _working_root(root: Path, source_path: str | None) -> Iterator[Path]:
    if source_path is None:
        yield root
        return
    root = _native_path(root)
    name = Path(source_path).stem
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is None:
        raise ValueError("invalid private input identity")
    _plain_directory(root)
    parent = root / "materializations"
    parent.mkdir(mode=0o700, exist_ok=True)
    _plain_directory(parent)
    owned = parent / name
    owned.mkdir(mode=0o700)
    try:
        yield owned
    finally:
        if owned.exists():
            _plain_directory(owned)
            if owned.resolve(strict=True).parent != parent.resolve(strict=True):
                raise ValueError("invalid owned scratch root")
            shutil.rmtree(owned)


def _execute(request: WorkerInput, root: Path) -> dict[str, object]:
    if request.operation == "refresh":
        return _refresh(request, root)
    if request.model_fields_set & {"origin_source_sha256", "before_source_path", "new_source_path"}:
        raise ValueError("unexpected refresh fields")
    if request.operation == "follow_up":
        if request.model_fields_set - {"operation", "parent", "selection_kind", "selection_id", "question", "workspace_id"}:
            raise ValueError("unexpected follow-up fields")
        from .project_worker import WorkerInput as FollowUpInput
        from .project_worker import _follow_up

        follow_up = FollowUpInput.model_validate({"parent": request.parent, "selection_kind": request.selection_kind,
            "selection_id": request.selection_id, "question": request.question, "workspace_id": request.workspace_id})
        return _follow_up(follow_up).model_dump(mode="json")
    if request.model_fields_set & {"parent", "selection_kind", "selection_id"}:
        raise ValueError("unexpected project fields")
    if request.operation == "analyze":
        if (request.snapshot is None or request.question is None or request.source_base64 is not None
                or "expected_source_state_sha256" in request.model_fields_set
                or request.source_path is not None or request.captured_at is not None or request.mapping is not None
                or request.workspace_id is not None or request.source_id is not None):
            raise ValueError("invalid analysis fields")
        from .upload_analysis import analyze_initial_upload, analyze_upload_metrics

        snapshot = parse_upload_snapshot(json.dumps(request.snapshot))
        analyzer = (analyze_upload_metrics if request.analysis_model == 'upload-snapshot-metric-observation/v2'
                    else analyze_initial_upload)
        return analyzer(snapshot, request.question).model_dump(mode="json")
    if "analysis_model" in request.model_fields_set:
        raise ValueError("unexpected analysis version")
    if ((request.source_base64 is None) == (request.source_path is None)
            or request.captured_at is None or request.snapshot is not None or request.question is not None):
        raise ValueError("invalid upload fields")
    return _upload(request, root)


def _refresh(request: WorkerInput, root: Path) -> dict[str, object]:
    from datetime import datetime

    from .project_refresh import refresh_uploaded_project

    fields = {"operation", "workspace_id", "source_id", "parent", "origin_source_sha256",
              "before_source_path", "new_source_path", "captured_at"}
    if (request.model_fields_set != fields or request.parent is None or request.source_id is None
            or request.origin_source_sha256 is None or request.before_source_path is None
            or request.new_source_path is None or request.captured_at is None):
        raise ValueError("invalid refresh input")
    parent = parse_upload_snapshot(json.dumps(request.parent))
    if parent.workspace_id != request.workspace_id:
        raise ValueError("invalid refresh workspace")
    before = _read_source_path(root, request.before_source_path)
    new = _read_source_path(root, request.new_source_path)
    with _working_root(root, request.before_source_path) as working_root:
        return refresh_uploaded_project(parent=parent, origin_source_sha256=request.origin_source_sha256,
            before_payload=before, new_payload=new, source_id=request.source_id, private_root=working_root,
            captured_at=datetime.fromisoformat(request.captured_at.replace("Z", "+00:00")),
            maximum_upload_bytes=MAXIMUM_UPLOAD_BYTES, maximum_expanded_bytes=MAXIMUM_EXPANDED_BYTES,
            maximum_entries=MAXIMUM_ENTRIES).model_dump(mode="json")


def _upload(request: WorkerInput, root: Path) -> dict[str, object]:
    if request.captured_at is None:
        raise ValueError("missing capture time")
    if request.source_path is not None:
        data = _read_source_path(root, request.source_path)
    elif request.source_base64 is not None:
        data = base64.b64decode(request.source_base64, validate=True)
    else:
        raise ValueError("missing source input")
    with _working_root(root, request.source_path) as working_root:
        project = UploadProject(working_root, maximum_upload_bytes=MAXIMUM_UPLOAD_BYTES,
                                maximum_expanded_bytes=MAXIMUM_EXPANDED_BYTES, maximum_entries=MAXIMUM_ENTRIES)
        if request.operation == "preview":
            if (request.workspace_id is not None or request.source_id is not None or request.mapping is not None
                    or "expected_source_state_sha256" in request.model_fields_set):
                raise ValueError("unexpected preview fields")
            return project.preview(data, captured_at=request.captured_at)
        if request.workspace_id is None or request.source_id is None or request.mapping is None:
            raise ValueError("missing confirmation fields")
        try:
            return project.confirm(data, workspace_id=request.workspace_id, source_id=request.source_id,
                                   mapping=request.mapping, captured_at=request.captured_at,
                                   expected_source_state_sha256=request.expected_source_state_sha256).model_dump(mode="json")
        except (UploadMappingRejected, UploadSourceStateChanged) as exc:
            return exc.envelope()


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
        print("worker_invalid_configuration", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        root = Path(sys.argv[1])
        reference = None
        if is_reference(raw):
            reference = decode_json_model(raw, PayloadReference, maximum_bytes=MAX_INPUT_BYTES)
            raw = read_input(root, reference)
        request = decode_json_model(raw, WorkerInput,
            maximum_bytes=MAX_INPUT_BYTES if reference is None else MAX_UPLOAD_OUTPUT_BYTES)
        value = _execute(request, root)
        output = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        output_limit = MAX_UPLOAD_OUTPUT_BYTES if reference is not None or request.operation == "confirm" else MAX_OUTPUT_BYTES
        if len(output) > output_limit:
            raise ValueError("worker output too large")
        if reference is not None:
            output = write_output(root, reference, output)
        if sys.stdout.buffer.write(output) != len(output):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        print("worker_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
