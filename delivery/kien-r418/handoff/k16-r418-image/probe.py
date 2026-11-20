"""Exercise the changed container runtime against the sealed R4.18 artifacts."""
import asyncio
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import zlib
from importlib.metadata import distributions

import aletheia_lab
import cryptography
import pandas
import psycopg
import pyarrow
import sklearn

kit = Path('/kit')
manifest = json.loads((kit / 'source-manifest.json').read_bytes())
expected = json.loads(Path('/baseline/result.json').read_bytes())
assert sys.flags.isolated and sys.version_info[:3] == (3, 12, 16)
assert os.environ['ALETHEIA_RELEASE_ID'] == manifest['source_identity']
assert os.getuid() == os.getgid() == 10001
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert all(int(status[n], 16) == 0 for n in ('CapEff', 'CapBnd', 'CapPrm'))
assert status['NoNewPrivs'].strip() == '1' and status['Seccomp'].strip() == '2'
root = Path('/var/lib/aletheia')
try:
    (root / 'root-write-probe').write_bytes(b'x')
except OSError as error:
    assert error.errno == 30
else:
    raise AssertionError('Expected read-only root')
assert tuple(map(int, sqlite3.sqlite_version.split('.'))) >= (3, 54, 0)
assert zlib.decompress(zlib.compress(b'K16-image')) == b'K16-image'
assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
programs = {name: str(Path(path).resolve()) if (path := shutil.which(name)) else None
            for name in ('mount', 'nsenter', 'systemd-homed', 'homectl', 'perl')}
assert all(programs[name] is None for name in ('systemd-homed', 'homectl', 'perl'))
assert all(programs[name] in (None, '/bin/busybox', '/usr/bin/busybox') for name in ('mount', 'nsenter'))
for role in ('api', 'worker'):
    command = [sys.executable, '-I', '-B', '-m', 'aletheia_lab.hosted.container_entrypoint', role]
    run = subprocess.run(command, capture_output=True, timeout=10)
    assert run.returncode == 1 and not run.stdout
    assert run.stderr == b'Hosted container configuration is unavailable.\n'

package_root = Path(aletheia_lab.__file__).resolve().parent
files = {name.removeprefix('src/aletheia_lab/'): digest for name, digest in manifest['files'].items()
         if name.startswith('src/aletheia_lab/')}
assert len(files) == manifest['runtime_count'] == 676
assert all(hashlib.sha256((package_root / name).read_bytes()).hexdigest() == digest for name, digest in files.items())
site = package_root.parent
lock = (kit / 'source/deploy/hosted/requirements.lock').read_text()
versions = dict(re.findall(r'^([a-z0-9-]+)==([^\s]+)', lock, re.M))
installed = {re.sub(r'[-_.]+', '-', d.metadata['Name']).lower(): d.version for d in distributions(path=[str(site)])}
assert len(versions) == 52 and installed == {**versions, 'aletheia-lab': '0.1.0'}

# Reuse the installed-wheel exercise with real child processes. Root stays read-only.
sys.path.insert(0, str(kit))
from installed_wheel_check import exercise
fixture_path = 'source/fixtures/metric-dimensions-hosted-v2/snapshot.json'
assert hashlib.sha256((kit / fixture_path).read_bytes()).hexdigest() == manifest['files'][fixture_path.removeprefix('source/')]
with tempfile.TemporaryDirectory() as folder:
    from aletheia_lab.hosted.container_entrypoint import secret_environment

    secrets = Path(folder) / 'secrets'
    secrets.mkdir(mode=0o700)
    names = ('database_api', 'database_worker', 'csrf_secret', 'admission_secret', 'login_proof_key')
    for name in names:
        path = secrets / name
        path.write_text('synthetic-' + name, encoding='utf-8')
        path.chmod(0o400)
    assert secret_environment('worker', secrets) == {'ALETHEIA_HOSTED_DATABASE_URL': 'synthetic-database_worker'}
    api = secret_environment('api', secrets)
    assert len(api) == 4 and api['ALETHEIA_HOSTED_DATABASE_URL'] == 'synthetic-database_api'
    assert psycopg.pq.__impl__ == 'binary'
    assert pyarrow.table({'witness': [16]}).column('witness').to_pylist() == [16]
    exercise_result = asyncio.run(exercise(kit, Path(folder), site))
assert all(result['sha256'] == expected['outputs'][fmt]['sha256']
           for fmt, result in exercise_result['outputs'].items())
print(json.dumps(dict(status='pass', source_identity=manifest['source_identity'],
    runtime_identity=manifest['runtime_identity'], runtime_files=676, pinned_dependencies=52,
    python=platform.python_version(), architecture=platform.machine(), sqlite=sqlite3.sqlite_version,
    openssl=ssl.OPENSSL_VERSION, zlib=zlib.ZLIB_RUNTIME_VERSION, uid=os.getuid(), gid=os.getgid(),
    read_only=True, caps_zero=True, no_new_privileges=True, seccomp_filter=True, programs=programs,
    missing_secrets_api_worker_fail_closed=True, synthetic_role_secret_mapping=True,
    native_imports=['cryptography', 'pandas', 'psycopg_binary', 'pyarrow', 'scikit-learn'],
    children=4, output_byte_parity=True,
    outputs=exercise_result['outputs'])))
