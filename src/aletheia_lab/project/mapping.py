"""Fail-closed semantic mapping for imported project observations."""

from __future__ import annotations

import math
import re
from typing import Annotated, Final, Generic, Literal, Self, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from aletheia_lab.project.collectors import ProjectFileCollection
from aletheia_lab.project.contracts import ProjectBundle, build_project_bundle
from aletheia_lab.project.identity import (
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    PROJECT_ITEM_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
    normalize_text,
)
from aletheia_lab.project.importer import ProjectImportArtifact
from aletheia_lab.project.mapping_identifiers import (
    _IGNORABLE_IDENTIFIER_CHARACTERS as _IGNORABLE_IDENTIFIER_CHARACTERS,
)
from aletheia_lab.project.mapping_identifiers import mapping_identifier
from aletheia_lab.project.mapping_identifiers import metric_label as metric_label

from .mapping_observations import (
    ProjectMappingError as ProjectMappingError,
)
from .mapping_observations import (
    _finite_number as _finite_number,
)
from .mapping_observations import (
    _metric_observations,
)
from .mapping_observations import (
    _metric_records as _metric_records,
)
from .mapping_observations import (
    _record_value as _record_value,
)
from .mapping_observations import (
    _step_number as _step_number,
)

_identifier = mapping_identifier

MAPPING_SCHEMA_VERSION: Final[Literal["project-mapping-configuration/v1"]] = (
    "project-mapping-configuration/v1"
)
MAPPING_RESULT_SCHEMA_VERSION: Final[Literal["project-mapping-result/v1"]] = (
    "project-mapping-result/v1"
)
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


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


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


class _MetricSourceMappingBase(_StrictFrozenModel):
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
    def _fields_are_distinct(self) -> Self:
        fields = (self.metric_name_field, self.metric_value_field, self.run_id_field)
        if len(fields) != len(set(fields)):
            raise ValueError("metric name, value and run fields must be distinct")
        if self.step_field is not None and self.step_field in fields:
            raise ValueError("metric step field must be distinct")
        return self


class MetricSourceMapping(_MetricSourceMappingBase):
    pass


class MetricSourceMappingV2(_MetricSourceMappingBase):
    class_field: str | None = None
    unit_field: str | None = None

    @field_validator("class_field", "unit_field")
    @classmethod
    def _optional_field(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _identifier(value, label=str(getattr(info, "field_name", "metric field")))

    @model_validator(mode="after")
    def _dimension_fields_are_distinct(self) -> Self:
        selected = [value for value in (self.metric_name_field, self.metric_value_field, self.run_id_field,
                    self.step_field, self.class_field, self.unit_field) if value is not None]
        if len(selected) != len(set(selected)):
            raise ValueError("metric dimension fields must be distinct")
        return self


AnyMetricSourceMapping: TypeAlias = MetricSourceMapping | MetricSourceMappingV2


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


class _MetricDefinitionBase(_StrictFrozenModel):
    metric_name: str
    direction: Literal["higher_is_better", "lower_is_better"]
    regression_threshold: float = Field(ge=0)

    @field_validator("metric_name")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        return _identifier(value, label="metric_name")

    @field_validator("regression_threshold")
    @classmethod
    def _finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("regression_threshold must be finite")
        return 0.0 if value == 0.0 else value


class MetricDefinition(_MetricDefinitionBase):
    pass


class MetricDefinitionV2(_MetricDefinitionBase):
    unit: str | None

    @field_validator("unit")
    @classmethod
    def _unit_label(cls, value: str | None) -> str | None:
        return None if value is None else metric_label(value, label="metric unit", max_length=64)


AnyMetricDefinition: TypeAlias = MetricDefinition | MetricDefinitionV2
_SourceT = TypeVar("_SourceT", MetricSourceMapping, MetricSourceMappingV2)
_DefinitionT = TypeVar("_DefinitionT", MetricDefinition, MetricDefinitionV2)


def _configuration_payload(
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
    target: DatasetTargetMapping,
    metric_sources: tuple[AnyMetricSourceMapping, ...],
    runs: tuple[RunMapping, ...],
    baseline_run_id: str,
    metric_definitions: tuple[AnyMetricDefinition, ...] = (),
    schema_version: str = MAPPING_SCHEMA_VERSION,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "project_id": project_id,
        "project_bundle_id": project_bundle_id,
        "file_collection_sha256": file_collection_sha256,
        "target": target.model_dump(mode="json"),
        "metric_sources": [value.model_dump(mode="json") for value in metric_sources],
        "runs": [value.model_dump(mode="json") for value in runs],
        "baseline_run_id": baseline_run_id,
    }
    if metric_definitions:
        payload["metric_definitions"] = [
            value.model_dump(mode="json") for value in metric_definitions
        ]
    return payload


class _MappingConfigurationBase(_StrictFrozenModel, Generic[_SourceT, _DefinitionT]):
    schema_version: str
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    project_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    file_collection_sha256: Sha256
    target: DatasetTargetMapping
    metric_sources: tuple[_SourceT, ...]
    runs: tuple[RunMapping, ...]
    baseline_run_id: str
    metric_definitions: tuple[_DefinitionT, ...] = ()
    mapping_sha256: Sha256

    @field_validator("metric_sources")
    @classmethod
    def _canonical_metrics(
        cls, values: tuple[_SourceT, ...]
    ) -> tuple[_SourceT, ...]:
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

    @field_validator("metric_definitions")
    @classmethod
    def _canonical_definitions(
        cls,
        values: tuple[_DefinitionT, ...],
    ) -> tuple[_DefinitionT, ...]:
        names = tuple(value.metric_name for value in values)
        if len(names) != len(set(names)):
            raise ValueError("metric definitions must have unique names")
        return tuple(sorted(values, key=lambda value: value.metric_name))

    @field_validator("baseline_run_id")
    @classmethod
    def _canonical_baseline(cls, value: str) -> str:
        return _identifier(value, label="baseline_run_id")

    @model_validator(mode="after")
    def _digest_reconciles(self) -> Self:
        payload = _configuration_payload(
            project_id=self.project_id,
            project_bundle_id=self.project_bundle_id,
            file_collection_sha256=self.file_collection_sha256,
            target=self.target,
            metric_sources=self.metric_sources,
            runs=self.runs,
            baseline_run_id=self.baseline_run_id,
            metric_definitions=self.metric_definitions,
            schema_version=self.schema_version,
        )
        if self.mapping_sha256 != canonical_project_sha256(payload):
            raise ValueError("mapping_sha256 does not match mapping configuration")
        return self


class ProjectMappingConfiguration(_MappingConfigurationBase[MetricSourceMapping, MetricDefinition]):
    schema_version: Literal["project-mapping-configuration/v1"] = MAPPING_SCHEMA_VERSION


class ProjectMappingConfigurationV2(_MappingConfigurationBase[MetricSourceMappingV2, MetricDefinitionV2]):
    schema_version: Literal["project-mapping-configuration/v2"] = "project-mapping-configuration/v2"


AnyMappingConfiguration: TypeAlias = ProjectMappingConfiguration | ProjectMappingConfigurationV2
_CONFIGURATION_ADAPTER = TypeAdapter(Annotated[AnyMappingConfiguration, Field(discriminator="schema_version")])


def parse_mapping_configuration(value: object) -> AnyMappingConfiguration:
    return (_CONFIGURATION_ADAPTER.validate_json(value) if isinstance(value, str | bytes)
            else _CONFIGURATION_ADAPTER.validate_python(value))


def build_project_mapping_configuration(
    *,
    project_id: str,
    project_bundle_id: str,
    file_collection_sha256: str,
    target: DatasetTargetMapping,
    metric_sources: tuple[AnyMetricSourceMapping, ...],
    runs: tuple[RunMapping, ...],
    baseline_run_id: str,
    metric_definitions: tuple[AnyMetricDefinition, ...] = (),
    schema_version: Literal["project-mapping-configuration/v1", "project-mapping-configuration/v2"] = MAPPING_SCHEMA_VERSION,
) -> AnyMappingConfiguration:
    sorted_metrics = tuple(sorted(metric_sources, key=lambda value: value.mapping_id))
    sorted_runs = tuple(sorted(runs, key=lambda value: value.run_id))
    sorted_definitions = tuple(
        sorted(metric_definitions, key=lambda value: value.metric_name)
    )
    payload = _configuration_payload(
        project_id=project_id,
        project_bundle_id=project_bundle_id,
        file_collection_sha256=file_collection_sha256,
        target=target,
        metric_sources=sorted_metrics,
        runs=sorted_runs,
        baseline_run_id=baseline_run_id,
        metric_definitions=sorted_definitions,
        schema_version=schema_version,
    )
    model = ProjectMappingConfigurationV2 if schema_version.endswith("/v2") else ProjectMappingConfiguration
    return model.model_validate(dict(
        project_id=project_id,
        project_bundle_id=project_bundle_id,
        file_collection_sha256=file_collection_sha256,
        target=target,
        metric_sources=sorted_metrics,
        runs=sorted_runs,
        baseline_run_id=baseline_run_id,
        metric_definitions=sorted_definitions,
        mapping_sha256=canonical_project_sha256(payload),
    ))


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


class _MetricObservationBase(_StrictFrozenModel):
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
    def _value_is_finite(self) -> Self:
        if not math.isfinite(self.metric_value):
            raise ValueError("metric_value must be finite")
        payload = self.model_dump(mode="json", exclude={"observation_id"})
        if self.observation_id != f"p3-metric-{canonical_project_sha256(payload)}":
            raise ValueError("observation_id does not match metric identity")
        return self


class MetricObservation(_MetricObservationBase):
    pass


class MetricObservationV2(_MetricObservationBase):
    class_label: str | None
    unit: str | None
    unit_origin: Literal["source", "declared", "unknown"]

    @field_validator("class_label", "unit")
    @classmethod
    def _dimension_label(cls, value: str | None, info: object) -> str | None:
        name = str(getattr(info, "field_name", "metric label"))
        return None if value is None else metric_label(value, label=name, max_length=64 if name == "unit" else 128)

    @model_validator(mode="after")
    def _unit_origin_matches(self) -> Self:
        if (self.unit is None) != (self.unit_origin == "unknown"):
            raise ValueError("metric unit origin must preserve known or unknown unit state")
        return self


AnyMetricObservation: TypeAlias = MetricObservation | MetricObservationV2
MetricIdentity: TypeAlias = tuple[str, str, str | None, str | None, int | None]
_ObservationT = TypeVar("_ObservationT", MetricObservation, MetricObservationV2)


def metric_identity(value: AnyMetricObservation) -> MetricIdentity:
    return (value.run_id, value.metric_name, getattr(value, "class_label", None),
            getattr(value, "unit", None), value.step)


def metric_sort_key(value: AnyMetricObservation) -> tuple[str, str, str, str, int]:
    identity = metric_identity(value)
    return identity[0], identity[1], identity[2] or "", identity[3] or "", -1 if value.step is None else value.step


class _MappingResultBase(_StrictFrozenModel, Generic[_ObservationT]):
    schema_version: str
    status: Literal["valid", "blocked"]
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    project_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    mapping_sha256: Sha256
    decisions: tuple[MappingCandidateDecision, ...]
    issues: tuple[MappingValidationIssue, ...]
    metric_observations: tuple[_ObservationT, ...]
    result_sha256: Sha256

    @model_validator(mode="after")
    def _result_reconciles(self) -> Self:
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


class ProjectMappingResult(_MappingResultBase[MetricObservation]):
    schema_version: Literal["project-mapping-result/v1"] = MAPPING_RESULT_SCHEMA_VERSION


class ProjectMappingResultV2(_MappingResultBase[MetricObservationV2]):
    schema_version: Literal["project-mapping-result/v2"] = "project-mapping-result/v2"

    @model_validator(mode="after")
    def _units_are_consistent(self) -> Self:
        validate_metric_units(self.metric_observations)
        return self


AnyMappingResult: TypeAlias = ProjectMappingResult | ProjectMappingResultV2
_RESULT_ADAPTER = TypeAdapter(Annotated[AnyMappingResult, Field(discriminator="schema_version")])


def parse_mapping_result(value: object) -> AnyMappingResult:
    return (_RESULT_ADAPTER.validate_json(value) if isinstance(value, str | bytes)
            else _RESULT_ADAPTER.validate_python(value))


def validate_metric_units(observations: tuple[AnyMetricObservation, ...]) -> None:
    units: dict[str, str | None] = {}
    for observation in observations:
        unit = getattr(observation, "unit", None)
        if observation.metric_name in units and units[observation.metric_name] != unit:
            raise ProjectMappingError("one metric cannot contain incompatible units")
        units[observation.metric_name] = unit


def validate_mapping_dimensions(configuration: AnyMappingConfiguration, result: AnyMappingResult) -> None:
    version2 = isinstance(configuration, ProjectMappingConfigurationV2)
    if version2 != isinstance(result, ProjectMappingResultV2):
        raise ProjectMappingError("mapping configuration and result versions differ")
    if not version2 or not isinstance(configuration, ProjectMappingConfigurationV2):
        return
    sources = {value.mapping_id: value for value in configuration.metric_sources}
    definitions = {value.metric_name: value for value in configuration.metric_definitions}
    for observation in result.metric_observations:
        if not isinstance(observation, MetricObservationV2):
            raise ProjectMappingError("v2 mapping requires v2 metric observations")
        source = sources.get(observation.source_mapping_id)
        definition = definitions.get(observation.metric_name)
        if (source is None or definition is None or source.project_item_id != observation.project_item_id
                or observation.unit != definition.unit
                or (source.class_field is None) != (observation.class_label is None)):
            raise ProjectMappingError("mapping metric dimensions do not reconcile")
        expected = "unknown" if definition.unit is None else "source" if source.unit_field is not None else "declared"
        if observation.unit_origin != expected:
            raise ProjectMappingError("mapping metric unit provenance does not reconcile")


def _issue(code: MappingIssueCode, candidate_id: str, subject: object) -> MappingValidationIssue:
    return MappingValidationIssue(
        code=code,
        message=_ISSUE_MESSAGES[code],
        candidate_id=candidate_id,
        subject_sha256=canonical_project_sha256(subject),
    )


def validate_project_mapping(
    bundle: ProjectBundle,
    artifacts: tuple[ProjectImportArtifact, ...],
    collection: ProjectFileCollection,
    configuration: AnyMappingConfiguration,
) -> AnyMappingResult:
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

    extracted: list[AnyMetricObservation] = []
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
            current = _metric_observations(metric, item, source_artifact, checked_config.metric_definitions)
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
    identities: set[tuple[object, ...]] = set()
    counts = {run_id: 0 for run_id in declared_runs}
    for metric_observation in extracted:
        identity: tuple[object, ...] = (
            metric_observation.run_id,
            metric_observation.metric_name,
            metric_observation.step,
        )
        if isinstance(metric_observation, MetricObservationV2):
            identity = metric_identity(metric_observation)
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
            key=metric_sort_key,
        )
    )
    provisional = {
        "schema_version": "project-mapping-result/v2" if isinstance(checked_config, ProjectMappingConfigurationV2) else MAPPING_RESULT_SCHEMA_VERSION,
        "status": "blocked" if blocked else "valid",
        "project_id": checked_bundle.project_id,
        "project_bundle_id": checked_bundle.project_bundle_id,
        "mapping_sha256": checked_config.mapping_sha256,
        "decisions": [value.model_dump(mode="json") for value in decisions],
        "issues": [value.model_dump(mode="json") for value in sorted_issues],
        "metric_observations": [value.model_dump(mode="json") for value in final_observations],
    }
    result_model = ProjectMappingResultV2 if isinstance(checked_config, ProjectMappingConfigurationV2) else ProjectMappingResult
    return result_model.model_validate(dict(
        status="blocked" if blocked else "valid",
        project_id=checked_bundle.project_id,
        project_bundle_id=checked_bundle.project_bundle_id,
        mapping_sha256=checked_config.mapping_sha256,
        decisions=decisions,
        issues=sorted_issues,
        metric_observations=final_observations,
        result_sha256=canonical_project_sha256(provisional),
    ))


def bind_project_mapping(
    bundle: ProjectBundle,
    configuration: AnyMappingConfiguration,
    result: AnyMappingResult,
) -> ProjectBundle:
    """Bind a validated configuration into a new immutable ProjectBundle identity."""

    checked_bundle = _checked(bundle)
    checked_config = _checked(configuration)
    checked_result = _checked(result)
    validate_mapping_dimensions(checked_config, checked_result)
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
