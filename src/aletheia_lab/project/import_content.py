"""Validation and safe metadata extraction for untrusted project content."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml

from aletheia_lab.project.identity import canonical_project_json
from aletheia_lab.project.import_policy import ProjectImportPolicy, ProjectIssueCode

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


class ImportContentError(ValueError):
    """One safe issue code raised while processing untrusted source bytes."""

    def __init__(self, code: ProjectIssueCode) -> None:
        super().__init__(code)
        self.code = code


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ImportContentError("duplicate_structured_key")
        result[key] = value
    return result


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ImportContentError("non_finite_number")
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
                raise ImportContentError("duplicate_structured_key")
            seen.add(key_identity)
            _reject_yaml_duplicate_keys(key_node)
            _reject_yaml_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for nested in node.value:
            _reject_yaml_duplicate_keys(nested)


def _parse_json(text: str, *, notebook: bool) -> object:
    parsed = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ImportContentError("non_finite_number")
        ),
    )
    if notebook and (not isinstance(parsed, dict) or not isinstance(parsed.get("cells"), list)):
        raise ImportContentError("structured_content_invalid")
    return parsed


def _parse_yaml(text: str) -> object:
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is not None:
        _reject_yaml_duplicate_keys(node)
    return yaml.safe_load(text)


def _validate_delimited(text: str, *, delimiter: str) -> None:
    rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    header = next(rows, None)
    if header is not None and len(header) != len(set(header)):
        raise ImportContentError("duplicate_structured_key")
    if header is not None and any(not column.strip() for column in header):
        raise ImportContentError("structured_content_invalid")
    expected_width = None if header is None else len(header)
    if expected_width is not None and any(len(row) != expected_width for row in rows):
        raise ImportContentError("structured_content_invalid")


def _validate_structured_content(path: str, text: str) -> None:
    extension = Path(path).suffix.lower()
    try:
        if extension in {".json", ".ipynb"}:
            parsed = _parse_json(text, notebook=extension == ".ipynb")
        elif extension in {".yaml", ".yml"}:
            parsed = _parse_yaml(text)
        elif extension == ".toml":
            parsed = tomllib.loads(text)
        elif extension in {".csv", ".tsv"}:
            _validate_delimited(text, delimiter="," if extension == ".csv" else "\t")
            parsed = None
        else:
            return
        _reject_non_finite(parsed)
    except ImportContentError:
        raise
    except (csv.Error, json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise ImportContentError("structured_content_invalid") from exc


def decode_and_validate(
    relative_path: str,
    source_bytes: bytes,
    policy: ProjectImportPolicy,
) -> str:
    """Decode bounded text and reject malformed or unsafe structured content."""

    try:
        text = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportContentError("invalid_utf8") from exc
    if _CONTROL_CHARACTER.search(text):
        raise ImportContentError("unsafe_control_character")
    if any(len(line.encode("utf-8")) > policy.max_line_bytes for line in text.splitlines()):
        raise ImportContentError("line_too_long")
    _validate_structured_content(relative_path, text)
    return text


def secret_occurrences(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in _SECRET_PATTERNS)


def redact_pii(text: str) -> tuple[str, int]:
    email_count = len(_EMAIL.findall(text))
    phone_count = len(_PHONE.findall(text))
    redacted = _EMAIL.sub("[REDACTED:pii.email]", text)
    redacted = _PHONE.sub("[REDACTED:pii.phone]", redacted)
    return redacted, email_count + phone_count


def _safe_field_name(value: str, *, index: int) -> str:
    """Keep schema labels useful without leaking embedded credentials or PII."""

    _, pii_count = redact_pii(value)
    if secret_occurrences(value) or pii_count:
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
        raise ImportContentError("structured_content_invalid") from exc
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


def dataset_metadata_bytes(
    relative_path: str,
    *,
    binary: bool,
    source_bytes: bytes,
    text: str | None,
) -> bytes:
    """Build path-free aggregate metadata for one dataset or metric source."""

    if binary:
        metadata = _parquet_metadata(source_bytes)
    else:
        assert text is not None
        delimiter = "\t" if Path(relative_path).suffix.lower() == ".tsv" else ","
        metadata = _csv_metadata(text, delimiter=delimiter)
    payload = {"schema_version": "project-dataset-metadata/v1", **metadata}
    return (canonical_project_json(payload) + "\n").encode("utf-8")


def path_contains_sensitive_content(value: str) -> bool:
    return bool(
        _EMAIL.search(value)
        or _PHONE.search(value)
        or any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    )


def contains_prompt_injection(text: str) -> bool:
    return _PROMPT_INJECTION.search(text) is not None


def source_modified_at(stat_result: os.stat_result) -> str:
    stamp = datetime.fromtimestamp(stat_result.st_mtime_ns / 1_000_000_000, tz=UTC)
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "ImportContentError",
    "contains_prompt_injection",
    "dataset_metadata_bytes",
    "decode_and_validate",
    "path_contains_sensitive_content",
    "redact_pii",
    "secret_occurrences",
    "source_modified_at",
]
