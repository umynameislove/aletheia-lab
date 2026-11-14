#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
kit="$(mktemp -d /tmp/aletheia-r4-qualification-XXXXXXXX)"
revision="${1:-R4.1}"
case "$revision" in R4.1|R4.2|R4.3|R4.4|R4.5|R4.6|R4.7|R4.8|R4.9|R4.18) ;; *) exit 2 ;; esac
original="$root/Release-K01-K18-$revision"
lane="linux-ext4-${revision,,}"
if [[ "$revision" == R4.18 && "${2:-}" == fresh ]]; then
  bash "$root/tools/setup_linux.sh"
  bash "$root/tools/setup_pg16_linux.sh"
fi
python="${ALETHEIA_R4_TEST_PYTHON:-$(cat "$root/evidence/linux-runtime-path.txt")/venv/bin/python}"
"$python" -B - "$original" "$kit" <<'PY'
from pathlib import Path
import json, shutil, sys

original, kit = map(Path, sys.argv[1:])
manifest = original / 'source-manifest.json'
shutil.copy2(manifest, kit)
shutil.copytree(original / 'tools', kit / 'tools', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for name in json.loads(manifest.read_bytes())['files']:
    path = kit / 'source' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original / 'source' / name, path)
PY
pg_root="$(cat "$root/evidence/linux-pg16-path.txt")"
pg_bin="$pg_root/root/usr/lib/postgresql/16/bin"
export LD_LIBRARY_PATH="$pg_root/root/usr/lib/x86_64-linux-gnu"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
database_file="$root/.private/linux-database-url.txt"
evidence="$root/evidence"
"$python" -B "$kit/tools/run_checks.py" verify > "$evidence/$lane-verify.log"
run() {
  phase="$1"
  shift
  "$python" -B "$kit/tools/run_checks.py" "$phase" --output "$evidence/$lane-$phase" "$@" > "$evidence/$lane-$phase-console.log" 2>&1
}
if [[ "$revision" == R4.18 ]]; then
  if [[ "${2:-}" == k14-export ]]; then
    "$python" -B - "$kit" "$evidence/linux-r418-k14-export" "$root/.private/database-url.txt" <<'PY'
from datetime import datetime, timezone
import json, os, sys, time
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

kit, output, db_file = map(Path, sys.argv[1:])
sys.path.insert(0, str(kit / 'tools'))
import run_checks as checks

manifest = checks.verify()
database = db_file.read_text(encoding='utf-8-sig').strip()
assert urlsplit(database).hostname in ('127.0.0.1', 'localhost')
env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
env.update(K04_TEST_DATABASE_URL=database, K14_TEST_DATABASE_URL=database,
           PYTHONPATH=str(kit/'source/src'), PYTEST_ADDOPTS='', PYTHONIOENCODING='utf-8')
output.mkdir()
command = [sys.executable, '-B', '-m', 'pytest', '-q', '-ra', '--tb=short', '-o', 'addopts=',
           '-p', 'no:cacheprovider', '--basetemp='+str(kit/'test-work'), '--junitxml='+str(output/'junit.xml'),
           'tests/integration/test_k14_export_fifo_postgres.py::test_native_five_export_burst_serializes_one_real_child_and_replays_durable_receipt_while_full']
identities = {k: manifest[k] for k in ('source_identity', 'runtime_identity')}
(output/'attempt.json').write_text(json.dumps(dict(**identities, command=command, started_utc=datetime.now(timezone.utc).isoformat()), indent=2))
started = time.monotonic()
secrets = checks.sensitive_values(db_file)
with (output/'pytest.log').open('x', encoding='utf-8') as log:
    run = checks.run_logged(command, cwd=kit/'source', env=env, log=log, secrets=secrets)
checks.sanitize_junit(output/'junit.xml', secrets)
cases = list(ElementTree.parse(output/'junit.xml').iter('testcase'))
stable = checks.verify() == manifest
qualified = run.returncode == 0 and len(cases) == 1 and stable and not any(
    c.find(tag) is not None for c in cases for tag in ('error', 'failure', 'skipped'))
receipt = dict(**identities, exit_code=run.returncode, qualified=qualified, source_stable=stable,
               executed=len(cases), pytest_seconds=sum(float(c.get('time', '0')) for c in cases),
               elapsed_seconds=time.monotonic()-started, full_release_or_primary50k_pass=False)
(output/'result.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt))
sys.exit(0 if qualified else 1)
PY
    exit $?
  fi
  exit_status=0
  run pitr --pg-bin "$pg_bin" || exit_status=1
  run health || exit_status=1
  exit "$exit_status"
fi
if [[ "$revision" == R4.2 ]]; then
  exit_status=0
  run pitr --pg-bin "$pg_bin" || exit_status=1
  run calibration --database-file "$database_file" || exit_status=1
  exit "$exit_status"
fi
if [[ "$revision" == R4.3 || "$revision" == R4.4 || "$revision" == R4.5 || "$revision" == R4.6 || "$revision" == R4.7 || "$revision" == R4.8 || "$revision" == R4.9 ]]; then
  exit_status=0
  lane="$lane-ready"
  "$python" -m pip install -c "$root/test-constraints.txt" 'jsonschema[format]==4.25.1' >&2
  printf '%s\n' "$kit/source/src" > "$(dirname "$python")/../lib/python3.12/site-packages/aletheia-r4-test-source.pth"
  run unit || exit_status=1
  if [[ "${2:-all}" != unit ]]; then
    run postgres --database-file "$database_file" --pg-bin "$pg_bin" || exit_status=1
  fi
  exit "$exit_status"
fi
exit_status=0
run pitr --pg-bin "$pg_bin" || exit_status=1
run health || exit_status=1
run calibration --database-file "$database_file" || exit_status=1
run renewal --database-file "$database_file" || exit_status=1
run metric100k || exit_status=1
run large100k --database-file "$database_file" || exit_status=1
run unit || exit_status=1
run postgres --database-file "$database_file" --pg-bin "$pg_bin" || exit_status=1
exit "$exit_status"
