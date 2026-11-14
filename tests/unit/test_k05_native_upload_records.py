"""Native upload envelopes retain the existing view IDs and fail closed."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_project_mapping import _case, _configuration

from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecord,
    build_initial_upload_result,
    native_metadata,
    native_parents,
)
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.product.view import ProductView
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import validate_project_mapping


def upload_case(tmp_path):
    case = _case(tmp_path)
    mapping = _configuration(case)
    result = validate_project_mapping(case.bundle, case.artifacts, case.collection, mapping)
    snapshot = build_upload_snapshot(workspace_id='ws1', source_id='src1', source_sha256='a' * 64,
        mapping=mapping, mapping_result=result, captured_at=datetime(2026, 11, 10, tzinfo=UTC))
    view = analyze_initial_upload(snapshot, 'What is observed?')
    return snapshot, view


def test_typed_native_bytes_roundtrip_preserves_existing_ids_and_scope(tmp_path):
    snapshot, view = upload_case(tmp_path)
    record = build_initial_upload_result(snapshot, 'What is observed?', view)
    assert record.record_id == view.result.id
    assert UploadResultRecord.model_validate_json(record.model_dump_json()) == record
    assert native_metadata(snapshot) == ('upload_snapshot', snapshot.snapshot_id, snapshot.mapping.project_id,
        snapshot.schema_version, canonical_project_sha256(snapshot.model_dump(mode='json')))
    assert native_metadata(record) == ('upload_result', view.result.id, view.project.id, record.schema_version,
        canonical_project_sha256(record.model_dump(mode='json')))
    assert native_parents(snapshot) == ()
    assert native_parents(record) == (('snapshot', snapshot.snapshot_id),)
    with pytest.raises(ValidationError):
        record.workspace_id = 'other'


@pytest.mark.parametrize('field,value', [('workspace_id','other'),('project_id','p3-project-'+'b'*64),
    ('snapshot_id','upload-snapshot-'+'c'*64)])
def test_native_scope_or_initial_identity_mismatch_rejects(tmp_path, field, value):
    snapshot, view = upload_case(tmp_path)
    payload = dict(workspace_id=snapshot.workspace_id, project_id=view.project.id, snapshot_id=snapshot.snapshot_id, view=view)
    payload[field] = value
    with pytest.raises(ValidationError):
        UploadResultRecord.model_validate(payload)


def test_initial_factory_rejects_other_question_or_changed_projection(tmp_path):
    snapshot, view = upload_case(tmp_path)
    with pytest.raises(ValueError, match='differs'):
        build_initial_upload_result(snapshot, 'A different question?', view)
    payload = view.model_dump(mode='python')
    payload['result']['summary'] = 'Different but structurally valid observation'
    changed = ProductView.model_validate(payload)
    with pytest.raises(ValueError, match='differs'):
        build_initial_upload_result(snapshot, 'What is observed?', changed)


def test_native_demo_visibility_and_unknown_fields_reject(tmp_path):
    snapshot, view = upload_case(tmp_path)
    payload = dict(workspace_id=snapshot.workspace_id, project_id=view.project.id, snapshot_id=snapshot.snapshot_id,
                   view=view.model_dump(mode='python'))
    payload['view']['demo_only'] = True
    with pytest.raises(ValidationError):
        UploadResultRecord.model_validate(payload)
    payload['view']['demo_only'] = False
    payload['unknown'] = 'denied'
    with pytest.raises(ValidationError):
        UploadResultRecord.model_validate(payload)


def test_native_followup_reuses_p6_binding_has_unique_questions_and_stable_replay(tmp_path):
    from aletheia_lab.hosted.native_upload_records import build_upload_follow_up_result
    from aletheia_lab.product.follow_up import (
        build_product_follow_up_view,
        resolve_product_follow_up_selection,
    )

    snapshot, view = upload_case(tmp_path)
    parent = build_initial_upload_result(snapshot, 'What is observed?', view)
    children = []
    for question in ('What remains uncertain?', 'Which evidence is still missing?'):
        selection = resolve_product_follow_up_selection(view, selection_kind='claim',
            selection_id=view.claims[0].id, question=question)
        pending = build_product_follow_up_view(view, selection)
        assert pending.result.id == 'p6-result-' + '0' * 64
        with pytest.raises(ValidationError):
            UploadResultRecord(workspace_id=parent.workspace_id, project_id=parent.project_id,
                snapshot_id=parent.snapshot_id, parent_result_id=parent.record_id, view=pending)
        child = build_upload_follow_up_result(parent.workspace_id, parent.view, pending)
        assert child.record_id != pending.result.id and child.record_id != parent.record_id
        assert child.parent_result_id == parent.record_id
        assert child.view.conversation.turns[-1].result_id == child.record_id
        assert child.view.conversation.turns[:-1] == parent.view.conversation.turns
        assert build_upload_follow_up_result(parent.workspace_id, parent.view, pending) == child
        assert UploadResultRecord.model_validate_json(child.model_dump_json()) == child
        assert native_parents(child) == tuple(sorted((('snapshot', parent.snapshot_id), ('parent_result', parent.record_id))))
        children.append(child)
    assert children[0].record_id != children[1].record_id
    assert parent.record_id == view.result.id


def test_native_result_view_revalidates_and_rejects_unknown_record(tmp_path):
    from aletheia_lab.hosted.native_upload_records import result_view

    snapshot, view = upload_case(tmp_path)
    record = build_initial_upload_result(snapshot, 'What is observed?', view)
    assert result_view(record) == view
    with pytest.raises(TypeError):
        result_view(object())
    broken = record.model_copy(update={'project_id': 'p3-project-' + 'f' * 64})
    with pytest.raises(ValidationError):
        result_view(broken)
