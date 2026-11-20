"""Prove all five private ref snapshots can be read offline in independent repositories."""
import json
import subprocess
from k0101_capture import ROOT, WORKSPACE, OUT, PRIVATE

before = json.loads((OUT/'before.json').read_bytes())
records = []
for original in before['repositories']:
    name = original['directory'].replace('/', '-')
    source = PRIVATE/name
    bundle = source/'refs.bundle'
    if name == '_repo_aletheia_ref':
        bundle = source/'refs-complete.bundle'
        repo = WORKSPACE/original['directory']
        if not bundle.exists():
            refs = [line.split()[1] for line in (source/'refs.bin').read_text().splitlines()]
            subprocess.run(['git', '-c', 'safe.directory='+repo.as_posix(), '-C', str(repo),
                'bundle', 'create', str(bundle), *refs], check=True, capture_output=True)
    restored = ROOT/'tmp/k0101-private-bundle-check-v2'/name
    restored.parent.mkdir(exist_ok=True)
    command = ['git', '-c', 'safe.directory='+restored.as_posix(), '-C', str(restored)]
    log = b''
    if not restored.exists():
        run = subprocess.run(['git', 'clone', '--bare', str(bundle), str(restored)], capture_output=True)
        log += run.stdout+run.stderr
        (OUT/(name+'-offline-bundle.log')).write_bytes(log)
        assert run.returncode == 0, log.decode(errors='replace')
    for line in (source/'refs.bin').read_text().splitlines():
        commit, ref = line.split()
        subprocess.run([*command, 'cat-file', '-e', commit+'^{commit}'], check=True, capture_output=True)
    checked = subprocess.run([*command, 'fsck', '--connectivity-only', '--no-reflogs'], capture_output=True)
    log += checked.stdout+checked.stderr
    (OUT/(name+'-offline-bundle.log')).write_bytes(log)
    assert checked.returncode == 0, log.decode(errors='replace')
    records.append(dict(repository=original['directory'], offline_clone=True, all_original_ref_commits_present=True,
        git_fsck_connectivity=True, bundle=str(bundle), shallow=subprocess.check_output([*command, 'rev-parse', '--is-shallow-repository']).decode().strip()))
(OUT/'original-bundles-offline-restore.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(repositories=len(records), offline_restore='pass')))
