"""One-shot canonical project computation; no persistence or authorization."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import BinaryIO, Literal, TextIO

from pydantic import BaseModel, ConfigDict

from aletheia_lab.hosted.request_boundary import decode_json_model
from aletheia_lab.hosted.worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.product.results import (
    ProductResultEnvelope,
    _validate_derived_result,
    _view_from_record,
)
from aletheia_lab.product.view import parse_product_view_json

from .native_upload_records import CanonicalResultRecord, build_upload_follow_up_result


class WorkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    parent: dict[str, object]
    selection_kind: Literal["claim", "node"]
    selection_id: Identifier
    question: str
    workspace_id: Identifier | None = None


def _follow_up(request: WorkerInput) -> CanonicalResultRecord:
    parent = parse_product_view_json(json.dumps(request.parent, allow_nan=False))
    selection = resolve_product_follow_up_selection(parent, selection_kind=request.selection_kind,
                                                    selection_id=request.selection_id, question=request.question)
    child = build_product_follow_up_view(parent, selection)
    _validate_derived_result(parent, child)
    if parent.snapshot.id.startswith("upload-snapshot-"):
        if request.workspace_id is None:
            raise ValueError("native follow-up workspace required")
        return build_upload_follow_up_result(request.workspace_id, parent, child)
    envelope = ProductResultEnvelope(view=child.model_dump(mode="json"))
    record = build_product_lifecycle_record(record_kind="result", project_id=parent.project.id,
        snapshot_id=parent.snapshot.id, parent_result_id=parent.result.id, payload=envelope.model_dump(mode="json"))
    _validate_derived_result(parent, _view_from_record(record))
    return record


def main(argv: Sequence[str] | None = None, *, stdin: BinaryIO | None = None,
         stdout: BinaryIO | None = None, stderr: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    source = sys.stdin.buffer if stdin is None else stdin
    destination = sys.stdout.buffer if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    if args != ["follow-up"]:
        print("project_worker_invalid_operation", file=errors)
        return 2
    try:
        request = decode_json_model(source.read(MAX_INPUT_BYTES + 1), WorkerInput, maximum_bytes=MAX_INPUT_BYTES)
        output = _follow_up(request).model_dump_json().encode("utf-8")
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError("worker output too large")
        if destination.write(output) != len(output):
            raise OSError("incomplete output")
        destination.flush()
        return 0
    except Exception:
        print("project_worker_failed", file=errors)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
