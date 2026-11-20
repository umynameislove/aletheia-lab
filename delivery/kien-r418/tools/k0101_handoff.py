"""Produce the focused task row and review packet; no final Bao acceptance."""
import csv
import io
import json
import subprocess
import zipfile
from collections import Counter
from k0101_closeout import ROOT, WORKSPACE, OUT, HANDOFF, INTEGRATION, git, sha, save

record = json.loads((OUT/'result.json').read_bytes())
sealed = json.loads((ROOT/'Release-K01-K18-R4.18/source-manifest.json').read_bytes())
assert all(sha((ROOT/'Release-K01-K18-R4.18/source'/n).read_bytes()) == h for n,h in sealed['files'].items())
restores = json.loads((OUT/'original-bundles-offline-restore.json').read_bytes())
assert len(restores) == 5 and all(r['git_fsck_connectivity'] for r in restores)
assert '29 pre-existing' in record['whitespace_check']
manifest = json.loads((OUT/'source-manifest.json').read_bytes())
with zipfile.ZipFile(io.BytesIO(git('archive', '--format=zip', record['refs']['main']).stdout)) as main:
    protected = {n:sha(main.read(n)) for n in main.namelist() if n.startswith('src/aletheia_lab/')
        and not n.endswith('/') and not n.startswith(('src/aletheia_lab/product/', 'src/aletheia_lab/project/'))
        and n != 'src/aletheia_lab/filesystem.py'}
assert protected and all(manifest['files'].get(n) == h for n,h in protected.items())
with (OUT/'wip-disposition.csv').open(encoding='utf-8-sig', newline='') as stream:
    counts = Counter(r['disposition'] for r in csv.DictReader(stream))
save(OUT/'scope-verification.json', dict(sealed_original_2557_files_unchanged=True,
    existing_main_runtime_outside_product_project_filesystem_unchanged=len(protected),
    protected_runtime_files=protected, wip_disposition_counts=counts,
    private_wip_contents_excluded_from_shared_packet=True, final_bao_report_exported=False))
canonical = WORKSPACE/'_audit_k01_k18_20261113/kien/Aletheia-Kien-API/01-Ca-nhan/09-Dau-viec-chi-tiet.csv'
with canonical.open(encoding='utf-8-sig', newline='') as stream:
    reader = csv.DictReader(stream)
    fields = reader.fieldnames
    rows = list(reader)
row = next(r for r in rows if r['task_id'] == 'K-01.01')
save(OUT/'original-k0101-tracking-row.json', row)
row = dict(row)
row.update(status='passed', evidence='KET_QUA_K01.01.md; evidence/result.json; evidence/wip-disposition.csv; evidence/offline-restore.json; evidence/original-bundles-offline-restore.json',
    consumer_check='Git/WIP local task verified; shared remote CI and human review remain separate, awaiting Quan.',
    observed='14/11/2026 K01.01: 5 original repos and 183 WIP paths preserved. Full main/API/UI ancestry merged at cf9738b1accef69e6f1402fb241bad17fcc3a477. Offline clone reproduces 2557/2557 files. Five original ref bundles independently cloned/fsck. Runtime676 and UI180 exact. No runtime edits or rerun of passed suites.',
    open_issues='No remaining local Git/WIP task requirement. Separate shared gate: Quan confirms team branch/reviewer, pushes merged tree and supplies remote SHA/CI. No final Bao acceptance.')
with (HANDOFF/'K01.01-tracking.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerow(row)
assert not git('status', '--porcelain').stdout
packet = ROOT/'Gui-Bu-K01.01-R418-20261114.zip'
assert not packet.exists()
inputs = {p.name:p for p in HANDOFF.iterdir() if p.is_file()}
inputs.update({'evidence/'+p.name:p for p in OUT.iterdir() if p.is_file()})
for name in ('windows-unit-r418-sealed/result.json', 'quan-r418-ui-peer-review.json', 'r418-build-install-receipt.json'):
    inputs['prior-evidence/'+name] = ROOT/'evidence'/name
with zipfile.ZipFile(packet, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name,p in sorted(inputs.items()):
        archive.write(p, name)
with zipfile.ZipFile(packet) as archive:
    assert archive.testzip() is None
    assert {n:sha(archive.read(n)) for n in archive.namelist()} == {n:sha(p.read_bytes()) for n,p in inputs.items()}
packet.with_name(packet.name+'.sha256').write_text(sha(packet.read_bytes())+'  '+packet.name+'\n', encoding='ascii')
save(OUT/'handoff-receipt.json', dict(path=str(packet), bytes=packet.stat().st_size, sha256=sha(packet.read_bytes()),
    files=len(inputs), all_archived_bytes_verified=True, final_bao_report_exported=False,
    original_canonical_tracking_not_modified=True, focused_k0101_row_status='passed'))
print(json.dumps(dict(packet=str(packet), files=len(inputs), protected_main_runtime=len(protected), wip_counts=counts)))
