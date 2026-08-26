"""Fail-closed semantic mapping for imported project observations."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from typing import Annotated, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.collectors import ProjectFileCollection
from aletheia_lab.project.contracts import ProjectBundle, ProjectItem, build_project_bundle
from aletheia_lab.project.identity import (
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    PROJECT_ITEM_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
    normalize_text,
)
from aletheia_lab.project.importer import ProjectImportArtifact

MAPPING_SCHEMA_VERSION: Final[Literal["project-mapping-configuration/v1"]] = (
    "project-mapping-configuration/v1"
)
MAPPING_RESULT_SCHEMA_VERSION: Final[Literal["project-mapping-result/v1"]] = (
    "project-mapping-result/v1"
)
_IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$"
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_ModelT = TypeVar("_ModelT", bound=BaseModel)

MappingIssueCode = Literal[
    "project_mismatch",
    "bundle_mismatch",
    "collection_mismatch",
    "unknown_item",
    "wrong_item_type",
    "unknown_field",
    "artifact_mismatch",
    "metric_payload_invalid",
    "metric_value_invalid",
    "metric_identity_duplicate",
    "unknown_run",
    "run_without_metrics",
    "baseline_invalid",
]

_ISSUE_MESSAGES: Final[dict[MappingIssueCode, str]] = {
    "project_mismatch": "Mapping configuration belongs to a different project.",
    "bundle_mismatch": "Mapping configuration targets a different ProjectBundle.",
    "collection_mismatch": "Mapping configuration targets a different file collection.",
    "unknown_item": "A mapping references an item outside the bound ProjectBundle.",
    "wrong_item_type": "A mapping references an item with an incompatible source type.",
    "unknown_field": "A required target, identifier or metric field is absent.",
    "artifact_mismatch": "Mapping artifacts do not reconcile with ProjectBundle items.",
    "metric_payload_invalid": "A mapped metric payload cannot be parsed unambiguously.",
    "metric_value_invalid": "A mapped metric value must be one finite number.",
    "metric_identity_duplicate": "Metric observations contain a duplicate canonical identity.",
    "unknown_run": "A metric observation references an undeclared run.",
    "run_without_metrics": "A declared run has no mapped metric observation.",
    "baseline_invalid": "The selected baseline must identify exactly one declared run.",
}


class ProjectMappingError(ValueError):
    """Raised when a valid mapping result is required but unavailable."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


def _identifier(value: str, *, label: str) -> str:
    normalize_text(value, label=label, max_length=128)
    if re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
        raise ValueError(f"{label} must be a portable identifier")
    return value


class DatasetTargetMapping(_StrictFrozenModel):
    mapping_id: str
    project_item_id: str = Field(pattern=PROJECT_ITEM_ID_PATTERN)
    target_field: str
    identifier_field: str

    @field_validator("mapping_id", "target_field", "identifier_field")
    @classmethod
    def _canonical_identifier(cls, value: str, info: object) -> str:
        return _identifier(value, label=str(getattr(info, "field_name", "mapping field")))

    @model_validator(mode="after")
    def _fields_are_distinct(self) -> DatasetTargetMapping:
        if self.target_field == self.identifier_field:
            raise ValueError("target and identifier fields must be distinct")
        return self


class MetricSourceMapping(_StrictFrozenModel):
    mapping_id: str
    project_item_id: str = Field(pattern=PROJECT_ITEM_ID_PATTERN)
    format: Literal["csv", "json"]
    records_path: tuple[str, ...] = ()
    metric_name_field: str
    metric_value_field: str
    run_id_field: str
    step_field: str | None = None

    @field_validator(
        "mapping_id", "metric_name_field", "metric_value_field", "run_id_field", "step_field"
    )
    @classmethod
    def _canonical_identifier(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _identifier(value, label=str(getattr(info, "field_name", "metric field")))

    @field_validator("records_path")
    @classmethod
    def _canonical_path(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(value, label="metric records path") for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("metric records path must not repeat components")
        return normalized

    @model_validator(mode="after")
    def _fields_are_distinct(self) -> MetricSourceMapping:
        fields = (self.metric_name_field, self.metric_value_field, self.run_id_field)
        if len(fields) != len(set(fields)):
            raise ValueError("metric name, value and run fields must be distinct")
        if self.step_field is not None and self.step_field in fields:
            raise ValueError("metric step field must be distinct")
        return self


class RunMapping(_StrictFrozenModel):
    run_id: str
    config_item_ids: tuple[str, ...] = ()

    @field_validator("run_id")
    @classmethod
    def _canonical_run(cls, value: str) -> str:
        return _identifier(value, label="run_id")

    @field_validator("config_item_ids")
    @classmethod
    def _canonical_configs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(PROJECT_ITEM_ID_PATTERN, value) is None for value in values):
            raise ValueError("config_item_ids contain an invalid project item ID")
        if len(values) != len(set(values)):
            raise ValueError("config_item_ids must be unique")
        return tuple(sorted(values))


def _configuration_payload(
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
    target: DatasetTargetMapping,
    metric_sources: tuple[MetricSourceMapping, ...],
    runs: tuple[RunMapping, ...],
    baseline_run_id: str,
) -> dict[str, object]:
    return {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "project_id": project_id,
        "project_bundle_id": project_bundle_id,
        "file_collection_sha256": file_collection_sha256,
        "target": target.model_dump(mode="json"),
        "metric_sources": [value.model_dump(mode="json") for value in metric_sources],
        "runs": [value.model_dump(mode="json") for value in runs],
        "baseline_run_id": baseline_run_id,
    }


class ProjectMappingConfiguration(_StrictFrozenModel):
    schema_version: Literal["project-mapping-configuration/v1"] = MAPPING_SCHEMA_VERSION
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    project_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    file_collection_sha256: Sha256
    target: DatasetTargetMapping
    metric_sources: tuple[MetricSourceMapping, ...]
    runs: tuple[RunMapping, ...]
    baseline_run_id: str
    mapping_sha256: Sha256

    @field_validator("metric_sources")
    @classmethod
    def _canonical_metrics(
        cls, values: tuple[MetricSourceMapping, ...]
    ) -> tuple[MetricSourceMapping, ...]:
        ids = tuple(value.mapping_id for value in values)
        items = tuple(value.project_item_id for value in values)
        if not values or len(ids) != len(set(ids)) or len(items) != len(set(items)):
            raise ValueError("metric source mappings must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.mapping_id))

    @field_validator("runs")
    @classmethod
    def _canonical_runs(cls, values: tuple[RunMapping, ...]) -> tuple[RunMapping, ...]:
        ids = tuple(value.run_id for value in values)
        if not values or len(ids) != len(set(ids)):
            raise ValueError("run mappings must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.run_id))

    @field_validator("baseline_run_id")
    @classmethod
    def _canonical_baseline(cls, value: str) -> str:
        return _identifier(value, label="baseline_run_id")

    @model_validator(mode="after")
    def _digest_reconciles(self) -> ProjectMappingConfiguration:
        payload = _configuration_payload(
            project_id=self.project_id,
            project_bundle_id=self.project_bundle_id,
            file_collection_sha256=self.file_collection_sha256,
            target=self.target,
            metric_sources=self.metric_sources,
            runs=self.runs,
            baseline_run_id=self.baseline_run_id,
        )
        if self.mapping_sha256 != canonical_project_sha256(payload):
            raise ValueError("mapping_sha256 does not match mapping configuration")
        return self


def build_project_mapping_configuration(
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
    target: DatasetTargetMapping,
    metric_sources: tuple[MetricSourceMapping, ...],
    runs: tuple[RunMapping, ...],
    baseline_run_id: str,
) -> ProjectMappingConfiguration:
    sorted_metrics = tuple(sorted(metric_sources, key=lambda value: value.mapping_id))
    sorted_runs = tuple(sorted(runs, key=lambda value: value.run_id))
    payload = _configuration_payload(
        project_id=project_id,
        project_bundle_id=project_bundle_id,
        file_collection_sha256=file_collection_sha256,
        target=target,
        metric_sources=sorted_metrics,
        runs=sorted_runs,
        baseline_run_id=baseline_run_id,
    )
    return ProjectMappingConfiguration(
        project_id=project_id,
        project_bundle_id=project_bundle_id,
        file_collection_sha256=file_collection_sha256,
        target=target,
        metric_sources=sorted_metrics,
        runs=sorted_runs,
        baseline_run_id=baseline_run_id,
        mapping_sha256=canonical_project_sha256(payload),
    )


class MappingValidationIssue(_StrictFrozenModel):
    code: MappingIssueCode
    severity: Literal["blocker"] = "blocker"
    message: str
    candidate_id: str
    subject_sha256: Sha256

    @field_validator("candidate_id")
    @classmethod
    def _canonical_candidate(cls, value: str) -> str:
        return normalize_text(value, label="mapping candidate ID", max_length=256)

    @model_validator(mode="after")
    def _message_is_fixed(self) -> MappingValidationIssue:
        if self.message != _ISSUE_MESSAGES[self.code]:
            raise ValueError("mapping issue message must match its code")
        return self


class MappingCandidateDecision(_StrictFrozenModel):
    candidate_id: str
    status: Literal["accepted", "rejected"]
    issue_codes: tuple[MappingIssueCode, ...] = ()

    @field_validator("candidate_id")
    @classmethod
    def _canonical_candidate(cls, value: str) -> str:
        return normalize_text(value, label="mapping candidate ID", max_length=256)

    @field_validator("issue_codes")
    @classmethod
    def _canonical_codes(cls, values: tuple[MappingIssueCode, ...]) -> tuple[MappingIssueCode, ...]:
        if len(values) != len(set(values)):
            raise ValueError("mapping decision issue codes must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _status_reconciles(self) -> MappingCandidateDecision:
        if (self.status == "accepted") == bool(self.issue_codes):
            raise ValueError("accepted decisions have no issues; rejected decisions require issues")
        return self


class MetricObservation(_StrictFrozenModel):
    observation_id: str = Field(pattern=r"^p3-metric-[0-9a-f]{64}$")
    source_mapping_id: str
    project_item_id: str = Field(pattern=PROJECT_ITEM_ID_PATTERN)
    run_id: str
    metric_name: str
    metric_value: float
    step: int | None = Field(default=None, ge=0)
    source_record_index: int = Field(ge=0)

    @field_validator("source_mapping_id", "run_id", "metric_name")
    @classmethod
    def _canonical_identifier(cls, value: str, info: object) -> str:
        return _identifier(value, label=str(getattr(info, "field_name", "metric identity")))

    @model_validator(mode="after")
    def _value_is_finite(self) -> MetricObservation:
        if not math.isfinite(self.metric_value):
            raise ValueError("metric_value must be finite")
        payload = self.model_dump(mode="json", exclude={"observation_id"})
        if self.observation_id != f"p3-metric-{canonical_project_sha256(payload)}":
            raise ValueError("observation_id does not match metric identity")
        return self


class ProjectMappingResult(_StrictFrozenModel):
    schema_version: Literal["project-mapping-result/v1"] = MAPPING_RESULT_SCHEMA_VERSION
    status: Literal["valid", "blocked"]
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    project_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    mapping_sha256: Sha256
    decisions: tuple[MappingCandidateDecision, ...]
    issues: tuple[MappingValidationIssue, ...]
    metric_observations: tuple[MetricObservation, ...]
    result_sha256: Sha256

    @model_validator(mode="after")
    def _result_reconciles(self) -> ProjectMappingResult:
        candidate_ids = tuple(value.candidate_id for value in self.decisions)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("mapping decisions must reconcile unique candidates")
        rejected = {value.candidate_id for value in self.decisions if value.status == "rejected"}
        issue_candidates = {value.candidate_id for value in self.issues}
        if rejected != issue_candidates:
            raise ValueError("mapping issues must exactly reconcile rejected candidates")
        if self.status == "valid" and (self.issues or not self.metric_observations):
            raise ValueError("valid mapping requires observations and no issues")
        if self.status == "blocked" and (not self.issues or self.metric_observations):
            raise ValueError("blocked mapping must fail closed without observations")
        payload = self.model_dump(mode="json", exclude={"result_sha256"})
        if self.result_sha256 != canonical_project_sha256(payload):
            raise ValueError("result_sha256 does not match mapping result")
        return self


def _issue(code: MappingIssueCode, candidate_id: str, subject: object) -> MappingValidationIssue:
    return MappingValidationIssue(
        code=code,
        message=_ISSUE_MESSAGES[code],
        candidate_id=candidate_id,
        subject_sha256=canonical_project_sha256(subject),
    )


def _record_value(record: dict[str, object], field: str) -> object:
    if field not in record:
        raise KeyError(field)
    return record[field]


def _metric_records(mapping: MetricSourceMapping, content: bytes) -> list[dict[str, object]]:
    try:
        text = content.decode("utf-8", errors="strict")
        if mapping.format == "csv":
            parsed = list(csv.DictReader(io.StringIO(text, newline=""), strict=True))
            return [dict(record) for record in parsed]
        payload: object = json.loads(text)
        for component in mapping.records_path:
            if not isinstance(payload, dict) or component not in payload:
                raise ValueError("records path is absent")
            payload = payload[component]
        if not isinstance(payload, list) or any(not isinstance(record, dict) for record in payload):
            raise ValueError("metric records must be a list of objects")
        return [dict(record) for record in payload]
    except (UnicodeDecodeError, csv.Error, json.JSONDecodeError, ValueError) as exc:
        raise ProjectMappingError("metric payload invalid") from exc


def _finite_number(value: object) -> float:
    if isinstance(value, bool):
        raise ProjectMappingError("metric value invalid")
    try:
        number = float(value) if isinstance(value, str | int | float) else math.nan
    except ValueError as exc:
        raise ProjectMappingError("metric value invalid") from exc
    if not math.isfinite(number):
        raise ProjectMappingError("metric value invalid")
    return 0.0 if number == 0.0 else number


def _step_number(value: object) -> int:
    if isinstance(value, bool):
        raise ProjectMappingError("metric step invalid")
    if isinstance(value, int):
        step = value
    elif (isinstance(value, str) and re.fullmatch(r"\d+", value) is not None) or (
        isinstance(value, float) and value.is_integer()
    ):
        step = int(value)
    else:
        raise ProjectMappingError("metric step invalid")
    if step < 0:
        raise ProjectMappingError("metric step invalid")
    return step


def _metric_observations(
    mapping: MetricSourceMapping, item: ProjectItem, artifact: ProjectImportArtifact
) -> tuple[MetricObservation, ...]:
    observations: list[MetricObservation] = []
    for index, record in enumerate(_metric_records(mapping, artifact.content)):
        try:
            raw_name = _record_value(record, mapping.metric_name_field)
            raw_run = _record_value(record, mapping.run_id_field)
            raw_value = _record_value(record, mapping.metric_value_field)
            raw_step = None if mapping.step_field is None else _record_value(record, mapping.step_field)
        except KeyError as exc:
            raise ProjectMappingError("metric field missing") from exc
        try:
            if not isinstance(raw_name, str) or not isinstance(raw_run, str):
                raise TypeError("metric identity fields must be strings")
            name = _identifier(raw_name, label="metric_name")
            run_id = _identifier(raw_run, label="run_id")
        except (TypeError, ValueError) as exc:
            raise ProjectMappingError("metric identity invalid") from exc
        value = _finite_number(raw_value)
        step = None if raw_step is None else _step_number(raw_step)
        provisional = {
            "source_mapping_id": mapping.mapping_id,
            "project_item_id": item.project_item_id,
            "run_id": run_id,
            "metric_name": name,
            "metric_value": value,
            "step": step,
            "source_record_index": index,
        }
        observations.append(
            MetricObservation(
                observation_id=f"p3-metric-{canonical_project_sha256(provisional)}",
                source_mapping_id=mapping.mapping_id,
                project_item_id=item.project_item_id,
                run_id=run_id,
                metric_name=name,
                metric_value=value,
                step=step,
                source_record_index=index,
            )
        )
    return tuple(observations)


def validate_project_mapping(
    bundle: ProjectBundle,
    artifacts: tuple[ProjectImportArtifact, ...],
    collection: ProjectFileCollection,
    configuration: ProjectMappingConfiguration,
) -> ProjectMappingResult:
    """Validate all candidates, reconcile their disposition, and fail closed."""

    checked_bundle = _checked(bundle)
    checked_collection = _checked(collection)
    checked_config = _checked(configuration)
    candidate_ids = [
        "configuration",
        f"target:{checked_config.target.mapping_id}",
        *(f"metric:{value.mapping_id}" for value in checked_config.metric_sources),
        *(f"run:{value.run_id}" for value in checked_config.runs),
        "baseline",
    ]
    issues: list[MappingValidationIssue] = []

    def reject(code: MappingIssueCode, candidate: str, subject: object) -> None:
        issues.append(_issue(code, candidate, subject))

    if checked_config.project_id != checked_bundle.project_id:
        reject("project_mismatch", "configuration", checked_config.project_id)
    if checked_config.project_bundle_id != checked_bundle.project_bundle_id:
        reject("bundle_mismatch", "configuration", checked_config.project_bundle_id)
    if (
        checked_collection.project_id != checked_bundle.project_id
        or checked_collection.project_bundle_id != checked_bundle.project_bundle_id
        or checked_config.file_collection_sha256 != checked_collection.collection_sha256
    ):
        reject("collection_mismatch", "configuration", checked_collection.collection_sha256)

    items = {item.project_item_id: item for item in checked_bundle.items}
    observations = {value.project_item_id: value for value in checked_collection.observations}
    if set(observations) != set(items):
        reject("collection_mismatch", "configuration", "observation-census")
    for item_id, observation in observations.items():
        item = items.get(item_id)
        if item is None:
            continue
        if (
            observation.relative_path != item.relative_path
            or observation.source_type != item.source_type
            or observation.source_sha256 != item.content_sha256
            or observation.artifact_sha256 != item.artifact.sha256
            or observation.visibility != item.visibility
            or observation.redaction_state != item.redaction_state
        ):
            reject("collection_mismatch", "configuration", item_id)
    artifact_by_item: dict[str, ProjectImportArtifact] = {}
    item_by_path = {item.relative_path: item for item in checked_bundle.items}
    for artifact in artifacts:
        item = item_by_path.get(artifact.relative_path)
        if item is None or item.artifact != artifact.reference or item.project_item_id in artifact_by_item:
            reject("artifact_mismatch", "configuration", artifact.relative_path)
            continue
        artifact_by_item[item.project_item_id] = artifact
    if set(artifact_by_item) != set(items):
        reject("artifact_mismatch", "configuration", "artifact-census")

    target_candidate = f"target:{checked_config.target.mapping_id}"
    target_item = items.get(checked_config.target.project_item_id)
    target_observation = observations.get(checked_config.target.project_item_id)
    if target_item is None or target_observation is None:
        reject("unknown_item", target_candidate, checked_config.target.project_item_id)
    elif target_item.source_type != "dataset" or target_observation.dataset is None:
        reject("wrong_item_type", target_candidate, target_item.project_item_id)
    else:
        columns = set(target_observation.dataset.columns)
        if checked_config.target.target_field not in columns:
            reject("unknown_field", target_candidate, checked_config.target.target_field)
        if checked_config.target.identifier_field not in columns:
            reject("unknown_field", target_candidate, checked_config.target.identifier_field)

    extracted: list[MetricObservation] = []
    for metric in checked_config.metric_sources:
        candidate = f"metric:{metric.mapping_id}"
        item = items.get(metric.project_item_id)
        source_artifact = artifact_by_item.get(metric.project_item_id)
        if item is None or source_artifact is None:
            reject("unknown_item", candidate, metric.project_item_id)
            continue
        if item.source_type != "metrics":
            reject("wrong_item_type", candidate, item.project_item_id)
            continue
        suffix = item.relative_path.rsplit(".", maxsplit=1)[-1].lower()
        if suffix != metric.format:
            reject("wrong_item_type", candidate, suffix)
            continue
        try:
            current = _metric_observations(metric, item, source_artifact)
        except ProjectMappingError as exc:
            code: MappingIssueCode = (
                "metric_value_invalid" if "value" in str(exc) or "record" in str(exc) else "metric_payload_invalid"
            )
            reject(code, candidate, metric.mapping_id)
            continue
        if not current:
            reject("metric_payload_invalid", candidate, metric.mapping_id)
            continue
        extracted.extend(current)

    declared_runs = {value.run_id: value for value in checked_config.runs}
    identities: set[tuple[str, str, int | None]] = set()
    counts = {run_id: 0 for run_id in declared_runs}
    for metric_observation in extracted:
        identity = (
            metric_observation.run_id,
            metric_observation.metric_name,
            metric_observation.step,
        )
        candidate = f"metric:{metric_observation.source_mapping_id}"
        if identity in identities:
            reject("metric_identity_duplicate", candidate, identity)
        identities.add(identity)
        if metric_observation.run_id not in declared_runs:
            reject("unknown_run", candidate, metric_observation.run_id)
        else:
            counts[metric_observation.run_id] += 1

    for run in checked_config.runs:
        candidate = f"run:{run.run_id}"
        for item_id in run.config_item_ids:
            item = items.get(item_id)
            if item is None:
                reject("unknown_item", candidate, item_id)
            elif item.source_type != "config":
                reject("wrong_item_type", candidate, item_id)
        if counts[run.run_id] == 0:
            reject("run_without_metrics", candidate, run.run_id)
    if checked_config.baseline_run_id not in declared_runs:
        reject("baseline_invalid", "baseline", checked_config.baseline_run_id)

    issue_by_candidate: dict[str, set[MappingIssueCode]] = {value: set() for value in candidate_ids}
    for issue in issues:
        issue_by_candidate[issue.candidate_id].add(issue.code)
    decisions = tuple(
        MappingCandidateDecision(
            candidate_id=candidate,
            status="rejected" if issue_by_candidate[candidate] else "accepted",
            issue_codes=tuple(sorted(issue_by_candidate[candidate])),
        )
        for candidate in candidate_ids
    )
    sorted_issues = tuple(sorted(issues, key=lambda value: (value.candidate_id, value.code)))
    blocked = bool(sorted_issues)
    final_observations = () if blocked else tuple(
        sorted(
            extracted,
            key=lambda value: (
                value.run_id,
                value.metric_name,
                -1 if value.step is None else value.step,
            ),
        )
    )
    provisional = {
        "schema_version": MAPPING_RESULT_SCHEMA_VERSION,
        "status": "blocked" if blocked else "valid",
        "project_id": checked_bundle.project_id,
        "project_bundle_id": checked_bundle.project_bundle_id,
        "mapping_sha256": checked_config.mapping_sha256,
        "decisions": [value.model_dump(mode="json") for value in decisions],
        "issues": [value.model_dump(mode="json") for value in sorted_issues],
        "metric_observations": [value.model_dump(mode="json") for value in final_observations],
    }
    return ProjectMappingResult(
        status="blocked" if blocked else "valid",
        project_id=checked_bundle.project_id,
        project_bundle_id=checked_bundle.project_bundle_id,
        mapping_sha256=checked_config.mapping_sha256,
        decisions=decisions,
        issues=sorted_issues,
        metric_observations=final_observations,
        result_sha256=canonical_project_sha256(provisional),
    )


def bind_project_mapping(
    bundle: ProjectBundle,
    configuration: ProjectMappingConfiguration,
    result: ProjectMappingResult,
) -> ProjectBundle:
    """Bind a validated configuration into a new immutable ProjectBundle identity."""

    checked_bundle = _checked(bundle)
    checked_config = _checked(configuration)
    checked_result = _checked(result)
    if (
        checked_result.status != "valid"
        or checked_result.project_bundle_id != checked_bundle.project_bundle_id
        or checked_result.mapping_sha256 != checked_config.mapping_sha256
    ):
        raise ProjectMappingError("only the matching valid mapping result can bind a bundle")
    return build_project_bundle(
        project_id=checked_bundle.project_id,
        display_name=checked_bundle.display_name,
        granted_root_fingerprint=checked_bundle.granted_root_fingerprint,
        created_at=checked_bundle.created_at,
        updated_at=checked_bundle.updated_at,
        items=checked_bundle.items,
        permission_policy_sha256=checked_bundle.permission_policy_sha256,
        provider_policy_sha256=checked_bundle.provider_policy_sha256,
        mapping_configuration_sha256=checked_config.mapping_sha256,
        validation_summary_sha256=checked_bundle.validation_summary_sha256,
        ingestion_report_sha256=checked_bundle.ingestion_report_sha256,
        deletion_policy_sha256=checked_bundle.deletion_policy_sha256,
        retention_policy_sha256=checked_bundle.retention_policy_sha256,
        snapshot_refs=checked_bundle.snapshot_refs,
        evidence_bundle_refs=checked_bundle.evidence_bundle_refs,
    )
