#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
py="$(cat "$root/evidence/linux-r415-open5/scratch-path.txt")/venv/bin/python"
test -x "$py"
out="$root/evidence/linux-r418-fifo"
mkdir "$out"
scratch="$(mktemp -d /tmp/aletheia-r418-fifo-XXXXXXXX)"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
"$py" -B - "$root" "$out" "$scratch" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,os,subprocess,sys,time
from xml.etree import ElementTree

root,out,scratch=map(Path,sys.argv[1:])
kit=root/'Release-K01-K18-R4.18'
sys.path.insert(0,str(kit/'tools'))
import run_checks as checks
manifest=checks.verify()
source=scratch/'source'
for name,digest in manifest['files'].items():
    data=(kit/'source'/name).read_bytes()
    assert hashlib.sha256(data).hexdigest()==digest
    target=source/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
env=dict(os.environ,PYTHONPATH=str(source/'src'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
command=[sys.executable,'-B','-m','pytest','-q','-ra','-o','addopts=','-p','no:cacheprovider',
         '--basetemp='+str(scratch/'work'),'--junitxml='+str(out/'junit.xml'),
         'tests/unit/test_project_import_boundary.py::test_special_file_is_blocked_when_platform_supports_fifo']
identity={key:manifest[key] for key in ('source_identity','runtime_identity')}
(out/'attempt.json').write_text(json.dumps(dict(**identity,command=command,scope='Actual FIFO on owned WSL ext4, with the sealed source copied and verified.',started_utc=datetime.now(timezone.utc).isoformat()),indent=2))
started=time.monotonic()
with (out/'pytest.log').open('x') as log:
    proc=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
cases=list(ElementTree.parse(out/'junit.xml').iter('testcase'))
qualified=proc.returncode==0 and len(cases)==1 and not any(c.find(tag) is not None for c in cases for tag in ('failure','error','skipped'))
assert all(hashlib.sha256((source/name).read_bytes()).hexdigest()==digest for name,digest in manifest['files'].items())
checks.verify()
receipt=dict(**identity,qualified=qualified,exit_code=proc.returncode,executed=len(cases),source_stable=True,
             elapsed_seconds=time.monotonic()-started,finished_utc=datetime.now(timezone.utc).isoformat(),full_release_or_primary50k_pass=False)
(out/'result.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
sys.exit(0 if qualified else 1)
PY
