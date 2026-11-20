"""Release normalization preserves payloads while removing host-only metadata."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location('k18_release_build', Path(__file__).parents[2] / 'scripts/k18_release_build.py')
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def _inventory(files):
    return {'file_count': len(files), 'files': files,
            'source_identity': hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def test_wheel_payload_and_record_survive_host_mode_and_timestamp_variation(tmp_path):
    outputs = []
    for number, mode in enumerate((0o100664, 0o100600)):
        source, target = tmp_path / f'wheel{number}.whl', tmp_path / f'normalized{number}.whl'
        with zipfile.ZipFile(source, 'w') as archive:
            for name, data in [('pkg/module.py', b'VALUE=1\n'), ('pkg.dist-info/RECORD', b'unchanged-record')]:
                info = zipfile.ZipInfo(name, (2020 + number, 1, 1, 0, 0, 0))
                info.external_attr = mode << 16
                archive.writestr(info, data)
        release._wheel(source, target, 1794540675)
        outputs.append(target.read_bytes())
        with zipfile.ZipFile(target) as archive:
            assert archive.read('pkg/module.py') == b'VALUE=1\n'
            assert archive.read('pkg.dist-info/RECORD') == b'unchanged-record'
            assert all(item.external_attr >> 16 == 0o100644 for item in archive.infolist())
    assert outputs[0] == outputs[1]


def test_sdist_same_payload_reproduces_despite_tar_owner_pax_order_and_clock(tmp_path):
    outputs = []
    for number in range(2):
        source, target = tmp_path / f'raw{number}.tar.gz', tmp_path / f'normalized{number}.tar.gz'
        with tarfile.open(source, 'w:gz') as archive:
            for name in (['pkg/b.py', 'pkg/a.py'] if number else ['pkg/a.py', 'pkg/b.py']):
                info = tarfile.TarInfo(name)
                info.uid, info.gid, info.uname = 501 + number, 20 + number, 'host-owner'
                info.mode, info.mtime = 0o600 + number, 1700000000.5 + number
                info.pax_headers = {'atime': str(info.mtime), 'comment': 'host-only'}
                info.size = len(name.encode())
                archive.addfile(info, io.BytesIO(name.encode()))
        release._sdist(source, target, 1794540675)
        outputs.append(target.read_bytes())
        with tarfile.open(target) as archive:
            assert archive.getnames() == ['pkg/a.py', 'pkg/b.py']
            for member in archive.getmembers():
                assert member.uid == member.gid == 0 and member.mtime == 1794540675
                assert member.mode == 0o644 and not member.pax_headers
                assert archive.extractfile(member).read() == member.name.encode()
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'C:/drive', 'folder\\file', 'folder//file', '.venv/private', '.env', 'actors.jsonl'])
def test_source_inventory_rejects_unsafe_or_private_paths_before_output(tmp_path, name):
    with pytest.raises(ValueError, match='Unsafe'):
        release._stage(tmp_path, tmp_path / 'output', _inventory({name: 'a' * 64}))
    assert not (tmp_path / 'output').exists()


def test_source_inventory_hash_and_symlink_failure_do_not_create_partial_output(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.py').write_bytes(b'actual')
    (source / 'b.py').symlink_to(source / 'a.py')
    sha = hashlib.sha256(b'actual').hexdigest()
    for name, expected in [('a.py', '0' * 64), ('b.py', sha)]:
        with pytest.raises(ValueError):
            release._stage(source, tmp_path / 'output', _inventory({name: expected}))
        assert not (tmp_path / 'output').exists()


def test_forged_source_identity_is_rejected_even_when_all_file_hashes_match(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.py').write_bytes(b'actual')
    inventory = _inventory({'a.py': hashlib.sha256(b'actual').hexdigest()})
    inventory['source_identity'] = 'a' * 64
    with pytest.raises(ValueError, match='identity'):
        release._stage(source, tmp_path / 'output', inventory)
    assert not (tmp_path / 'output').exists()
