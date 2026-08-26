"""Production collector contracts for deterministic local project observations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from aletheia_lab.project import (
    ProjectCollectionError,
    ProjectGitState,
    ProjectImportArtifact,
    ProjectImportResult,
    collect_git_state,
    collect_project_files,
    grant_project_root,
    import_local_project,
)

_NOW = "2026-08-25T00:00:00Z"


def _import(root: Path) -> ProjectImportResult:
    return import_local_project(
        grant_project_root(root), display_name="Collector fixture", ingested_at=_NOW
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _repository(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Synthetic Test")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    # Keep the synthetic repository independent of the runner's global Git
    # configuration.  Git for Windows commonly enables ``core.autocrlf``;
    # without an explicit repository policy an LF fixture can appear modified
    # immediately after commit even though the collector has not mutated it.
    _git(root, "config", "core.autocrlf", "false")
    (root / "tracked.txt").write_text("first\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "initial")


def test_csv_import_releases_only_content_bound_metadata(tmp_path: Path) -> None:
    raw = b"customer_id,outcome\n001,approved\n002,declined\n"
    (tmp_path / "dataset.csv").write_bytes(raw)

    result = _import(tmp_path)

    assert result.status == "imported_with_restrictions"
    assert result.bundle is not None
    item = result.bundle.items[0]
    artifact = result.artifacts[0]
    payload = json.loads(artifact.content)
    assert payload == {
        "column_count": 2,
        "columns": ["customer_id", "outcome"],
        "format": "csv",
        "row_count": 2,
        "schema_version": "project-dataset-metadata/v1",
    }
    assert b"approved" not in artifact.content
    assert item.content_sha256 != item.artifact.sha256
    assert item.redaction_reasons == ("dataset.raw_rows_withheld",)
    assert any(issue.code == "dataset_rows_withheld" for issue in result.preview.issues)


def test_parquet_import_reads_footer_metadata_without_releasing_rows(tmp_path: Path) -> None:
    path = tmp_path / "dataset.parquet"
    table = pa.table({"entity_id": [1, 2, 3], "private_value": ["a", "b", "c"]})
    pq.write_table(table, path, row_group_size=2)
    raw = path.read_bytes()

    result = _import(tmp_path)

    assert result.status == "imported_with_restrictions"
    assert result.bundle is not None
    payload = json.loads(result.artifacts[0].content)
    assert payload["format"] == "parquet"
    assert payload["row_count"] == 3
    assert payload["row_group_count"] == 2
    assert payload["columns"] == ["entity_id", "private_value"]
    assert result.artifacts[0].content != raw
    assert b"private_value" in result.artifacts[0].content
    assert b"PAR1" not in result.artifacts[0].content


def test_malformed_parquet_fails_atomically(tmp_path: Path) -> None:
    (tmp_path / "broken.parquet").write_bytes(b"PAR1not-a-valid-footerPAR1")

    result = _import(tmp_path)

    assert result.status == "blocked"
    assert result.bundle is None
    assert result.artifacts == ()
    assert any(issue.code == "structured_content_invalid" for issue in result.preview.issues)


def test_sensitive_dataset_field_labels_are_replaced_deterministically(tmp_path: Path) -> None:
    (tmp_path / "dataset.csv").write_text(
        "person@example.invalid,password=synthetic_secret\na,b\n", encoding="utf-8"
    )

    result = _import(tmp_path)

    assert result.bundle is not None
    payload = json.loads(result.artifacts[0].content)
    assert payload["columns"] == ["field_1_redacted", "field_2_redacted"]
    assert "example.invalid" not in result.artifacts[0].content.decode()
    assert "synthetic_secret" not in result.artifacts[0].content.decode()


def test_file_catalog_contains_summaries_not_raw_payloads(tmp_path: Path) -> None:
    (tmp_path / "dataset.csv").write_text("id,value\n1,TOP_SECRET_ROW\n", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        '{"model":"linear","threshold":0.5}', encoding="utf-8"
    )
    (tmp_path / "events.log").write_text("one\ntwo\n", encoding="utf-8")
    result = _import(tmp_path)
    assert result.bundle is not None

    first = collect_project_files(result.bundle, result.artifacts)
    second = collect_project_files(result.bundle, tuple(reversed(result.artifacts)))

    assert first == second
    assert first.collection_sha256 == second.collection_sha256
    by_path = {value.relative_path: value for value in first.observations}
    assert by_path["dataset.csv"].dataset is not None
    assert by_path["dataset.csv"].dataset.row_count == 1
    assert by_path["config.json"].top_level_keys == ("model", "threshold")
    assert by_path["events.log"].line_count == 2
    serialized = first.model_dump_json()
    assert "TOP_SECRET_ROW" not in serialized
    assert "linear" not in serialized


def test_file_catalog_rejects_missing_or_tampered_artifact(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"key":"value"}', encoding="utf-8")
    result = _import(tmp_path)
    assert result.bundle is not None
    artifact = result.artifacts[0]

    with pytest.raises(ProjectCollectionError, match="exactly reconcile"):
        collect_project_files(result.bundle, ())
    forged = ProjectImportArtifact(
        relative_path="other.json", reference=artifact.reference, content=artifact.content
    )
    with pytest.raises(ProjectCollectionError, match="exactly reconcile"):
        collect_project_files(result.bundle, (forged,))


def test_git_collector_distinguishes_clean_and_dirty_states(tmp_path: Path) -> None:
    _repository(tmp_path)
    grant = grant_project_root(tmp_path)

    clean = collect_git_state(grant)
    assert clean.repository_state == "attached"
    assert clean.commit_sha is not None
    assert clean.branch is not None
    assert not clean.dirty
    assert clean.changed_files == ()

    (tmp_path / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("new\n", encoding="utf-8")
    dirty = collect_git_state(grant)
    assert dirty.dirty
    assert {(change.status, change.relative_path) for change in dirty.changed_files} == {
        (" M", "tracked.txt"),
        ("??", "untracked.txt"),
    }
    assert clean.commit_sha == dirty.commit_sha
    assert clean.state_sha256 != dirty.state_sha256


def test_git_collector_records_staged_rename_and_detached_head(tmp_path: Path) -> None:
    _repository(tmp_path)
    _git(tmp_path, "mv", "tracked.txt", "renamed.txt")

    renamed = collect_git_state(grant_project_root(tmp_path))

    assert len(renamed.changed_files) == 1
    change = renamed.changed_files[0]
    assert change.status == "R "
    assert change.relative_path == "renamed.txt"
    assert change.previous_path == "tracked.txt"

    _git(tmp_path, "reset", "--hard", "-q", "HEAD")
    _git(tmp_path, "checkout", "-q", "--detach", "HEAD")
    detached = collect_git_state(grant_project_root(tmp_path))
    assert detached.repository_state == "detached"
    assert detached.branch is None
    assert detached.commit_sha is not None


def test_git_collector_requires_exact_granted_worktree_root(tmp_path: Path) -> None:
    _repository(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()

    with pytest.raises(ProjectCollectionError, match="exact Git worktree root"):
        collect_git_state(grant_project_root(nested))


def test_git_collector_rejects_noncanonical_public_path(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / " leading.txt").write_text("unsafe spelling\n", encoding="utf-8")

    with pytest.raises(ProjectCollectionError, match="canonical public value"):
        collect_git_state(grant_project_root(tmp_path))


def test_git_state_rejects_forged_digest(tmp_path: Path) -> None:
    _repository(tmp_path)
    state = collect_git_state(grant_project_root(tmp_path))
    payload = state.model_dump(mode="python")
    payload["state_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="state_sha256"):
        ProjectGitState.model_validate(payload)
