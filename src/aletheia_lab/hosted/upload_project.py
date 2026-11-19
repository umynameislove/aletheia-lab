"""Hosted ZIP adapter reusing the read-only P3 importer and mapping validators."""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import TypedDict, cast

from pydantic import ValidationError

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.candidates import (
    MappingCandidates,
    _contains_sensitive_text,
    _metric_structure,
    _records_at,
    build_mapping_candidates,
    confirm_mapping_is_eligible,
)
from aletheia_lab.product.mapping import (
    _metric_definition,
    _metric_mapping,
    _run_mapping,
    build_confirm_mapping,
    metric_definitions_match_observations,
)
from aletheia_lab.project.collectors import collect_project_files
from aletheia_lab.project.contracts import ProjectItem
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.import_policy import ProjectImportPolicy
from aletheia_lab.project.importer import (
    GrantedProjectRoot,
    ProjectImportArtifact,
    grant_immutable_project_root,
    import_local_project,
    inspect_local_project,
)
from aletheia_lab.project.mapping import (
    AnyMappingConfiguration,
    DatasetTargetMapping,
    MetricDefinition,
    validate_project_mapping,
)

from .source_bundle import SourceEntry, SourceRejected, inspect_source_bundle
from .upload_snapshot import AnyUploadSnapshot, build_upload_snapshot

# P3 includes bundle audit time in its identity. This is the content projection
# marker, not a claimed source timestamp; actual acceptance is captured_at.
_CONTENT_PROJECTION_TIME = "1970-01-01T00:00:00Z"
_SOURCE_SEAL_VERSION = "hosted-source-seal/v1"
_MAX_ISSUES = 128


class UploadSourceStateChanged(SourceRejected):
    """A preview no longer describes this parser/policy's source projection."""

    def envelope(self) -> dict[str, object]:
        return {"schema_version": "hosted-upload-error/v1", "code": "source_changed"}


class _MetricInput(TypedDict):
    mapping_id: str


class _RunInput(TypedDict):
    run_id: str


class _DefinitionInput(TypedDict):
    metric_name: str


class _ValidatedMapping(TypedDict):
    metric_sources: list[_MetricInput]
    runs: list[_RunInput]
    metric_definitions: list[_DefinitionInput]


def _source_seal(source_sha256: str, items: Sequence[ProjectItem], policy: ProjectImportPolicy) -> str:
    return canonical_project_sha256({"schema_version": _SOURCE_SEAL_VERSION,
        "source_sha256": source_sha256, "policy_sha256": policy.canonical_sha256(),
        "items": [item.identity_payload() for item in items]})


class UploadMappingRejected(SourceRejected):
    """Fixed-code, field-addressed diagnostics; never include source exceptions."""

    def __init__(self, issues: list[dict[str, object]]) -> None:
        super().__init__()
        self.issues = issues[:_MAX_ISSUES]

    def envelope(self) -> dict[str, object]:
        return {"schema_version": "hosted-upload-error/v1", "code": "mapping_invalid", "issues": self.issues}


def _native_path(path: Path) -> Path:
    """Use Windows extended syntax only for trusted absolute private paths."""
    value = str(path)
    if os.name != "nt" or not path.is_absolute() or value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def _plain_directory(path: Path) -> None:
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 1024):
            raise SourceRejected()


def _materialize(payload: bytes, root: Path, *, maximum_upload_bytes: int,
                 maximum_expanded_bytes: int, maximum_entries: int) -> Path:
    entries = inspect_source_bundle(payload, maximum_upload_bytes=maximum_upload_bytes,
        maximum_expanded_bytes=maximum_expanded_bytes, maximum_entries=maximum_entries)
    if not root.is_absolute():
        raise ValueError("absolute server-owned root required")
    _plain_directory(root)
    target = root / content_hasher(payload).hexdigest()
    if not target.exists():
        _publish_materialization(payload, root, target, entries)
    _plain_directory(target)
    expected = {entry.path: entry for entry in entries}
    seen = set()
    for path in target.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
            raise SourceRejected()
        if stat.S_ISDIR(info.st_mode):
            continue
        relative = path.relative_to(target).as_posix()
        expected_entry = expected.get(relative)
        if not stat.S_ISREG(info.st_mode) or expected_entry is None or info.st_size != expected_entry.byte_size:
            raise SourceRejected()
        digest = content_hasher()
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        if digest.hexdigest() != expected_entry.sha256:
            raise SourceRejected()
        seen.add(relative)
    if seen != set(expected):
        raise SourceRejected()
    return target


def _excluded_metrics(items: Sequence[ProjectItem], artifacts: Sequence[ProjectImportArtifact]) -> tuple[list[dict[str, object]], int, set[str]]:
    by_path = {artifact.relative_path: artifact for artifact in artifacts}
    excluded: list[dict[str, object]] = []
    seen = set()
    excluded_items = set()
    for item in items:
        if item.source_type != "metrics" or item.visibility != "diagnosis" or item.redaction_state == "withheld":
            continue
        structure = _metric_structure(item, by_path[item.relative_path])
        if structure is None:
            continue
        _, _, fields, records = structure
        name_fields = [name for name in fields if name.casefold() in {"metric", "metric_name", "name"}]
        if len(name_fields) != 1:
            continue
        for index, record in enumerate(records):
            name = record.get(name_fields[0])
            if (not isinstance(name, str) or not name or len(name) > 128
                    or _contains_sensitive_text(name) or any(ord(character) < 32 or ord(character) == 127 for character in name)):
                continue
            try:
                MetricDefinition(metric_name=name, direction="lower_is_better", regression_threshold=0.0)
            except ValueError:
                excluded_items.add(item.project_item_id)
                key = (item.project_item_id, name)
                if key not in seen:
                    seen.add(key)
                    if len(excluded) < _MAX_ISSUES:
                        excluded.append({"project_item_id": item.project_item_id, "metric_name": name,
                            "reason": "unsupported_metric_identifier", "source_record_index": index})
    return excluded, len(seen), excluded_items


def _input_issues(mapping: dict[str, object]) -> list[dict[str, object]]:
    """Ask the same P3 validators for field positions before the shared adapter."""
    issues: list[dict[str, object]] = []
    expected = {"target", "metric_sources", "runs", "baseline_run_id", "metric_definitions"}
    version2 = mapping.get('schema_version') == 'project-mapping-request/v2' if isinstance(mapping, dict) else False
    if version2:
        expected.add('schema_version')
    if not isinstance(mapping, dict) or set(mapping) != expected:
        return [{"code": "mapping_shape_invalid", "field": "mapping"}]
    validators: dict[str, Callable[[object], object]] = {"target": DatasetTargetMapping.model_validate,
                  "metric_sources": partial(_metric_mapping, version2=version2), "runs": _run_mapping,
                  "metric_definitions": partial(_metric_definition, version2=version2)}
    safe_fields = {"mapping_id", "project_item_id", "target_field", "identifier_field", "format", "records_path",
                   "metric_name_field", "metric_value_field", "run_id_field", "step_field", "run_id", "config_item_ids",
                   "metric_name", "direction", "regression_threshold", "class_field", "unit_field", "unit"}
    for field, validator in validators.items():
        value = mapping[field]
        if field == "target":
            values = [value]
        elif isinstance(value, list) and value:
            values = value
        else:
            issues.append({"code": "mapping_required", "field": field})
            continue
        for index, entry in enumerate(values):
            location = field if field == "target" else f"{field}.{index}"
            issues.extend(_mapping_entry_issues(validator, entry, location, safe_fields))
            if len(issues) >= _MAX_ISSUES:
                return issues[:_MAX_ISSUES]
    return issues


def _eligibility_issues(configuration: AnyMappingConfiguration, candidates: MappingCandidates,
                        mapping: _ValidatedMapping) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    targets = {value.project_item_id: value for value in candidates.targets if value.available}
    target = targets.get(configuration.target.project_item_id)
    if target is None:
        issues.append({"code": "mapping_candidate_unavailable", "field": "target.project_item_id"})
    else:
        for field in ("target_field", "identifier_field"):
            if getattr(configuration.target, field) not in target.field_names:
                issues.append({"code": "unknown_field", "field": "target." + field})
    metrics = {value.project_item_id: value for value in candidates.metric_sources if value.available}
    indices = {value["mapping_id"]: index for index, value in enumerate(mapping["metric_sources"])}
    for source in configuration.metric_sources:
        prefix = f"metric_sources.{indices[source.mapping_id]}"
        candidate = metrics.get(source.project_item_id)
        if candidate is None:
            issues.append({"code": "mapping_candidate_unavailable", "field": prefix + ".project_item_id"})
            continue
        if source.format != candidate.format:
            issues.append({"code": "mapping_relation_invalid", "field": prefix + ".format"})
        for field in ("metric_name_field", "metric_value_field", "run_id_field", "step_field", "class_field", "unit_field"):
            selected = getattr(source, field, None)
            if selected is not None and selected not in candidate.field_names:
                issues.append({"code": "unknown_field", "field": prefix + "." + field})
        if (source.format == "csv" and source.records_path) or (
            source.format == "json" and candidate.records_path_candidates
            and source.records_path not in candidate.records_path_candidates
        ):
            issues.append({"code": "mapping_relation_invalid", "field": prefix + ".records_path"})
    return issues or [{"code": "mapping_candidate_unavailable", "field": "mapping"}]


def _underflow_issue(raw: object, field: str, index: int) -> dict[str, object] | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        return None
    try:
        rounded = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if rounded != 0.0:
        return None
    try:
        exact = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return {"code": "metric_value_invalid", "field": field, "record_index": index}
    if exact.is_finite() and not exact.is_zero():
        return {"code": "numeric_underflow", "field": field, "record_index": index}
    return None


def _numeric_underflow_issues(configuration: AnyMappingConfiguration, items: Sequence[ProjectItem],
                             artifacts: Sequence[ProjectImportArtifact], indices: Mapping[str, int]) -> list[dict[str, object]]:
    """Validate numeric lexemes before the unchanged float-based P3 conversion."""
    by_item = {item.project_item_id: item for item in items}
    by_path = {artifact.relative_path: artifact for artifact in artifacts}
    issues: list[dict[str, object]] = []
    for source in configuration.metric_sources:
        if len(issues) >= _MAX_ISSUES:
            return issues[:_MAX_ISSUES]
        field = f"metric_sources.{indices[source.mapping_id]}"
        item = by_item[source.project_item_id]
        artifact = by_path[item.relative_path]
        try:
            if source.format == "json":
                # Validation only: preserve numeric lexemes; P3 still parses the business facts.
                lexical = json.loads(artifact.content.decode("utf-8"), parse_float=Decimal)
                records = _records_at(lexical, source.records_path)
            else:
                structure = _metric_structure(item, artifact)
                records = () if structure is None else structure[3]
        except (UnicodeError, ValueError, InvalidOperation, RecursionError):
            issues.append({"code": "metric_value_invalid", "field": field})
            continue
        fields = (source.metric_value_field,) + (() if source.step_field is None else (source.step_field,))
        for index, record in enumerate(records):
            for selected_field in fields:
                if len(issues) >= _MAX_ISSUES:
                    return issues[:_MAX_ISSUES]
                issue = _underflow_issue(record.get(selected_field), field, index)
                if issue is not None:
                    issues.append(issue)
                if len(issues) >= _MAX_ISSUES:
                    return issues[:_MAX_ISSUES]
    return issues


class UploadProject:
    def __init__(self, private_root: Path, *, maximum_upload_bytes: int,
                 maximum_expanded_bytes: int, maximum_entries: int) -> None:
        self._root = _native_path(private_root)
        self._budgets = dict(maximum_upload_bytes=maximum_upload_bytes,
                             maximum_expanded_bytes=maximum_expanded_bytes, maximum_entries=maximum_entries)
        self._policy = ProjectImportPolicy(max_item_bytes=maximum_expanded_bytes,
            max_total_bytes=maximum_expanded_bytes, max_items=maximum_entries,
            max_discovered_entries=maximum_entries, max_path_depth=32)

    @contextmanager
    def _grant(self, payload: bytes) -> Iterator[GrantedProjectRoot]:
        _plain_directory(self._root)
        # The imported artifacts are in memory before this owned scratch tree closes.
        with tempfile.TemporaryDirectory(prefix="upload-job-", dir=self._root) as owned:
            root = _materialize(payload, Path(owned), **self._budgets)
            yield grant_immutable_project_root(root, source_sha256=content_hasher(payload).hexdigest())

    def preview(self, payload: bytes, *, captured_at: str) -> dict[str, object]:
        with self._grant(payload) as grant:
            inspection = inspect_local_project(grant, ingested_at=_CONTENT_PROJECTION_TIME, policy=self._policy)
        if inspection.source_state_sha256 is None:
            raise SourceRejected()
        candidates = build_mapping_candidates(inspection.items, inspection.artifacts).model_dump(mode="json")
        excluded, excluded_total, excluded_items = _excluded_metrics(inspection.items, inspection.artifacts)
        for candidate in candidates["metric_sources"]:
            if candidate["project_item_id"] in excluded_items:
                candidate.update(available=False, mapping_id=None, unavailable_reason="unsupported_source",
                    records_path_candidates=[], field_names=[], run_id_field_candidates=[], metric_name_candidates=[])
        for run in candidates["runs"]:
            available_sources = [item_id for item_id in run["source_metric_item_ids"] if item_id not in excluded_items]
            if available_sources:
                run["source_metric_item_ids"] = available_sources
            else:
                run.update(available=False, unavailable_reason="unsupported_source")
        source_sha256 = content_hasher(payload).hexdigest()
        return {"source_sha256": source_sha256, "mapping_candidates": candidates,
                "source_state_sha256": _source_seal(source_sha256, inspection.items, self._policy),
                "excluded_metrics": excluded, "excluded_metrics_total": excluded_total}

    def confirm(self, payload: bytes, *, workspace_id: str, source_id: str,
                mapping: dict[str, object], captured_at: str,
                expected_source_state_sha256: str | None = None) -> AnyUploadSnapshot:
        from datetime import datetime

        with self._grant(payload) as grant:
            imported = import_local_project(grant, display_name="Uploaded Project",
                                            ingested_at=_CONTENT_PROJECTION_TIME, policy=self._policy)
        if imported.bundle is None:
            raise SourceRejected()
        if (expected_source_state_sha256 is not None
                and _source_seal(content_hasher(payload).hexdigest(), imported.bundle.items, self._policy)
                != expected_source_state_sha256):
            raise UploadSourceStateChanged()
        issues = _input_issues(mapping)
        if issues:
            raise UploadMappingRejected(issues)
        collection = collect_project_files(imported.bundle, imported.artifacts)
        try:
            configuration = build_confirm_mapping(mapping, project_id=imported.bundle.project_id,
                project_bundle_id=imported.bundle.project_bundle_id, file_collection_sha256=collection.collection_sha256)
        except ProductError:
            raise UploadMappingRejected([{"code": "mapping_relation_invalid", "field": "mapping"}]) from None
        _, _, excluded_ids = _excluded_metrics(imported.bundle.items, imported.artifacts)
        # The existing P3 validators above establish these arrays and text identifier keys.
        validated_mapping = cast(_ValidatedMapping, mapping)
        metric_indices = {value["mapping_id"]: index for index, value in enumerate(validated_mapping["metric_sources"])}
        run_indices = {value["run_id"]: index for index, value in enumerate(validated_mapping["runs"])}
        definition_indices = {value["metric_name"]: index for index, value in enumerate(validated_mapping["metric_definitions"])}
        if any(metric.project_item_id in excluded_ids for metric in configuration.metric_sources):
            raise UploadMappingRejected([{"code": "unsupported_metric_identifier", "field": f"metric_sources.{metric_indices[metric.mapping_id]}"}
                for metric in configuration.metric_sources if metric.project_item_id in excluded_ids])
        candidates = build_mapping_candidates(imported.bundle.items, imported.artifacts)
        if not confirm_mapping_is_eligible(configuration, candidates):
            raise UploadMappingRejected(_eligibility_issues(configuration, candidates, validated_mapping))
        numeric_issues = _numeric_underflow_issues(configuration, imported.bundle.items, imported.artifacts, metric_indices)
        if numeric_issues:
            raise UploadMappingRejected(numeric_issues)
        result = validate_project_mapping(imported.bundle, imported.artifacts, collection, configuration)
        if result.status != "valid":
            fields = {"configuration": "mapping", "baseline": "baseline_run_id",
                f"target:{configuration.target.mapping_id}": "target"}
            fields.update({f"metric:{metric.mapping_id}": f"metric_sources.{metric_indices[metric.mapping_id]}" for metric in configuration.metric_sources})
            fields.update({f"run:{run.run_id}": f"runs.{run_indices[run.run_id]}" for run in configuration.runs})
            raise UploadMappingRejected([{"code": issue.code, "field": fields[issue.candidate_id]}
                                         for issue in result.issues])
        if not metric_definitions_match_observations(configuration, result):
            observed = {row.metric_name for row in result.metric_observations}
            declared = {row.metric_name for row in configuration.metric_definitions}
            issues = [{"code": "metric_definition_missing", "field": "metric_definitions"} for _ in observed - declared]
            issues += [{"code": "metric_definition_unknown", "field": f"metric_definitions.{definition_indices[definition.metric_name]}"}
                       for definition in configuration.metric_definitions if definition.metric_name not in observed]
            raise UploadMappingRejected(issues)
        return build_upload_snapshot(workspace_id=workspace_id, source_id=source_id,
            source_sha256=content_hasher(payload).hexdigest(), mapping=configuration, mapping_result=result,
            captured_at=datetime.fromisoformat(captured_at.replace("Z", "+00:00")))
def _publish_materialization(payload: bytes, root: Path, target: Path,
                             entries: tuple[SourceEntry, ...]) -> None:
    # A private unique tree is published by rename only after all writes.
    # Concurrent readers can never mistake a partially extracted tree for ready.
    temporary = Path(tempfile.mkdtemp(prefix="upload-materialize-", dir=root))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for entry in entries:
                destination = temporary.joinpath(*entry.path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry.path) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=65536)
        for attempt in range(4):
            try:
                temporary.rename(target)
                break
            except OSError as exc:
                if target.is_dir():
                    break
                # Windows scanners can briefly hold a just-closed file.
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 3:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        # Only this call's known unique directory, under the verified root.
        if temporary.exists():
            assert temporary.parent == root and temporary.name.startswith("upload-materialize-")
            shutil.rmtree(temporary)

def _mapping_entry_issues(validator: Callable[[object], object], entry: object,
                          location: str, safe_fields: set[str]) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    try:
        validator(entry)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            path = [str(part) for part in error["loc"] if isinstance(part, int) or part in safe_fields]
            issues.append({"code": "mapping_value_invalid", "field": ".".join([location, *path])})
    except (TypeError, ValueError, KeyError):
        issues.append({"code": "mapping_value_invalid", "field": location})
    return issues

