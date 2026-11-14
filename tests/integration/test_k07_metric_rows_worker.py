"""The S/M/L metric-row axis through the actual isolated upload parser."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.upload_operations import UploadOperations
from aletheia_lab.hosted.upload_snapshot import UploadSnapshotV2
from aletheia_lab.hosted.worker_limits import MAX_UPLOAD_OUTPUT_BYTES
from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor

pytestmark = pytest.mark.integration


def _source(rows, *, version2=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value,step" + (",class,unit" if version2 else "") + "\n" + "".join(
            f"lần-{index % 5},độ_chính_xác,{index / 1000},{index // 5}" +
            (f",lớp {'A' if index % 2 else 'B'},%" if version2 else "") + "\n" for index in range(rows)))
    return output.getvalue()


@pytest.mark.parametrize("rows", [1000, 10000, 100000])
@pytest.mark.parametrize("version2", [False, True], ids=["v1", "v2"])
def test_actual_worker_retains_every_metric_row_and_unicode_identity(tmp_path, rows, version2):
    if rows == 100000 and os.environ.get("K07_TEST_L_ROWS") != "1":
        pytest.skip("Explicit K07_TEST_L_ROWS=1 required for the bounded L parser run")

    async def run():
        data = _source(rows, version2=version2)

        class Sources:
            async def read(self, **kwargs):
                assert kwargs["workspace_id"] == "a" and kwargs["source_id"] == "src_synthetic"
                return data

        class RecordingWorker(UploadWorkerSupervisor):
            sizes = []

            async def run_upload(self, payload):
                result = await super().run_upload(payload)
                self.sizes.append(len(result))
                return result

        worker = RecordingWorker(python=Path(sys.executable).absolute(),
            source_root=Path(__file__).resolve().parents[2] / "src", private_root=tmp_path,
            slots=1, timeout_seconds=180)
        operations = UploadOperations(Sources(), worker, private_root=tmp_path)
        arguments = dict(credential_digest=SecretStr("a" * 64), workspace_id="a", source_id="src_synthetic")
        started = time.monotonic()
        try:
            preview = await operations.preview(**arguments)
            target = preview.mapping_candidates["targets"][0]
            metric = preview.mapping_candidates["metric_sources"][0]
            mapping = {"target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                "target_field": "target", "identifier_field": "id"},
                "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                    "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
                    "run_id_field": "run", "step_field": "step"}],
                "runs": [{"run_id": f"lần-{index}", "config_item_ids": []} for index in range(5)],
                "baseline_run_id": "lần-0", "metric_definitions": [{"metric_name": "độ_chính_xác",
                    "direction": "higher_is_better", "regression_threshold": 0.08}]}
            if version2:
                mapping["schema_version"] = "project-mapping-request/v2"
                mapping["metric_sources"][0].update(class_field="class", unit_field="unit")
                mapping["metric_definitions"][0]["unit"] = "%"
            snapshot = await operations.prepare_confirm(**arguments, mapping=mapping)
            observations = snapshot.mapping_result.metric_observations
            assert len(observations) == rows
            assert {row.source_record_index for row in observations} == set(range(rows))
            assert {row.run_id for row in observations} == {f"lần-{index}" for index in range(5)}
            assert {row.metric_name for row in observations} == {"độ_chính_xác"}
            assert snapshot.source_sha256 == hashlib.sha256(data).hexdigest()
            assert len({(row.run_id, row.metric_name, row.step) for row in observations}) == rows
            assert worker.sizes[1] <= MAX_UPLOAD_OUTPUT_BYTES
            if version2:
                assert isinstance(snapshot, UploadSnapshotV2)
                assert {row.class_label for row in observations} == {"lớp A", "lớp B"}
                assert {(row.unit, row.unit_origin) for row in observations} == {("%", "source")}
            print("K07_METRIC_ROWS " + json.dumps({"rows": rows, "version2": version2, "source_bytes": len(data),
                "preview_output_bytes": worker.sizes[0], "snapshot_output_bytes": worker.sizes[1],
                "elapsed_seconds": time.monotonic() - started}))
        finally:
            await worker.aclose()
        assert not list((tmp_path / "inputs").iterdir())
        assert not list((tmp_path / "materializations").glob("*"))

    asyncio.run(run())
