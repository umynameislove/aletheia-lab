"""Immutable policy and issue contracts for local-project import.

This module contains data-only contracts.  It never touches the filesystem,
opens a network connection, executes imported content or grants authority.  A
policy digest can therefore be reviewed and bound into a :class:`ProjectBundle`
before any downstream evidence projection is allowed.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import (
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
    normalize_relative_project_path,
    normalize_text,
)

PROJECT_IMPORT_POLICY_SCHEMA_VERSION: Final[Literal["project-import-policy/v1"]] = (
    "project-import-policy/v1"
)
PROJECT_VALIDATION_ISSUE_SCHEMA_VERSION: Final[
    Literal["project-validation-issue/v1"]
] = "project-validation-issue/v1"
PROJECT_IMPORT_DECISION_SCHEMA_VERSION: Final[
    Literal["project-import-decision/v1"]
] = "project-import-decision/v1"
PROJECT_IMPORT_PREVIEW_SCHEMA_VERSION: Final[Literal["project-import-preview/v1"]] = (
    "project-import-preview/v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProjectId = Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]

ProjectIssueSeverity = Literal["info", "warning", "error", "blocker"]
ProjectIssueStage = Literal["authorization", "discovery", "read", "content", "assembly"]
ProjectImportAction = Literal["include", "exclude", "redact", "withhold", "block"]

ProjectIssueCode = Literal[
    "root_not_absolute",
    "root_unavailable",
    "root_not_directory",
    "root_link_not_allowed",
    "root_changed",
    "path_invalid",
    "path_outside_root",
    "link_not_allowed",
    "reparse_point_not_allowed",
    "hardlink_not_allowed",
    "special_file_not_allowed",
    "path_depth_exceeded",
    "item_limit_exceeded",
    "item_too_large",
    "total_size_exceeded",
    "source_changed_during_read",
    "source_read_failed",
    "invalid_utf8",
    "unsafe_control_character",
    "line_too_long",
    "structured_content_invalid",
    "duplicate_structured_key",
    "non_finite_number",
    "secret_withheld",
    "pii_redacted",
    "untrusted_instruction_text",
    "empty_project",
    "atomic_import_aborted",
]

ProjectDecisionReason = Literal[
    "included",
    "hidden_path_excluded",
    "directory_excluded",
    "file_type_not_allowed",
    "pii_redacted",
    "secret_withheld",
    "root_changed",
    "path_invalid",
    "path_outside_root",
    "link_not_allowed",
    "reparse_point_not_allowed",
    "hardlink_not_allowed",
    "special_file_not_allowed",
    "path_depth_exceeded",
    "item_limit_exceeded",
    "item_too_large",
    "total_size_exceeded",
    "source_changed_during_read",
    "source_read_failed",
    "invalid_utf8",
    "unsafe_control_character",
    "line_too_long",
    "structured_content_invalid",
    "duplicate_structured_key",
    "non_finite_number",
]

SUPPORTED_TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".cfg",
        ".conf",
        ".csv",
        ".ini",
        ".ipynb",
        ".json",
        ".log",
        ".md",
        ".py",
        ".toml",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }
)
SUPPORTED_TEXT_NAMES: Final[frozenset[str]] = frozenset(
    {"Dockerfile", "LICENSE", "Makefile"}
)

DEFAULT_EXCLUDED_DIRECTORIES: Final[tuple[str, ...]] = tuple(
    sorted(
        {
            ".git",
            ".hg",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".svn",
            ".venv",
            "__pycache__",
            "htmlcov",
            "node_modules",
            "venv",
        }
    )
)

_EXTENSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\.[a-z0-9][a-z0-9._+-]{0,31}$")
_REASON_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"

# Severity and stage are part of the public contract.  Callers cannot downgrade
# an unsafe path or failed atomic import to a warning to force a bundle through.
ISSUE_CONTRACT: Final[
    dict[ProjectIssueCode, tuple[ProjectIssueSeverity, ProjectIssueStage, str]]
] = {
    "root_not_absolute": (
        "blocker",
        "authorization",
        "The granted project root must be an explicit absolute path.",
    ),
    "root_unavailable": (
        "blocker",
        "authorization",
        "The granted project root is unavailable or cannot be inspected.",
    ),
    "root_not_directory": (
        "blocker",
        "authorization",
        "The granted project root must be a directory.",
    ),
    "root_link_not_allowed": (
        "blocker",
        "authorization",
        "A symbolic link or reparse point cannot be granted as the project root.",
    ),
    "root_changed": (
        "blocker",
        "discovery",
        "The granted project root changed after authorization.",
    ),
    "path_invalid": (
        "blocker",
        "discovery",
        "A project path is not a canonical portable relative path.",
    ),
    "path_outside_root": (
        "blocker",
        "discovery",
        "A candidate resolves outside the granted project root.",
    ),
    "link_not_allowed": (
        "blocker",
        "discovery",
        "Symbolic links are not followed by the project importer.",
    ),
    "reparse_point_not_allowed": (
        "blocker",
        "discovery",
        "Filesystem reparse points are not followed by the project importer.",
    ),
    "hardlink_not_allowed": (
        "blocker",
        "discovery",
        "Multiply linked files are not admitted by the project importer.",
    ),
    "special_file_not_allowed": (
        "blocker",
        "discovery",
        "Only regular files and ordinary directories may be imported.",
    ),
    "path_depth_exceeded": (
        "blocker",
        "discovery",
        "A candidate exceeds the configured project path depth.",
    ),
    "item_limit_exceeded": (
        "blocker",
        "discovery",
        "The project exceeds the configured candidate item limit.",
    ),
    "item_too_large": (
        "blocker",
        "discovery",
        "A candidate exceeds the configured per-item byte limit.",
    ),
    "total_size_exceeded": (
        "blocker",
        "discovery",
        "The project exceeds the configured aggregate byte limit.",
    ),
    "source_changed_during_read": (
        "blocker",
        "read",
        "A source changed between discovery and the completed bounded read.",
    ),
    "source_read_failed": (
        "blocker",
        "read",
        "A candidate could not be read through the bounded import boundary.",
    ),
    "invalid_utf8": (
        "blocker",
        "content",
        "An allowed text candidate is not valid UTF-8.",
    ),
    "unsafe_control_character": (
        "blocker",
        "content",
        "An allowed text candidate contains an unsafe control character.",
    ),
    "line_too_long": (
        "blocker",
        "content",
        "An allowed text candidate exceeds the configured line byte limit.",
    ),
    "structured_content_invalid": (
        "blocker",
        "content",
        "Structured project content is malformed for its declared file type.",
    ),
    "duplicate_structured_key": (
        "blocker",
        "content",
        "Structured project content contains an ambiguous duplicate key.",
    ),
    "non_finite_number": (
        "blocker",
        "content",
        "Structured project content contains a non-finite number.",
    ),
    "secret_withheld": (
        "error",
        "content",
        "High-risk credential-like content was withheld from diagnosis visibility.",
    ),
    "pii_redacted": (
        "warning",
        "content",
        "Personally identifying content was redacted before diagnosis visibility.",
    ),
    "untrusted_instruction_text": (
        "warning",
        "content",
        "Instruction-like project text remains untrusted data and grants no authority.",
    ),
    "empty_project": (
        "blocker",
        "assembly",
        "No eligible project item remains for an atomic import.",
    ),
    "atomic_import_aborted": (
        "blocker",
        "assembly",
        "The atomic project import was aborted because a blocking issue exists.",
    ),
}

_REASON_ACTION: Final[dict[ProjectDecisionReason, ProjectImportAction]] = {
    "included": "include",
    "hidden_path_excluded": "exclude",
    "directory_excluded": "exclude",
    "file_type_not_allowed": "exclude",
    "pii_redacted": "redact",
    "secret_withheld": "withhold",
    "root_changed": "block",
    "path_invalid": "block",
    "path_outside_root": "block",
    "link_not_allowed": "block",
    "reparse_point_not_allowed": "block",
    "hardlink_not_allowed": "block",
    "special_file_not_allowed": "block",
    "path_depth_exceeded": "block",
    "item_limit_exceeded": "block",
    "item_too_large": "block",
    "total_size_exceeded": "block",
    "source_changed_during_read": "block",
    "source_read_failed": "block",
    "invalid_utf8": "block",
    "unsafe_control_character": "block",
    "line_too_long": "block",
    "structured_content_invalid": "block",
    "duplicate_structured_key": "block",
    "non_finite_number": "block",
}


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class ProjectImportPolicy(_StrictFrozenModel):
    """Default-deny, local-only policy for one import transaction."""

    schema_version: Literal["project-import-policy/v1"] = PROJECT_IMPORT_POLICY_SCHEMA_VERSION
    allowed_extensions: tuple[str, ...] = tuple(sorted(SUPPORTED_TEXT_EXTENSIONS))
    allowed_exact_names: tuple[str, ...] = tuple(sorted(SUPPORTED_TEXT_NAMES))
    excluded_directory_names: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORIES
    include_hidden_paths: bool = False
    max_item_bytes: int = Field(default=8 << 20, ge=1, le=1 << 30)
    max_total_bytes: int = Field(default=64 << 20, ge=1, le=1 << 34)
    max_items: int = Field(default=10_000, ge=1, le=1_000_000)
    max_discovered_entries: int = Field(default=50_000, ge=1, le=2_000_000)
    max_path_depth: int = Field(default=32, ge=1, le=256)
    max_line_bytes: int = Field(default=1 << 20, ge=128, le=1 << 28)
    scan_secrets: Literal[True] = True
    scan_pii: Literal[True] = True
    execution_mode: Literal["disabled"] = "disabled"
    network_mode: Literal["disabled"] = "disabled"
    source_mutation_mode: Literal["forbidden"] = "forbidden"
    unsupported_file_action: Literal["exclude"] = "exclude"
    unsafe_path_action: Literal["block"] = "block"

    @field_validator("allowed_extensions")
    @classmethod
    def _canonical_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one supported extension must be allowed")
        if len(value) != len(set(value)):
            raise ValueError("allowed extensions must be unique")
        if any(_EXTENSION_PATTERN.fullmatch(item) is None for item in value):
            raise ValueError("allowed extensions must be lowercase portable extensions")
        unsupported = set(value) - SUPPORTED_TEXT_EXTENSIONS
        if unsupported:
            raise ValueError("allowed extensions must use audited text collectors")
        return tuple(sorted(value))

    @field_validator("allowed_exact_names", "excluded_directory_names")
    @classmethod
    def _canonical_names(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = str(getattr(info, "field_name", "names"))
        normalized = tuple(
            normalize_text(item, label=field_name, max_length=128) for item in value
        )
        if any("/" in item or "\\" in item or item in {".", ".."} for item in normalized):
            raise ValueError(f"{field_name} must contain single path components")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{field_name} must contain unique values")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def _limits_are_consistent(self) -> ProjectImportPolicy:
        if self.max_item_bytes > self.max_total_bytes:
            raise ValueError("max_item_bytes must not exceed max_total_bytes")
        if self.max_items > self.max_discovered_entries:
            raise ValueError("max_items must not exceed max_discovered_entries")
        return self

    def canonical_sha256(self) -> str:
        checked = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_project_sha256(checked.model_dump(mode="json"))


class ProjectValidationIssue(_StrictFrozenModel):
    """Structured issue whose severity cannot be weakened by a caller."""

    schema_version: Literal["project-validation-issue/v1"] = (
        PROJECT_VALIDATION_ISSUE_SCHEMA_VERSION
    )
    code: ProjectIssueCode
    severity: ProjectIssueSeverity
    stage: ProjectIssueStage
    message: str = Field(min_length=1, max_length=256)
    subject_sha256: Sha256
    relative_path: str | None = Field(default=None, max_length=1024)
    occurrences: int = Field(default=1, ge=1, le=1_000_000)

    @field_validator("message")
    @classmethod
    def _canonical_message(cls, value: str) -> str:
        return normalize_text(value, label="validation issue message", max_length=256)

    @field_validator("relative_path")
    @classmethod
    def _canonical_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else normalize_relative_project_path(value)

    @model_validator(mode="after")
    def _contract_is_exact(self) -> ProjectValidationIssue:
        severity, stage, message = ISSUE_CONTRACT[self.code]
        if (self.severity, self.stage, self.message) != (severity, stage, message):
            raise ValueError("issue severity, stage and message must match the issue code")
        return self

    @classmethod
    def create(
        cls,
        code: ProjectIssueCode,
        *,
        subject_sha256: str,
        relative_path: str | None = None,
        occurrences: int = 1,
    ) -> Self:
        severity, stage, message = ISSUE_CONTRACT[code]
        return cls(
            code=code,
            severity=severity,
            stage=stage,
            message=message,
            subject_sha256=subject_sha256,
            relative_path=relative_path,
            occurrences=occurrences,
        )


class ProjectImportDecision(_StrictFrozenModel):
    """One deterministic include, exclude, redact, withhold or block decision."""

    schema_version: Literal["project-import-decision/v1"] = (
        PROJECT_IMPORT_DECISION_SCHEMA_VERSION
    )
    relative_path: str
    action: ProjectImportAction
    reason_code: ProjectDecisionReason = Field(pattern=_REASON_PATTERN)
    subject_sha256: Sha256
    source_type: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    media_type: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
    )
    source_byte_size: int | None = Field(default=None, ge=0, le=1 << 30)
    content_sha256: Sha256 | None = None
    artifact_sha256: Sha256 | None = None

    @field_validator("relative_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_relative_project_path(value)

    @model_validator(mode="after")
    def _decision_is_consistent(self) -> ProjectImportDecision:
        if self.action != _REASON_ACTION[self.reason_code]:
            raise ValueError("decision action does not match its reason code")
        admitted = self.action in {"include", "redact", "withhold"}
        details = (
            self.source_type,
            self.media_type,
            self.source_byte_size,
            self.content_sha256,
            self.artifact_sha256,
        )
        if admitted and any(item is None for item in details):
            raise ValueError("admitted decisions require complete content metadata")
        if not admitted and any(item is not None for item in details):
            raise ValueError("excluded or blocked decisions must not claim content admission")
        return self


def _preview_digest(
    *,
    project_id: str,
    root_fingerprint: str,
    policy_sha256: str,
    decisions: tuple[ProjectImportDecision, ...],
    issues: tuple[ProjectValidationIssue, ...],
) -> str:
    return canonical_project_sha256(
        {
            "schema_version": PROJECT_IMPORT_PREVIEW_SCHEMA_VERSION,
            "project_id": project_id,
            "root_fingerprint": root_fingerprint,
            "policy_sha256": policy_sha256,
            "decisions": [item.model_dump(mode="json") for item in decisions],
            "issues": [item.model_dump(mode="json") for item in issues],
        }
    )


class ProjectImportPreview(_StrictFrozenModel):
    """Canonical reconciliation view emitted before any bundle is returned."""

    schema_version: Literal["project-import-preview/v1"] = PROJECT_IMPORT_PREVIEW_SCHEMA_VERSION
    project_id: ProjectId
    root_fingerprint: Sha256
    policy_sha256: Sha256
    decisions: tuple[ProjectImportDecision, ...]
    issues: tuple[ProjectValidationIssue, ...]
    included_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    redacted_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    preview_sha256: Sha256

    @field_validator("decisions")
    @classmethod
    def _canonical_decisions(
        cls, value: tuple[ProjectImportDecision, ...]
    ) -> tuple[ProjectImportDecision, ...]:
        paths = tuple(item.relative_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("import decisions must contain unique relative paths")
        return tuple(sorted(value, key=lambda item: item.relative_path))

    @field_validator("issues")
    @classmethod
    def _canonical_issues(
        cls, value: tuple[ProjectValidationIssue, ...]
    ) -> tuple[ProjectValidationIssue, ...]:
        keys = tuple(
            (item.stage, item.code, item.relative_path or "", item.subject_sha256)
            for item in value
        )
        if len(keys) != len(set(keys)):
            raise ValueError("import issues must be reconciled before preview assembly")
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.stage,
                    item.code,
                    item.relative_path or "",
                    item.subject_sha256,
                ),
            )
        )

    @model_validator(mode="after")
    def _counts_and_digest_match(self) -> ProjectImportPreview:
        expected_counts = {
            "include": self.included_count,
            "exclude": self.excluded_count,
            "redact": self.redacted_count,
            "withhold": self.withheld_count,
            "block": self.blocked_count,
        }
        for action, declared in expected_counts.items():
            if declared != sum(item.action == action for item in self.decisions):
                raise ValueError(f"{action} count does not match import decisions")
        digest = _preview_digest(
            project_id=self.project_id,
            root_fingerprint=self.root_fingerprint,
            policy_sha256=self.policy_sha256,
            decisions=self.decisions,
            issues=self.issues,
        )
        if self.preview_sha256 != digest:
            raise ValueError("preview_sha256 does not match decisions and issues")
        return self


def build_project_import_preview(
    *,
    project_id: str,
    root_fingerprint: str,
    policy_sha256: str,
    decisions: tuple[ProjectImportDecision, ...],
    issues: tuple[ProjectValidationIssue, ...],
) -> ProjectImportPreview:
    sorted_decisions = tuple(sorted(decisions, key=lambda item: item.relative_path))
    sorted_issues = tuple(
        sorted(
            issues,
            key=lambda item: (
                item.stage,
                item.code,
                item.relative_path or "",
                item.subject_sha256,
            ),
        )
    )
    return ProjectImportPreview(
        project_id=project_id,
        root_fingerprint=root_fingerprint,
        policy_sha256=policy_sha256,
        decisions=sorted_decisions,
        issues=sorted_issues,
        included_count=sum(item.action == "include" for item in sorted_decisions),
        excluded_count=sum(item.action == "exclude" for item in sorted_decisions),
        redacted_count=sum(item.action == "redact" for item in sorted_decisions),
        withheld_count=sum(item.action == "withhold" for item in sorted_decisions),
        blocked_count=sum(item.action == "block" for item in sorted_decisions),
        preview_sha256=_preview_digest(
            project_id=project_id,
            root_fingerprint=root_fingerprint,
            policy_sha256=policy_sha256,
            decisions=sorted_decisions,
            issues=sorted_issues,
        ),
    )
