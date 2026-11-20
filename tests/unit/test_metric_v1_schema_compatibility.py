"""V2 additions must preserve published V1 model schemas exactly."""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

_EXPECTED = {'aletheia_lab.hosted.native_upload_records.UploadResultRecord': '029053917b8f7c4ad5dd3c3c721b7b57baac575f5aa9ee00c75a7e2e0dbb31b9', 'aletheia_lab.hosted.upload_snapshot.UploadSnapshot': '52550dbcb66f70b9df367848651461455652d33a1f398f3149fd3fa244d94a18', 'aletheia_lab.product.view.ProductView': 'fc7e8a2a400dd6367223bc3dd8e2806da8e4a26bff093fb8847810d8dcd00316', 'aletheia_lab.project.mapping.MetricDefinition': '778131d7b6b56314b52bd1cdf496686fec23f86ec05bc6ee2d18aa14480135cc', 'aletheia_lab.project.mapping.MetricObservation': '23f658f1794372c428313aecb779903f77de2c9885c0feafd67a604c9519d144', 'aletheia_lab.project.mapping.MetricSourceMapping': 'c158b8fb8f75d8ae0d8fc14bba3b5ee5503755e79d8cc2c044ed45144f7d31c0', 'aletheia_lab.project.mapping.ProjectMappingConfiguration': '348b755c062c994500cab3d945d4ff21b09b9c8ce9b075f99d8cd0604d2f9839', 'aletheia_lab.project.mapping.ProjectMappingResult': '654e6d1ea6fb0bf1cd191c560d6539ad35d3908d49f15612800dd589e8f67265', 'aletheia_lab.project.regression.ProjectSnapshotComparison': 'e71fa6f598db91d9c72415db506d0dfb68eb1a4c4c1603a52ee6d4208a580b88', 'aletheia_lab.project.snapshots.ProjectSnapshot': '41fbe782b64c27995655e1ebeb6eda3cbcba127c6a86e12f31654f18e87056dc'}


@pytest.mark.parametrize("qualified_name,expected", _EXPECTED.items())
def test_metric_v2_preserves_v1_schema(qualified_name: str, expected: str) -> None:
    module, name = qualified_name.rsplit(".", 1)
    model = getattr(importlib.import_module(module), name)
    payload = json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert hashlib.sha256(payload).hexdigest() == expected
