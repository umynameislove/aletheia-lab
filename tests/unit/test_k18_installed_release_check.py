"""The installed-release verifier refuses disabled or misleading evidence."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / 'scripts/k18_installed_release_check.py'
_spec = importlib.util.spec_from_file_location('k18_installed_check', _SCRIPT)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


@pytest.mark.parametrize('flag', ['-O', '-OO'])
def test_optimized_interpreter_fails_before_output_or_missing_kit_read(tmp_path, flag):
    source = str(_SCRIPT.parents[1] / 'src')
    code = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv[1],run_name='__main__')"
    output = tmp_path / 'must-not-exist'
    result = subprocess.run([sys.executable, '-I', flag, '-c', code, source, str(_SCRIPT),
        '--kit', str(tmp_path / 'absent-kit'), '--output', str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'optimization disables verification oracles' in result.stderr
    assert not output.exists()


def test_build_epoch_fails_before_output_or_missing_kit_read(tmp_path):
    source = str(_SCRIPT.parents[1] / 'src')
    code = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv[1],run_name='__main__')"
    output = tmp_path / 'must-not-exist'
    result = subprocess.run([sys.executable, '-I', '-c', code, source, str(_SCRIPT),
        '--kit', str(tmp_path / 'absent-kit'), '--output', str(output)], capture_output=True, text=True,
        timeout=30, env=os.environ | {'SOURCE_DATE_EPOCH': '1794540675'})
    assert result.returncode != 0 and 'build-only SOURCE_DATE_EPOCH' in result.stderr
    assert not output.exists()


def _metadata(site: Path, name: str, version: str) -> None:
    folder = site / (name + '.dist-info')
    folder.mkdir(exist_ok=True)
    (folder / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')


def test_actual_installed_metadata_must_match_complete_runtime_closure(tmp_path):
    lock = ''.join(f'package-{number}==1.0\n' for number in range(52))
    for number in range(52):
        _metadata(tmp_path, f'package-{number}', '1.0')
    _metadata(tmp_path, 'pip', '24.2')
    _metadata(tmp_path, 'aletheia-lab', '0.1.0')
    assert len(check.check_runtime_versions(tmp_path, lock)) == 54
    _metadata(tmp_path, 'package-1', '2.0')
    with pytest.raises(RuntimeError, match='versions/extras'):
        check.check_runtime_versions(tmp_path, lock)
    _metadata(tmp_path, 'package-1', '1.0')
    _metadata(tmp_path, 'pypdf', '6.21.4')
    with pytest.raises(RuntimeError, match='versions/extras'):
        check.check_runtime_versions(tmp_path, lock)
    with pytest.raises(RuntimeError, match='52-pin'):
        check.check_runtime_versions(tmp_path, 'package-0==1.0\n')
