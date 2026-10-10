"""Safe mapping choices derived from admitted P3 inspection artifacts."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from typing import Final, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aletheia_lab.project.collectors import DatasetMetadata
from aletheia_lab.project.contracts import ProjectItem
from aletheia_lab.project.importer import ProjectImportArtifact
from aletheia_lab.project.mapping import ProjectMappingConfiguration

UnavailableReason: TypeAlias = Literal[
    "sensitive_fields_withheld",
    "unsupported_source",
    "unreadable_source",
]

_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_SENSITIVE_LABEL: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:ssn|social[_ -]?security|e-?mail|phone|password|passwd|"
    r"secret|token|api[_ -]?key|private[_ -]?key|credential)(?:$|[^a-z0-9])"
)
_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
)
_PHONE: Final[re.Pattern[str]] = re.compile(r"(?<!\d)\+[1-9](?:[ -]?\d){7,14}(?!\d)")
_RUN_FIELD_NAMES: Final[frozenset[str]] = frozenset({"run", "run_id", "runid"})
_METRIC_NAME_FIELD_NAMES: Final[frozenset[str]] = frozenset({"metric", "metric_name", "name"})
_SAFE_DATASET_LABEL: Final[str] = "A dataset with withheld field labels"
_SAFE_METRIC_LABEL: Final[str] = "A metric source with withheld field labels"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class CandidateBase(_StrictModel):
    project_item_id: str
    label: str
    mapping_id: str | None
    available: bool
    unavailable_reason: UnavailableReason | None
    relative_path: str | None

    @model_validator(mode="after")
    def _availability_reconciles(self) -> Self:
        if self.available:
            if self.mapping_id is None or self.unavailable_reason is not None:
                raise ValueError("available candidates require an ID and no reason")
        elif self.mapping_id is not None or self.unavailable_reason is None:
            raise ValueError("unavailable candidates require a reason and no mapping ID")
        return self


class TargetCandidate(CandidateBase):
    field_names: tuple[str, ...]

    @model_validator(mode="after")
    def _withheld_fields_are_empty(self) -> Self:
        if not self.available and self.field_names:
            raise ValueError("unavailable target candidates cannot expose fields")
        return self


class MetricSourceCandidate(CandidateBase):
    format: Literal["csv", "json"]
    records_path_candidates: tuple[tuple[str, ...], ...]
    field_names: tuple[str, ...]
    run_id_field_candidates: tuple[str, ...]
    metric_name_candidates: tuple[str, ...]

    @field_validator("records_path_candidates")
    @classmethod
    def _paths_are_unique(
        cls,
        values: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        if len(values) != len(set(values)):
            raise ValueError("records-path candidates must be unique")
        return values

    @field_validator("metric_name_candidates")
    @classmethod
    def _metric_names_are_safe(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            len(values) != len(set(values))
            or any(_IDENTIFIER.fullmatch(value) is None for value in values)
            or any(_contains_sensitive_text(value) for value in values)
        ):
            raise ValueError("metric-name candidates must be unique safe identifiers")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _fields_reconcile(self) -> Self:
        if not self.available and (
            self.records_path_candidates
            or self.field_names
            or self.run_id_field_candidates
            or self.metric_name_candidates
        ):
            raise ValueError("unavailable metric candidates cannot expose structure")
        if any(value not in self.field_names for value in self.run_id_field_candidates):
            raise ValueError("run fields must belong to the exposed metric fields")
        if self.format == "csv" and self.records_path_candidates:
            raise ValueError("CSV candidates cannot use records paths")
        return self


class ConfigCandidate(CandidateBase):
    pass


class RunCandidate(_StrictModel):
    run_id: str
    available: bool
    unavailable_reason: UnavailableReason | None
    source_metric_item_ids: tuple[str, ...]
    available_config_item_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _availability_reconciles(self) -> Self:
        if self.available != (self.unavailable_reason is None):
            raise ValueError("run availability and reason do not reconcile")
        if not self.source_metric_item_ids:
            raise ValueError("run candidates require a metric source")
        return self


class MappingCandidates(_StrictModel):
    targets: tuple[TargetCandidate, ...]
    metric_sources: tuple[MetricSourceCandidate, ...]
    configs: tuple[ConfigCandidate, ...]
    runs: tuple[RunCandidate, ...]


def _mapping_id(kind: Literal["target", "metric", "config"], item: ProjectItem) -> str:
    return f"{kind}-{item.project_item_id.rsplit('-', maxsplit=1)[-1][:16]}"


def _contains_sensitive_text(value: str) -> bool:
    return bool(_SENSITIVE_LABEL.search(value) or _EMAIL.search(value) or _PHONE.search(value))


def _eligible_field_names(values: Iterable[object]) -> tuple[tuple[str, ...], bool]:
    names: list[str] = []
    sensitive = False
    for value in values:
        if not isinstance(value, str):
            continue
        if _contains_sensitive_text(value):
            sensitive = True
            continue
        if _IDENTIFIER.fullmatch(value) is not None and value not in names:
            names.append(value)
    return tuple(names), sensitive


def _artifact_by_path(
    items: tuple[ProjectItem, ...],
    artifacts: tuple[ProjectImportArtifact, ...],
) -> dict[str, ProjectImportArtifact]:
    by_path = {value.relative_path: value for value in artifacts}
    if len(by_path) != len(artifacts) or set(by_path) != {item.relative_path for item in items}:
        raise ValueError("inspection artifacts do not reconcile with items")
    for item in items:
        if by_path[item.relative_path].reference != item.artifact:
            raise ValueError("inspection artifact reference does not reconcile")
    return by_path


def _target_candidate(
    item: ProjectItem,
    artifact: ProjectImportArtifact,
) -> TargetCandidate:
    try:
        metadata = DatasetMetadata.model_validate_json(artifact.content)
        fields, sensitive = _eligible_field_names(metadata.columns)
    except (TypeError, ValueError):
        return TargetCandidate(
            project_item_id=item.project_item_id,
            label=item.relative_path,
            mapping_id=None,
            available=False,
            unavailable_reason="unreadable_source",
            relative_path=item.relative_path,
            field_names=(),
        )
    if sensitive:
        return TargetCandidate(
            project_item_id=item.project_item_id,
            label=_SAFE_DATASET_LABEL,
            mapping_id=None,
            available=False,
            unavailable_reason="sensitive_fields_withheld",
            relative_path=None,
            field_names=(),
        )
    return TargetCandidate(
        project_item_id=item.project_item_id,
        label=item.relative_path,
        mapping_id=_mapping_id("target", item),
        available=True,
        unavailable_reason=None,
        relative_path=item.relative_path,
        field_names=fields,
    )


def _json_record_paths(value: object) -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = []

    def visit(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, list):
            if node and all(isinstance(record, dict) for record in node):
                paths.append(path)
            return
        if isinstance(node, dict):
            for key in sorted(node):
                visit(node[key], (*path, str(key)))

    visit(value, ())
    return tuple(paths)


def _records_at(value: object, path: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    selected = value
    for component in path:
        if not isinstance(selected, dict) or component not in selected:
            return ()
        selected = selected[component]
    if not isinstance(selected, list) or any(not isinstance(record, dict) for record in selected):
        return ()
    return tuple(dict(record) for record in selected)


def _metric_structure(
    item: ProjectItem,
    artifact: ProjectImportArtifact,
) -> (
    tuple[
        Literal["csv", "json"],
        tuple[tuple[str, ...], ...],
        tuple[str, ...],
        tuple[dict[str, object], ...],
    ]
    | None
):
    suffix = item.relative_path.rsplit(".", maxsplit=1)[-1].lower()
    try:
        text = artifact.content.decode("utf-8", errors="strict")
        if suffix == "csv":
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            records = tuple(dict(value) for value in reader)
            fields = tuple(reader.fieldnames or ())
            return "csv", (), fields, records
        if suffix == "json":
            payload: object = json.loads(text)
            paths = _json_record_paths(payload)
            records = tuple(record for path in paths for record in _records_at(payload, path))
            if paths:
                fields = tuple(dict.fromkeys(key for record in records for key in record))
            elif isinstance(payload, dict):
                fields = tuple(str(key) for key in payload)
            else:
                fields = ()
            return "json", paths, fields, records
    except (UnicodeDecodeError, csv.Error, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _run_values(
    records: tuple[dict[str, object], ...],
    run_fields: tuple[str, ...],
) -> tuple[str, ...]:
    if len(run_fields) != 1:
        return ()
    field = run_fields[0]
    values: set[str] = set()
    for record in records:
        value = record.get(field)
        if (
            not isinstance(value, str)
            or _IDENTIFIER.fullmatch(value) is None
            or _contains_sensitive_text(value)
        ):
            return ()
        values.add(value)
    return tuple(sorted(values))


def _metric_name_values(
    records: tuple[dict[str, object], ...],
    fields: tuple[str, ...],
) -> tuple[tuple[str, ...], bool]:
    name_fields = tuple(value for value in fields if value.casefold() in _METRIC_NAME_FIELD_NAMES)
    if len(name_fields) != 1 or not records:
        return (), False
    field = name_fields[0]
    values: set[str] = set()
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            return (), False
        if _contains_sensitive_text(value):
            return (), True
        values.add(value)
    return tuple(sorted(values)), False


def _metric_candidate(
    item: ProjectItem,
    artifact: ProjectImportArtifact,
) -> tuple[MetricSourceCandidate, tuple[str, ...]]:
    structure = _metric_structure(item, artifact)
    suffix = item.relative_path.rsplit(".", maxsplit=1)[-1].lower()
    format_value: Literal["csv", "json"] = "json" if suffix == "json" else "csv"
    if structure is None:
        return (
            MetricSourceCandidate(
                project_item_id=item.project_item_id,
                label=item.relative_path,
                mapping_id=None,
                available=False,
                unavailable_reason="unreadable_source",
                relative_path=item.relative_path,
                format=format_value,
                records_path_candidates=(),
                field_names=(),
                run_id_field_candidates=(),
                metric_name_candidates=(),
            ),
            (),
        )
    format_value, paths, raw_fields, records = structure
    fields, sensitive = _eligible_field_names(raw_fields)
    if sensitive:
        return (
            MetricSourceCandidate(
                project_item_id=item.project_item_id,
                label=_SAFE_METRIC_LABEL,
                mapping_id=None,
                available=False,
                unavailable_reason="sensitive_fields_withheld",
                relative_path=None,
                format=format_value,
                records_path_candidates=(),
                field_names=(),
                run_id_field_candidates=(),
                metric_name_candidates=(),
            ),
            (),
        )
    run_fields = tuple(value for value in fields if value.casefold() in _RUN_FIELD_NAMES)
    metric_names, sensitive_metric_name = _metric_name_values(records, fields)
    if sensitive_metric_name:
        return (
            MetricSourceCandidate(
                project_item_id=item.project_item_id,
                label=_SAFE_METRIC_LABEL,
                mapping_id=None,
                available=False,
                unavailable_reason="sensitive_fields_withheld",
                relative_path=None,
                format=format_value,
                records_path_candidates=(),
                field_names=(),
                run_id_field_candidates=(),
                metric_name_candidates=(),
            ),
            (),
        )
    candidate = MetricSourceCandidate(
        project_item_id=item.project_item_id,
        label=item.relative_path,
        mapping_id=_mapping_id("metric", item),
        available=True,
        unavailable_reason=None,
        relative_path=item.relative_path,
        format=format_value,
        records_path_candidates=paths,
        field_names=fields,
        run_id_field_candidates=run_fields,
        metric_name_candidates=metric_names,
    )
    return candidate, _run_values(records, run_fields)


def build_mapping_candidates(
    items: tuple[ProjectItem, ...],
    artifacts: tuple[ProjectImportArtifact, ...],
) -> MappingCandidates:
    """Build deterministic UI choices without constructing a ProjectBundle."""

    by_path = _artifact_by_path(items, artifacts)
    eligible = tuple(
        sorted(
            (
                item
                for item in items
                if item.visibility == "diagnosis"
                and item.redaction_state != "withheld"
                and item.source_type in {"dataset", "metrics", "config"}
            ),
            key=lambda value: value.relative_path,
        )
    )
    targets: list[TargetCandidate] = []
    metrics: list[MetricSourceCandidate] = []
    configs: list[ConfigCandidate] = []
    run_sources: dict[str, set[str]] = {}
    for item in eligible:
        artifact = by_path[item.relative_path]
        if item.source_type == "dataset":
            targets.append(_target_candidate(item, artifact))
        elif item.source_type == "metrics":
            metric, run_ids = _metric_candidate(item, artifact)
            metrics.append(metric)
            for run_id in run_ids:
                run_sources.setdefault(run_id, set()).add(item.project_item_id)
        elif item.source_type == "config":
            configs.append(
                ConfigCandidate(
                    project_item_id=item.project_item_id,
                    label=item.relative_path,
                    mapping_id=_mapping_id("config", item),
                    available=True,
                    unavailable_reason=None,
                    relative_path=item.relative_path,
                )
            )
    config_ids = tuple(value.project_item_id for value in configs if value.available)
    runs = tuple(
        RunCandidate(
            run_id=run_id,
            available=True,
            unavailable_reason=None,
            source_metric_item_ids=tuple(sorted(run_sources[run_id])),
            available_config_item_ids=config_ids,
        )
        for run_id in sorted(run_sources)
    )
    return MappingCandidates(
        targets=tuple(targets),
        metric_sources=tuple(metrics),
        configs=tuple(configs),
        runs=runs,
    )


def confirm_mapping_is_eligible(
    configuration: ProjectMappingConfiguration,
    candidates: MappingCandidates,
) -> bool:
    """Fail closed when a confirm request routes around preview eligibility."""

    targets = {value.project_item_id: value for value in candidates.targets if value.available}
    target = targets.get(configuration.target.project_item_id)
    if target is None or any(
        field not in target.field_names
        for field in (
            configuration.target.target_field,
            configuration.target.identifier_field,
        )
    ):
        return False

    metric_candidates = {
        value.project_item_id: value for value in candidates.metric_sources if value.available
    }
    for mapping in configuration.metric_sources:
        candidate = metric_candidates.get(mapping.project_item_id)
        if candidate is None or mapping.format != candidate.format:
            return False
        selected_fields = (
            mapping.metric_name_field,
            mapping.metric_value_field,
            mapping.run_id_field,
            *(() if mapping.step_field is None else (mapping.step_field,)),
        )
        if any(value not in candidate.field_names for value in selected_fields):
            return False
        if (
            candidate.run_id_field_candidates
            and mapping.run_id_field not in candidate.run_id_field_candidates
        ):
            return False
        if (
            mapping.format == "json"
            and candidate.records_path_candidates
            and mapping.records_path not in candidate.records_path_candidates
        ):
            return False
        if mapping.format == "csv" and mapping.records_path:
            return False

    config_ids = {value.project_item_id for value in candidates.configs if value.available}
    return not any(
        config_id not in config_ids
        for run in configuration.runs
        for config_id in run.config_item_ids
    )
