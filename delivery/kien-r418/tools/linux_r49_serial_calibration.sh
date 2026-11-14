#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
original="$root/Release-K01-K18-R4.9"
kit="$(mktemp -d /tmp/aletheia-r49-serial-calibration-XXXXXXXX)"
python="$(find /tmp -maxdepth 1 -type d -name 'aletheia-r45-test-*' | head -n1)/venv/bin/python"
test -x "$python"
"$python" -B - "$original" "$kit" <<'PYKIT'
from pathlib import Path
import json, shutil, sys
original, kit = map(Path, sys.argv[1:])
manifest = original / 'source-manifest.json'
shutil.copy2(manifest, kit)
(kit / 'tools').mkdir()
shutil.copy2(original / 'tools/run_checks.py', kit / 'tools/run_checks.py')
for name in json.loads(manifest.read_bytes())['files']:
    target = kit / 'source' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original / 'source' / name, target)
PYKIT
printf '%s\n' "$kit/source/src" > "$(dirname "$python")/../lib/python3.12/site-packages/aletheia-r4-test-source.pth"
pg_root="$(cat "$root/evidence/linux-pg16-path.txt")"
export LD_LIBRARY_PATH="$pg_root/root/usr/lib/x86_64-linux-gnu"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
status=0
for model in closed open; do
  "$python" -B - "$kit" "$root" "$model" <<'PYRUN' || status=1
from pathlib import Path
from datetime import datetime, timezone
import json, os, subprocess, sys, tempfile, time

kit, root = map(Path, sys.argv[1:3])
model = sys.argv[3]
sys.path.insert(0, str(kit / 'tools'))
import run_checks as operator
manifest = operator.verify()
output = root / 'evidence' / ('linux-r49-serial-' + model)
output.mkdir(exist_ok=False)
database_file = root / '.private/linux-database-url.txt'
database = database_file.read_text(encoding='utf-8-sig').strip()
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTEST_ADDOPTS='', PYTHONIOENCODING='utf-8')
for name in list(env):
    if name.endswith('_TEST_DATABASE_URL') or name.startswith('K17_'):
        env.pop(name)
env.update(K17_TEST_DATABASE_URL=database, K17_CALIBRATION_MODE=model, K17_EVIDENCE_DIR=str(output))
work = Path(tempfile.mkdtemp(prefix='pytest-', dir=kit))
work.rmdir()
command = [sys.executable, '-B', '-m', 'pytest', '-q', '-ra', '--tb=short', '-p', 'no:cacheprovider', '-o', 'addopts=',
           '--basetemp=' + str(work), '--junitxml=' + str(output / 'junit.xml'),
           'tests/integration/test_k17_capacity_postgres.py::test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports']
meta = {k: manifest[k] for k in ('source_identity', 'runtime_identity', 'file_count', 'runtime_count')}
(output / 'attempt.json').write_text(json.dumps(dict(phase='calibration', mode=model,
    started_utc=datetime.now(timezone.utc).isoformat(), command=command, **meta), indent=2), encoding='utf-8')
started = time.monotonic()
secrets = operator.sensitive_values(database_file)
with tempfile.TemporaryDirectory(prefix='aletheia-serial-pycache-') as cache:
    env['PYTHONPYCACHEPREFIX'] = cache
    with (output / 'pytest.log').open('w', encoding='utf-8') as log:
        result = operator.run_logged(command, cwd=kit / 'source', env=env, log=log, secrets=secrets)
    operator.sanitize_junit(output / 'junit.xml', secrets)
operator.verify()
observed = output / 'calibration100-result.json'
profile = output / 'calibration100-profile.json'
qualified = False
if observed.is_file():
    actual = json.loads(observed.read_bytes())
    assert json.loads(profile.read_bytes())['mode'] == model
    qualified = result.returncode == 0 and actual['measured_positive']['observed_subset_success_target_met']
report = dict(phase='calibration', mode=model, pytest_functional_exit_code=result.returncode,
    exit_code=0 if qualified else 1, qualified=qualified, full_release_or_primary50k_pass=False,
    source_stable=True, elapsed_seconds=time.monotonic()-started,
    finished_utc=datetime.now(timezone.utc).isoformat(), **meta)
(output / 'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
sys.exit(report['exit_code'])
PYRUN
done
exit "$status"
