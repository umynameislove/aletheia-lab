"""Controlled native Joblib cache loads; run only in an isolated private process.

The query key hashes function arguments, not consumed serialized bytes. All
pickle inputs are created here; arbitrary external pickle files are never loaded.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.warrant_development_io import _private_dir, _write
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import content_sha256

CONTROLS = (
    ("healthy", "a", "a"),
    ("replacement", "a", "b"),
    ("correction", "a", "a"),
    ("legitimate", "b", "b"),
    ("reverse", "b", "a"),
)
joblib = importlib.import_module("joblib")
numpy_pickle: Any = importlib.import_module("joblib.numpy_pickle")


def cached_value(context: int, variant: str) -> tuple[int, ...]:
    """Distinct synthetic values, not fitted models or a natural incident corpus."""
    base = 10 * context
    return (base + 1, base + 2, base + 3) if variant == "a" else (base + 3, base + 2, base + 1)


def producer_identity() -> dict[str, Any]:
    backends = importlib.import_module("joblib._store_backends")
    memory = importlib.import_module("joblib.memory")

    return {
        "name": "joblib.Memory",
        "version": joblib.__version__,
        "license": "BSD-3-Clause",
        "mmap_mode": None,
        "source_sha256": {
            name: file_sha256(Path(str(module.__file__)))
            for name, module in (
                ("memory.py", memory),
                ("_store_backends.py", backends),
                ("numpy_pickle.py", numpy_pickle),
            )
        },
    }


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _document(name: str, raw: bytes, kind: str, authority: str) -> dict[str, str]:
    text = raw.decode("utf-8")
    if "/Users/" in text or "/private/" in text or "\\Users\\" in text:
        raise ValueError("native source contains a private absolute path")
    return {
        "id": name,
        "text": text,
        "sha256": content_sha256(raw),
        "kind": kind,
        "authority": authority,
        "scope": "attempt-0",
    }


def _one_load(
    cached: Any, *, path: Path, trusted: bytes, requested: bytes
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Bind the witness to the same buffer passed to the native pickle consumer."""
    original = numpy_pickle.load
    captures: list[dict[str, Any]] = []

    def consume(file: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(file.name) != path or file.tell() != 0:
            raise ValueError("consumer escaped the controlled cache")
        raw = file.read()
        if raw != trusted:
            raise ValueError("native opened bytes differ from the controlled snapshot")
        buffer = io.BytesIO(raw)
        returned = original(buffer, *args, **kwargs)
        captures.append(
            {
                "consumed_sha256": content_sha256(raw),
                "byte_count": len(raw),
                "return_fingerprint": content_sha256(_json(returned)),
            }
        )
        return returned

    stdout, stderr = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(stderr)
    root = logging.getLogger()
    old_level, old_handlers = root.level, root.handlers[:]
    root.handlers, root.level = [handler], logging.INFO
    numpy_pickle.load = consume
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returned = cached()
    finally:
        numpy_pickle.load = original
        root.handlers, root.level = old_handlers, old_level
        handler.close()
    if len(captures) != 1:
        raise ValueError("each case must execute exactly one native consumer read")
    intent = _json(
        {
            "schema_version": "cache-intent/v1",
            "requested_sha256": content_sha256(requested),
        }
    )
    witness = _json(
        {"schema_version": "cache-consumer/v1", "consumed_sha256": captures[0]["consumed_sha256"]}
    )
    documents = [
        _document("native-out", stdout.getvalue().encode(), "joblib-stdout", "native-message"),
        _document("native-query", stderr.getvalue().encode(), "joblib-query", "native-message"),
        _document("intent", intent, "caller-intent", "caller-pin"),
        _document("consumer", witness, "consumer-witness", "same-buffer-consumer"),
    ]
    return documents, {**captures[0], "returned": list(returned)}


def generate_source(output: Path) -> dict[str, Any]:
    """Create every fixed control; any native failure blocks the whole plan.

    The working directory must be the new private output. Relative cache paths
    keep native text shareable without paraphrasing the messages.
    """
    directory = _private_dir(output)
    if Path.cwd().resolve() != directory.resolve() or (directory / "source.json").exists():
        raise ValueError("source requires a fresh private subprocess working directory")
    memory = joblib.Memory("cache", verbose=0, mmap_mode=None)
    # Import by its package name even under ``python -m``. Joblib otherwise
    # embeds the __main__ source filename in the native cache path.
    from aletheia_lab.evaluation.native_cache_source import cached_value as native_value

    cached = memory.cache(native_value)
    cases: list[dict[str, Any]] = []
    for context in range(1):
        blobs, paths = {}, {}
        for variant in ("a", "b"):
            cached(context, variant)
            key = cached._get_args_id(context, variant)
            path = Path("cache/joblib") / cached.func_id / key / "output.pkl"
            blobs[variant], paths[variant] = path.read_bytes(), path
            publish_immutable_file(directory / f"frozen-{variant}.pkl", blobs[variant])
        for control, requested, installed in CONTROLS:
            path = paths[requested]
            # Replace only a file generated above in this isolated cache.
            path.write_bytes(blobs[installed])
            cached._verbose = 20
            docs, observed = _one_load(
                partial(cached, context, requested),
                path=path,
                trusted=blobs[installed],
                requested=blobs[requested],
            )
            expected_return = list(cached_value(context, installed))
            if observed["returned"] != expected_return:
                raise ValueError("native return failed the independently fixed schedule")
            cases.append(
                {
                    "case_id": f"case-{len(cases):02d}",
                    "context": context,
                    "control": control,
                    "documents": docs,
                    "reference": {
                        "requested_sha256": content_sha256(blobs[requested]),
                        "consumed_sha256": content_sha256(blobs[installed]),
                        "status": "no_binding_fault" if requested == installed else "binding_fault",
                        "expected_return": expected_return,
                    },
                    "consumer_observation": observed,
                }
            )
        cached._verbose = 0
    result = {
        "schema_version": "native-cache-source/v1",
        "producer": producer_identity(),
        "source_cluster_count": 1,
        "source_type": "controlled_native_producer_with_synthetic_values",
        "cases": cases,
    }
    _write(directory / "source.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = generate_source(args.output)
    print(json.dumps({"native_loads": len(result["cases"]), "provider_calls": 0}))


if __name__ == "__main__":
    main()
