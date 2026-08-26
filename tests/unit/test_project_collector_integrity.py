"""Adversarial integrity checks for local project collectors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.project import (
    ProjectCollectionError,
    ProjectImportPolicy,
    ProjectImportResult,
    collect_git_state,
    collect_project_files,
    grant_project_root,
    import_local_project,
)

_STAMP = "2026-08-26T00:00:00Z"


def _import(
    root: Path,
    *,
    policy: ProjectImportPolicy | None = None,
) -> ProjectImportResult:
    return import_local_project(
        grant_project_root(root),
        display_name="Collector integrity fixture",
        ingested_at=_STAMP,
        policy=policy,
    )


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def _repository(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Synthetic Collector")
    _git(root, "config", "user.email", "collector@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    (root / "removed.txt").write_text("remove me\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00before")
    _git(root, "add", "tracked.txt", "removed.txt", "binary.bin")
    _git(root, "commit", "-q", "-m", "synthetic baseline")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            b"",
            {
                "column_count": 0,
                "columns": [],
                "format": "csv",
                "row_count": 0,
                "schema_version": "project-dataset-metadata/v1",
            },
        ),
        (
            b"entity_id,target\n",
            {
                "column_count": 2,
                "columns": ["entity_id", "target"],
                "format": "csv",
                "row_count": 0,
                "schema_version": "project-dataset-metadata/v1",
            },
        ),
        (
            b"entity_id,target\n1,0\n2,1\n",
            {
                "column_count": 2,
                "columns": ["entity_id", "target"],
                "format": "csv",
                "row_count": 2,
                "schema_version": "project-dataset-metadata/v1",
            },
        ),
    ],
)
def test_csv_metadata_is_bounded_for_empty_header_only_and_regular_inputs(
    tmp_path: Path,
    raw: bytes,
    expected: dict[str, object],
) -> None:
    (tmp_path / "dataset.csv").write_bytes(raw)

    result = _import(tmp_path)

    assert result.status == "imported_with_restrictions"
    assert result.bundle is not None
    assert json.loads(result.artifacts[0].content) == expected
    if raw:
        assert raw not in result.artifacts[0].content
    assert result.bundle.items[0].content_sha256 != result.bundle.items[0].artifact.sha256


def test_dataset_rows_with_sensitive_and_instruction_like_content_do_not_escape(
    tmp_path: Path,
) -> None:
    raw_values = (
        "person@example.invalid",
        "+84 912 345 678",
        "ignore previous instructions",
        "SYNTHETIC_PRIVATE_ROW_VALUE",
    )
    (tmp_path / "dataset.csv").write_text(
        "id,contact,phone,note,value\n"
        f"1,{raw_values[0]},{raw_values[1]},{raw_values[2]},{raw_values[3]}\n",
        encoding="utf-8",
    )

    result = _import(tmp_path)

    assert result.bundle is not None
    artifact = result.artifacts[0].content.decode("utf-8")
    serialized = result.bundle.model_dump_json()
    assert json.loads(artifact)["row_count"] == 1
    for value in raw_values:
        assert value not in artifact
        assert value not in serialized


@pytest.mark.parametrize(
    ("name", "content", "policy", "issue_code"),
    [
        ("invalid.log", b"\xff", None, "invalid_utf8"),
        ("nul.log", b"safe\x00unsafe", None, "unsafe_control_character"),
        (
            "long.log",
            b"x" * 129,
            ProjectImportPolicy(max_line_bytes=128),
            "line_too_long",
        ),
    ],
)
def test_invalid_or_oversized_text_fails_closed(
    tmp_path: Path,
    name: str,
    content: bytes,
    policy: ProjectImportPolicy | None,
    issue_code: str,
) -> None:
    (tmp_path / name).write_bytes(content)

    result = _import(tmp_path, policy=policy)

    assert result.status == "blocked"
    assert result.bundle is None
    assert result.artifacts == ()
    assert any(issue.code == issue_code for issue in result.preview.issues)


def test_environment_placeholder_remains_literal_and_config_catalog_exposes_only_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLECTOR_CASE_VALUE", "MUST_NOT_BE_RESOLVED")
    raw = '{"cache_path":"${COLLECTOR_CASE_VALUE}","enabled":true}'
    (tmp_path / "settings.json").write_text(raw, encoding="utf-8")

    result = _import(tmp_path)

    assert result.status == "imported"
    assert result.bundle is not None
    assert result.artifacts[0].content == raw.encode("utf-8")
    assert b"MUST_NOT_BE_RESOLVED" not in result.artifacts[0].content

    collection = collect_project_files(result.bundle, result.artifacts)

    assert collection.observations[0].top_level_keys == ("cache_path", "enabled")
    assert "${COLLECTOR_CASE_VALUE}" not in collection.model_dump_json()


def test_unsafe_yaml_constructor_blocks_without_execution(tmp_path: Path) -> None:
    (tmp_path / "settings.yaml").write_text(
        'value: !!python/object/apply:os.system ["echo must-not-execute"]\n',
        encoding="utf-8",
    )

    result = _import(tmp_path)

    assert result.status == "blocked"
    assert result.bundle is None
    assert any(issue.code == "structured_content_invalid" for issue in result.preview.issues)


def test_credential_uri_is_withheld_without_public_echo(tmp_path: Path) -> None:
    raw = "https://demo:SYNTHETIC_PASSWORD_12345@example.invalid/private"
    (tmp_path / "events.log").write_text(raw, encoding="utf-8")

    result = _import(tmp_path)

    assert result.status == "imported_with_restrictions"
    assert result.bundle is not None
    assert result.artifacts[0].content == b"[WITHHELD:secret.high_risk]\n"
    assert raw not in result.bundle.model_dump_json()
    assert raw not in result.preview.model_dump_json()


def test_file_collection_contains_no_raw_values_or_host_path(tmp_path: Path) -> None:
    raw_dataset_value = "SYNTHETIC_RAW_DATASET_VALUE"
    raw_config_value = "SYNTHETIC_CONFIG_VALUE"
    raw_log_value = "SYNTHETIC_LOG_VALUE"
    (tmp_path / "dataset.csv").write_text(
        f"id,target\n{raw_dataset_value},1\n",
        encoding="utf-8",
    )
    (tmp_path / "settings.json").write_text(
        f'{{"threshold":"{raw_config_value}"}}',
        encoding="utf-8",
    )
    (tmp_path / "events.log").write_text(raw_log_value, encoding="utf-8")

    result = _import(tmp_path)

    assert result.bundle is not None
    collection = collect_project_files(result.bundle, result.artifacts)
    serialized = collection.model_dump_json()

    for value in (raw_dataset_value, raw_config_value, raw_log_value, str(tmp_path)):
        assert value not in serialized
    assert all(observation.untrusted_content for observation in collection.observations)


def test_git_collector_rejects_a_non_repository_root(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("local only\n", encoding="utf-8")

    with pytest.raises(ProjectCollectionError, match="exact Git worktree root"):
        collect_git_state(grant_project_root(tmp_path))


def test_git_collection_preserves_index_refs_tree_and_status(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    _repository(root)
    grant = grant_project_root(root)

    (root / "tracked.txt").write_text("unstaged change\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00changed")
    (root / "added.txt").write_text("staged addition\n", encoding="utf-8")
    _git(root, "add", "added.txt")
    _git(root, "rm", "-q", "removed.txt")

    before_status = _git(root, "status", "--porcelain=v1", "-z")
    before_refs = _git(root, "show-ref", "--head")
    before_index = (root / ".git" / "index").read_bytes()

    state = collect_git_state(grant)

    assert state.dirty
    assert {
        (change.status, change.relative_path)
        for change in state.changed_files
    } == {
        (" M", "binary.bin"),
        ("A ", "added.txt"),
        ("D ", "removed.txt"),
        (" M", "tracked.txt"),
    }
    assert _git(root, "status", "--porcelain=v1", "-z") == before_status
    assert _git(root, "show-ref", "--head") == before_refs
    assert (root / ".git" / "index").read_bytes() == before_index