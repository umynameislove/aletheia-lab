"""Verify a clean hashlocked runtime and installed release, including four real worker children.

Run with python -I; no development dependencies, external .pth or source import is used.
The --kit directory supplies source-manifest.json and its pinned public fixture.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from importlib.metadata import distributions, requires, version
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import aletheia_lab
from aletheia_lab.hosted.managed_worker import _supervisor_type
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.upload_analysis import analyze_upload_metrics
from aletheia_lab.hosted.upload_snapshot import parse_upload_snapshot
from aletheia_lab.product.demo import demo_product_view
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import parse_product_view_json


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_runtime_versions(site: Path, lock: str) -> dict[str, str]:
    def normalize(value: str) -> str:
        return re.sub(r'[-_.]+', '-', value).lower()

    entries = re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', lock, re.MULTILINE)
    expected = {normalize(name): pinned for name, pinned in entries}
    if len(entries) != len(expected) or len(expected) != 52:
        raise RuntimeError('The complete 52-pin runtime hash lock is required.')
    installed = {}
    for package in distributions(path=[str(site)]):
        name = package.metadata['Name']
        if not isinstance(name, str) or not isinstance(package.version, str):
            raise RuntimeError('Installed distribution metadata is incomplete.')
        key = normalize(name)
        if key in installed or not Path(str(package.locate_file(''))).resolve().is_relative_to(site.resolve()):
            raise RuntimeError('Duplicate or external distribution metadata.')
        installed[key] = package.version
    if set(installed) != set(expected) | {'pip', 'aletheia-lab'} or any(installed.get(name) != pinned for name, pinned in expected.items()):
        raise RuntimeError('Installed runtime versions/extras differ from the 52-pin lock.')
    return installed


def origins(site: Path) -> dict[str, str]:
    result = {}
    for name, module in sys.modules.items():
        path = getattr(module, '__file__', None)
        if isinstance(path, str) and (name == 'aletheia_lab' or name.startswith('aletheia_lab.')):
            result[name] = str(Path(path).resolve())
    assert result and all(Path(name).is_relative_to(site / 'aletheia_lab') for name in result.values())
    return result


async def exercise(kit: Path, out: Path, site: Path) -> dict[str, Any]:
    fixture = kit / 'source/fixtures/metric-dimensions-hosted-v2/snapshot.json'
    snapshot = parse_upload_snapshot(fixture.read_bytes())
    question = 'Những số liệu nào được ghi nhận từ dữ liệu này?'
    direct = analyze_upload_metrics(snapshot, question)
    assert direct.runtime.model == 'upload-snapshot-metric-observation/v2'
    assert direct.runtime.provider == 'local_evidence' and not direct.runtime.external_call
    private = out / 'private'
    private.mkdir(mode=0o700)
    worker = _supervisor_type()(python=Path(sys.executable).absolute(), source_root=site,
        private_root=private, slots=1, timeout_seconds=15)
    request = json.dumps({'operation': 'analyze', 'snapshot': snapshot.model_dump(mode='json'),
        'question': question, 'analysis_model': direct.runtime.model}, ensure_ascii=False).encode()
    try:
        view = parse_product_view_json(await worker.run_upload(request))
    finally:
        await worker.aclose()
    assert view == direct
    counts = []
    for claim in view.claims:
        matched = re.search(r': (\d+) validated observations;', claim.text)
        assert matched is not None
        counts.append(int(matched.group(1)))
    assert sum(counts) == len(snapshot.mapping_result.metric_observations) == 10
    renderer, outputs = ReportRenderer(), {}
    formats: tuple[tuple[Literal['json', 'markdown', 'pdf'], str], ...] = (
        ('json', 'json'), ('markdown', 'md'), ('pdf', 'pdf'))
    try:
        for fmt, ext in formats:
            started = time.monotonic()
            data = await renderer.render(fmt, view.model_dump_json().encode())
            assert data == render_product_report(view, fmt)
            (out / ('report.' + ext)).write_bytes(data)
            outputs[fmt] = {'bytes': len(data), 'sha256': digest(data), 'seconds': time.monotonic() - started}
    finally:
        await renderer.aclose()
    assert json.loads((out / 'report.json').read_bytes()) == view.model_dump(mode='json')
    pdf = (out / 'report.pdf').read_bytes()
    assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')
    assert b'/FontFile2' in pdf and b'/ToUnicode' in pdf
    for folder in ('inputs', 'materializations', 'payload-exchanges'):
        assert not list((private / folder).glob('*'))
    return {'runtime': view.runtime.model_dump(mode='json'), 'snapshot_observations': sum(counts),
            'claims': len(counts), 'outputs': outputs, 'pdf_structural_markers': True, 'pdf_parser_validation': 'separate development-tool verifier'}


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError('Run without -O or -OO; optimization disables verification oracles.')
    if 'SOURCE_DATE_EPOCH' in os.environ:
        raise RuntimeError('Remove build-only SOURCE_DATE_EPOCH before runtime verification.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    kit, out = args.kit.resolve(), args.output.resolve()
    assert sys.flags.isolated == 1, 'Run this check with python -I.'
    assert 'PYTHONPATH' not in os.environ, 'Remove PYTHONPATH for the installed-wheel check.'
    site = Path(aletheia_lab.__file__).resolve().parent.parent
    assert site.is_relative_to(Path(sys.prefix).resolve()), 'A private, noneditable wheel installation is required.'
    assert not list(site.glob('*.pth')), 'No .pth reuse is permitted in this clean runtime proof.'
    assert 'include-system-site-packages = false' in (Path(sys.prefix) / 'pyvenv.cfg').read_text().lower()
    assert not site.is_relative_to(kit / 'source')
    assert not any(Path(item).resolve().is_relative_to(kit / 'source') for item in sys.path if item)
    manifest = json.loads((kit / 'source-manifest.json').read_text(encoding='utf-8'))
    runtime = manifest['runtime']
    assert digest(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()) == manifest['source_identity']
    assert digest(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()) == manifest['runtime_identity']
    assert {name: value for name, value in manifest['files'].items() if name.startswith('src/aletheia_lab/')} == runtime
    assert len(runtime) == manifest['runtime_count'] and all(name.startswith('src/aletheia_lab/') for name in runtime)
    lock_name = 'deploy/hosted/requirements.lock'
    lock = (kit / 'source' / lock_name).read_bytes()
    assert digest(lock) == manifest['files'][lock_name]
    installed_versions = check_runtime_versions(site, lock.decode())
    fixture_name = 'fixtures/metric-dimensions-hosted-v2/snapshot.json'
    assert digest((kit / 'source' / fixture_name).read_bytes()) == manifest['files'][fixture_name]
    for name, expected in runtime.items():
        assert digest((site / name.removeprefix('src/')).read_bytes()) == expected, name
    assert demo_product_view() == json.loads(files('aletheia_lab.product').joinpath('synthetic_p6_view.json').read_bytes())
    assert version('reportlab') == '5.1.0'
    pins = [x for x in requires('aletheia-lab') or [] if x.split(';')[0].strip().lower() == 'reportlab==5.1.0']
    assert len(pins) == 2 and any('extra == "hosted"' in x for x in pins) and any('extra == "pdf"' in x for x in pins)
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    origin_dir = out / 'child-origins'
    origin_dir.mkdir(mode=0o700)
    hook = site / 'sitecustomize.py'
    assert not hook.exists(), 'Use a clean test venv; an existing sitecustomize.py is preserved.'
    # A temporary startup hook records real child imports; always remove it afterwards.
    audit = '''import atexit,json,os,sys
from pathlib import Path
@atexit.register
def record():
 modules={n:str(Path(m.__file__).resolve()) for n,m in sys.modules.items() if (n=='aletheia_lab' or n.startswith('aletheia_lab.')) and getattr(m,'__file__',None)}
 if modules: Path(ORIGIN,str(os.getpid())+'.json').write_text(json.dumps({'pid':os.getpid(),'executable':sys.executable,'pythonpath':os.environ.get('PYTHONPATH'),'modules':modules},indent=2)+'\\n',encoding='utf-8')
'''.replace('ORIGIN', repr(str(origin_dir)))
    cached_before = set((site / '__pycache__').glob('sitecustomize.*.pyc'))
    with hook.open('x', encoding='utf-8') as handle:
        handle.write(audit)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
            result = runner.run(exercise(kit, out, site))
    finally:
        hook.unlink()
        for cached in (site / '__pycache__').glob('sitecustomize.*.pyc'):
            if cached not in cached_before:
                cached.unlink()
    children = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(origin_dir.glob('*.json'))]
    assert len(children) == 4 and len({child['pid'] for child in children}) == 4
    for child in children:
        assert Path(child['executable']).resolve() == Path(sys.executable).resolve()
        assert child['pythonpath'] == str(site) and child['modules']
        assert all(Path(path).is_relative_to(site / 'aletheia_lab') for path in child['modules'].values())
    for name, expected in runtime.items():
        assert digest((site / name.removeprefix('src/')).read_bytes()) == expected, name
    result.update(isolated_runtime_dependency_closure_verified=True, runtime_pin_count=52,
        installed_distribution_versions=installed_versions,
        install_provenance='Fresh hashlocked installation is established separately by the installer receipt, not inferred from package presence.',
        external_pth_files=[], sys_path=list(sys.path), executable=sys.executable, status='passed', installed_prefix=sys.prefix, installed_root=str(site),
        runtime_file_count=len(runtime), all_runtime_byte_parity=True, actual_installed_analysis_child=True,
        actual_installed_report_children=3, parent_module_origins=origins(site), child_origin_records=len(children),
        resource_sha256={name: digest(files('aletheia_lab.product').joinpath(name).read_bytes()) for name in
            ('synthetic_p6_view.json', 'fonts/NotoSansMono-Regular.ttf', 'fonts/OFL.txt', 'fonts/provenance.json')},
        dependency_versions={name: version(name) for name in ('reportlab', 'pydantic', 'numpy', 'pyarrow')},
        installed_pdf_requirements=pins, no_source_pythonpath=True,
        scope='Installed wheel, actual small analysis child and three report children; no PostgreSQL, Docker, IdP, provider or physical100k claim.')
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'passed', 'runtime_files': len(runtime), 'actual_children': len(children),
                      'outputs': result['outputs'], 'runtime_dependencies_only': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
