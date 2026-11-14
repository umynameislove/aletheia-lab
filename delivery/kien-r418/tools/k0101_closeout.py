"""Verify and package the isolated K01.01 integration without editing release bytes."""
from datetime import datetime, timezone
import csv
import hashlib
import json
import re
from pathlib import Path
import subprocess
import zipfile

from k0101_capture import ROOT, WORKSPACE, OUT, PRIVATE, snapshot

INTEGRATION = Path.home()/'.codex/worktrees/k0101-r418-integration/_repo_aletheia_ref'
HANDOFF = ROOT/'handoff/k0101-r418-integration'
BRANCH = 'codex/k0101-r418-integration'


def git(*args, check=True):
    return subprocess.run(['git', '-c', 'safe.directory='+INTEGRATION.as_posix(),
        '-C', str(INTEGRATION), *args], check=check, capture_output=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(mapping):
    return sha(json.dumps(mapping, sort_keys=True, separators=(',', ':')).encode())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    HANDOFF.mkdir(exist_ok=True)
    before = json.loads((OUT/'before.json').read_bytes())
    sealed = json.loads((ROOT/'Release-K01-K18-R4.18/source-manifest.json').read_bytes())
    peer = ROOT/'tmp/r418-ui-peer-integration'
    head = git('rev-parse', 'HEAD').stdout.decode().strip()
    assert head == 'cf9738b1accef69e6f1402fb241bad17fcc3a477'
    assert not git('status', '--porcelain').stdout
    assert git('rev-parse', '--is-shallow-repository').stdout.strip() == b'false'
    refs = {name: git('rev-parse', 'refs/codex/k0101/'+name).stdout.decode().strip()
            for name in ('main', 'quan', 'kien-api', 'kien-old', 'quan-old')}
    for name in ('main', 'quan', 'kien-api', 'kien-old'):
        git('merge-base', '--is-ancestor', refs[name], head)
    divergences = {}
    for name in refs:
        divergences[name] = [int(n) for n in git('rev-list', '--left-right', '--count', refs['main']+'...'+refs[name]).stdout.split()]
    old_quan = git('log', '--format=%H %s', refs['quan-old'], '--not', refs['main']).stdout.decode()
    (OUT/'old-quan-unique-commits.txt').write_text(old_quan, encoding='utf-8')
    assert len(old_quan.splitlines()) == 1
    expected = {}
    for name in sealed['files']:
        data = ((peer if name.startswith('dashboard/web/') else ROOT/'Release-K01-K18-R4.18/source')/name).read_bytes()
        expected[name] = sha(data)
    expected['.gitattributes'] = sha((INTEGRATION/'.gitattributes').read_bytes())
    working = {n:sha((INTEGRATION/n).read_bytes()) for n in expected}
    assert working == expected
    source_zip = HANDOFF/'Source-K01.01-R418-cf9738b.zip'
    if not source_zip.exists():
        git('archive', '--format=zip', '--output='+str(source_zip), head)
    with zipfile.ZipFile(source_zip) as archive:
        assert archive.testzip() is None
        archived = {n:sha(archive.read(n)) for n in archive.namelist() if not n.endswith('/')}
    assert archived == expected, 'Git archive must reproduce every selected byte.'
    runtime = {n:h for n,h in expected.items() if n.startswith('src/aletheia_lab/')}
    ui = {n:h for n,h in expected.items() if n.startswith('dashboard/web/')}
    assert len(runtime) == 676 and identity(runtime) == sealed['runtime_identity']
    peer_manifest = json.loads((ROOT/'evidence/quan-r418-ui-received/Phan-hoi-R4.18-UI/01-Source-va-build/source-manifest.json').read_bytes())
    ui_identity = sha(''.join(n+'\t'+ui[n]+'\n' for n in sorted(ui)).encode())
    assert ui == peer_manifest['entries']
    assert len(ui) == 180 and ui_identity == peer_manifest['identity']
    assert len(expected) == 2557
    save(OUT/'source-manifest.json', dict(commit=head, file_count=len(expected), source_identity=identity(expected),
        runtime_count=len(runtime), runtime_identity=identity(runtime), ui_count=len(ui), ui_identity=ui_identity,
        sealed_parent=sealed['source_identity'], files=expected))
    # This ledger covers WIP only; original refs and all committed files are retained separately.
    ledger = []
    after = []
    for original in before['repositories']:
        current = snapshot(original['directory'], None)
        for key in ('head', 'status_sha256', 'tracked_patch_sha256', 'staged_patch_sha256', 'wip'):
            assert current[key] == original[key], (original['directory'], key)
        repo = WORKSPACE/original['directory']
        old_refs = (PRIVATE/original['directory'].replace('/', '-')/'refs.bin').read_bytes().splitlines()
        current_refs = subprocess.check_output(['git', '-c', 'safe.directory='+repo.as_posix(), '-C', str(repo), 'show-ref']).splitlines()
        assert set(old_refs).issubset(set(current_refs)), original['directory']
        after.append(dict(directory=original['directory'], unchanged_head_status_wip=True,
            original_refs_preserved=True, new_refs=len(set(current_refs)-set(old_refs))))
        for path, info in original['wip'].items():
            if info.get('symlink') is not None:
                disposition = 'symlink preserved in original and private snapshot; not introduced into product'
            elif info.get('deleted'):
                disposition = 'original deletion retained; tracked version recoverable from Git'
            elif expected.get(path) == info.get('sha256'):
                disposition = 'included byte-identically in integration and original WIP preserved'
            elif path in expected:
                disposition = 'superseded by verified R4.18/peer file; original WIP preserved, not silently dropped'
            else:
                disposition = 'retained outside product in original and private snapshot; no unreviewed WIP imported'
            ledger.append(dict(repository=original['directory'], path=path, original_sha256=info.get('sha256', ''),
                integration_sha256=expected.get(path, ''), disposition=disposition))
    assert len(ledger) == 183
    with (OUT/'wip-disposition.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ledger[0]))
        writer.writeheader()
        writer.writerows(ledger)
    save(OUT/'original-preservation-after.json', after)
    # Recompute the final HEAD diff; earlier inventory preceded the attribute-only commit.
    for label, ref in refs.items():
        payload = git('diff', '--name-status', ref, head).stdout
        (OUT/('final-diff-'+label+'.tsv')).write_bytes(payload)
    whitespace = git('-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
        'diff', '--check', refs['main'], head, check=False)
    (OUT/'preexisting-whitespace.log').write_bytes(whitespace.stdout+whitespace.stderr)
    assert whitespace.returncode == 2
    bundle = HANDOFF/'K01.01-R418-integration.bundle'
    if not bundle.exists():
        git('bundle', 'create', str(bundle), BRANCH, *['refs/codex/k0101/'+n for n in refs])
    verified = git('bundle', 'verify', str(bundle))
    (OUT/'integration-bundle-verify.log').write_bytes(verified.stdout+verified.stderr)
    restore = ROOT/'tmp/k0101-r418-offline-restore'
    if not restore.exists():
        cloned = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
            'clone', '--no-checkout', '--branch', BRANCH, str(bundle), str(restore)],
            check=True, capture_output=True)
        (OUT/'offline-clone.log').write_bytes(cloned.stdout+cloned.stderr)
    restored_git = ['git', '-c', 'safe.directory='+restore.as_posix(), '-C', str(restore)]
    subprocess.run([*restored_git, '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
        'checkout', BRANCH], check=True, capture_output=True)
    restored_files = subprocess.check_output([*restored_git, 'ls-files', '-z']).decode().split('\0')
    assert set(restored_files)-{''} == set(expected)
    assert {n:sha((restore/n).read_bytes()) for n in expected} == expected
    assert not subprocess.check_output([*restored_git, 'status', '--porcelain'])
    assert subprocess.check_output([*restored_git, 'rev-parse', 'HEAD']).decode().strip() == head
    # Confirm every originally referenced commit remains an object after offline recovery.
    original_commits = set()
    for original in before['repositories']:
        for line in (PRIVATE/original['directory'].replace('/', '-')/'refs.bin').read_text().splitlines():
            original_commits.add(line.split()[0])
    recovered_original_commits = []
    separate_bundles = []
    for commit in sorted(original_commits):
        found = subprocess.run([*restored_git, 'cat-file', '-e', commit+'^{commit}'], capture_output=True)
        (recovered_original_commits if found.returncode == 0 else separate_bundles).append(commit)
    save(OUT/'offline-restore.json', dict(commit=head, files=2557, all_file_hashes_exact=True,
        clean=True, network_used=False, original_commits_available=recovered_original_commits,
        original_commits_retained_only_in_private_bundles=separate_bundles))
    receipt = dict(recorded_utc=datetime.now(timezone.utc).isoformat(), scope='K01.01 local Git/history/WIP preservation only',
        local_status='pass', remote_integration_ci_status='not_run', human_backend_review_status='awaiting_Quan',
        branch=BRANCH, commit=head, refs=refs, divergence_main_vs_ref=divergences,
        source_identity=identity(expected), source_files=len(expected), runtime_identity=identity(runtime), runtime_files=len(runtime),
        offline_clone_checkout_files_exact=True, offline_clone_worktree_clean=True,
        ui_identity=ui_identity, ui_files=len(ui), git_archive_all_files_exact=True, clean_worktree=True,
        original_repositories_preserved=5, original_wip_paths_preserved=183, original_refs_preserved=True,
        merge_conflicts_resolved=5, obsolete_ui_fixtures_removed_but_retained_in_history=10,
        sealed_runtime_unchanged=True, ui_matches_verified_peer=True,
        full_test_suites_rerun=False, reason='Exact byte binding to existing sealed backend and peer UI receipts; no runtime edits.',
        whitespace_check=str(len(re.findall(rb'^.+:\d+: ', whitespace.stdout, re.MULTILINE)))+' pre-existing formatting findings retained from selected source files; not counted as a passed check',
        original_releases_and_k16_untouched=True, all_k01_k18_pass=False, final_bao_report_exported=False,
        artifacts={p.name:dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes())) for p in (source_zip,bundle)})
    save(OUT/'result.json', receipt)
    for p in (source_zip,bundle):
        (p.with_name(p.name+'.sha256')).write_text(sha(p.read_bytes())+'  '+p.name+'\n', encoding='ascii')
    print(json.dumps({k:receipt[k] for k in ('local_status','commit','source_identity','source_files','original_wip_paths_preserved')}))


if __name__ == '__main__':
    main()
