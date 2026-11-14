"""New upload comparisons retain canonical catalog metadata and legacy decoding."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_k04_native_catalog_adapter import DIGEST, _application
from test_k05_native_upload_records import upload_case

from aletheia_lab.hosted.postgres_catalog import (
    CatalogIntegrityError,
    RecordIndex,
    canonical_metadata,
    canonical_parents,
    decode_record,
)
from aletheia_lab.hosted.upload_comparison import (
    build_upload_comparison,
    comparison_metadata,
    comparison_parents,
)
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot, parse_upload_snapshot
from aletheia_lab.hosted_contract.workspaces import CanonicalRecord
from aletheia_lab.project.persistence import _json_bytes


@pytest.mark.parametrize('legacy', [False, True])
def test_native_comparison_decodes_before_legacy_kind_and_rejects_foreign_index_fields(tmp_path, legacy):
    before = (upload_case(tmp_path)[0] if legacy else parse_upload_snapshot(
        (Path(__file__).parents[2] / 'fixtures/metric-dimensions-hosted-v2/snapshot.json').read_bytes()))
    after = build_upload_snapshot(workspace_id=before.workspace_id, source_id='synthetic-replacement-source',
        source_sha256='b' * 64, mapping=before.mapping, mapping_result=before.mapping_result,
        captured_at=before.captured_at + timedelta(days=1))
    comparison = build_upload_comparison(before, after)
    kind, identity, project, schema, canonical = comparison_metadata(comparison)
    payload = _json_bytes(comparison.model_dump(mode='json'))
    index = RecordIndex(identity, project, kind, schema, canonical, hashlib.sha256(payload).hexdigest(), None, None)
    assert decode_record(index, payload) == comparison
    assert canonical_metadata(comparison) == comparison_metadata(comparison)
    assert canonical_parents(comparison) == comparison_parents(comparison) == tuple(sorted(comparison_parents(comparison)))
    for damaged in (replace(index, canonical_sha256='0' * 64), replace(index, project_id='p3-project-' + '0' * 64),
                    replace(index, record_id='p3-comparison-' + '0' * 64), replace(index, source_id='foreign-source'),
                    replace(index, snapshot_id=before.snapshot_id), replace(index, parent_result_id='p6-result-' + '0' * 64),
                    replace(index, schema_version='legacy-comparison/v1')):
        with pytest.raises(CatalogIntegrityError):
            decode_record(damaged, payload)
    app, checks = _application(comparison)

    async def run():
        response = await app._record(DIGEST, before.workspace_id, project, identity)
        await response.checkpoint()
        return response

    response = asyncio.run(run())
    public = CanonicalRecord.model_validate_json(response.response.body)
    assert public.record_kind == 'snapshot_comparison' and public.record_id == comparison.comparison_id
    assert public.data == comparison.model_dump(mode='json')
    assert checks[-1][2] == identity
