"""Recovery artifacts authenticate before any database archive can be executed."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.recovery_points import RecoveryRejected, _canonical, verify_bundle


def _bundle(tmp_path):
    root = tmp_path / 'backup'
    root.mkdir()
    store = FilesystemObjectStore(root / 'objects')
    object_ = store.publish(b'hello', media_type='application/octet-stream')
    (root / 'database.dump').write_bytes(b'synthetic archive; verification does not execute it')
    (root / 'objects.jsonl').write_bytes(_canonical({'sha256': object_.sha256, 'byte_size': 5,
        'media_type': 'application/octet-stream', 'schema_version': None}) + b'\n')
    body = {'format': 'hosted-recovery-bundle/v1', 'backup_id': 'a' * 32,
        'snapshot_at': '2026-11-13T00:00:00+00:00', 'scope': 'Synthetic verification only.',
        'schema': 'restored_app', 'objects': 1,
        'object_bytes': 5, 'files': {name: {'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest(),
        'bytes': (root / name).stat().st_size} for name in ('database.dump', 'objects.jsonl')}}
    key = b'synthetic-operator-signing-key-only-for-test-32'
    (root / 'manifest.json').write_bytes(_canonical({'manifest': body,
        'hmac_sha256': hmac.digest(key, _canonical(body), 'sha256').hex()}))
    return root, key, store, object_


def test_signed_bundle_verifies_physical_closure_without_database_execution(tmp_path):
    root, key, _, _ = _bundle(tmp_path)
    assert verify_bundle(root, key)['object_bytes'] == 5


@pytest.mark.parametrize('target', ['manifest', 'database.dump', 'objects.jsonl', 'object', 'key', 'marker'])
def test_tampered_or_missing_recovery_inputs_fail_closed(tmp_path, target):
    root, key, store, object_ = _bundle(tmp_path)
    if target == 'key':
        key = b'other-synthetic-private-operator-key-32'
    elif target == 'object':
        store.path(object_.sha256).write_bytes(b'wrong')
    elif target == 'marker':
        (root / 'objects/.k05-object-store').unlink()
    else:
        path = root / ('manifest.json' if target == 'manifest' else target)
        path.write_bytes(path.read_bytes() + b'corrupt')
    with pytest.raises(RecoveryRejected):
        verify_bundle(root, key)


def test_short_signing_key_is_rejected(tmp_path):
    root, _, _, _ = _bundle(tmp_path)
    with pytest.raises(RecoveryRejected):
        verify_bundle(root, b'weak')
