#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
kit="$(mktemp -d /tmp/aletheia-r412-ext4-XXXXXXXX)"
python="$(find /tmp -maxdepth 1 -type d -name 'aletheia-r45-test-*' | head -n1)/venv/bin/python"
tar -xzf "$root/tmp/r412-sealed-test-source.tar.gz" -C "$kit"
pg_root="$(cat "$root/evidence/linux-pg16-path.txt")"
export LD_LIBRARY_PATH="$pg_root/root/usr/lib/x86_64-linux-gnu"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME
"$python" -B - "$root" "$kit" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, sys, time
root, kit = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / 'tools'))
import run_checks as operator
manifest = json.loads((kit / 'source-manifest.json').read_bytes())
def verify():
    assert all(hashlib.sha256((kit / 'source' / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest['files'].items())
verify()
out = root / 'evidence/linux-r412-ext4-child-check'
out.mkdir(exist_ok=False)
dbfile = root / '.private/linux-database-url.txt'
env = dict(os.environ, PYTHONPATH=str(kit/'source/src'), PYTHONIOENCODING='utf-8', PYTEST_ADDOPTS='',
           K09_TEST_DATABASE_URL=dbfile.read_text(encoding='utf-8-sig').strip())
command = [sys.executable, '-B', '-m', 'pytest', '-q', '-ra', '--tb=short', '-o', 'addopts=', '-p', 'no:cacheprovider',
           '--basetemp='+str(kit/'work'), '--junitxml='+str(out/'junit.xml'),
           'tests/integration/test_k09_execution_postgres.py::test_ack_process_loss_lease_takeover_stale_fence_and_after_commit_replay']
meta = {key: manifest[key] for key in ('source_identity','runtime_identity')}
(out/'attempt.json').write_text(json.dumps(dict(command=command, started_utc=datetime.now(timezone.utc).isoformat(),
    source_location='native ext4', unchanged_child_timeout_seconds=20, **meta),indent=2),encoding='utf-8')
started=time.monotonic()
with (out/'pytest.log').open('w',encoding='utf-8') as log:
    result=operator.run_logged(command,cwd=kit/'source',env=env,log=log,secrets=operator.sensitive_values(dbfile))
operator.sanitize_junit(out/'junit.xml',operator.sensitive_values(dbfile))
verify()
report=dict(exit_code=result.returncode,source_stable=True,elapsed_seconds=time.monotonic()-started,
            native_ext4=True,timeout_relaxed=False,primary_or_full_release_pass=False,**meta)
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
sys.exit(result.returncode)
PY
