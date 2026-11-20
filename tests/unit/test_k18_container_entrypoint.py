"""Mounted secrets are role-specific, bounded and never printed on failed exec."""
from __future__ import annotations

import sys

import pytest

from aletheia_lab.hosted import container_entrypoint as entry
from aletheia_lab.hosted.object_store import ObjectIntegrityError


def test_worker_reads_only_its_own_database_secret_and_api_requires_all_keys(tmp_path):
    (tmp_path / 'database_worker').write_text('synthetic-worker-db-url\n')
    assert entry.secret_environment('worker', tmp_path) == {'ALETHEIA_HOSTED_DATABASE_URL': 'synthetic-worker-db-url'}
    with pytest.raises(ObjectIntegrityError):
        entry.secret_environment('api', tmp_path)
    for name in ('database_api', 'csrf_secret', 'admission_secret', 'login_proof_key'):
        (tmp_path / name).write_text('synthetic-' + name + '\n')
    values = entry.secret_environment('api', tmp_path)
    assert values['ALETHEIA_HOSTED_DATABASE_URL'] == 'synthetic-database_api'
    assert len(values) == 4 and 'ALETHEIA_HOSTED_OIDC_CLIENT_SECRET' not in values


@pytest.mark.parametrize('defect', ['oversize', 'multiline', 'symlink', 'exec'])
def test_invalid_secret_or_exec_failure_prints_only_generic_error(tmp_path, monkeypatch, capsys, defect):
    path = tmp_path / 'database_worker'
    sentinel = 'PRIVATE_MOUNTED_SECRET_SENTINEL'
    path.write_text(sentinel)
    if defect == 'oversize':
        path.write_text(sentinel * 10000)
    elif defect == 'multiline':
        path.write_text(sentinel + '\nother-line')
    elif defect == 'symlink':
        private = tmp_path / 'private'
        path.rename(private)
        path.symlink_to(private)
    monkeypatch.setattr(sys, 'argv', ['container', 'worker', '--secret-dir', str(tmp_path)])
    def failed(*args):
        raise OSError(sentinel)
    monkeypatch.setattr(entry.os, 'execve', failed)
    assert entry.main() == 1
    assert capsys.readouterr().err == 'Hosted container configuration is unavailable.\n'
