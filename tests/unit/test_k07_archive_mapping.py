from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from aletheia_lab.hosted.source_bundle import SourceRejected, inspect_source_bundle
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadMappingRejected, UploadProject
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot
from aletheia_lab.hosted.upload_worker import WorkerInput, _execute, _read_source_path
from aletheia_lab.project.importer import (
    grant_immutable_project_root,
    grant_project_root,
    inspect_local_project,
)


def bundle(items, *, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in items:
            info = zipfile.ZipInfo(name) if isinstance(name, str) else name
            info.compress_type = compression
            archive.writestr(info, content)
    return output.getvalue()


def inspect(payload):
    return inspect_source_bundle(payload, maximum_upload_bytes=1_000_000,
        maximum_expanded_bytes=1_000_000, maximum_entries=100)


def project(root):
    root.mkdir(exist_ok=True)
    return UploadProject(root, maximum_upload_bytes=1_000_000,
                         maximum_expanded_bytes=1_000_000, maximum_entries=100)


def payload(metric="loss", values=("0", "-0.125", "1e-300", "0.25", "0.5")):
    records = "run,name,value\n" + "".join(f"run-{index},{metric},{value}\n" for index, value in enumerate(values))
    return bundle([("dữ_liệu/dataset.csv", "id,target\n1,0\n2,1\n"),
                   ("dữ_liệu/metrics.csv", records), ("cấu_hình/config.json", '{"seed":42}\n')])


def mapping(preview):
    candidates = preview["mapping_candidates"]
    target, metric = (candidates[field][0] for field in ("targets", "metric_sources"))
    return {
        "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                   "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
            "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
            "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": run["run_id"], "config_item_ids": []} for run in candidates["runs"]],
        "baseline_run_id": "run-0",
        "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.0}],
    }


@pytest.mark.parametrize("items", [
    [("café/metrics.csv", "a"), ("cafe\u0301/config.json", "{}")],
    [("data/metrics.csv", "a"), ("DATA/config.json", "{}")],
    [("x?y.csv", "a")], [("conin$.txt", "a")], [("COM¹.txt", "a")],
    [("a/" * 32 + "metrics.csv", "a")],
])
def test_ambiguous_windows_unicode_and_deep_names_are_rejected(items):
    with pytest.raises(SourceRejected):
        inspect(bundle(items))


@pytest.mark.parametrize("name", ["café/metrics.csv", "cafe\u0301/metrics.csv"])
def test_nfc_and_nfd_names_are_preserved_when_unambiguous(name):
    assert inspect(bundle([(name, "run,name,value\nr,loss,0\n")]))[0].path == name


def test_only_zip_store_or_deflate_and_no_unix_link_extra():
    with pytest.raises(SourceRejected):
        inspect(bundle([("metrics.csv", "content")], compression=zipfile.ZIP_BZIP2))
    link = zipfile.ZipInfo("metrics.csv")
    link.create_system = 3
    link.external_attr = (stat.S_IFREG | 0o600) << 16
    link.extra = struct.pack("<HH", 0x000D, 4) + b"link"
    with pytest.raises(SourceRejected):
        inspect(bundle([(link, "content")]))
    with pytest.raises(SourceRejected):
        inspect(b"executable-prefix" + bundle([("metrics.csv", "content")]))
    with pytest.raises(SourceRejected):
        inspect(bundle([("metrics.csv", "content")])[:-10])


def test_source_seal_and_snapshot_identity_survive_root_and_capture_changes(tmp_path):
    data = payload()
    first, second = (project(tmp_path / name) for name in ("worker-one", "worker-two"))
    before = first.preview(data, captured_at="2026-11-10T00:00:00Z")
    after = second.preview(data, captured_at="2026-11-11T00:00:00Z")
    assert before == after
    selected = mapping(before)
    one = first.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected, captured_at="2026-11-10T00:00:00Z")
    two = second.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected, captured_at="2026-11-11T00:00:00Z")
    assert one.snapshot_id == two.snapshot_id
    assert one.record_sha256 != two.record_sha256
    assert len(one.mapping.runs) == 5
    assert [row.metric_value for row in one.mapping_result.metric_observations] == [0.0, -0.125, 1e-300, 0.25, 0.5]
    reloaded = UploadSnapshot.model_validate_json(two.model_dump_json())
    view = analyze_initial_upload(reloaded, "What is observed?")
    assert view.snapshot.id == two.snapshot_id
    assert view.evidence[0].source_sha256 == hashlib.sha256(data).hexdigest()


def test_changed_bytes_refuse_old_mapping(tmp_path):
    instance = project(tmp_path / "root")
    before = instance.preview(payload(), captured_at="2026-11-10T00:00:00Z")
    changed = payload(values=("1", "2", "3", "4", "5"))
    after = instance.preview(changed, captured_at="2026-11-10T00:00:00Z")
    assert before["source_state_sha256"] != after["source_state_sha256"]
    with pytest.raises(UploadMappingRejected) as caught:
        instance.confirm(changed, workspace_id="ws1", source_id="src1", mapping=mapping(before), captured_at="2026-11-10T00:00:00Z")
    assert caught.value.issues[0]["code"] == "mapping_candidate_unavailable"


def test_unicode_mapping_keeps_five_runs_fields_and_metric_through_analysis(tmp_path):
    runs = [f"lần-{index}" for index in range(5)]
    data = bundle([("dữ_liệu.csv", "mã,nhãn\n1,0\n2,1\n"),
        ("metrics.csv", "run,name,giá_trị\n" + "".join(
            f"{run},độ_chính_xác,{index / 10}\n" for index, run in enumerate(runs)))])
    instance = project(tmp_path / "root")
    preview = instance.preview(data, captured_at="2026-11-10T00:00:00Z")
    assert preview["excluded_metrics"] == []
    assert [run["run_id"] for run in preview["mapping_candidates"]["runs"]] == runs
    selected = mapping(preview)
    selected["target"].update(target_field="nhãn", identifier_field="mã")
    selected["metric_sources"][0]["metric_value_field"] = "giá_trị"
    selected["baseline_run_id"] = runs[0]
    selected["metric_definitions"][0].update(metric_name="độ_chính_xác", direction="higher_is_better")
    snapshot = instance.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected,
        captured_at="2026-11-10T00:00:00Z", expected_source_state_sha256=preview["source_state_sha256"])
    reloaded = UploadSnapshot.model_validate_json(snapshot.model_dump_json())
    assert [row.run_id for row in reloaded.mapping_result.metric_observations] == runs
    assert {row.metric_name for row in reloaded.mapping_result.metric_observations} == {"độ_chính_xác"}
    view = analyze_initial_upload(reloaded, "What is observed?")
    assert view.snapshot.metric_definitions[0].metric_name == "độ_chính_xác"
    assert view.snapshot.metric_definitions[0].direction == "higher_is_better"
    assert not list((tmp_path / "root").iterdir())


@pytest.mark.parametrize("identifier", ["độ chính xác", "../secret", "x/nhãn", "e\u0301", "x\u200dy", "x\n", "🔥",
    "pa\u034fssword", "pa\ufe0fssword", "x\u115fy", "x\U000e0100y", "\U00011f02", "b\u0301\U00011f41"])
def test_mapping_and_product_reject_unsafe_or_noncanonical_identifiers(identifier):
    from aletheia_lab.product.view import ProductMetricDefinition, ProductMetricObservation
    from aletheia_lab.project.mapping import MetricDefinition

    for definition in (MetricDefinition, ProductMetricDefinition):
        with pytest.raises(ValueError):
            definition(metric_name=identifier, direction="higher_is_better", regression_threshold=0.0)
    with pytest.raises(ValueError):
        ProductMetricObservation(snapshot_id="snapshot-1", run_id=identifier, value=0.0)


@pytest.mark.parametrize("metric", ["pa\u034fssword", "pa\ufe0fssword", "pa\U000e0100ssword", "pa\u200dssword", "pa\u202essword"])
def test_invisible_characters_cannot_expose_sensitive_metric_labels(tmp_path, metric):
    preview = project(tmp_path / "root").preview(payload(metric=metric), captured_at="2026-11-10T00:00:00Z")
    assert metric not in json.dumps(preview, ensure_ascii=False)
    assert preview["excluded_metrics"] == []
    candidate = preview["mapping_candidates"]["metric_sources"][0]
    assert not candidate["available"] and candidate["unavailable_reason"] == "sensitive_fields_withheld"


def test_unsupported_metric_is_explicitly_unavailable_without_renaming(tmp_path):
    instance = project(tmp_path / "root")
    data = payload(metric="độ chính xác")
    preview = instance.preview(data, captured_at="2026-11-10T00:00:00Z")
    excluded = preview["excluded_metrics"]
    assert len(excluded) == 1
    assert preview["excluded_metrics_total"] == 1
    assert excluded[0]["metric_name"] == "độ chính xác"
    assert excluded[0]["reason"] == "unsupported_metric_identifier"
    candidate = preview["mapping_candidates"]["metric_sources"][0]
    assert candidate["available"] is False and candidate["mapping_id"] is None
    assert candidate["unavailable_reason"] == "unsupported_source"
    safe = instance.preview(payload(metric="patient_ssn"), captured_at="2026-11-10T00:00:00Z")
    assert "patient_ssn" not in json.dumps(safe)


@pytest.mark.parametrize(("mutation", "field", "code"), [
    (lambda value: value["target"].update(target_field="missing"), "target.target_field", "unknown_field"),
    (lambda value: value["metric_sources"][0].update(metric_value_field="missing"), "metric_sources.0.metric_value_field", "unknown_field"),
    (lambda value: value["metric_definitions"][0].update(direction="sideways"), "metric_definitions.0.direction", "mapping_value_invalid"),
    (lambda value: value.update(baseline_run_id="absent"), "baseline_run_id", "baseline_invalid"),
    (lambda value: value["metric_definitions"].append({"metric_name": "extra", "direction": "lower_is_better", "regression_threshold": 0.0}),
     "metric_definitions.1", "metric_definition_unknown"),
])
def test_mapping_errors_locate_input_without_raw_exception(tmp_path, mutation, field, code):
    instance = project(tmp_path / "root")
    data = payload()
    selected = mapping(instance.preview(data, captured_at="2026-11-10T00:00:00Z"))
    mutation(selected)
    with pytest.raises(UploadMappingRejected) as caught:
        instance.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected, captured_at="2026-11-10T00:00:00Z")
    assert {"code": code, "field": field} in caught.value.issues
    serialized = json.dumps(caught.value.envelope())
    assert str(tmp_path) not in serialized and "Traceback" not in serialized


def test_concurrent_materialization_and_disk_full_cleanup(tmp_path, monkeypatch):
    instance = project(tmp_path / "root")
    with ThreadPoolExecutor(max_workers=2) as pool:
        previews = list(pool.map(lambda _: instance.preview(payload(), captured_at="2026-11-10T00:00:00Z"), range(2)))
    assert previews[0] == previews[1]
    assert not list((tmp_path / "root").glob("upload-materialize-*"))
    import aletheia_lab.hosted.upload_project as adapter
    def disk_full(*args, **kwargs):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(adapter.shutil, "copyfileobj", disk_full)
    other = payload(values=("10", "20", "30", "40", "50"))
    with pytest.raises(OSError):
        instance.preview(other, captured_at="2026-11-10T00:00:00Z")
    assert not (tmp_path / "root" / hashlib.sha256(other).hexdigest()).exists()
    assert not list((tmp_path / "root").glob("upload-materialize-*"))


def test_logical_grant_keeps_physical_boundary_and_local_identity(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for root in (one, two):
        root.mkdir()
        (root / "metrics.csv").write_text("run,name,value\nr,loss,0\n")
    assert grant_project_root(one).project_id != grant_project_root(two).project_id
    first = grant_immutable_project_root(one, source_sha256="a" * 64)
    assert first.project_id == grant_immutable_project_root(two, source_sha256="a" * 64).project_id
    one.rename(tmp_path / "gone")
    one.mkdir()
    (one / "metrics.csv").write_text("run,name,value\nr,loss,0\n")
    assert inspect_local_project(first, ingested_at="2026-11-10T00:00:00Z").source_state_sha256 is None


def test_worker_input_files_stay_under_private_inputs_and_mapping_errors_are_typed(tmp_path):
    root = tmp_path / "root"
    (root / "inputs").mkdir(parents=True)
    archive = root / "inputs" / "source.zip"
    archive.write_bytes(payload())
    assert _read_source_path(root, str(archive)) == payload()
    external = tmp_path / "source.zip"
    external.write_bytes(payload())
    with pytest.raises(ValueError):
        _read_source_path(root, str(external))
    request = WorkerInput(operation="confirm", source_path=str(archive), workspace_id="ws1", source_id="src1",
                          mapping={}, captured_at="2026-11-10T00:00:00Z")
    assert _execute(request, root) == {"schema_version": "hosted-upload-error/v1", "code": "mapping_invalid",
        "issues": [{"code": "mapping_shape_invalid", "field": "mapping"}]}
    with pytest.raises(ValueError):
        _execute(WorkerInput(operation="preview", source_path=str(archive), source_base64="AA==",
                             captured_at="2026-11-10T00:00:00Z"), root)


def test_source_projection_version_change_requires_a_fresh_preview(tmp_path, monkeypatch):
    import aletheia_lab.hosted.upload_project as adapter
    from aletheia_lab.hosted.upload_project import UploadSourceStateChanged

    instance = project(tmp_path / "root")
    data = payload()
    preview = instance.preview(data, captured_at="2026-11-10T00:00:00Z")
    selected = mapping(preview)
    instance.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected,
        captured_at="2026-11-10T00:00:00Z", expected_source_state_sha256=preview["source_state_sha256"])
    monkeypatch.setattr(adapter, "_SOURCE_SEAL_VERSION", "hosted-source-seal/v2")
    with pytest.raises(UploadSourceStateChanged) as caught:
        instance.confirm(data, workspace_id="ws1", source_id="src1", mapping=selected,
            captured_at="2026-11-10T00:00:00Z", expected_source_state_sha256=preview["source_state_sha256"])
    assert caught.value.envelope() == {"schema_version": "hosted-upload-error/v1", "code": "source_changed"}


def test_bounded_metric_exclusions_keep_every_unsupported_source_and_run_unavailable(tmp_path):
    instance = UploadProject(tmp_path, maximum_upload_bytes=1_000_000,
        maximum_expanded_bytes=1_000_000, maximum_entries=1000)
    items = [("dataset.csv", "id,target\n1,0\n")]
    items += [(f"run-{index}/metrics.csv", f"run,name,value\nrun-{index},độ {index},0\n") for index in range(130)]
    preview = instance.preview(bundle(items), captured_at="2026-11-10T00:00:00Z")
    assert len(preview["excluded_metrics"]) == 128
    assert preview["excluded_metrics_total"] == 130
    assert len(preview["mapping_candidates"]["metric_sources"]) == 130
    assert all(not candidate["available"] for candidate in preview["mapping_candidates"]["metric_sources"])
    assert all(not candidate["available"] for candidate in preview["mapping_candidates"]["runs"])
