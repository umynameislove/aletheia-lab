"""Deterministic, visibility-safe collectors for imported project state."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.contracts import ProjectBundle, ProjectItem
from aletheia_lab.project.identity import (
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    PROJECT_ITEM_ID_PATTERN,
    SHA256_PATTERN,
    ProjectIdentityError,
    canonical_project_sha256,
    normalize_relative_project_path,
    normalize_text,
)
from aletheia_lab.project.importer import (
    GrantedProjectRoot,
    ProjectImportArtifact,
    _grant_is_current,
)

COLLECTION_SCHEMA_VERSION: Final[Literal["project-file-collection/v1"]] = (
    "project-file-collection/v1"
)
GIT_STATE_SCHEMA_VERSION: Final[Literal["project-git-state/v1"]] = "project-git-state/v1"
FILE_COLLECTOR_VERSION: Final[Literal["project-file-catalog/1.0.0"]] = (
    "project-file-catalog/1.0.0"
)
GIT_COLLECTOR_VERSION: Final[Literal["project-git-state/1.0.0"]] = (
    "project-git-state/1.0.0"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_GIT_SHA_PATTERN: Final[str] = r"^[0-9a-f]{40,64}$"
_STATUS_PATTERN: Final[str] = r"^(?:[ MADRCU?!]{2})$"


class ProjectCollectionError(ValueError):
    """Raised when source artifacts cannot produce a reconciled collection."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


class DatasetMetadata(_StrictFrozenModel):
    """Bounded dataset structure that intentionally contains no row values."""

    schema_version: Literal["project-dataset-metadata/v1"]
    format: Literal["csv", "tsv", "parquet"]
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: tuple[str, ...]
    types: tuple[str, ...] = ()
    row_group_count: int | None = Field(default=None, ge=0)

    @field_validator("columns", "types")
    @classmethod
    def _safe_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_text(value, label="dataset metadata label", max_length=512) for value in values)

    @model_validator(mode="after")
    def _shape_reconciles(self) -> DatasetMetadata:
        if self.column_count != len(self.columns):
            raise ValueError("dataset column_count does not match columns")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("dataset column labels must be unique")
        if self.types and len(self.types) != self.column_count:
            raise ValueError("dataset types do not match columns")
        if self.format == "parquet" and self.row_group_count is None:
            raise ValueError("Parquet metadata requires row_group_count")
        if self.format != "parquet" and self.row_group_count is not None:
            raise ValueError("row_group_count is only valid for Parquet")
        return self


class ProjectFileObservation(_StrictFrozenModel):
    """One content-bound file observation without raw source payloads."""

    project_item_id: str = Field(pattern=PROJECT_ITEM_ID_PATTERN)
    relative_path: str
    source_type: Literal["dataset", "metrics", "log", "config", "git", "artifact", "other"]
    source_sha256: Sha256
    artifact_sha256: Sha256
    artifact_media_type: str
    visibility: Literal["local_only", "diagnosis", "outbound"]
    redaction_state: Literal["none", "redacted", "withheld"]
    line_count: int | None = Field(default=None, ge=0)
    top_level_keys: tuple[str, ...] = ()
    dataset: DatasetMetadata | None = None
    untrusted_content: bool = True
    observation_sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def _path_is_canonical(cls, value: str) -> str:
        return normalize_relative_project_path(value)

    @field_validator("top_level_keys")
    @classmethod
    def _keys_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            normalize_text(value, label="structured top-level key", max_length=256)
            for value in values
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("structured top-level keys must be unique")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def _identity_reconciles(self) -> ProjectFileObservation:
        payload = self.model_dump(mode="json", exclude={"observation_sha256"})
        if self.observation_sha256 != canonical_project_sha256(payload):
            raise ValueError("observation_sha256 does not match file observation")
        if self.source_type == "dataset" and self.dataset is None:
            raise ValueError("dataset observations require metadata")
        if self.source_type != "dataset" and self.dataset is not None:
            raise ValueError("dataset metadata cannot be attached to another source type")
        return self


class ProjectFileCollection(_StrictFrozenModel):
    schema_version: Literal["project-file-collection/v1"] = COLLECTION_SCHEMA_VERSION
    collector_version: Literal["project-file-catalog/1.0.0"] = FILE_COLLECTOR_VERSION
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    project_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    observations: tuple[ProjectFileObservation, ...]
    collection_sha256: Sha256

    @field_validator("observations")
    @classmethod
    def _canonical_observations(
        cls, values: tuple[ProjectFileObservation, ...]
    ) -> tuple[ProjectFileObservation, ...]:
        paths = tuple(value.relative_path for value in values)
        if not values or len(paths) != len(set(paths)):
            raise ValueError("file collection requires unique non-empty observations")
        return tuple(sorted(values, key=lambda value: value.relative_path))

    @model_validator(mode="after")
    def _digest_reconciles(self) -> ProjectFileCollection:
        payload = self.model_dump(mode="json", exclude={"collection_sha256"})
        if self.collection_sha256 != canonical_project_sha256(payload):
            raise ValueError("collection_sha256 does not match file collection")
        return self


def _artifact_map(
    bundle: ProjectBundle, artifacts: tuple[ProjectImportArtifact, ...]
) -> dict[str, ProjectImportArtifact]:
    checked = _checked(bundle)
    by_path = {artifact.relative_path: artifact for artifact in artifacts}
    if len(by_path) != len(artifacts) or set(by_path) != {
        item.relative_path for item in checked.items
    }:
        raise ProjectCollectionError("artifacts must exactly reconcile with bundle items")
    for item in checked.items:
        artifact = by_path[item.relative_path]
        if artifact.reference != item.artifact:
            raise ProjectCollectionError("artifact reference does not match project item")
    return by_path


def _structured_keys(item: ProjectItem, content: bytes) -> tuple[str, ...]:
    if item.redaction_state == "withheld":
        return ()
    try:
        text = content.decode("utf-8", errors="strict")
        suffix = Path(item.relative_path).suffix.lower()
        parsed: object
        if suffix == ".json":
            parsed = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            parsed = yaml.safe_load(text)
        elif suffix == ".toml":
            parsed = tomllib.loads(text)
        else:
            return ()
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise ProjectCollectionError("validated structured artifact could not be decoded") from exc
    if not isinstance(parsed, dict):
        return ()
    keys = tuple(str(key) for key in parsed)
    return tuple(sorted(keys))


def _observation(item: ProjectItem, artifact: ProjectImportArtifact) -> ProjectFileObservation:
    dataset = None
    if item.source_type == "dataset":
        try:
            dataset = DatasetMetadata.model_validate_json(artifact.content)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ProjectCollectionError("dataset artifact is not valid metadata") from exc
    line_count = None
    if item.source_type in {"log", "other"} and item.redaction_state != "withheld":
        line_count = len(artifact.content.splitlines())
    provisional = {
        "project_item_id": item.project_item_id,
        "relative_path": item.relative_path,
        "source_type": item.source_type,
        "source_sha256": item.content_sha256,
        "artifact_sha256": item.artifact.sha256,
        "artifact_media_type": item.artifact.media_type,
        "visibility": item.visibility,
        "redaction_state": item.redaction_state,
        "line_count": line_count,
        "top_level_keys": _structured_keys(item, artifact.content),
        "dataset": None if dataset is None else dataset.model_dump(mode="json"),
        "untrusted_content": True,
    }
    return ProjectFileObservation(
        project_item_id=item.project_item_id,
        relative_path=item.relative_path,
        source_type=item.source_type,
        source_sha256=item.content_sha256,
        artifact_sha256=item.artifact.sha256,
        artifact_media_type=item.artifact.media_type,
        visibility=item.visibility,
        redaction_state=item.redaction_state,
        line_count=line_count,
        top_level_keys=_structured_keys(item, artifact.content),
        dataset=dataset,
        untrusted_content=True,
        observation_sha256=canonical_project_sha256(provisional),
    )


def collect_project_files(
    bundle: ProjectBundle, artifacts: tuple[ProjectImportArtifact, ...]
) -> ProjectFileCollection:
    """Build a deterministic catalog without copying source payloads."""

    checked = _checked(bundle)
    by_path = _artifact_map(checked, artifacts)
    observations = tuple(_observation(item, by_path[item.relative_path]) for item in checked.items)
    provisional = {
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "collector_version": FILE_COLLECTOR_VERSION,
        "project_id": checked.project_id,
        "project_bundle_id": checked.project_bundle_id,
        "observations": [value.model_dump(mode="json") for value in observations],
    }
    return ProjectFileCollection(
        project_id=checked.project_id,
        project_bundle_id=checked.project_bundle_id,
        observations=observations,
        collection_sha256=canonical_project_sha256(provisional),
    )


class GitChangedFile(_StrictFrozenModel):
    status: str = Field(pattern=_STATUS_PATTERN)
    relative_path: str
    previous_path: str | None = None

    @field_validator("relative_path", "previous_path")
    @classmethod
    def _canonical_path(cls, value: str | None) -> str | None:
        return None if value is None else normalize_relative_project_path(value)


class ProjectGitState(_StrictFrozenModel):
    schema_version: Literal["project-git-state/v1"] = GIT_STATE_SCHEMA_VERSION
    collector_version: Literal["project-git-state/1.0.0"] = GIT_COLLECTOR_VERSION
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    repository_state: Literal["unborn", "attached", "detached"]
    commit_sha: str | None = Field(default=None, pattern=_GIT_SHA_PATTERN)
    branch: str | None = Field(default=None, max_length=255)
    dirty: bool
    changed_files: tuple[GitChangedFile, ...]
    git_version: str = Field(min_length=1, max_length=128)
    state_sha256: Sha256

    @field_validator("branch", "git_version")
    @classmethod
    def _canonical_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return normalize_text(value, label=str(getattr(info, "field_name", "Git field")), max_length=255)

    @field_validator("changed_files")
    @classmethod
    def _canonical_changes(cls, values: tuple[GitChangedFile, ...]) -> tuple[GitChangedFile, ...]:
        keys = tuple((value.relative_path, value.previous_path, value.status) for value in values)
        if len(keys) != len(set(keys)):
            raise ValueError("Git changed-file records must be unique")
        return tuple(sorted(values, key=lambda value: (value.relative_path, value.previous_path or "", value.status)))

    @model_validator(mode="after")
    def _state_reconciles(self) -> ProjectGitState:
        if self.repository_state == "unborn" and (self.commit_sha is not None or self.branch is None):
            raise ValueError("unborn Git state requires a branch and no commit")
        if self.repository_state == "attached" and (self.commit_sha is None or self.branch is None):
            raise ValueError("attached Git state requires branch and commit")
        if self.repository_state == "detached" and (self.commit_sha is None or self.branch is not None):
            raise ValueError("detached Git state requires commit without branch")
        if self.dirty != bool(self.changed_files):
            raise ValueError("Git dirty flag must match changed files")
        payload = self.model_dump(mode="json", exclude={"state_sha256"})
        if self.state_sha256 != canonical_project_sha256(payload):
            raise ValueError("state_sha256 does not match Git state")
        return self


@dataclass(frozen=True, slots=True)
class _GitCommand:
    arguments: tuple[str, ...]
    allow_failure: bool = False


def _run_git(root: Path, command: _GitCommand) -> bytes | None:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }
    process = subprocess.run(
        ("git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false", *command.arguments),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if process.returncode != 0:
        if command.allow_failure:
            return None
        raise ProjectCollectionError("read-only Git collection failed")
    return process.stdout


def _decode_git(value: bytes, *, label: str, trim: bool = True) -> str:
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProjectCollectionError(f"{label} is not valid UTF-8") from exc
    candidate = decoded.strip() if trim else decoded
    try:
        return normalize_text(candidate, label=label, max_length=4096)
    except ProjectIdentityError as exc:
        raise ProjectCollectionError(f"{label} is not a canonical public value") from exc


def _changed_files(raw: bytes) -> tuple[GitChangedFile, ...]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[GitChangedFile] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        if len(entry) < 4 or entry[2:3] != b" ":
            raise ProjectCollectionError("Git status output is malformed")
        status = entry[:2].decode("ascii", errors="strict")
        path = _decode_git(entry[3:], label="Git changed path", trim=False)
        previous = None
        if "R" in status or "C" in status:
            index += 1
            if index >= len(fields):
                raise ProjectCollectionError("Git rename status is incomplete")
            previous = _decode_git(fields[index], label="Git previous path", trim=False)
        changes.append(GitChangedFile(status=status, relative_path=path, previous_path=previous))
        index += 1
    return tuple(changes)


def collect_git_state(grant: GrantedProjectRoot) -> ProjectGitState:
    """Collect local Git identity and worktree status without remote or mutating commands."""

    if not isinstance(grant, GrantedProjectRoot) or not _grant_is_current(grant):
        raise ProjectCollectionError("granted project root is no longer current")
    root = grant._path
    top = _run_git(root, _GitCommand(("rev-parse", "--show-toplevel"), allow_failure=True))
    if top is None or os.path.normcase(os.path.realpath(_decode_git(top, label="Git root"))) != os.path.normcase(os.path.realpath(root)):
        raise ProjectCollectionError("granted project root is not an exact Git worktree root")
    commit_raw = _run_git(root, _GitCommand(("rev-parse", "--verify", "HEAD"), allow_failure=True))
    branch_raw = _run_git(root, _GitCommand(("symbolic-ref", "--quiet", "--short", "HEAD"), allow_failure=True))
    branch = None if branch_raw is None else _decode_git(branch_raw, label="Git branch")
    commit = None if commit_raw is None else _decode_git(commit_raw, label="Git commit")
    if commit is None:
        state: Literal["unborn", "attached", "detached"] = "unborn"
        if branch is None:
            raise ProjectCollectionError("unborn Git repository has no symbolic branch")
    elif branch is None:
        state = "detached"
    else:
        state = "attached"
    status_raw = _run_git(root, _GitCommand(("status", "--porcelain=v1", "-z", "--untracked-files=all")))
    assert status_raw is not None
    changes = _changed_files(status_raw)
    version_raw = _run_git(root, _GitCommand(("--version",)))
    assert version_raw is not None
    provisional = {
        "schema_version": GIT_STATE_SCHEMA_VERSION,
        "collector_version": GIT_COLLECTOR_VERSION,
        "project_id": grant.project_id,
        "repository_state": state,
        "commit_sha": commit,
        "branch": branch,
        "dirty": bool(changes),
        "changed_files": [value.model_dump(mode="json") for value in changes],
        "git_version": _decode_git(version_raw, label="Git version"),
    }
    return ProjectGitState(
        project_id=grant.project_id,
        repository_state=state,
        commit_sha=commit,
        branch=branch,
        dirty=bool(changes),
        changed_files=changes,
        git_version=_decode_git(version_raw, label="Git version"),
        state_sha256=canonical_project_sha256(provisional),
    )
