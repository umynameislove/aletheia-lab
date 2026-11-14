"""Signed recovery inputs still need safe temporal ordering before target writes."""
import hashlib
import hmac
import importlib.util
from pathlib import Path

import pytest
from test_k18_recovery_points import _bundle

from aletheia_lab.hosted.deletion_checkpoint import DeletionCheckpointPage
from aletheia_lab.hosted.recovery_points import RecoveryRejected, _canonical


@pytest.mark.parametrize('checkpoint_time,rejected', [('2026-11-12T23:59:59+00:00', True),
    ('2026-11-13T00:00:01+00:00', False)])
def test_restore_controller_refuses_older_authority_checkpoint_before_any_target_write(tmp_path, monkeypatch, checkpoint_time, rejected):
    bundle, key, _, _ = _bundle(tmp_path)
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    (checkpoint / 'authority.jsonl').write_bytes(b'')
    (checkpoint / 'deletions.jsonl').write_bytes(DeletionCheckpointPage(after=None, items=(), next_cursor=None).model_dump_json().encode() + b'\n')
    body = {'format': 'hosted-authority-checkpoint/v1', 'checkpoint_id': 'b' * 32,
        'schema': 'restored_app', 'captured_at': checkpoint_time, 'authority_rows': 0,
        'deletion_pages': 1, 'files': {name: {'sha256': hashlib.sha256((checkpoint / name).read_bytes()).hexdigest(),
            'bytes': (checkpoint / name).stat().st_size} for name in ('authority.jsonl', 'deletions.jsonl')}}
    manifest = checkpoint / 'manifest.json'
    manifest.write_bytes(_canonical({'manifest': body, 'hmac_sha256': hmac.digest(key, _canonical(body), 'sha256').hex()}))
    secret = tmp_path / 'private-key'
    database = tmp_path / 'private-database'
    secret.write_bytes(key)
    database.write_text('postgresql://synthetic.invalid/offline')
    secret.chmod(0o600)
    database.chmod(0o600)
    path = Path(__file__).resolve().parents[2] / 'scripts/k18_recovery_admin.py'
    spec = importlib.util.spec_from_file_location('k18_operator_review_subject', path)
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)

    class ReachedTargetWrite(Exception):
        pass

    def target_write(*args, **options):
        raise ReachedTargetWrite('Validated checkpoint ordering reached the restore boundary.')
    monkeypatch.setattr(subject, 'restore_bundle', target_write)
    args = subject.arguments(['restore', '--key-file', str(secret), '--database-file', str(database),
        '--object-root', str(tmp_path / 'target-objects'), '--bundle', str(bundle),
        '--checkpoint', str(checkpoint), '--checkpoint-sha256', hashlib.sha256(manifest.read_bytes()).hexdigest(),
        '--pg-bin', str(tmp_path / 'unused')])
    with pytest.raises(RecoveryRejected if rejected else ReachedTargetWrite):
        subject.execute(args)
