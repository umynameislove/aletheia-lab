#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
kit="$root/Release-K01-K18-R4.18"
scratch="$(mktemp -d /tmp/aletheia-r418-calibration-XXXXXXXX)"
out="$root/evidence/linux-r418-open5"
mkdir "$out"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
python3 -m venv "$scratch/venv"
py="$scratch/venv/bin/python"
"$py" -m pip install --only-binary=:all: --require-hashes -r "$kit/source/deploy/hosted/requirements.lock"
"$py" -m pip install --only-binary=:all: -c "$root/test-constraints.txt" pytest==9.2.0 hypothesis==6.170.15 httpx==0.28.1 'jsonschema[format]==4.25.1'
"$py" -m pip check
printf '%s\n' "$scratch" > "$out/scratch-path.txt"
"$py" -B - "$root" "$kit" "$scratch" "$out" <<'PY'
from pathlib import Path
from datetime import datetime,timezone
import json,os,sys,time,shutil,hashlib
root,kit,scratch,out=map(Path,sys.argv[1:])
sys.path.insert(0,str(kit/'tools'))
import run_checks as op
manifest=op.verify()
source=scratch/'source';source.mkdir()
for n,digest in manifest['files'].items():
    p=source/n
    p.parent.mkdir(parents=True,exist_ok=True)
    data=(kit/'source'/n).read_bytes()
    assert hashlib.sha256(data).hexdigest()==digest
    p.write_bytes(data)
dbfile=root/'.private/database-url.txt'
database=dbfile.read_text(encoding='utf-8-sig').strip()
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTEST_ADDOPTS='')
for key in list(env):
    if key.startswith('K17_') or key.upper().startswith('PG') or key.endswith('_TEST_DATABASE_URL'):
        env.pop(key)
env.update(K17_TEST_DATABASE_URL=database,K17_EVIDENCE_DIR=str(out),K17_CALIBRATION_MODE='open',
           K17_CALIBRATION_ARRIVALS_PER_SECOND='5',K17_CALIBRATION_WARMUP_SECONDS='30',
           K17_CALIBRATION_DURATION_SECONDS='120',PYTHONPATH=str(source/'src'))
command=[sys.executable,'-B','-m','pytest','-q','-ra','--tb=short','-o','addopts=','-p','no:cacheprovider',
         '--basetemp='+str(scratch/'private-work'),'--junitxml='+str(out/'junit.xml'),
         'tests/integration/test_k17_capacity_postgres.py::test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports']
identity={k:manifest[k] for k in ('source_identity','runtime_identity')}
meta=dict(phase='calibration',mode='open',rate=5.0,warmup_seconds=30,measured_seconds=120,command=command,
          database='Owned Docker Desktop PG16 via WSL loopback on the same laptop; separate from WSL-native PG used in R4.11.',
          started_utc=datetime.now(timezone.utc).isoformat(),**identity)
(out/'attempt.json').write_text(json.dumps(meta,indent=2))
t=time.monotonic();secrets=op.sensitive_values(dbfile)
with (out/'pytest.log').open('x',encoding='utf-8') as log:
    proc=op.run_logged(command,cwd=source,env=env,log=log,secrets=secrets)
op.sanitize_junit(out/'junit.xml',secrets)
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
op.verify()
raw=out/'calibration100-result.json'
result=json.loads(raw.read_bytes()) if raw.is_file() else {}
local=result.get('measured_positive',{}).get('observed_subset_success_target_met',False)
qualified=proc.returncode==0 and local
receipt=dict(**identity,phase='calibration',exit_code=0 if qualified else 1,pytest_functional_exit_code=proc.returncode,
             qualified=qualified,source_stable=True,elapsed_seconds=time.monotonic()-t,
             finished_utc=datetime.now(timezone.utc).isoformat(),full_release_or_primary50k_pass=False)
(out/'result.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt));sys.exit(receipt['exit_code'])
PY
