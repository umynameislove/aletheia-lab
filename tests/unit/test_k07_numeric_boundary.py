import io
import zipfile

import pytest

from aletheia_lab.hosted.upload_project import UploadMappingRejected, UploadProject


def fixture(tmp_path, format_value, lexical):
    if format_value == "csv":
        metrics = "run,name,value\nbaseline,loss,0\ncandidate,loss," + lexical + "\n"
    else:
        metrics = ('{"records":[{"run":"baseline","name":"loss","value":0},'
                   '{"run":"candidate","name":"loss","value":' + lexical + '}]}')
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics." + format_value, metrics)
    instance = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    source = data.getvalue()
    preview = instance.preview(source, captured_at="2026-11-10T00:00:00Z")
    target, metric = (preview["mapping_candidates"][key][0] for key in ("targets", "metric_sources"))
    mapping = {"target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
        "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
            "format": format_value, "records_path": ["records"] if format_value == "json" else [],
            "metric_name_field": "name", "metric_value_field": "value", "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": "baseline", "config_item_ids": []}, {"run_id": "candidate", "config_item_ids": []}],
        "baseline_run_id": "baseline",
        "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.0}]}
    return instance, source, mapping


@pytest.mark.parametrize(("format_value", "lexical"), [
    ("csv", "1e-400"), ("csv", "-1e-400"), ("json", "1e-400"), ("json", '"1e-400"'),
])
def test_nonzero_underflow_is_rejected_at_the_exact_source_record(tmp_path, format_value, lexical):
    instance, source, mapping = fixture(tmp_path, format_value, lexical)
    with pytest.raises(UploadMappingRejected) as caught:
        instance.confirm(source, workspace_id="ws1", source_id="src1", mapping=mapping, captured_at="2026-11-10T00:00:00Z")
    assert caught.value.issues == [{"code": "numeric_underflow", "field": "metric_sources.0", "record_index": 1}]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("format_value", ["csv", "json"])
@pytest.mark.parametrize("lexical", ["1e-300", "0", "-0.5"])
def test_representable_tiny_zero_and_negative_keep_the_domain_value(tmp_path, format_value, lexical):
    instance, source, mapping = fixture(tmp_path, format_value, lexical)
    snapshot = instance.confirm(source, workspace_id="ws1", source_id="src1", mapping=mapping, captured_at="2026-11-10T00:00:00Z")
    candidate = next(row for row in snapshot.mapping_result.metric_observations if row.run_id == "candidate")
    assert candidate.metric_value == float(lexical)
    assert list(tmp_path.iterdir()) == []


def test_extreme_json_exponent_failure_has_fixed_diagnostics(tmp_path):
    instance, source, mapping = fixture(tmp_path, "json", "1e-99999999999999999999999999")
    with pytest.raises(UploadMappingRejected) as caught:
        instance.confirm(source, workspace_id="ws1", source_id="src1", mapping=mapping, captured_at="2026-11-10T00:00:00Z")
    assert caught.value.issues == [{"code": "metric_value_invalid", "field": "metric_sources.0"}]
