"""Edge contracts that keep project collectors fail closed."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from aletheia_lab.project import (
    DatasetMetadata,
    ProjectBundle,
    ProjectCollectionError,
    ProjectCollector,
    ProjectFileCollection,
    ProjectFileObservation,
    ProjectGitState,
    ProjectImportArtifact,
    ProjectImportResult,
    build_project_bundle,
    build_project_item,
    canonical_project_sha256,
    collect_git_state,
    collect_project_files,
    grant_project_root,
    import_local_project,
    project_id_for_root,
)
from aletheia_lab.project.collectors import _changed_files, _decode_git, _GitCommand, _run_git

_NOW = "2026-08-25T00:00:00Z"


def _import(root: Path) -> ProjectImportResult:
    return import_local_project(
        grant_project_root(root), display_name="Edge fixture", ingested_at=_NOW
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


@pytest.mark.parametrize(
    "changes",
    [
        {"column_count": 1},
        {"columns": ("a", "a")},
        {"types": ("int64",)},
        {"format": "parquet", "row_group_count": None},
        {"format": "csv", "row_group_count": 1},
    ],
)
def test_dataset_metadata_rejects_inconsistent_shape(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "schema_version": "project-dataset-metadata/v1",
        "format": "csv",
        "row_count": 2,
        "column_count": 2,
        "columns": ("a", "b"),
        "types": (),
        "row_group_count": None,
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        DatasetMetadata.model_validate(values)


def test_file_observation_and_collection_reject_forged_identity(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("safe\n", encoding="utf-8")
    imported = _import(tmp_path)
    assert imported.bundle is not None
    collection = collect_project_files(imported.bundle, imported.artifacts)
    observation_payload = collection.observations[0].model_dump(mode="python")
    observation_payload["observation_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="observation_sha256"):
        ProjectFileObservation.model_validate(observation_payload)

    collection_payload = collection.model_dump(mode="python")
    collection_payload["collection_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="collection_sha256"):
        ProjectFileCollection.model_validate(collection_payload)

    duplicate = collection.model_copy(
        update={"observations": (collection.observations[0], collection.observations[0])}
    )
    with pytest.raises(ValidationError, match="unique"):
        ProjectFileCollection.model_validate(duplicate.model_dump(mode="python"))


def test_file_observation_rejects_ambiguous_metadata_contract(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("safe\n", encoding="utf-8")
    imported = _import(tmp_path)
    assert imported.bundle is not None
    observation = collect_project_files(imported.bundle, imported.artifacts).observations[0]

    duplicate_keys = observation.model_dump(mode="python")
    duplicate_keys["top_level_keys"] = ("same", "same")
    with pytest.raises(ValidationError, match="top-level keys"):
        ProjectFileObservation.model_validate(duplicate_keys)

    metadata = DatasetMetadata(
        schema_version="project-dataset-metadata/v1",
        format="csv",
        row_count=0,
        column_count=0,
        columns=(),
    )
    for source_type, dataset, message in (
        ("dataset", None, "require metadata"),
        ("other", metadata, "cannot be attached"),
    ):
        payload = observation.model_dump(mode="python")
        payload.update(
            {
                "source_type": source_type,
                "dataset": dataset,
            }
        )
        digest_payload = {
            key: value for key, value in payload.items() if key != "observation_sha256"
        }
        digest_payload["dataset"] = (
            None if dataset is None else dataset.model_dump(mode="json")
        )
        payload["observation_sha256"] = canonical_project_sha256(digest_payload)
        with pytest.raises(ValidationError, match=message):
            ProjectFileObservation.model_validate(payload)


def _manual_bundle(
    *, source_type: Literal["dataset", "config"], relative_path: str, content: bytes
) -> tuple[ProjectBundle, ProjectImportArtifact]:
    fingerprint = "1" * 64
    project_id = project_id_for_root(fingerprint)
    item = build_project_item(
        project_id=project_id,
        relative_path=relative_path,
        source_type=source_type,
        media_type="application/json",
        source_schema="synthetic/v1",
        source_bytes=content,
        source_modified_at=_NOW,
        ingested_at=_NOW,
        collector=ProjectCollector(name="synthetic", version="1.0.0"),
        visibility="diagnosis",
        parse_status="parsed",
    )
    bundle = build_project_bundle(
        project_id=project_id,
        display_name="Manual fixture",
        granted_root_fingerprint=fingerprint,
        created_at=_NOW,
        updated_at=_NOW,
        items=(item,),
    )
    return bundle, ProjectImportArtifact(
        relative_path=relative_path, reference=item.artifact, content=content
    )


@pytest.mark.parametrize(
    ("source_type", "relative_path", "content", "message"),
    [
        ("dataset", "dataset.csv", b"not metadata", "dataset artifact"),
        ("config", "config.json", b"not json", "structured artifact"),
    ],
)
def test_catalog_rejects_invalid_prevalidated_artifact(
    source_type: Literal["dataset", "config"],
    relative_path: str,
    content: bytes,
    message: str,
) -> None:
    bundle, artifact = _manual_bundle(
        source_type=source_type, relative_path=relative_path, content=content
    )

    with pytest.raises(ProjectCollectionError, match=message):
        collect_project_files(bundle, (artifact,))


def test_catalog_treats_structured_non_object_as_keyless(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text("[]", encoding="utf-8")
    imported = _import(tmp_path)
    assert imported.bundle is not None

    observation = collect_project_files(imported.bundle, imported.artifacts).observations[0]

    assert observation.top_level_keys == ()


def test_git_collector_supports_unborn_repository(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "untracked.txt").write_text("new\n", encoding="utf-8")

    state = collect_git_state(grant_project_root(tmp_path))

    assert state.repository_state == "unborn"
    assert state.commit_sha is None
    assert state.branch is not None
    assert state.dirty


@pytest.mark.parametrize(
    "updates",
    [
        {"repository_state": "unborn", "commit_sha": "a" * 40},
        {"repository_state": "attached", "branch": None},
        {"repository_state": "detached", "branch": "main"},
        {"dirty": True, "changed_files": ()},
    ],
)
def test_git_state_rejects_inconsistent_terminal_state(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    _git(tmp_path, "init", "-q")
    state = collect_git_state(grant_project_root(tmp_path))
    payload = state.model_dump(mode="python")
    payload.update(updates)

    with pytest.raises(ValidationError):
        ProjectGitState.model_validate(payload)


def test_git_status_parser_rejects_malformed_or_incomplete_records() -> None:
    with pytest.raises(ProjectCollectionError, match="malformed"):
        _changed_files(b"bad\0")
    with pytest.raises(ProjectCollectionError, match="incomplete"):
        _changed_files(b"R  renamed.txt\0")
    with pytest.raises(ProjectCollectionError, match="not valid UTF-8"):
        _decode_git(b"\xff", label="Git field")


def test_required_git_command_failure_is_fail_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")

    with pytest.raises(ProjectCollectionError, match="read-only Git collection failed"):
        _run_git(tmp_path, _GitCommand(("not-a-git-subcommand",)))


def test_git_collection_rejects_stale_grant(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    grant = grant_project_root(root)
    root.rename(tmp_path / "moved")

    with pytest.raises(ProjectCollectionError, match="no longer current"):
        collect_git_state(grant)
