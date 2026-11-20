"""Exact inclusive thresholds and immutable all-row generation comparisons."""
from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest
from test_k07_dimensions_v2 import snapshot_v2, source_v2

from aletheia_lab.hosted.project_refresh import refresh_uploaded_project
from aletheia_lab.hosted.upload_comparison import (
    UploadGenerationComparison,
    build_upload_comparison,
)


def _changed(tmp_path, offset=0, *, before_data=None, after_data=None):
    data, before, _, _ = snapshot_v2(tmp_path / 'initial', data=before_data)
    root = tmp_path / 'refresh'
    root.mkdir()
    after = refresh_uploaded_project(parent=before, origin_source_sha256=before.source_sha256,
        before_payload=data, new_payload=source_v2(offset=offset) if after_data is None else after_data,
        source_id='source_changed', private_root=root,
        captured_at=before.captured_at + timedelta(hours=1), maximum_upload_bytes=1_000_000,
        maximum_expanded_bytes=1_000_000, maximum_entries=100).snapshot
    return before, after


def _metrics(transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source_v2())) as old, zipfile.ZipFile(output, 'w') as new:
        for info in old.infolist():
            content = old.read(info.filename)
            if info.filename == 'metrics.csv':
                content = transform(content.decode()).encode()
            new.writestr(info, content)
    return output.getvalue()


@pytest.mark.parametrize('offset,adverse', [(-.08, 10), (-.079, 0), (.08, 0)])
def test_direction_and_inclusive_decimal_threshold_preserve_all_operands(tmp_path, offset, adverse):
    before, after = _changed(tmp_path, offset)
    immutable = before.model_dump_json()
    comparison = build_upload_comparison(before, after)
    assert comparison.before_rows == comparison.after_rows == 10
    assert sum(s.adverse for s in comparison.series) == adverse
    assert sum(s.paired for s in comparison.series) == 10
    assert comparison.before_snapshot_id == before.snapshot_id
    assert comparison.after_snapshot_id == after.snapshot_id
    assert comparison.causal_status == 'unverified'
    for series in comparison.series:
        left = next(r for r in before.mapping_result.metric_observations if (r.run_id, r.class_label) == (series.run_id, series.class_label))
        right = next(r for r in after.mapping_result.metric_observations if (r.run_id, r.class_label) == (series.run_id, series.class_label))
        assert Decimal(series.minimum_delta) == Decimal(str(right.metric_value)) - Decimal(str(left.metric_value))
    assert UploadGenerationComparison.model_validate_json(comparison.model_dump_json()) == comparison
    assert build_upload_comparison(before, after) == comparison
    assert before.model_dump_json() == immutable
    with pytest.raises(ValueError):
        UploadGenerationComparison.model_validate(comparison.model_dump(mode='python') | {'before_rows': 9})


def test_noop_and_cross_project_are_rejected_not_fake_comparisons(tmp_path):
    _, before, _, _ = snapshot_v2(tmp_path / 'initial')
    with pytest.raises(ValueError, match='no-op'):
        build_upload_comparison(before, before)
    _, other, _, _ = snapshot_v2(tmp_path / 'other', data=source_v2(offset=.1))
    with pytest.raises(ValueError, match='scope'):
        build_upload_comparison(before, other)


def test_added_removed_classes_have_no_invented_delta_and_full_census(tmp_path):
    before, after = _changed(tmp_path, after_data=_metrics(lambda text: text.replace('lớp B', 'lớp C')))
    comparison = build_upload_comparison(before, after)
    assert comparison.before_rows == comparison.after_rows == 10
    assert sum(s.paired for s in comparison.series) == 5
    assert sum(s.unchanged for s in comparison.series) == 5
    assert sum(s.added for s in comparison.series) == sum(s.removed for s in comparison.series) == 5
    assert all(s.minimum_delta is None and s.maximum_delta is None
               for s in comparison.series if s.added or s.removed)


def test_zero_and_tiny_signed_operands_keep_exact_decimal_deltas(tmp_path):
    def rows(text, sign):
        lines = text.splitlines()
        output = [lines[0]]
        for line in lines[1:]:
            fields = line.split(',')
            fields[2] = '0.0' if fields[3] == 'lớp A' else sign + '1e-200'
            output.append(','.join(fields))
        return '\n'.join(output) + '\n'
    before, after = _changed(tmp_path, before_data=_metrics(lambda text: rows(text, '-')),
                             after_data=_metrics(lambda text: rows(text, '')))
    comparison = build_upload_comparison(before, after)
    assert sum(s.unchanged for s in comparison.series) == sum(s.increased for s in comparison.series) == 5
    assert all(s.adverse == 0 for s in comparison.series)
    assert all(Decimal(s.minimum_delta) == Decimal('2e-200') for s in comparison.series if s.class_label == 'lớp B')
