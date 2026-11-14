"""Hosted metric dimensions survive hashing, publication codecs and refresh."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecordV2,
    build_initial_upload_result,
    native_metadata,
    parse_upload_result_record,
)
from aletheia_lab.hosted.postgres_catalog import RecordIndex, decode_record
from aletheia_lab.hosted.project_refresh import PreparedRefreshV2, refresh_uploaded_project
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadMappingRejected, UploadProject
from aletheia_lab.hosted.upload_snapshot import (
    UploadSnapshotV2,
    build_upload_snapshot,
    parse_upload_snapshot,
)
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.view import ProductViewV2
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import parse_mapping_result


def source_v2(*, unit='%', empty_class=False, single_class=False, offset=0):
    rows = ['run,name,value,class,unit']
    for run in range(5):
        labels = ('lớp A',) if single_class else ('lớp A', 'lớp B')
        for index, label in enumerate(labels):
            rows.append(f'lần-{run},độ_chính_xác,{.5 + offset + index / 10},{"" if empty_class else label},{unit}')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, content in [('dataset.csv', 'id,target\n1,0\n2,1\n'), ('metrics.csv', '\n'.join(rows) + '\n')]:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            archive.writestr(info, content)
    return output.getvalue()


def mapping_v2(preview, *, unit='%', class_field='class', unit_field='unit'):
    candidates = preview['mapping_candidates']
    target, metric = (candidates[key][0] for key in ('targets', 'metric_sources'))
    return {'schema_version': 'project-mapping-request/v2',
        'target': {'mapping_id': target['mapping_id'], 'project_item_id': target['project_item_id'],
                   'target_field': 'target', 'identifier_field': 'id'},
        'metric_sources': [{'mapping_id': metric['mapping_id'], 'project_item_id': metric['project_item_id'],
            'format': 'csv', 'records_path': [], 'metric_name_field': 'name', 'metric_value_field': 'value',
            'run_id_field': 'run', 'step_field': None, 'class_field': class_field, 'unit_field': unit_field}],
        'runs': [{'run_id': f'lần-{run}', 'config_item_ids': []} for run in range(5)], 'baseline_run_id': 'lần-0',
        'metric_definitions': [{'metric_name': 'độ_chính_xác', 'direction': 'higher_is_better',
                                'regression_threshold': .08, 'unit': unit}]}


def snapshot_v2(tmp_path, *, data=None, **mapping_options):
    data = source_v2() if data is None else data
    tmp_path.mkdir(exist_ok=True)
    project = UploadProject(tmp_path, maximum_upload_bytes=1_000_000, maximum_expanded_bytes=1_000_000, maximum_entries=100)
    preview = project.preview(data, captured_at='2026-11-11T00:00:00Z')
    selected = mapping_v2(preview, **mapping_options)
    snapshot = project.confirm(data, workspace_id='a', source_id='source_v2', mapping=selected,
        captured_at='2026-11-11T00:00:00Z', expected_source_state_sha256=preview['source_state_sha256'])
    assert isinstance(snapshot, UploadSnapshotV2)
    return data, snapshot, preview, selected


def test_v2_snapshot_and_initial_result_roundtrip_through_canonical_catalog_codec(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    assert parse_upload_snapshot(snapshot.model_dump_json()) == snapshot
    observations = snapshot.mapping_result.metric_observations
    assert len(observations) == 10
    assert {row.class_label for row in observations} == {'lớp A', 'lớp B'}
    assert {(row.unit, row.unit_origin) for row in observations} == {('%', 'source')}
    view = analyze_initial_upload(snapshot, 'What is known from this uploaded snapshot?')
    assert isinstance(view, ProductViewV2) and view.snapshot.metric_definitions[0].unit == '%'
    assert not view.snapshot.metric_changes and view.result.causal_status == 'unverified'
    result = build_initial_upload_result(snapshot, view.conversation.turns[0].question, view)
    assert isinstance(result, UploadResultRecordV2)
    assert parse_upload_result_record(result.model_dump_json()) == result
    for model in (snapshot, result):
        kind, identity, project, version, digest = native_metadata(model)
        payload = model.model_dump_json().encode()
        index = RecordIndex(identity, project, kind, version, digest, hashlib.sha256(payload).hexdigest(),
            model.snapshot_id if isinstance(model, UploadResultRecordV2) else None, None,
            model.source_id if isinstance(model, UploadSnapshotV2) else None)
        assert decode_record(index, payload) == model


def test_v2_refresh_noop_preserves_first_snapshot_and_mismatch_keeps_parent(tmp_path):
    data, parent, _, _ = snapshot_v2(tmp_path / 'initial')
    root = tmp_path / 'refresh'
    root.mkdir()
    kwargs = dict(parent=parent, origin_source_sha256=parent.source_sha256, before_payload=data,
        source_id='source_changed', private_root=root, captured_at=datetime(2026, 11, 12, tzinfo=UTC),
        maximum_upload_bytes=1_000_000, maximum_expanded_bytes=1_000_000, maximum_entries=100)
    original = parent.model_dump_json()
    unchanged = refresh_uploaded_project(**kwargs, new_payload=data)
    assert isinstance(unchanged, PreparedRefreshV2) and unchanged.status == 'unchanged'
    assert unchanged.snapshot == parent
    with pytest.raises(ProductError, match='mapping is invalid'):
        refresh_uploaded_project(**kwargs, new_payload=source_v2(unit='ms'))
    assert parent.model_dump_json() == original
    changed = refresh_uploaded_project(**kwargs, new_payload=source_v2(offset=.1))
    assert isinstance(changed, PreparedRefreshV2) and changed.status == 'new_snapshot'
    assert {row.class_label for row in changed.snapshot.mapping_result.metric_observations} == {'lớp A', 'lớp B'}
    assert all(row.unit == '%' for row in changed.snapshot.mapping_result.metric_observations)


@pytest.mark.parametrize('unit,unit_field,expected', [('%', None, 'declared'), (None, 'unit', 'unknown')])
def test_explicit_declared_or_unknown_unit_is_not_inferred(tmp_path, unit, unit_field, expected):
    _, snapshot, _, _ = snapshot_v2(tmp_path, data=source_v2(unit='' if unit is None else unit),
                                   unit=unit, unit_field=unit_field)
    assert {(row.unit, row.unit_origin) for row in snapshot.mapping_result.metric_observations} == {(unit, expected)}


@pytest.mark.parametrize('options,data', [
    ({'unit': None}, source_v2()),
    ({}, source_v2(empty_class=True)),
    ({'class_field': None}, source_v2()),
])
def test_bad_or_ignored_source_dimensions_fail_without_partial_snapshot(tmp_path, options, data):
    with pytest.raises(UploadMappingRejected):
        snapshot_v2(tmp_path, data=data, **options)


def test_rehashed_unit_origin_forgery_does_not_bypass_snapshot_semantics(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    payload = snapshot.mapping_result.model_dump(mode='json')
    row = payload['metric_observations'][0]
    row['unit_origin'] = 'declared'
    row['observation_id'] = 'p3-metric-' + canonical_project_sha256({k: v for k, v in row.items() if k != 'observation_id'})
    payload['result_sha256'] = canonical_project_sha256({k: v for k, v in payload.items() if k != 'result_sha256'})
    forged = parse_mapping_result(json.dumps(payload, ensure_ascii=False))
    with pytest.raises(ValueError, match='provenance'):
        build_upload_snapshot(workspace_id=snapshot.workspace_id, source_id=snapshot.source_id,
            source_sha256=snapshot.source_sha256, mapping=snapshot.mapping, mapping_result=forged,
            captured_at=snapshot.captured_at)
