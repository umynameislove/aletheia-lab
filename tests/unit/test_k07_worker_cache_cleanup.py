import io
import zipfile

import pytest

from aletheia_lab.hosted.source_bundle import SourceRejected
from aletheia_lab.hosted.upload_project import UploadMappingRejected, UploadProject, _materialize
from aletheia_lab.hosted.upload_worker import WorkerInput, _execute


def source(*, malformed=False):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0\n")
        archive.writestr("config.json", "{" if malformed else '{"seed":42}\n')
    return data.getvalue()


def test_adapter_releases_owned_cache_after_success_and_parser_or_mapping_failure(tmp_path):
    adapter = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    preview = adapter.preview(source(), captured_at="2026-11-10T00:00:00Z")
    assert preview["mapping_candidates"]["targets"]
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SourceRejected):
        adapter.preview(source(malformed=True), captured_at="2026-11-10T00:00:00Z")
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(UploadMappingRejected):
        adapter.confirm(source(), workspace_id="ws1", source_id="src1", mapping={}, captured_at="2026-11-10T00:00:00Z")
    assert list(tmp_path.iterdir()) == []


def test_file_worker_uses_request_owned_scratch_and_preserves_parent_input(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    request_id = "a" * 32
    path = inputs / (request_id + ".zip")
    data = source()
    path.write_bytes(data)
    result = _execute(WorkerInput(operation="preview", source_path=str(path), captured_at="2026-11-10T00:00:00Z"), tmp_path)
    assert result["mapping_candidates"]["targets"]
    assert path.read_bytes() == data
    assert not (tmp_path / "materializations" / request_id).exists()
    path.write_bytes(source(malformed=True))
    with pytest.raises(SourceRejected):
        _execute(WorkerInput(operation="preview", source_path=str(path), captured_at="2026-11-10T00:00:00Z"), tmp_path)
    assert path.exists()
    assert not (tmp_path / "materializations" / request_id).exists()


def test_materialization_still_verifies_bytes_when_reusing_an_existing_owned_tree(tmp_path):
    data = source()
    options = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    target = _materialize(data, tmp_path, **options)
    (target / "metrics.csv").write_text("tampered")
    with pytest.raises(SourceRejected):
        _materialize(data, tmp_path, **options)
