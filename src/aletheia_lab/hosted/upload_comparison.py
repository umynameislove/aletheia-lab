"""Complete metric census between two immutable upload generations.

The bounded record stores counts and exact decimal delta ranges per series.
Every raw operand remains in the two named snapshots, never in a truncated list.
"""
from __future__ import annotations

import json
from decimal import Decimal, localcontext
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricIdentity, metric_identity

from .upload_snapshot import AnyUploadSnapshot, parse_upload_snapshot

COMPARISON_SCHEMA = 'hosted-upload-generation-comparison/v1'


class MetricSeriesComparison(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    run_id: str
    metric_name: str
    class_label: str | None
    unit: str | None
    direction: Literal['higher_is_better', 'lower_is_better'] | None
    regression_threshold: str | None
    assessment: Literal['configured', 'not_assessed']
    paired: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    increased: int = Field(ge=0)
    decreased: int = Field(ge=0)
    adverse: int | None = Field(ge=0)
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    minimum_delta: str | None
    maximum_delta: str | None
    operands_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _counts(self) -> Self:
        configured = self.assessment == 'configured'
        if configured != (self.direction is not None) or configured != (self.regression_threshold is not None) or configured != (self.adverse is not None):
            raise ValueError('Unknown metric policy must remain unassessed.')
        if self.regression_threshold is not None:
            threshold = Decimal(self.regression_threshold)
            if not threshold.is_finite() or threshold < 0:
                raise ValueError('Invalid comparison threshold.')
        if self.paired != self.unchanged + self.increased + self.decreased or (self.adverse is not None and self.adverse > self.increased + self.decreased):
            raise ValueError('Comparison counts do not reconcile.')
        if (self.minimum_delta is None) != (self.paired == 0) or (self.maximum_delta is None) != (self.paired == 0):
            raise ValueError('Comparison delta range does not reconcile.')
        if self.minimum_delta is not None and self.maximum_delta is not None:
            lo, hi = Decimal(self.minimum_delta), Decimal(self.maximum_delta)
            if not lo.is_finite() or not hi.is_finite() or lo > hi:
                raise ValueError('Invalid comparison delta range.')
        return self


class UploadGenerationComparison(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal['hosted-upload-generation-comparison/v1'] = 'hosted-upload-generation-comparison/v1'
    comparison_id: str = Field(pattern=r'^p3-comparison-[0-9a-f]{64}$')
    workspace_id: Identifier
    project_id: str = Field(pattern=r'^p3-project-[0-9a-f]{64}$')
    before_snapshot_id: str = Field(pattern=r'^upload-snapshot-[0-9a-f]{64}$')
    after_snapshot_id: str = Field(pattern=r'^upload-snapshot-[0-9a-f]{64}$')
    before_record_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    after_record_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    before_source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    after_source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    before_rows: int = Field(ge=0)
    after_rows: int = Field(ge=0)
    series: tuple[MetricSeriesComparison, ...] = Field(max_length=64)
    causal_status: Literal['unverified'] = 'unverified'
    aggregation: Literal['all_validated_rows_per_series'] = 'all_validated_rows_per_series'
    record_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _identity(self) -> Self:
        if self.before_snapshot_id == self.after_snapshot_id:
            raise ValueError('A no-op has no new generation comparison.')
        keys = [(s.run_id, s.metric_name, s.class_label, s.unit) for s in self.series]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate comparison series.')
        if (sum(s.paired + s.removed for s in self.series) != self.before_rows
                or sum(s.paired + s.added for s in self.series) != self.after_rows):
            raise ValueError('Comparison row census does not reconcile.')
        payload = self.model_dump(mode='json', exclude={'comparison_id', 'record_sha256'})
        if self.comparison_id != 'p3-comparison-' + canonical_project_sha256(payload):
            raise ValueError('Comparison identity mismatch.')
        if self.record_sha256 != canonical_project_sha256(self.model_dump(mode='json', exclude={'record_sha256'})):
            raise ValueError('Comparison record hash mismatch.')
        return self


def build_upload_comparison(before: AnyUploadSnapshot, after: AnyUploadSnapshot) -> UploadGenerationComparison:
    before, after = (parse_upload_snapshot(value.model_dump(mode='python')) for value in (before, after))
    return _compare_validated(before, after)


def _compare_validated(before: AnyUploadSnapshot, after: AnyUploadSnapshot) -> UploadGenerationComparison:
    """Internal caller has just fully validated both frozen snapshot models."""
    if (before.workspace_id != after.workspace_id or before.mapping.project_id != after.mapping.project_id
            or before.schema_version != after.schema_version or before.mapping.metric_definitions != after.mapping.metric_definitions):
        raise ValueError('Upload comparison scope or metric policy changed.')
    if before.snapshot_id == after.snapshot_id:
        raise ValueError('A no-op has no new generation comparison.')
    old = {metric_identity(row): row for row in before.mapping_result.metric_observations}
    new = {metric_identity(row): row for row in after.mapping_result.metric_observations}
    definitions = {d.metric_name: d for d in after.mapping.metric_definitions}
    groups: dict[tuple[str, str, str | None, str | None], list[MetricIdentity]] = {}
    for identity in sorted(old.keys() | new.keys(), key=lambda value: json.dumps(value, ensure_ascii=True)):
        groups.setdefault(identity[:4], []).append(identity)
    if len(groups) > 64:
        raise ValueError('Upload comparison exceeds the supported series bound; no rows were omitted.')
    series = []
    with localcontext() as context:
        # Finite binary64 operands can span over 600 decimal places.
        context.prec = 800
        for key, identities in groups.items():
            run, metric, label, unit = key
            policy = definitions.get(metric)
            threshold = None if policy is None else Decimal(str(policy.regression_threshold))
            counts = dict(paired=0, unchanged=0, increased=0, decreased=0, adverse=0, added=0, removed=0)
            deltas = []
            digest = content_hasher()
            for identity in identities:
                left, right = old.get(identity), new.get(identity)
                delta = None
                if left is None:
                    kind = 'added'
                elif right is None:
                    kind = 'removed'
                else:
                    counts['paired'] += 1
                    delta = Decimal(str(right.metric_value)) - Decimal(str(left.metric_value))
                    deltas.append(delta)
                    kind = 'increased' if delta > 0 else 'decreased' if delta < 0 else 'unchanged'
                    adverse = policy is not None and (delta < 0 if policy.direction == 'higher_is_better' else delta > 0)
                    if adverse and threshold is not None and abs(delta) >= threshold:
                        counts['adverse'] += 1
                counts[kind] += 1
                fact = [identity, None if left is None else left.observation_id,
                        None if right is None else right.observation_id, kind, None if delta is None else str(delta)]
                digest.update(json.dumps(fact, ensure_ascii=True, separators=(',', ':')).encode() + b'\n')
            common = {key: value for key, value in counts.items() if key != 'adverse'}
            series.append(MetricSeriesComparison(run_id=run, metric_name=metric, class_label=label, unit=unit,
                direction=None if policy is None else policy.direction,
                regression_threshold=None if threshold is None else str(threshold),
                assessment='not_assessed' if policy is None else 'configured',
                adverse=None if policy is None else counts['adverse'], **common,
                minimum_delta=None if not deltas else str(min(deltas)), maximum_delta=None if not deltas else str(max(deltas)),
                operands_sha256=digest.hexdigest()))
    payload: dict[str, object] = dict(schema_version=COMPARISON_SCHEMA, workspace_id=before.workspace_id,
        project_id=before.mapping.project_id, before_snapshot_id=before.snapshot_id, after_snapshot_id=after.snapshot_id,
        before_record_sha256=before.record_sha256, after_record_sha256=after.record_sha256,
        before_source_sha256=before.source_sha256, after_source_sha256=after.source_sha256,
        before_rows=len(old), after_rows=len(new), series=[s.model_dump(mode='json') for s in series],
        causal_status='unverified', aggregation='all_validated_rows_per_series')
    payload['comparison_id'] = 'p3-comparison-' + canonical_project_sha256(payload)
    payload['record_sha256'] = canonical_project_sha256(payload)
    return UploadGenerationComparison.model_validate_json(json.dumps(payload))


def comparison_metadata(model: UploadGenerationComparison) -> tuple[str, str, str, str, str]:
    return 'snapshot_comparison', model.comparison_id, model.project_id, model.schema_version, canonical_project_sha256(model.model_dump(mode='json'))


def comparison_parents(model: UploadGenerationComparison) -> tuple[tuple[str, str], ...]:
    return (('after_snapshot', model.after_snapshot_id), ('before_snapshot', model.before_snapshot_id))
