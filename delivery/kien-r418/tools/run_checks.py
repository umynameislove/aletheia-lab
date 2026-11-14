"""Portable opt-in test entrypoint. Only explicit guarded recovery controls its owned Docker fixture; no IdP/provider action."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from pathlib import Path
from xml.etree import ElementTree

KIT = Path(__file__).resolve().parents[1]
SOURCE = KIT / 'source'


def verify() -> dict:
    manifest = json.loads((KIT / 'source-manifest.json').read_text(encoding='utf-8'))
    if SOURCE.is_symlink():
        raise ValueError('A regular source directory is required.')
    expected = set(manifest['files'])
    for map_name, count_name, identity_name in (('files', 'file_count', 'source_identity'), ('runtime', 'runtime_count', 'runtime_identity')):
        mapping = manifest[map_name]
        identity = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if manifest[count_name] != len(mapping) or manifest[identity_name] != identity:
            raise ValueError('Manifest count or aggregate identity is inconsistent.')
    if any(manifest['files'].get(name) != value for name, value in manifest['runtime'].items()):
        raise ValueError('Runtime inventory must be a matching source subset.')
    actual = set()
    ignored = {'.pytest_cache', '.mypy_cache', '.ruff_cache', '.hypothesis'}
    for path in SOURCE.rglob('*'):
        if path.is_symlink():
            raise ValueError('Source symlinks are not allowed.')
        relative = path.relative_to(SOURCE)
        if '__pycache__' in relative.parts or path.suffix == '.pyc':
            raise ValueError('Pre-existing source bytecode caches are not allowed.')
        if any(part in ignored for part in relative.parts):
            continue
        if path.is_file():
            name = relative.as_posix()
            if name not in expected:
                raise ValueError('Unexpected unsealed source file.')
            actual.add(name)
    if actual != expected:
        raise ValueError('Incomplete sealed source.')
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid source manifest path.')
        path = SOURCE / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Maintained source does not match its sealed manifest.')
    return manifest


def cleanup_generated_caches() -> dict:
    """Remove only regular bytecode created after the initial cache-free verify.

    The caller owns this single phase. This is not protection against another
    process maliciously changing the source tree during validation or cleanup.
    """
    if SOURCE.is_symlink() or not SOURCE.is_dir():
        raise ValueError('A regular source directory is required for cache cleanup.')
    directories = []
    bytecode = []
    for path in SOURCE.rglob('*'):
        relative = path.relative_to(SOURCE)
        if '__pycache__' not in relative.parts:
            continue
        mode = path.lstat().st_mode
        cache_index = relative.parts.index('__pycache__')
        if stat.S_ISDIR(mode) and cache_index == len(relative.parts) - 1:
            directories.append(path)
        elif (stat.S_ISREG(mode) and path.suffix == '.pyc'
              and cache_index == len(relative.parts) - 2):
            bytecode.append(path)
        else:
            raise ValueError('Unexpected source cache content; nothing was removed.')
    # Validate the entire cache inventory before the first deletion. Unknown
    # files, nested directories and symlinks remain available for inspection.
    for path in bytecode:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Source cache changed during cleanup.')
        path.unlink()
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.rmdir()
    return {'initial_source_caches_verified_absent': True,
            'removed_regular_pyc_files': len(bytecode),
            'removed_empty_cache_directories': len(directories),
            'scope': 'Only caches created in this owned phase; no hostile-host guarantee.'}


def executed_cases(path: Path) -> int:
    cases = list(ElementTree.parse(path).getroot().iter('testcase'))
    if any(case.find('failure') is not None or case.find('error') is not None for case in cases):
        raise ValueError('JUnit contains a failed executed case.')
    return sum(case.find('skipped') is None for case in cases)


def prepare_work_parent() -> Path:
    work_parent = KIT / 'work'
    def check() -> None:
        if (work_parent.is_symlink() or work_parent.is_junction()
                or work_parent.resolve().is_relative_to(SOURCE.resolve())):
            raise ValueError('Work parent must be a regular directory outside sealed source.')
    check()
    work_parent.mkdir(parents=True, exist_ok=True)
    check()
    return work_parent


def load_recovery_config(path: Path) -> tuple[dict[str, str], Path]:
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if set(value) != {'container_id', 'container_name', 'image_id', 'port', 'database_file'}:
        raise ValueError('Recovery config keys must match the explicit fixture contract.')
    if (not re.fullmatch('[0-9a-f]{64}', value['container_id'])
            or not re.fullmatch('sha256:[0-9a-f]{64}', value['image_id'])
            or not re.fullmatch('k06_recovery_[0-9a-f]{32}', value['container_name'])
            or type(value['port']) is not int or not 1024 < value['port'] < 65536
            or value['port'] in (15432, 55437)):
        raise ValueError('Recovery requires exact fresh fixture identities and a separate port.')
    database_file = Path(value['database_file'])
    if not database_file.is_absolute():
        raise ValueError('Recovery private database file must be absolute.')
    database = database_file.read_text(encoding='utf-8-sig').strip()
    parsed = urlsplit(database)
    if '?' in database or '#' in database:
        raise ValueError('Recovery database URL cannot contain a query or fragment.')
    if not (parsed.scheme == 'postgresql' and parsed.hostname == '127.0.0.1'
            and parsed.username == 'k06' and parsed.path == '/k06_recovery' and parsed.port == value['port']):
        raise ValueError('Recovery database must be the separate owned loopback fixture.')
    result = {f'K06_RECOVERY_{name.upper()}': str(value[name.lower()])
              for name in ('CONTAINER_ID', 'CONTAINER_NAME', 'IMAGE_ID', 'PORT')}
    result.update(K06_RECOVERY_ALLOW='owned-container-only', K06_RECOVERY_DATABASE_URL=database)
    return result, database_file


def sensitive_values(database_file: Path | None) -> tuple[str, ...]:
    if database_file is None:
        return ()
    database = database_file.read_text(encoding='utf-8-sig').strip()
    password = urlsplit(database).password
    return tuple(value for value in (database, password, unquote(password or '')) if value)


def sanitize(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)postgres(?:ql)?://[^\s<>\"\']+', '[DATABASE_URL_REDACTED]', text)


def run_logged(command, *, cwd, env, log, secrets):
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace') as process:
        assert process.stdout is not None
        for line in process.stdout:
            log.write(sanitize(line, secrets))
            log.flush()
        process.wait()
        return subprocess.CompletedProcess(command, process.returncode)


def sanitize_junit(path: Path, secrets: tuple[str, ...]) -> None:
    if path.is_file():
        import html
        values = tuple({*secrets, *(html.escape(value) for value in secrets)})
        raw = path.read_text(encoding='utf-8')
        cleaned = sanitize(raw, values)
        if cleaned != raw:
            path.write_text(cleaned, encoding='utf-8')


def strict_gate(output: Path, phase: str, pytest_exit: int) -> dict:
    result = {'passed': False, 'pytest_exit': pytest_exit, 'skips_allowed': False,
              'automatic_retry': False, 'diagnostic_scope': 'Observation does not resolve a commit-unknown outcome.'}
    path = output / 'junit.xml'
    if not path.is_file():
        return dict(result, reason='junit_missing')
    cases = list(ElementTree.parse(path).getroot().iter('testcase'))
    result.update(executed=len(cases), skipped=sum(c.find('skipped') is not None for c in cases))
    if pytest_exit or not cases or result['skipped'] or any(
            c.find('failure') is not None or c.find('error') is not None for c in cases):
        if phase == 'upload200':
            result['failure_diagnostic_present'] = (output / 'transfer-200MiB_failure-result.json').is_file()
        return dict(result, reason='executed_cases_not_all_passed')
    if phase == 'recovery':
        if len(cases) != 2:
            return dict(result, reason='two_recovery_cases_not_executed')
        for name in ('graceful', 'crash'):
            witness = output / 'recovery-evidence' / (name + '-witness.json')
            if not witness.is_file():
                return dict(result, reason='recovery_witness_missing')
            value = json.loads(witness.read_text(encoding='utf-8'))
            if value.get('same_original_receipt') is not True or value.get('durable_settings_on') is not True:
                return dict(result, reason='recovery_witness_invalid')
    if phase == 'upload200':
        receipt = output / 'transfer-200MiB-result.json'
        result['failure_diagnostic_present'] = (output / 'transfer-200MiB_failure-result.json').is_file()
        if not receipt.is_file():
            return dict(result, reason='completed_transfer_diagnostic_missing')
        value = json.loads(receipt.read_text(encoding='utf-8'))
        expected_progress = [i * 4 * 1024**2 for i in range(1, 51)]
        if not (value.get('schema_version') == 'k07-transfer-observation/v2'
                and value.get('observation_kind') == 'completed' and value.get('mib') == 200
                and value.get('byte_size') == 200 * 1024**2 and value.get('part_count') == 50
                and value.get('ready') is True and value.get('confirmed_progress') == expected_progress
                and isinstance(value.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', value['sha256'])
                and value.get('stored_sha256') == value['sha256']
                and value.get('recovered_or_retried_unknown') is False):
            return dict(result, reason='completed_transfer_diagnostic_invalid')
        result['transfer_sha256'] = value['sha256']
        if result['failure_diagnostic_present']:
            return dict(result, reason='failure_diagnostic_is_present')
    return dict(result, passed=True, reason='all_selected_cases_and_required_receipt_passed')


def selections(phase: str) -> list[str]:
    if phase == 'recovery':
        return ['tests/integration/test_k06_recovery_postgres.py']
    if phase == 'smoke':
        return ['tests/unit/test_j01_windows_worker.py', 'tests/unit/test_k02_windows_worker_cleanup.py',
                'tests/integration/test_k05_migrations_postgres.py', 'tests/integration/test_k02_split_postgres.py']
    if phase == 'upload200':
        return ['tests/unit/test_k07_upload_deadline.py', 'tests/integration/test_k07_upload_completion_postgres.py',
                'tests/integration/test_k07_upload_sizes_postgres.py::test_real_physical_resumable_transfer_to_verified_source[L-200MiB]']
    if phase == 'unit':
        patterns = ['test_j01_*.py', 'test_security_ci_contract.py'] + [f'test_k{i:02}_*.py' for i in range(2, 19)]
        return ['-m', 'not integration', *sorted({f.relative_to(SOURCE).as_posix()
            for pattern in patterns for f in (SOURCE / 'tests/unit').glob(pattern)})]
    if phase == 'postgres':
        paths = sorted(f.relative_to(SOURCE).as_posix() for i in range(2, 19)
                       for f in (SOURCE / 'tests/integration').glob(f'test_k{i:02}_*.py'))
        for pattern in ['test_j01_*.py', 'test_security_ci_contract.py'] + [f'test_k{i:02}_*.py' for i in range(2, 19)]:
            for path in (SOURCE / 'tests/unit').glob(pattern):
                for node in ast.parse(path.read_text(encoding='utf-8')).body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                        isinstance(child, ast.Attribute) and child.attr == 'integration'
                        for decorator in node.decorator_list for child in ast.walk(decorator)):
                        paths.append(path.relative_to(SOURCE).as_posix() + '::' + node.name)
        return sorted(set(paths))
    if phase == 'large100k':
        return ['tests/integration/test_k08_large_native_pipeline.py', '-k', '100000']
    if phase == 'metric100k':
        return ['tests/integration/test_k07_metric_rows_worker.py', '-k', '100000']
    if phase == 'pitr':
        return ['tests/integration/test_k18_physical_pitr.py']
    if phase == 'health':
        return ['tests/integration/test_k18_container_health_tls.py']
    if phase in ('calibration', 'renewal'):
        case = ('test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports'
                if phase == 'calibration' else 'test_actual_native_rotation_retains_actor_context_and_old_cookie_is_denied')
        return ['tests/integration/test_k17_capacity_postgres.py::' + case]
    raise ValueError('Unknown phase.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('verify', 'recovery', 'smoke', 'upload200', 'unit', 'postgres', 'large100k', 'metric100k', 'pitr', 'health', 'calibration', 'renewal'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--database-file', type=Path)
    parser.add_argument('--pg-bin', type=Path)
    parser.add_argument('--recovery-config', type=Path, help='Explicit public fixture identities and private database_file; this phase intentionally restart/KILLs only its guarded disposable container.')
    args = parser.parse_args()
    output = None
    created = False
    try:
        if sys.flags.optimize:
            raise ValueError('Optimized Python is not allowed for qualification.')
        manifest = verify()
        if args.phase == 'verify':
            print(json.dumps({k: manifest[k] for k in ('file_count', 'runtime_count', 'source_identity', 'runtime_identity')}))
            return 0
        if sys.version_info[:2] != (3, 12):
            raise ValueError('Use the qualified Python 3.12 version.')
        if args.output is None:
            raise ValueError('A new output directory is required.')
        if args.phase in ('smoke', 'upload200', 'postgres', 'large100k', 'calibration', 'renewal') and args.database_file is None:
            raise ValueError('Explicit dedicated test database file required.')
        if args.phase == 'recovery' and args.recovery_config is None:
            raise ValueError('Recovery requires its explicit fresh disposable fixture config.')
        if args.phase != 'recovery' and args.recovery_config is not None:
            raise ValueError('Recovery config is only accepted by the explicit recovery phase.')
        if args.phase == 'smoke' and sys.platform != 'win32':
            raise ValueError('The strict smoke phase requires native Windows Python; skips are not passes.')
        if args.phase in ('renewal', 'calibration') and os.name != 'posix':
            raise ValueError('The transport fixture requires Linux/WSL or another POSIX runner.')
        if args.phase == 'postgres' and args.pg_bin is None:
            raise ValueError('Full PostgreSQL phase requires explicit PG16 client binaries.')
        if args.phase == 'pitr' and (args.pg_bin is None or os.name != 'posix'):
            raise ValueError('PITR fixture needs POSIX and explicit PG16 binaries.')
        output = args.output.resolve()
        if output.is_relative_to(SOURCE.resolve()):
            raise ValueError('Output must be outside the sealed source.')
        parent = output.parent
        while not parent.exists():
            if parent.parent == parent:
                raise ValueError('The output volume does not exist.')
            parent = parent.parent
        if any(shutil.disk_usage(folder).free < 4 * 1024**3
               for folder in (KIT, parent, Path(tempfile.gettempdir()))):
            raise ValueError('Keep at least 4 GiB free on package, output and private TEMP drives.')
        if args.pg_bin is not None:
            suffix = '.exe' if os.name == 'nt' else ''
            for name in ('pg_dump', 'pg_restore'):
                tool = args.pg_bin / (name + suffix)
                version = subprocess.run([str(tool), '--version'], capture_output=True, text=True, timeout=10, check=True,
                                         env={name: value for name, value in os.environ.items() if not name.upper().startswith('PG')})
                if not version.stdout.startswith(name + ' (PostgreSQL) 16.'):
                    raise ValueError('PG16 client major version required.')
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        created = True
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTEST_ADDOPTS='', PYTHONIOENCODING='utf-8')
        for name in list(env):
            if (name.upper().startswith('PG') or re.match(r'^K(?:0[2-9]|1[0-8])_', name) or name.endswith('_TEST_DATABASE_URL') or name.startswith('K06_RECOVERY_')
                    or name in ('K07_TEST_L_ROWS', 'K08_TEST_L_ROWS', 'K18_TEST_PITR',
                                'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'HF_TOKEN')):
                env.pop(name, None)
        private_database_file = args.database_file
        if args.phase == 'recovery':
            recovery_env, private_database_file = load_recovery_config(args.recovery_config)
            env.update(recovery_env)
            recovery_output = output / 'recovery-evidence'
            recovery_output.mkdir()
            env['K06_RECOVERY_EVIDENCE_DIR'] = str(recovery_output)
        if args.database_file is not None:
            database = args.database_file.read_text(encoding='utf-8-sig').strip()
            if not database:
                raise ValueError('Empty test database file.')
            for i in range(2, 19):
                env[f'K{i:02}_TEST_DATABASE_URL'] = database
            env['K02_TEST_HTTPS'] = '1'
        if args.pg_bin is not None:
            env['K18_TEST_PG_BIN'] = str(args.pg_bin.resolve())
        if args.phase in ('upload200', 'postgres'):
            env['K07_EVIDENCE_DIR'] = str(output)
        if args.phase == 'large100k':
            env.update(K08_TEST_L_ROWS='1', K08_EVIDENCE_DIR=str(output))
        if args.phase == 'metric100k':
            env['K07_TEST_L_ROWS'] = '1'
        if args.phase == 'pitr':
            env.update(K18_TEST_PITR='1', K18_EVIDENCE_DIR=str(output))
        if args.phase == 'health':
            env['K18_TEST_HTTPS'] = '1'
        if args.phase in ('calibration', 'renewal'):
            env['K17_EVIDENCE_DIR'] = str(output)
        work_parent = prepare_work_parent()
        # pytest requires the parent to exist on native Windows; every phase owns a new child.
        work = Path(tempfile.mkdtemp(prefix=args.phase + '-', dir=work_parent))
        work.rmdir()
        command = [sys.executable, '-B', '-m', 'pytest', '-o', 'addopts=', '-q', '-ra',
                   '-p', 'no:cacheprovider', '--basetemp=' + str(work), '--junitxml=' + str(output / 'junit.xml'), *selections(args.phase)]
        (output / 'attempt.json').write_text(json.dumps({'phase': args.phase, 'command': command,
            **{k: manifest[k] for k in ('source_identity', 'runtime_identity', 'file_count', 'runtime_count')},
            'started_utc': datetime.now(timezone.utc).isoformat(), 'work_directory': str(work), 'scope': 'Explicit recovery phase controls only its guarded dedicated Docker fixture; other phases have no automatic Docker lifecycle/IdP/provider action.'}, indent=2) + '\n')
        run_started = time.monotonic()
        secrets = sensitive_values(private_database_file)
        with tempfile.TemporaryDirectory(prefix='aletheia-parent-pycache-') as parent_cache:
            # -B prevents writes, not reads. A fresh private prefix also stops
            # the pytest parent from reading inherited external bytecode.
            env['PYTHONPYCACHEPREFIX'] = parent_cache
            try:
                with (output / 'pytest.log').open('w', encoding='utf-8') as log:
                    result = run_logged(command, cwd=SOURCE, env=env, log=log, secrets=secrets)
                sanitize_junit(output / 'junit.xml', secrets)
            finally:
                cleanup = {'status': 'rejected', 'strict_source_verification_after': False,
                           'isolated_parent_cache_prefix': True}
                try:
                    cleanup.update(cleanup_generated_caches())
                    verify()
                    cleanup.update(status='passed', strict_source_verification_after=True)
                finally:
                    (output / 'cache-cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
        code = result.returncode
        gate_observation = {}
        if args.phase in ('smoke', 'upload200', 'recovery'):
            gate_observation = strict_gate(output, args.phase, result.returncode)
            if not gate_observation['passed']:
                code = result.returncode or 1
        if code == 0:
            actual = executed_cases(output / 'junit.xml')
            required = {'recovery': 2, 'metric100k': 2, 'large100k': 1, 'health': 1, 'pitr': 1, 'calibration': 1, 'renewal': 1}.get(args.phase)
            if actual == 0 or required is not None and actual != required:
                code = 1
            receipt = {'renewal': 'renewal-result.json', 'calibration': 'calibration100-result.json',
                       'pitr': 'physical-pitr.json', 'large100k': 'large-100000-receipt.json'}.get(args.phase)
            if receipt is not None and not (output / receipt).is_file():
                code = 1
        if args.phase == 'calibration' and code == 0:
            measured = json.loads((output / 'calibration100-result.json').read_text(encoding='utf-8'))
            if not measured['measured_positive']['observed_subset_success_target_met'] or any(
                measured['counts'].get(k, 0) for k in ('rejected', 'timeout', 'dropped', 'pending', 'unknown_mutations')):
                code = 1
        (output / 'result.json').write_text(json.dumps({'phase': args.phase, 'exit_code': code,
            'pytest_functional_exit_code': result.returncode,
            **{k: manifest[k] for k in ('source_identity', 'runtime_identity', 'file_count', 'runtime_count')},
            'platform': sys.platform, 'finished_utc': datetime.now(timezone.utc).isoformat(),
            'elapsed_seconds': time.monotonic() - run_started, 'strict_gate': gate_observation,
            'qualified': code == 0, 'automatic_retry': False, 'work_retained': True,
            'source_stable': True, 'full_release_or_primary50k_pass': False}, indent=2) + '\n')
        print(json.dumps({'phase': args.phase, 'exit_code': code, 'output': str(output)}))
        return code
    except KeyboardInterrupt:
        return 130
    except Exception:
        if created and output is not None:
            (output / 'failed.json').write_text('{"status":"wrapper_failed","full_release_pass":false,"automatic_retry":false}\n')
        print('Test preconditions or execution failed; preserve outputs and do not expose database credentials.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
