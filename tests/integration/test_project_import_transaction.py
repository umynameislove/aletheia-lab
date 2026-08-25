"""Integration tests for a complete local project import transaction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.project import grant_project_root, import_local_project

_STAMP = "2026-08-25T00:00:00Z"


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def test_nested_project_import_reconciles_policy_redaction_and_manifest(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\n')
    _write(tmp_path / "src" / "model.py", "def predict(value):\n    return value\n")
    _write(tmp_path / "reports" / "metrics.json", '{"accuracy":0.91}')
    _write(tmp_path / "reports" / "owner.txt", "owner=person@example.invalid\n")
    _write(tmp_path / "artifacts" / "weights.bin", b"\x00\x01\x02")

    result = import_local_project(
        grant_project_root(tmp_path.resolve()),
        display_name="Nested Project",
        ingested_at=_STAMP,
    )

    assert result.status == "imported_with_restrictions"
    assert result.bundle is not None
    assert result.preview.included_count == 3
    assert result.preview.redacted_count == 1
    assert result.preview.excluded_count == 1
    assert result.preview.blocked_count == 0
    assert result.bundle.project_manifest.item_count == 4
    assert result.bundle.project_manifest.source_byte_count == sum(
        item.byte_size for item in result.bundle.items
    )
    assert result.bundle.project_manifest.artifact_byte_count == sum(
        item.artifact.byte_size for item in result.bundle.items
    )
    owner = next(item for item in result.bundle.items if item.relative_path.endswith("owner.txt"))
    assert owner.visibility == "diagnosis"
    assert owner.redaction_state == "redacted"
    safe_owner = next(
        item.content for item in result.artifacts if item.relative_path == owner.relative_path
    )
    assert b"person@example.invalid" not in safe_owner


def test_import_identity_is_reproducible_across_process_hash_seeds(tmp_path: Path) -> None:
    _write(tmp_path / "config.json", '{"alpha":1,"beta":2}')
    _write(tmp_path / "notes.txt", "deterministic\n")
    script = """
import json
import sys
from pathlib import Path
from aletheia_lab.project import grant_project_root, import_local_project

root = Path(sys.argv[1])
result = import_local_project(
    grant_project_root(root),
    display_name="Subprocess Project",
    ingested_at="2026-08-25T00:00:00Z",
)
assert result.bundle is not None
print(json.dumps({
    "bundle_id": result.bundle.project_bundle_id,
    "bundle_sha256": result.bundle.canonical_sha256(),
    "preview_sha256": result.preview.preview_sha256,
    "report_sha256": result.ingestion_report_sha256,
}, sort_keys=True))
"""
    outputs: list[dict[str, str]] = []
    repository_root = Path(__file__).resolve().parents[2]
    for seed in ("1", "17", "999"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = str(repository_root / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path.resolve())],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs[0] == outputs[1] == outputs[2]


def test_one_malformed_file_prevents_partial_bundle_and_artifact_release(tmp_path: Path) -> None:
    _write(tmp_path / "a-valid.json", '{"status":"valid"}')
    _write(tmp_path / "b-invalid.json", '{"status":')
    _write(tmp_path / "c-valid.txt", "valid\n")

    result = import_local_project(
        grant_project_root(tmp_path.resolve()),
        display_name="Atomic Project",
        ingested_at=_STAMP,
    )

    assert result.status == "blocked"
    assert result.bundle is None
    assert result.artifacts == ()
    assert result.preview.included_count == 2
    assert result.preview.blocked_count == 1
    assert {issue.code for issue in result.preview.issues} == {
        "atomic_import_aborted",
        "structured_content_invalid",
    }
