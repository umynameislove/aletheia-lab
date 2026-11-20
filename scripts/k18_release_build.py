"""Build deterministic wheel/sdist from an explicit, hash-verified source inventory.

Run with the clean build interpreter installed from deploy/hosted/build-requirements.lock.
The new output directory contains the staged source, raw builds and normalized artifacts.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any


def _relative(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    blocked = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules', '.env', 'actors.jsonl'}
    if (not name or '\\' in name or ':' in name or value.is_absolute() or value.as_posix() != name
            or any(part in {'', '.', '..'} | blocked for part in value.parts)):
        raise ValueError('Unsafe source/archive inventory path.')
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stage(source: Path, output: Path, inventory: dict[str, Any]) -> None:
    files = inventory.get('files')
    if not isinstance(files, dict) or not files or inventory.get('file_count') != len(files):
        raise ValueError('A complete source file inventory is required.')
    identity = inventory.get('source_identity')
    if (not isinstance(identity, str) or re.fullmatch('[0-9a-f]{64}', identity) is None
            or _sha(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()) != identity):
        raise ValueError('Source inventory identity does not match its file map.')
    # Validate every path, digest and symlink before creating an output directory.
    checked: list[tuple[Path, str, str]] = []
    for name, expected in sorted(files.items()):
        if not isinstance(name, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError('Invalid source inventory entry.')
        relative = _relative(name)
        current = source
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError('Source inventory cannot contain symlinks.')
        if not current.is_file() or _sha(current.read_bytes()) != expected:
            raise ValueError(f'Source inventory hash mismatch: {name}')
        checked.append((current, name, expected))
    output.mkdir(mode=0o700)
    stage = output / 'source'
    for current, name, expected in checked:
        data = current.read_bytes()
        if _sha(data) != expected:
            raise ValueError('Source changed during staging.')
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        # These source artifacts are data/modules invoked through Python, never
        # executable payloads. Console entrypoint modes are set by the installer.
        target.chmod(0o644)


def _wheel(source: Path, target: Path, epoch: int) -> None:
    stamp = time.gmtime(epoch)[:6]
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as result:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel entries.')
        for name in sorted(names):
            _relative(name)
            original = archive.getinfo(name)
            if original.is_dir() or stat.S_ISLNK(original.external_attr >> 16):
                raise ValueError('Unexpected wheel entry type.')
            info = zipfile.ZipInfo(name, stamp)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            result.writestr(info, archive.read(name), compresslevel=9)


def _sdist(source: Path, target: Path, epoch: int) -> None:
    with tarfile.open(source, 'r:gz') as archive, target.open('wb') as handle, gzip.GzipFile(filename='', mode='wb', fileobj=handle, mtime=epoch, compresslevel=9) as compressed, tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as result:
        members = archive.getmembers()
        if len({item.name for item in members}) != len(members):
            raise ValueError('Duplicate sdist entries.')
        for original in sorted(members, key=lambda item: item.name):
            _relative(original.name)
            if not (original.isfile() or original.isdir()):
                raise ValueError('Unexpected sdist entry type.')
            info = tarfile.TarInfo(original.name)
            info.type = tarfile.DIRTYPE if original.isdir() else tarfile.REGTYPE
            info.mode, info.mtime = (0o755 if original.isdir() else 0o644), epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            if original.isfile():
                extracted = archive.extractfile(original)
                if extracted is None:
                    raise ValueError('Unreadable source distribution member.')
                data = extracted.read()
            else:
                data = b''
            info.size = len(data)
            result.addfile(info, io.BytesIO(data) if original.isfile() else None)


def build(source: Path, manifest: Path, output: Path, epoch: int | None = None) -> dict[str, Any]:
    source, output = source.resolve(strict=True), output.parent.resolve(strict=True) / output.name
    if output.exists() or not output.parent.is_dir() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the source tree.')
    inventory = json.loads(manifest.read_bytes())
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', inventory.get('source_date_epoch', 0))) if epoch is None else epoch
    if epoch < 315532800 or epoch > 0xFFFFFFFF:
        raise ValueError('SOURCE_DATE_EPOCH must fit ZIP/GZIP timestamps (1980 through 2106).')
    _stage(source, output, inventory)
    tool_lock = (output / 'source/deploy/hosted/build-requirements.lock').read_text()
    pins = dict(re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', tool_lock, re.MULTILINE))
    if not pins or any(version(name) != expected for name, expected in pins.items()):
        raise ValueError('Build dependencies must match the staged hash lock.')
    raw, artifacts = output / 'raw', output / 'dist'
    raw.mkdir()
    artifacts.mkdir()
    environment = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    environment.update(SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED='0', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_CONFIG_FILE=os.devnull)
    commands = [
        [sys.executable, '-I', '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation', '--no-cache-dir', '--wheel-dir', str(raw), str(output / 'source')],
        [sys.executable, '-I', '-c', 'from setuptools import setup; setup()', 'sdist', '--dist-dir', str(raw)],
    ]
    with (output / 'build.log').open('w') as log:
        for command in commands:
            subprocess.run(command, cwd=output / 'source', env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    wheels, sdists = list(raw.glob('*.whl')), list(raw.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError('Expected one application wheel and one source distribution.')
    _wheel(wheels[0], artifacts / wheels[0].name, epoch)
    _sdist(sdists[0], artifacts / sdists[0].name, epoch)
    result = {'source_date_epoch': epoch, 'source_file_count': len(inventory['files']),
              'source_identity': inventory.get('source_identity'),
              'build_tools': pins,
              'mode_policy': 'regular source/package files 0644; archive directories 0755; installer creates console entrypoints',
              'artifacts': {path.name: {'bytes': path.stat().st_size, 'sha256': _sha(path.read_bytes())} for path in sorted(artifacts.iterdir())}}
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--epoch', type=int)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.manifest, args.output, args.epoch), indent=2))


if __name__ == '__main__':
    main()
