"""Serial load-boundary costs without the historical harness's raw-copy sink.

Trusted single-file owned Joblib only, stable opened inode from first open.
Not hostile-host attestation, same-inode mutation support or concurrent capture.
"""

from __future__ import annotations

import importlib
import inspect
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_application_capture import _LOCK, descriptor_bytes
from aletheia_lab.project.identity import content_sha256


class ServingCapture:
    """Hash once per actual reconstruction; never wrap resident inference."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.scope: str | None = None
        self.events: list[dict[str, Any]] = []

    @contextmanager
    def installed(self) -> Iterator[None]:
        module: Any = importlib.import_module("joblib.numpy_pickle")
        original = module._unpickle
        signature = inspect.signature(original)
        if not _LOCK.acquire(blocking=False):
            raise RuntimeError("capture is serial process-local only")
        owner = threading.get_ident()

        def observed(stream: Any, *args: Any, **kwargs: Any) -> Any:
            if self.scope is None or threading.get_ident() != owner:
                raise RuntimeError("unscoped or concurrent reconstruction")
            if signature.bind(stream, *args, **kwargs).arguments.get("mmap_mode") is not None:
                raise ValueError("mmap is not supported")
            start = perf_counter_ns()
            payload = descriptor_bytes(stream, self.directory)
            copied = perf_counter_ns()
            digest = content_sha256(payload)
            hashed = perf_counter_ns()
            event = {
                "scope": self.scope,
                "digest": digest,
                "bytes": len(payload),
                "read_ns": copied - start,
                "hash_ns": hashed - copied,
                "reconstruct_ns": 0,
                "completed": False,
            }
            self.events.append(event)
            try:
                result = original(stream, *args, **kwargs)
                event["completed"] = True
                return result
            finally:
                event["reconstruct_ns"] = perf_counter_ns() - hashed

        module._unpickle = observed
        try:
            yield
        finally:
            module._unpickle = original
            _LOCK.release()


def tree_fingerprint(model: Any) -> str:
    """Independent post-request object sink check, never fed to tested checker."""
    state = model.tree_.__getstate__()
    nodes, values = state["nodes"], state["values"]
    # Structured node records have padding bytes with no model meaning. Their
    # values can change on reconstruction; hash named fields, never that padding.
    fields = {name: content_sha256(nodes[name].tobytes()) for name in nodes.dtype.names}
    metadata = {
        "features": int(model.n_features_in_),
        "outputs": int(model.n_outputs_),
        "fields": fields,
        "values": content_sha256(values.tobytes()),
        "classes": content_sha256(model.classes_.tobytes()),
        "values_shape": list(values.shape),
        "nodes_shape": list(nodes.shape),
    }
    return content_sha256(json.dumps(metadata, sort_keys=True).encode())
