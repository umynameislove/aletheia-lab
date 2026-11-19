"""An authenticated but incomplete producer checkpoint cannot apply any fence."""
from __future__ import annotations

import hashlib
import hmac

import pytest
from test_k13_deletion_checkpoint import _fence

from aletheia_lab.hosted.deletion_checkpoint import DeletionCheckpointPage
from aletheia_lab.hosted.recovery_checkpoint import verify_checkpoint
from aletheia_lab.hosted.recovery_points import RecoveryRejected, _canonical


def _checkpoint(root, pages, count=None):
    root.mkdir()
    key = b'synthetic-external-checkpoint-key-32bytes-only'
    (root / 'authority.jsonl').write_bytes(b'')
    (root / 'deletions.jsonl').write_bytes(b''.join(page.model_dump_json().encode() + b'\n' for page in pages))
    body = {'format': 'hosted-authority-checkpoint/v1', 'checkpoint_id': 'a' * 32,
        'schema': 'restored_app', 'captured_at': '2026-11-13T00:00:00+00:00',
        'authority_rows': 0, 'deletion_pages': len(pages) if count is None else count,
        'files': {name: {'bytes': (root / name).stat().st_size,
            'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in ('authority.jsonl', 'deletions.jsonl')}}
    manifest = root / 'manifest.json'
    manifest.write_bytes(_canonical({'manifest': body,
        'hmac_sha256': hmac.digest(key, _canonical(body), 'sha256').hex()}))
    return key, hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_complete_signed_checkpoint_needs_trusted_latest_inventory_hash(tmp_path):
    folder = tmp_path / 'checkpoint'
    key, digest = _checkpoint(folder, [DeletionCheckpointPage(after=None, items=(), next_cursor=None)])
    assert verify_checkpoint(folder, schema='restored_app', signing_key=key,
                             expected_manifest_sha256=digest)['deletion_pages'] == 1
    for value in ('b' * 64, 'invalid'):
        with pytest.raises(RecoveryRejected):
            verify_checkpoint(folder, schema='restored_app', signing_key=key, expected_manifest_sha256=value)


@pytest.mark.parametrize('defect', ['empty', 'truncated', 'trailing', 'wrong_count', 'gap'])
def test_signed_producer_chain_defects_fail_before_database_or_first_apply(tmp_path, defect):
    fence = _fence()
    cursor = (fence.workspace_id, fence.project_id)
    first = DeletionCheckpointPage(after=None, items=(fence,), next_cursor=cursor)
    final = DeletionCheckpointPage(after=cursor, items=(), next_cursor=None)
    pages = {'empty': [], 'truncated': [first], 'trailing': [first, final, final],
             'wrong_count': [first, final], 'gap': [final]}[defect]
    folder = tmp_path / 'checkpoint'
    key, digest = _checkpoint(folder, pages, count=3 if defect == 'wrong_count' else None)
    with pytest.raises(RecoveryRejected):
        verify_checkpoint(folder, schema='restored_app', signing_key=key, expected_manifest_sha256=digest)
