#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
python="$(find /tmp -maxdepth 1 -type d -name 'aletheia-r45-test-*' | head -n1)/venv/bin/python"
pg_root="$(cat "$root/evidence/linux-pg16-path.txt")"
export LD_LIBRARY_PATH="$pg_root/root/usr/lib/x86_64-linux-gnu"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME
"$python" -B - "$root" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json, os, sys, tempfile, time

root = Path(sys.argv[1])
kit = root / 'Release-K01-K18-R4.12'
sys.path.insert(0, str(root / 'tools'))
import run_checks as operator
manifest = json.loads((kit / 'source-manifest.json').read_bytes())
out = root / 'evidence/linux-r412-focused'
out.mkdir(exist_ok=False)
database_file = root / '.private/linux-database-url.txt'
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTEST_ADDOPTS='',
           PYTHONPATH=str(kit / 'source/src'))
database = database_file.read_text(encoding='utf-8-sig').strip()
for key in list(env):
    if key.endswith('_TEST_DATABASE_URL'):
        env.pop(key)
for key in ('K05', 'K06', 'K07', 'K09', 'K18'):
    env[key + '_TEST_DATABASE_URL'] = database
files = ['tests/unit/test_k09_execution.py', 'tests/unit/test_k05_migrations.py',
         'tests/unit/test_k02_job_boundary.py',
         'tests/integration/test_k07_repeat_import_postgres.py',
         'tests/integration/test_k09_revision_conflict_postgres.py',
         'tests/integration/test_k09_execution_postgres.py',
         'tests/integration/test_k06_follow_up_policy_postgres.py',
         'tests/integration/test_k06_follow_up_http_postgres.py',
         'tests/integration/test_k05_job_migration_postgres.py',
         'tests/integration/test_k18_migration_compatibility_postgres.py']
work = Path(tempfile.mkdtemp(prefix='aletheia-r412-focused-')) / 'work'
command = [sys.executable, '-B', '-m', 'pytest', '-q', '-ra', '--tb=short', '-o', 'addopts=',
           '-p', 'no:cacheprovider', '--basetemp=' + str(work), '--junitxml=' + str(out / 'junit.xml'), *files]
meta = {k: manifest[k] for k in ('source_identity', 'runtime_identity')}
(out / 'attempt.json').write_text(json.dumps(dict(started_utc=datetime.now(timezone.utc).isoformat(),
    command=command, scope='Native Linux focused regression, not load acceptance.', **meta), indent=2), encoding='utf-8')
started = time.monotonic()
with (out / 'pytest.log').open('w', encoding='utf-8') as log:
    result = operator.run_logged(command, cwd=kit / 'source', env=env, log=log,
                                 secrets=operator.sensitive_values(database_file))
operator.sanitize_junit(out / 'junit.xml', operator.sensitive_values(database_file))
import hashlib
assert all(hashlib.sha256((kit / 'source' / name).read_bytes()).hexdigest() == expected
           for name, expected in manifest['files'].items())
report = dict(exit_code=result.returncode, source_stable=True, elapsed_seconds=time.monotonic()-started,
              finished_utc=datetime.now(timezone.utc).isoformat(), primary_or_full_release_pass=False, **meta)
(out / 'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report))
sys.exit(result.returncode)
PY
