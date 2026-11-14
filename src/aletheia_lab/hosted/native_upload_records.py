"""Typed canonical envelopes for the existing hosted-upload P6 identities.

These records validate content and scope; authorization and publication remain
with the current K-04 policy and caller-owned storage transaction.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Generic, Literal, Self, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.product.lifecycle import ProductLifecycleRecord, _bind_result_id, _record_id
from aletheia_lab.product.results import (
    ProductResultEnvelope,
    _validate_derived_result,
    _view_from_record,
)
from aletheia_lab.product.view import (
    AnyProductView,
    ProductView,
    ProductViewV2,
    parse_product_view,
    parse_product_view_json,
)
from aletheia_lab.project.identity import PROJECT_ID_PATTERN, canonical_project_sha256

from .upload_analysis import (
    METRIC_OBSERVATION_MODEL,
    analysis_identity,
    analyze_initial_upload,
    analyze_upload_metrics,
)
from .upload_snapshot import (
    UPLOAD_SNAPSHOT_TYPES,
    AnyUploadSnapshot,
    UploadSnapshotV2,
    parse_upload_snapshot,
)

_RESULT = r'^p6-result-[0-9a-f]{64}$'
_ViewT = TypeVar('_ViewT', ProductView, ProductViewV2)


class _UploadResultRecordBase(BaseModel, Generic[_ViewT]):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')

    schema_version: str
    workspace_id: Identifier
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    snapshot_id: str = Field(pattern=r'^upload-snapshot-[0-9a-f]{64}$')
    parent_result_id: str | None = Field(default=None, pattern=_RESULT)
    view: _ViewT

    @property
    def record_id(self) -> str:
        return self.view.result.id

    @model_validator(mode='after')
    def _scope_and_self_ids(self) -> Self:
        view = self.view
        if (view.project.id != self.project_id or view.snapshot.id != self.snapshot_id
                or view.demo_only or view.visibility != 'diagnosis'
                or re.fullmatch(_RESULT, self.record_id) is None
                or self.parent_result_id == self.record_id):
            raise ValueError('Upload result does not reconcile with its canonical scope')
        if self.parent_result_id is None:
            if len(view.conversation.turns) != 1:
                raise ValueError('Initial upload result requires one originating turn')
            if view.runtime.provider != 'local_evidence':
                raise ValueError('Initial upload observation requires its pinned local runtime')
            identity = analysis_identity(self.workspace_id, self.snapshot_id,
                                        view.conversation.turns[0].question, view.runtime.model)
            digest = canonical_project_sha256(identity)
            if (self.record_id != 'p6-result-' + digest
                    or view.conversation.id != 'p6-conversation-' + digest
                    or view.conversation.turns[0].id != 'p6-turn-' + digest):
                raise ValueError('Initial upload result identities do not reconcile')
        else:
            envelope = ProductResultEnvelope(view=view.model_dump(mode='json')).model_dump(mode='json')
            expected = _record_id(record_kind='result', project_id=self.project_id, snapshot_id=self.snapshot_id,
                                  parent_result_id=self.parent_result_id, payload=envelope)
            if self.record_id != expected:
                raise ValueError('Upload follow-up result identity does not reconcile')
        return self


class UploadResultRecord(_UploadResultRecordBase[ProductView]):
    schema_version: Literal['hosted-upload-result/v1'] = 'hosted-upload-result/v1'


class UploadResultRecordV2(_UploadResultRecordBase[ProductViewV2]):
    schema_version: Literal['hosted-upload-result/v2'] = 'hosted-upload-result/v2'


AnyUploadResultRecord: TypeAlias = UploadResultRecord | UploadResultRecordV2
UPLOAD_RESULT_TYPES = (UploadResultRecord, UploadResultRecordV2)
_RESULT_ADAPTER = TypeAdapter(Annotated[AnyUploadResultRecord, Field(discriminator='schema_version')])
NativeUploadModel: TypeAlias = AnyUploadSnapshot | AnyUploadResultRecord
CanonicalResultRecord: TypeAlias = ProductLifecycleRecord | AnyUploadResultRecord


def parse_upload_result_record(value: object) -> AnyUploadResultRecord:
    return (_RESULT_ADAPTER.validate_json(value) if isinstance(value, str | bytes)
            else _RESULT_ADAPTER.validate_python(value))


def build_initial_upload_result(snapshot: AnyUploadSnapshot, question: str, view: AnyProductView) -> AnyUploadResultRecord:
    """Verify the existing exact projection before any database connection is held."""
    snapshot = parse_upload_snapshot(snapshot.model_dump(mode='python'))
    view = parse_product_view(view.model_dump(mode='python'))
    analyzer = analyze_upload_metrics if view.runtime.model == METRIC_OBSERVATION_MODEL else analyze_initial_upload
    if view != analyzer(snapshot, question):
        raise ValueError('Initial upload result differs from the verified snapshot projection')
    model = UploadResultRecordV2 if isinstance(snapshot, UploadSnapshotV2) else UploadResultRecord
    return parse_upload_result_record({'schema_version': model.model_fields['schema_version'].default,
        'workspace_id': snapshot.workspace_id, 'project_id': snapshot.mapping.project_id,
        'snapshot_id': snapshot.snapshot_id, 'view': view})


def build_upload_follow_up_result(workspace_id: str, parent: AnyProductView, view: AnyProductView) -> AnyUploadResultRecord:
    """Bind the existing P6 identity at the pure worker port; never mint a new domain."""
    parent = parse_product_view(parent.model_dump(mode='python'))
    view = parse_product_view(view.model_dump(mode='python'))
    if re.fullmatch(_RESULT, parent.result.id) is None or parent.result.id == 'p6-result-' + '0' * 64:
        raise ValueError('A persisted parent result is required')
    payload = ProductResultEnvelope(view=view.model_dump(mode='json')).model_dump(mode='json')
    identity = _record_id(record_kind='result', project_id=parent.project.id, snapshot_id=parent.snapshot.id,
                          parent_result_id=parent.result.id, payload=payload)
    bound = _bind_result_id('result', payload, identity)
    checked = parse_product_view_json(json.dumps(bound['view'], ensure_ascii=False, allow_nan=False))
    _validate_derived_result(parent, checked)
    model = UploadResultRecordV2 if isinstance(checked, ProductViewV2) else UploadResultRecord
    return parse_upload_result_record({'schema_version': model.model_fields['schema_version'].default,
        'workspace_id': workspace_id, 'project_id': parent.project.id, 'snapshot_id': parent.snapshot.id,
        'parent_result_id': parent.result.id, 'view': checked})


def result_view(record: CanonicalResultRecord) -> AnyProductView:
    if isinstance(record, UPLOAD_RESULT_TYPES):
        return parse_upload_result_record(record.model_dump(mode='python')).view
    if isinstance(record, ProductLifecycleRecord):
        checked = ProductLifecycleRecord.model_validate(record.model_dump(mode='python'))
        return _view_from_record(checked)
    raise TypeError('A typed canonical result is required')


def native_metadata(model: NativeUploadModel) -> tuple[str, str, str, str, str]:
    if isinstance(model, UPLOAD_SNAPSHOT_TYPES):
        checked: NativeUploadModel = parse_upload_snapshot(model.model_dump(mode='python'))
        kind, identity, project = 'upload_snapshot', model.snapshot_id, model.mapping.project_id
    elif isinstance(model, UPLOAD_RESULT_TYPES):
        checked = parse_upload_result_record(model.model_dump(mode='python'))
        kind, identity, project = 'upload_result', model.record_id, model.project_id
    else:
        raise TypeError('A typed native upload model is required')
    return kind, identity, project, checked.schema_version, canonical_project_sha256(checked.model_dump(mode='json'))


def native_parents(model: NativeUploadModel) -> tuple[tuple[str, str], ...]:
    if isinstance(model, UPLOAD_SNAPSHOT_TYPES):
        return ()
    if not isinstance(model, UPLOAD_RESULT_TYPES):
        raise TypeError('A typed native upload model is required')
    parents = [('snapshot', model.snapshot_id)]
    if model.parent_result_id is not None:
        parents.append(('parent_result', model.parent_result_id))
    return tuple(sorted(parents))
