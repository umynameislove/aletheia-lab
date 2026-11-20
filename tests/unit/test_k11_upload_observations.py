"""Actual validated metric operands, versioned identities and unchanged historical views."""
from __future__ import annotations

import csv
import io
import zipfile

import pytest
from test_k07_dimensions_v2 import snapshot_v2, source_v2

from aletheia_lab.hosted.native_upload_records import (
    build_initial_upload_result,
    parse_upload_result_record,
)
from aletheia_lab.hosted.upload_analysis import (
    MAX_OBSERVATION_SERIES,
    METRIC_OBSERVATION_MODEL,
    analyze_initial_upload,
    analyze_upload_metrics,
)
from aletheia_lab.product.boundary import ProductError


def _source_rows(rows):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['run', 'name', 'value', 'class', 'unit'])
    writer.writerows(rows)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('dataset.csv', 'id,target\n1,0\n2,1\n')
        archive.writestr('metrics.csv', stream.getvalue())
    return output.getvalue()


def test_metric_census_uses_every_run_class_operand_and_pinned_source(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    view = analyze_upload_metrics(snapshot, 'Những số liệu nào đã được ghi nhận?')
    assert view.runtime.model == METRIC_OBSERVATION_MODEL and not view.runtime.external_call
    assert len(view.claims) == len(view.evidence) == 10
    assert view.result.denominators.claims == 10 and '10 validated metric values' in view.result.summary
    assert view.result.causal_status == 'unverified' and view.result.disposition == 'abstain'
    for claim in view.claims:
        evidence = next(item for item in view.evidence if item.id == claim.citation_ids[0])
        assert claim.text == evidence.text and claim.max_strength == 'observation'
        assert evidence.source_sha256 == snapshot.source_sha256
        assert evidence.reproduction_ref.record_id == snapshot.snapshot_id
        assert 'unit' in claim.text and '(source)' in claim.text and '1 validated observations' in claim.text
    assert analyze_upload_metrics(snapshot, view.conversation.turns[0].question) == view
    record = build_initial_upload_result(snapshot, view.conversation.turns[0].question, view)
    assert parse_upload_result_record(record.model_dump_json()) == record


def test_new_model_does_not_reuse_or_rewrite_historical_result_identity(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path)
    old = analyze_initial_upload(snapshot, 'What is observed?')
    old_record = build_initial_upload_result(snapshot, 'What is observed?', old)
    old_bytes = old_record.model_dump_json()
    new = analyze_upload_metrics(snapshot, 'What is observed?')
    assert new.result.id != old.result.id
    assert new.conversation.id != old.conversation.id
    assert new.conversation.turns[0].id != old.conversation.turns[0].id
    assert parse_upload_result_record(old_bytes).model_dump_json() == old_bytes
    forged = build_initial_upload_result(snapshot, 'What is observed?', new).model_dump(mode='json')
    forged['view']['runtime']['model'] = old.runtime.model
    forged['view']['conversation']['turns'][0]['runtime']['model'] = old.runtime.model
    with pytest.raises(ValueError):
        parse_upload_result_record(forged)


def test_numeric_zero_negative_tiny_and_full_float_precision_are_not_changed(tmp_path):
    values = [0.0, -1.0, 1e-200, -1e-200, 0.12345678901234568]
    data = _source_rows((f'lần-{i}', 'độ_chính_xác', repr(value), 'lớp A', '%')
                        for i, value in enumerate(values))
    _, snapshot, _, _ = snapshot_v2(tmp_path, data=data)
    view = analyze_upload_metrics(snapshot, 'What are the measured values?')
    for row in snapshot.mapping_result.metric_observations:
        text = next(item.text for item in view.claims if f'Run {row.run_id},' in item.text)
        assert f'minimum {row.metric_value!r}; maximum {row.metric_value!r}' in text
    assert not view.snapshot.metric_changes


def test_unmapped_class_and_unit_remain_unknown(tmp_path):
    _, snapshot, _, _ = snapshot_v2(tmp_path, data=source_v2(single_class=True),
        unit=None, class_field=None, unit_field=None)
    view = analyze_upload_metrics(snapshot, 'What is observed?')
    assert len(view.claims) == 5
    assert all('class not mapped, unit unknown (unknown)' in item.text for item in view.claims)


def test_too_many_series_is_explicit_failure_not_a_truncated_success(tmp_path):
    rows = [(f'lần-{run}', 'độ_chính_xác', .5, f'class-{label}', '%')
            for run in range(5) for label in range(13)]
    _, snapshot, _, _ = snapshot_v2(tmp_path, data=_source_rows(rows))
    assert len(snapshot.mapping_result.metric_observations) > MAX_OBSERVATION_SERIES
    with pytest.raises(ProductError) as error:
        analyze_upload_metrics(snapshot, 'What is observed?')
    assert error.value.code == 'analysis_too_large'
