"""Fail-closed, read-only boundary for importing a local project.

The importer treats every project byte as untrusted data.  It does not import
Python modules, execute notebooks, invoke a shell, call a provider, open a
network connection or write into the granted source tree.  Bundle assembly is
an in-memory atomic transaction: any blocker returns a reconciled preview but
no bundle, artifact payload, snapshot reference or evidence reference.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, NoReturn, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml
from pydantic import ValidationError

from aletheia_lab.project.contracts import (
    ImmutableArtifactReference,
    ProjectBundle,
    ProjectCollector,
    ProjectItem,
    ProjectParseWarning,
    ProjectSourceType,
    build_project_bundle,
    build_project_item,
    verify_project_item_artifact,
)
from aletheia_lab.project.identity import (
    ProjectIdentityError,
    canonical_project_json,
    canonical_project_sha256,
    content_sha256,
    granted_root_fingerprint,
    normalize_relative_project_path,
    normalize_text,
    project_id_for_root,
)
from aletheia_lab.project.import_policy import (
    ProjectDecisionReason,
    ProjectImportDecision,
    ProjectImportPolicy,
    ProjectImportPreview,
    ProjectIssueCode,
    ProjectValidationIssue,
    build_project_import_preview,
)

ProjectImportStatus = Literal["imported", "imported_with_restrictions", "blocked"]

_COLLECTOR: Final[ProjectCollector] = ProjectCollector(
    name="secure-local-import",
    version="1.0.0",
)
_READ_CHUNK: Final[int] = 1 << 20
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|"
    r"treat\s+this\s+.*system\s+message|run\s+this\s+shell\s+command|"
    r"read\s+files\s+outside\s+the\s+project|upload\s+the\s+following\s+token)"
)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)\+[1-9](?:[ -]?\d){7,14}(?!\d)")
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@[^\s]+", re.IGNORECASE),
    re.compile(
        r"(?im)\b(?:api[_-]?key|password|private[_-]?key|secret|token)\s*[:=]\s*"
        r"[\"']?[^\s\"']{8,}"
    ),
)


@dataclass(frozen=True, slots=True, init=False)
class GrantedProjectRoot:
    """Opaque local capability created only after canonical root validation.

    The absolute path and filesystem identity remain private and are never
    copied into a bundle, preview, issue or ingestion report.
    """

    root_fingerprint: str
    project_id: str
    _path: Path = field(repr=False)
    _canonical_identity: str = field(repr=False)
    _device: int = field(repr=False)
    _inode: int = field(repr=False)

    @classmethod
    def _validated(
        cls,
        *,
        path: Path,
        canonical_identity: str,
        device: int,
        inode: int,
    ) -> GrantedProjectRoot:
        instance = object.__new__(cls)
        fingerprint = granted_root_fingerprint(canonical_identity)
        object.__setattr__(instance, "root_fingerprint", fingerprint)
        object.__setattr__(instance, "project_id", project_id_for_root(fingerprint))
        object.__setattr__(instance, "_path", path)
        object.__setattr__(instance, "_canonical_identity", canonical_identity)
        object.__setattr__(instance, "_device", device)
        object.__setattr__(instance, "_inode", inode)
        return instance


class ProjectImportBoundaryError(ValueError):
    """Authorization error carrying one stable issue without exposing a path."""

    def __init__(self, issue: ProjectValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass(frozen=True, slots=True)
class ProjectImportArtifact:
    """Diagnosis-safe bytes paired with their immutable reference."""

    relative_path: str
    reference: ImmutableArtifactReference
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        normalize_relative_project_path(self.relative_path)
        if not isinstance(self.content, bytes):
            raise TypeError("import artifact content must be bytes")
        if self.reference.sha256 != content_sha256(self.content):
            raise ValueError("import artifact content does not match its SHA-256")
        if self.reference.byte_size != len(self.content):
            raise ValueError("import artifact content does not match its byte size")


@dataclass(frozen=True, slots=True)
class ProjectImportResult:
    """Terminal, atomic result of one local-only import transaction."""

    status: ProjectImportStatus
    preview: ProjectImportPreview
    validation_summary_sha256: str
    ingestion_report_sha256: str
    bundle: ProjectBundle | None
    artifacts: tuple[ProjectImportArtifact, ...]

    def __post_init__(self) -> None:
        if self.validation_summary_sha256 != self.preview.preview_sha256:
            raise ValueError("validation summary must be the canonical preview digest")
        blocker_exists = any(issue.severity == "blocker" for issue in self.preview.issues)
        if self.status == "blocked":
            if not blocker_exists or self.bundle is not None or self.artifacts:
                raise ValueError("blocked imports must fail atomically without artifacts")
            return
        if blocker_exists or self.bundle is None:
            raise ValueError("successful imports require a bundle and no blocking issue")
        if self.bundle.snapshot_refs or self.bundle.evidence_bundle_refs:
            raise ValueError("project import cannot mint snapshot or evidence references")
        if self.bundle.validation_summary_sha256 != self.validation_summary_sha256:
            raise ValueError("bundle validation summary does not match the import result")
        if self.bundle.ingestion_report_sha256 != self.ingestion_report_sha256:
            raise ValueError("bundle ingestion report does not match the import result")
        item_by_path = {item.relative_path: item for item in self.bundle.items}
        if set(item_by_path) != {artifact.relative_path for artifact in self.artifacts}:
            raise ValueError("import artifacts must exactly reconcile with bundle items")
        for artifact in self.artifacts:
            item = item_by_path[artifact.relative_path]
            if item.artifact != artifact.reference:
                raise ValueError("import artifact reference does not match its project item")
            verify_project_item_artifact(item, artifact.content)


@dataclass(frozen=True, slots=True)
class _FileProfile:
    source_type: ProjectSourceType
    media_type: str
    source_schema: str
    binary: bool = False


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path = field(repr=False)
    relative_path: str
    stat_result: os.stat_result = field(repr=False)
    profile: _FileProfile


@dataclass(frozen=True, slots=True)
class _DirectoryObservation:
    path: Path = field(repr=False)
    stat_result: os.stat_result = field(repr=False)


@dataclass(frozen=True, slots=True)
class _PreparedItem:
    item: ProjectItem
    artifact: ProjectImportArtifact
    decision: ProjectImportDecision
    issues: tuple[ProjectValidationIssue, ...]
    final_stat: os.stat_result = field(repr=False)


class _ContentFailure(ValueError):
    def __init__(self, code: ProjectIssueCode) -> None:
        super().__init__(code)
        self.code = code


_PROFILES: Final[dict[str, _FileProfile]] = {
    ".cfg": _FileProfile("config", "text/plain", "untrusted-config-text/v1"),
    ".conf": _FileProfile("config", "text/plain", "untrusted-config-text/v1"),
    ".csv": _FileProfile("dataset", "text/csv", "untrusted-csv/v1"),
    ".ini": _FileProfile("config", "text/plain", "untrusted-ini/v1"),
    ".ipynb": _FileProfile("other", "application/x-ipynb+json", "untrusted-notebook-json/v1"),
    ".json": _FileProfile("config", "application/json", "untrusted-json/v1"),
    ".log": _FileProfile("log", "text/plain", "untrusted-log-text/v1"),
    ".md": _FileProfile("other", "text/markdown", "untrusted-markdown/v1"),
    ".parquet": _FileProfile(
        "dataset", "application/vnd.apache.parquet", "parquet-metadata/v1", binary=True
    ),
    ".py": _FileProfile("other", "text/x-python", "untrusted-python-text/v1"),
    ".toml": _FileProfile("config", "application/toml", "untrusted-toml/v1"),
    ".tsv": _FileProfile("dataset", "text/tab-separated-values", "untrusted-tsv/v1"),
    ".txt": _FileProfile("other", "text/plain", "untrusted-text/v1"),
    ".yaml": _FileProfile("config", "application/yaml", "untrusted-yaml/v1"),
    ".yml": _FileProfile("config", "application/yaml", "untrusted-yaml/v1"),
}
_EXACT_NAME_PROFILE: Final[_FileProfile] = _FileProfile(
    "other", "text/plain", "untrusted-project-text/v1"
)
_METRIC_PROFILES: Final[dict[str, _FileProfile]] = {
    ".csv": _FileProfile("metrics", "text/csv", "untrusted-metrics-csv/v1"),
    ".json": _FileProfile("metrics", "application/json", "untrusted-metrics-json/v1"),
}
_METRIC_NAMES: Final[frozenset[str]] = frozenset(
    {"metric.csv", "metric.json", "metrics.csv", "metrics.json"}
)


def _subject_sha256(value: str | bytes) -> str:
    content = value.encode("utf-8", errors="surrogatepass") if isinstance(value, str) else value
    return hashlib.sha256(content).hexdigest()


def _issue(
    code: ProjectIssueCode,
    *,
    subject: str | bytes,
    relative_path: str | None = None,
    occurrences: int = 1,
) -> ProjectValidationIssue:
    return ProjectValidationIssue.create(
        code,
        subject_sha256=_subject_sha256(subject),
        relative_path=relative_path,
        occurrences=occurrences,
    )


def _raise_root_issue(code: ProjectIssueCode, root: str | Path) -> NoReturn:
    raise ProjectImportBoundaryError(_issue(code, subject=os.fspath(root)))


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    file_attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse_flag and file_attributes & reparse_flag)


def _canonical_root_identity(path: Path) -> str:
    real = os.path.realpath(os.fspath(path))
    canonical = unicodedata.normalize("NFC", os.path.normcase(real))
    normalize_text(canonical, label="canonical granted root", max_length=4096)
    return f"{os.name}:{canonical}"


def grant_project_root(root: str | Path) -> GrantedProjectRoot:
    """Validate an explicit root and return a local, non-serializable capability."""

    path = Path(root)
    if not path.is_absolute():
        _raise_root_issue("root_not_absolute", root)
    try:
        inspected = path.lstat()
    except (OSError, ValueError):
        _raise_root_issue("root_unavailable", root)
    if stat.S_ISLNK(inspected.st_mode) or _is_reparse_point(inspected):
        _raise_root_issue("root_link_not_allowed", root)
    if not stat.S_ISDIR(inspected.st_mode):
        _raise_root_issue("root_not_directory", root)
    try:
        resolved = path.resolve(strict=True)
        resolved_stat = resolved.stat()
        identity = _canonical_root_identity(resolved)
    except (OSError, ValueError, ProjectIdentityError):
        _raise_root_issue("root_unavailable", root)
    if not stat.S_ISDIR(resolved_stat.st_mode):
        _raise_root_issue("root_not_directory", root)
    return GrantedProjectRoot._validated(
        path=resolved,
        canonical_identity=identity,
        device=int(resolved_stat.st_dev),
        inode=int(resolved_stat.st_ino),
    )


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def _directory_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_mtime_ns),
    )


def _grant_is_current(grant: GrantedProjectRoot) -> bool:
    try:
        inspected = grant._path.lstat()
        identity = _canonical_root_identity(grant._path.resolve(strict=True))
    except (OSError, ValueError, ProjectIdentityError):
        return False
    return (
        stat.S_ISDIR(inspected.st_mode)
        and not stat.S_ISLNK(inspected.st_mode)
        and not _is_reparse_point(inspected)
        and int(inspected.st_dev) == grant._device
        and int(inspected.st_ino) == grant._inode
        and identity == grant._canonical_identity
        and granted_root_fingerprint(identity) == grant.root_fingerprint
        and project_id_for_root(grant.root_fingerprint) == grant.project_id
    )


def _contained(root: Path, candidate: Path) -> bool:
    try:
        root_text = os.path.normcase(os.path.realpath(os.fspath(root)))
        candidate_text = os.path.normcase(os.path.realpath(os.fspath(candidate)))
        return os.path.commonpath((root_text, candidate_text)) == root_text
    except (OSError, ValueError):
        return False


def _decision_without_content(
    relative_path: str,
    *,
    action: Literal["exclude", "block"],
    reason: ProjectDecisionReason,
) -> ProjectImportDecision:
    return ProjectImportDecision(
        relative_path=relative_path,
        action=action,
        reason_code=reason,
        subject_sha256=_subject_sha256(relative_path),
    )


def _profile_for(path: Path, policy: ProjectImportPolicy) -> _FileProfile | None:
    if path.name in policy.allowed_exact_names:
        return _EXACT_NAME_PROFILE
    extension = path.suffix.lower()
    if extension not in policy.allowed_extensions:
        return None
    if path.name.lower() in _METRIC_NAMES:
        return _METRIC_PROFILES[extension]
    return _PROFILES.get(extension)


def _discover(
    grant: GrantedProjectRoot,
    policy: ProjectImportPolicy,
) -> tuple[
    tuple[_Candidate, ...],
    tuple[_DirectoryObservation, ...],
    tuple[ProjectImportDecision, ...],
    tuple[ProjectValidationIssue, ...],
]:
    candidates: list[_Candidate] = []
    directories: list[_DirectoryObservation] = []
    decisions: list[ProjectImportDecision] = []
    issues: list[ProjectValidationIssue] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    visited_entries = 0
    stack: list[tuple[Path, tuple[str, ...]]] = [(grant._path, ())]

    while stack:
        directory, parent_parts = stack.pop()
        try:
            directory_stat = directory.lstat()
        except OSError:
            relative = "/".join(parent_parts) if parent_parts else None
            issue = _issue(
                "source_read_failed",
                subject=relative or grant.root_fingerprint,
                relative_path=relative,
            )
            issues.append(issue)
            if relative is not None:
                decisions.append(
                    _decision_without_content(relative, action="block", reason="source_read_failed")
                )
            continue
        directories.append(_DirectoryObservation(path=directory, stat_result=directory_stat))
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: os.fsencode(item.name))
        except OSError:
            relative = "/".join(parent_parts) if parent_parts else None
            issue = _issue(
                "source_read_failed",
                subject=relative or grant.root_fingerprint,
                relative_path=relative,
            )
            issues.append(issue)
            if relative is not None:
                decisions.append(
                    _decision_without_content(relative, action="block", reason="source_read_failed")
                )
            continue

        child_directories: list[tuple[Path, tuple[str, ...]]] = []
        for entry in entries:
            visited_entries += 1
            raw_parts = (*parent_parts, entry.name)
            raw_relative = "/".join(raw_parts)
            try:
                raw_relative.encode("utf-8", errors="strict")
                relative = normalize_relative_project_path(
                    unicodedata.normalize("NFC", raw_relative)
                )
            except (ProjectIdentityError, UnicodeEncodeError):
                issues.append(_issue("path_invalid", subject=raw_relative))
                continue
            if _path_contains_sensitive_content(relative):
                issues.append(_issue("path_invalid", subject=relative))
                continue
            if visited_entries > policy.max_discovered_entries:
                issues.append(
                    _issue("item_limit_exceeded", subject=grant.root_fingerprint)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="item_limit_exceeded"
                    )
                )
                return tuple(candidates), tuple(directories), tuple(decisions), tuple(issues)
            if relative in seen_paths:
                issues.append(_issue("path_invalid", subject=relative, relative_path=relative))
                continue
            seen_paths.add(relative)
            if len(raw_parts) > policy.max_path_depth:
                issues.append(
                    _issue("path_depth_exceeded", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="path_depth_exceeded"
                    )
                )
                continue

            candidate_path = Path(entry.path)
            try:
                # CPython 3.11 on Windows can return zero ``st_dev`` and
                # ``st_ino`` from ``DirEntry.stat(follow_symlinks=False)``
                # while ``Path.lstat`` and ``os.fstat`` expose the real file
                # identity.  Capture discovery identity through the same
                # path-based API used after the bounded read so a stable file
                # is not falsely classified as a source race.  The later
                # lstat/fstat/lstat comparisons still fail closed on replacement.
                inspected = candidate_path.lstat()
            except OSError:
                issues.append(
                    _issue("source_read_failed", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(relative, action="block", reason="source_read_failed")
                )
                continue
            if stat.S_ISLNK(inspected.st_mode):
                issues.append(_issue("link_not_allowed", subject=relative, relative_path=relative))
                decisions.append(
                    _decision_without_content(relative, action="block", reason="link_not_allowed")
                )
                continue
            if _is_reparse_point(inspected):
                issues.append(
                    _issue("reparse_point_not_allowed", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="reparse_point_not_allowed"
                    )
                )
                continue
            if not _contained(grant._path, candidate_path):
                issues.append(
                    _issue("path_outside_root", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="path_outside_root"
                    )
                )
                continue
            hidden = any(part.startswith(".") for part in raw_parts)
            if entry.name in policy.excluded_directory_names and stat.S_ISDIR(inspected.st_mode):
                decisions.append(
                    _decision_without_content(
                        relative, action="exclude", reason="directory_excluded"
                    )
                )
                continue
            if hidden and not policy.include_hidden_paths:
                decisions.append(
                    _decision_without_content(
                        relative, action="exclude", reason="hidden_path_excluded"
                    )
                )
                continue
            if stat.S_ISDIR(inspected.st_mode):
                child_directories.append((candidate_path, raw_parts))
                continue
            if not stat.S_ISREG(inspected.st_mode):
                issues.append(
                    _issue("special_file_not_allowed", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="special_file_not_allowed"
                    )
                )
                continue
            if int(inspected.st_nlink) > 1:
                issues.append(
                    _issue("hardlink_not_allowed", subject=relative, relative_path=relative)
                )
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="hardlink_not_allowed"
                    )
                )
                continue
            profile = _profile_for(candidate_path, policy)
            if profile is None:
                decisions.append(
                    _decision_without_content(
                        relative, action="exclude", reason="file_type_not_allowed"
                    )
                )
                continue
            if inspected.st_size > policy.max_item_bytes:
                issues.append(_issue("item_too_large", subject=relative, relative_path=relative))
                decisions.append(
                    _decision_without_content(relative, action="block", reason="item_too_large")
                )
                continue
            if len(candidates) >= policy.max_items:
                issues.append(_issue("item_limit_exceeded", subject=grant.root_fingerprint))
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="item_limit_exceeded"
                    )
                )
                return tuple(candidates), tuple(directories), tuple(decisions), tuple(issues)
            total_bytes += int(inspected.st_size)
            if total_bytes > policy.max_total_bytes:
                issues.append(_issue("total_size_exceeded", subject=grant.root_fingerprint))
                decisions.append(
                    _decision_without_content(
                        relative, action="block", reason="total_size_exceeded"
                    )
                )
                continue
            candidates.append(
                _Candidate(
                    path=candidate_path,
                    relative_path=relative,
                    stat_result=inspected,
                    profile=profile,
                )
            )
        stack.extend(reversed(child_directories))
    return (
        tuple(sorted(candidates, key=lambda item: item.relative_path)),
        tuple(directories),
        tuple(decisions),
        tuple(issues),
    )


def _bounded_read(candidate: _Candidate, policy: ProjectImportPolicy) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate.path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _ContentFailure("source_changed_during_read")
        if _stat_signature(opened) != _stat_signature(candidate.stat_result):
            raise _ContentFailure("source_changed_during_read")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK, policy.max_item_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > policy.max_item_bytes:
                raise _ContentFailure("item_too_large")
        completed = os.fstat(descriptor)
        if _stat_signature(completed) != _stat_signature(opened):
            raise _ContentFailure("source_changed_during_read")
        after_path = candidate.path.lstat()
        if _stat_signature(after_path) != _stat_signature(opened):
            raise _ContentFailure("source_changed_during_read")
        return b"".join(chunks), completed
    except _ContentFailure:
        raise
    except OSError as exc:
        raise _ContentFailure("source_read_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _ContentFailure("duplicate_structured_key")
        result[key] = value
    return result


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise _ContentFailure("non_finite_number")
    if isinstance(value, dict):
        for key, nested in value.items():
            _reject_non_finite(key)
            _reject_non_finite(nested)
    elif isinstance(value, list | tuple | set):
        for nested in value:
            _reject_non_finite(nested)


def _reject_yaml_duplicate_keys(node: yaml.Node) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key_identity = yaml.serialize(key_node)
            if key_identity in seen:
                raise _ContentFailure("duplicate_structured_key")
            seen.add(key_identity)
            _reject_yaml_duplicate_keys(key_node)
            _reject_yaml_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for nested in node.value:
            _reject_yaml_duplicate_keys(nested)


def _validate_structured_content(path: str, text: str) -> None:
    extension = Path(path).suffix.lower()
    try:
        parsed: object
        if extension in {".json", ".ipynb"}:
            parsed = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    _ContentFailure("non_finite_number")
                ),
            )
            if extension == ".ipynb" and (
                not isinstance(parsed, dict) or not isinstance(parsed.get("cells"), list)
            ):
                raise _ContentFailure("structured_content_invalid")
        elif extension in {".yaml", ".yml"}:
            node = yaml.compose(text, Loader=yaml.SafeLoader)
            if node is not None:
                _reject_yaml_duplicate_keys(node)
            parsed = yaml.safe_load(text)
        elif extension == ".toml":
            parsed = tomllib.loads(text)
        elif extension in {".csv", ".tsv"}:
            delimiter = "," if extension == ".csv" else "\t"
            rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            header = next(rows, None)
            if header is not None and len(header) != len(set(header)):
                raise _ContentFailure("duplicate_structured_key")
            if header is not None and any(not column.strip() for column in header):
                raise _ContentFailure("structured_content_invalid")
            expected_width = None if header is None else len(header)
            for row in rows:
                if expected_width is not None and len(row) != expected_width:
                    raise _ContentFailure("structured_content_invalid")
            parsed = None
        else:
            return
        _reject_non_finite(parsed)
    except _ContentFailure:
        raise
    except (csv.Error, json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise _ContentFailure("structured_content_invalid") from exc


def _decode_and_validate(
    candidate: _Candidate,
    source_bytes: bytes,
    policy: ProjectImportPolicy,
) -> str:
    try:
        text = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _ContentFailure("invalid_utf8") from exc
    if _CONTROL_CHARACTER.search(text):
        raise _ContentFailure("unsafe_control_character")
    if any(len(line.encode("utf-8")) > policy.max_line_bytes for line in text.splitlines()):
        raise _ContentFailure("line_too_long")
    _validate_structured_content(candidate.relative_path, text)
    return text


def _safe_field_name(value: str, *, index: int) -> str:
    """Keep schema labels useful without leaking embedded credentials or PII."""

    _, pii_count = _redact_pii(value)
    if _secret_occurrences(value) or pii_count:
        return f"field_{index + 1}_redacted"
    return value


def _csv_metadata(text: str, *, delimiter: str) -> dict[str, object]:
    rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    header = next(rows, None)
    if header is None:
        return {"column_count": 0, "columns": [], "format": "csv", "row_count": 0}
    row_count = sum(1 for _ in rows)
    return {
        "column_count": len(header),
        "columns": [_safe_field_name(column, index=index) for index, column in enumerate(header)],
        "format": "tsv" if delimiter == "\t" else "csv",
        "row_count": row_count,
    }


def _parquet_metadata(source_bytes: bytes) -> dict[str, object]:
    try:
        parquet = pq.ParquetFile(pa.BufferReader(source_bytes))
        metadata = parquet.metadata
        arrow_schema = parquet.schema_arrow
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise _ContentFailure("structured_content_invalid") from exc
    return {
        "column_count": len(arrow_schema),
        "columns": [
            _safe_field_name(field.name, index=index) for index, field in enumerate(arrow_schema)
        ],
        "format": "parquet",
        "row_count": metadata.num_rows,
        "row_group_count": metadata.num_row_groups,
        "types": [str(field.type) for field in arrow_schema],
    }


def _dataset_metadata_bytes(
    candidate: _Candidate, source_bytes: bytes, text: str | None
) -> bytes:
    if candidate.profile.binary:
        metadata = _parquet_metadata(source_bytes)
    else:
        assert text is not None
        delimiter = "\t" if Path(candidate.relative_path).suffix.lower() == ".tsv" else ","
        metadata = _csv_metadata(text, delimiter=delimiter)
    payload = {
        "schema_version": "project-dataset-metadata/v1",
        **metadata,
    }
    return (canonical_project_json(payload) + "\n").encode("utf-8")


def _secret_occurrences(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in _SECRET_PATTERNS)


def _redact_pii(text: str) -> tuple[str, int]:
    email_count = len(_EMAIL.findall(text))
    phone_count = len(_PHONE.findall(text))
    redacted = _EMAIL.sub("[REDACTED:pii.email]", text)
    redacted = _PHONE.sub("[REDACTED:pii.phone]", redacted)
    return redacted, email_count + phone_count


def _path_contains_sensitive_content(value: str) -> bool:
    return bool(
        _EMAIL.search(value)
        or _PHONE.search(value)
        or any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    )


def _source_modified_at(stat_result: os.stat_result) -> str:
    stamp = datetime.fromtimestamp(stat_result.st_mtime_ns / 1_000_000_000, tz=UTC)
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _prepare_item(
    grant: GrantedProjectRoot,
    candidate: _Candidate,
    policy: ProjectImportPolicy,
    *,
    ingested_at: str,
) -> _PreparedItem:
    try:
        source_bytes, final_stat = _bounded_read(candidate, policy)
        text = None if candidate.profile.binary else _decode_and_validate(
            candidate, source_bytes, policy
        )
    except _ContentFailure as exc:
        raise exc

    issues: list[ProjectValidationIssue] = []
    warnings: list[ProjectParseWarning] = []
    redaction_state: Literal["none", "redacted", "withheld"] = "none"
    redaction_reasons: tuple[str, ...] = ()
    visibility: Literal["local_only", "diagnosis"] = "diagnosis"
    artifact_text = "" if text is None else text
    reason: ProjectDecisionReason = "included"
    action: Literal["include", "redact", "withhold"] = "include"

    secret_count = 0 if text is None else _secret_occurrences(text)
    if secret_count:
        issues.append(
            _issue(
                "secret_withheld",
                subject=source_bytes,
                relative_path=candidate.relative_path,
                occurrences=secret_count,
            )
        )
        redaction_state = "withheld"
        redaction_reasons = ("secret.high_risk",)
        visibility = "local_only"
        artifact_text = "[WITHHELD:secret.high_risk]\n"
        reason = "secret_withheld"
        action = "withhold"
    else:
        artifact_text, pii_count = _redact_pii(artifact_text)
        if pii_count:
            issues.append(
                _issue(
                    "pii_redacted",
                    subject=source_bytes,
                    relative_path=candidate.relative_path,
                    occurrences=pii_count,
                )
            )
            redaction_state = "redacted"
            redaction_reasons = ("pii.detected",)
            reason = "pii_redacted"
            action = "redact"

    if text is not None and _PROMPT_INJECTION.search(text):
        issues.append(
            _issue(
                "untrusted_instruction_text",
                subject=source_bytes,
                relative_path=candidate.relative_path,
            )
        )
        warnings.append(
            ProjectParseWarning(
                code="untrusted_instruction",
                message="Instruction-like text is retained only as untrusted project data",
            )
        )

    artifact_bytes = artifact_text.encode("utf-8")
    artifact_media_type = (
        "text/plain" if redaction_state in {"redacted", "withheld"} else candidate.profile.media_type
    )
    if candidate.profile.source_type == "dataset":
        artifact_bytes = _dataset_metadata_bytes(candidate, source_bytes, text)
        artifact_media_type = "application/json"
        redaction_state = "redacted"
        redaction_reasons = ("dataset.raw_rows_withheld",)
        visibility = "diagnosis"
        reason = "dataset_rows_withheld"
        action = "redact"
        issues.append(
            _issue(
                "dataset_rows_withheld",
                subject=source_bytes,
                relative_path=candidate.relative_path,
            )
        )
    item = build_project_item(
        project_id=grant.project_id,
        relative_path=candidate.relative_path,
        source_type=candidate.profile.source_type,
        media_type=candidate.profile.media_type,
        source_schema=candidate.profile.source_schema,
        source_bytes=source_bytes,
        source_modified_at=_source_modified_at(final_stat),
        ingested_at=ingested_at,
        collector=_COLLECTOR,
        visibility=visibility,
        redaction_state=redaction_state,
        redaction_reasons=redaction_reasons,
        parse_status="parsed",
        parse_warnings=tuple(warnings),
        artifact_bytes=artifact_bytes,
        artifact_media_type=artifact_media_type,
    )
    artifact = ProjectImportArtifact(
        relative_path=candidate.relative_path,
        reference=item.artifact,
        content=artifact_bytes,
    )
    decision = ProjectImportDecision(
        relative_path=candidate.relative_path,
        action=action,
        reason_code=reason,
        subject_sha256=_subject_sha256(candidate.relative_path),
        source_type=candidate.profile.source_type,
        media_type=candidate.profile.media_type,
        source_byte_size=len(source_bytes),
        content_sha256=item.content_sha256,
        artifact_sha256=item.artifact.sha256,
    )
    return _PreparedItem(
        item=item,
        artifact=artifact,
        decision=decision,
        issues=tuple(issues),
        final_stat=final_stat,
    )


def _observations_are_current(
    candidates: tuple[_Candidate, ...],
    prepared: tuple[_PreparedItem, ...],
    directories: tuple[_DirectoryObservation, ...],
) -> bool:
    if len(candidates) != len(prepared):
        return False
    for candidate, ready in zip(candidates, prepared, strict=True):
        try:
            current = candidate.path.lstat()
        except OSError:
            return False
        if _stat_signature(current) != _stat_signature(ready.final_stat):
            return False
    for observation in directories:
        try:
            current = observation.path.lstat()
        except OSError:
            return False
        if _directory_signature(current) != _directory_signature(observation.stat_result):
            return False
    return True


def _report_sha256(
    *,
    status: ProjectImportStatus,
    preview: ProjectImportPreview,
    items: tuple[ProjectItem, ...],
    artifacts: tuple[ProjectImportArtifact, ...],
) -> str:
    return canonical_project_sha256(
        {
            "schema_version": "project-ingestion-report/v1",
            "status": status,
            "preview_sha256": preview.preview_sha256,
            "project_item_ids": [item.project_item_id for item in items],
            "artifact_ids": [artifact.reference.artifact_id for artifact in artifacts],
        }
    )


def _canonical_ingested_at(value: str) -> str:
    normalize_text(value, label="ingested_at", max_length=32)
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
        value,
    ) is None:
        raise ValueError("ingested_at must be a canonical UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("ingested_at must be a valid UTC timestamp") from exc
    return value


def _blocked_result(
    grant: GrantedProjectRoot,
    policy: ProjectImportPolicy,
    decisions: tuple[ProjectImportDecision, ...],
    issues: tuple[ProjectValidationIssue, ...],
) -> ProjectImportResult:
    if not any(issue.code == "atomic_import_aborted" for issue in issues):
        issues = (
            *issues,
            _issue("atomic_import_aborted", subject=grant.root_fingerprint),
        )
    preview = build_project_import_preview(
        project_id=grant.project_id,
        root_fingerprint=grant.root_fingerprint,
        policy_sha256=policy.canonical_sha256(),
        decisions=decisions,
        issues=issues,
    )
    report = _report_sha256(status="blocked", preview=preview, items=(), artifacts=())
    return ProjectImportResult(
        status="blocked",
        preview=preview,
        validation_summary_sha256=preview.preview_sha256,
        ingestion_report_sha256=report,
        bundle=None,
        artifacts=(),
    )


def import_local_project(
    grant: GrantedProjectRoot,
    *,
    display_name: str,
    ingested_at: str,
    policy: ProjectImportPolicy | None = None,
) -> ProjectImportResult:
    """Read and atomically assemble one diagnosis-safe local ProjectBundle.

    Unsupported files are explicitly excluded.  Unsafe paths, source races,
    malformed allowed content and resource-limit violations are blockers.  A
    handled secret is retained only as a local-only withheld item; PII is
    redacted before artifact bytes become diagnosis-visible.
    """

    if not isinstance(grant, GrantedProjectRoot):
        raise TypeError("import_local_project requires a GrantedProjectRoot")
    selected_policy = ProjectImportPolicy() if policy is None else ProjectImportPolicy.model_validate(
        policy.model_dump(mode="python")
    )
    normalized_time = _canonical_ingested_at(ingested_at)
    normalize_text(display_name, label="project display name", max_length=128)
    if not _grant_is_current(grant):
        issue = _issue("root_changed", subject=grant.root_fingerprint)
        return _blocked_result(grant, selected_policy, (), (issue,))

    candidates, directories, discovery_decisions, discovery_issues = _discover(
        grant, selected_policy
    )
    if any(issue.severity == "blocker" for issue in discovery_issues):
        return _blocked_result(
            grant,
            selected_policy,
            discovery_decisions,
            discovery_issues,
        )

    prepared: list[_PreparedItem] = []
    decisions = list(discovery_decisions)
    issues = list(discovery_issues)
    for candidate in candidates:
        try:
            ready = _prepare_item(
                grant,
                candidate,
                selected_policy,
                ingested_at=normalized_time,
            )
        except _ContentFailure as exc:
            issue = _issue(
                exc.code,
                subject=candidate.relative_path,
                relative_path=candidate.relative_path,
            )
            issues.append(issue)
            reason = cast(ProjectDecisionReason, exc.code)
            decisions.append(
                _decision_without_content(
                    candidate.relative_path,
                    action="block",
                    reason=reason,
                )
            )
            continue
        prepared.append(ready)
        decisions.append(ready.decision)
        issues.extend(ready.issues)

    if not prepared and not any(issue.code == "empty_project" for issue in issues):
        issues.append(_issue("empty_project", subject=grant.root_fingerprint))
    if any(issue.severity == "blocker" for issue in issues):
        return _blocked_result(
            grant,
            selected_policy,
            tuple(decisions),
            tuple(issues),
        )
    prepared_tuple = tuple(prepared)
    if not _grant_is_current(grant) or not _observations_are_current(
        candidates, prepared_tuple, directories
    ):
        issues.append(_issue("source_changed_during_read", subject=grant.root_fingerprint))
        return _blocked_result(
            grant,
            selected_policy,
            tuple(decisions),
            tuple(issues),
        )

    sorted_prepared = tuple(sorted(prepared_tuple, key=lambda item: item.item.relative_path))
    sorted_items = tuple(item.item for item in sorted_prepared)
    sorted_artifacts = tuple(item.artifact for item in sorted_prepared)
    restricted = any(issue.severity in {"warning", "error"} for issue in issues)
    status: ProjectImportStatus = "imported_with_restrictions" if restricted else "imported"
    preview = build_project_import_preview(
        project_id=grant.project_id,
        root_fingerprint=grant.root_fingerprint,
        policy_sha256=selected_policy.canonical_sha256(),
        decisions=tuple(decisions),
        issues=tuple(issues),
    )
    report = _report_sha256(
        status=status,
        preview=preview,
        items=sorted_items,
        artifacts=sorted_artifacts,
    )
    try:
        bundle = build_project_bundle(
            project_id=grant.project_id,
            display_name=display_name,
            granted_root_fingerprint=grant.root_fingerprint,
            created_at=normalized_time,
            updated_at=normalized_time,
            items=sorted_items,
            permission_policy_sha256=selected_policy.canonical_sha256(),
            validation_summary_sha256=preview.preview_sha256,
            ingestion_report_sha256=report,
        )
    except (ProjectIdentityError, ValidationError, ValueError):
        issues.append(_issue("atomic_import_aborted", subject=grant.root_fingerprint))
        return _blocked_result(
            grant,
            selected_policy,
            tuple(decisions),
            tuple(issues),
        )
    return ProjectImportResult(
        status=status,
        preview=preview,
        validation_summary_sha256=preview.preview_sha256,
        ingestion_report_sha256=report,
        bundle=bundle,
        artifacts=sorted_artifacts,
    )
