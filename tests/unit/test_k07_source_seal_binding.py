import asyncio
import base64
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.preview_leases import PreviewChanged
from aletheia_lab.hosted.upload_operations import UploadOperations
from aletheia_lab.hosted.upload_worker import WorkerInput, _execute


def test_confirm_binds_the_preview_projection_version_for_the_same_source_bytes(tmp_path, monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0\n")
    source = data.getvalue()
    preview = _execute(WorkerInput(operation="preview", source_base64=base64.b64encode(source).decode(),
                                  captured_at="2026-11-10T00:00:00Z"), tmp_path)
    target, metric = (preview["mapping_candidates"][key][0] for key in ("targets", "metric_sources"))
    mapping = {"target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
        "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
            "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
            "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": "baseline", "config_item_ids": []}], "baseline_run_id": "baseline",
        "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.0}]}
    receipt = SimpleNamespace(source_sha256=preview["source_sha256"], source_state_sha256=preview["source_state_sha256"])

    class Sources:
        async def read(self, **kwargs):
            return source

    class Leases:
        async def validate(self, **kwargs):
            return receipt

    class Worker:
        async def run_upload(self, payload):
            return json.dumps(_execute(WorkerInput.model_validate_json(payload), tmp_path)).encode()

    import aletheia_lab.hosted.upload_project as adapter
    # This simulates a parser/policy version update, while exact source bytes stay unchanged.
    monkeypatch.setattr(adapter, "_SOURCE_SEAL_VERSION", "hosted-source-seal/v2", raising=False)
    with pytest.raises(PreviewChanged):
        asyncio.run(UploadOperations(Sources(), Worker(), leases=Leases()).prepare_confirm(
            credential_digest=SecretStr("a" * 64), workspace_id="ws1", source_id="src1",
            mapping=mapping, preview_id="prev_" + "0" * 32))
