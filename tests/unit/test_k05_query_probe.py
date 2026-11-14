"""Query probe validates domain templates and never claims a cold-cache run."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from test_k05_legacy_import import legacy_journey

from aletheia_lab.hosted.legacy_import import LegacySource, model_metadata, validate_project_models
from aletheia_lab.project.snapshots import ProjectSnapshot

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('k05_query_probe', ROOT / 'scripts/k05_query_probe.py')
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


def test_query_profile_keeps_real_canonical_objects_and_transitive_parents(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    with LegacySource(root) as source:
        project = source.export_project(project_id)
    projected = probe.query_profile(project, snapshot_limit=1)
    validate_project_models(projected.models, project_id)
    assert len([model for model in projected.models if isinstance(model, ProjectSnapshot)]) >= 1
    assert all(model_metadata(model)[0] in ('snapshot', 'project_bundle', 'result') for model in projected.models)
    for model in projected.models:
        assert model_metadata(model)[1] in {model_metadata(item)[1] for item in project.models}


def test_query_probe_refuses_network_database_before_reading_source(tmp_path, monkeypatch):
    monkeypatch.setenv('K05_QUERY_BAD_URL', 'postgresql://127.0.0.2/test')
    args = probe.arguments(['--template-copy', str(tmp_path), '--project-id', 'p3-project-' + '0' * 64,
        '--synthetic-template', '--output', str(tmp_path / 'report.json'), '--database-url-env', 'K05_QUERY_BAD_URL'])
    with pytest.raises(ValueError, match='loopback'):
        asyncio.run(probe.execute(args))
    assert not list(tmp_path.iterdir())


def test_query_probe_refuses_unbounded_counts(tmp_path):
    with pytest.raises(SystemExit):
        probe.arguments(['--template-copy', str(tmp_path), '--project-id', 'unused', '--synthetic-template',
            '--output', str(tmp_path / 'out.json'), '--maximum-records', '3000001'])


def test_query_probe_preserves_atomic_progress_and_checks_measured_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace

    args = probe.arguments(['--template-copy', str(tmp_path), '--project-id', 'unused', '--synthetic-template',
        '--output', str(tmp_path / 'progress.json'), '--maximum-database-bytes', '1048576'])
    report = {'status': 'running', 'copies_committed': 100}
    probe.checkpoint(args, report)
    assert json.loads(args.output.read_text(encoding='utf-8')) == report
    assert not args.output.with_suffix('.json.tmp').exists()

    class Connection:
        async def execute(self, query, parameters):
            assert parameters == ('owned_probe',)
            return self

        async def fetchone(self):
            return (1048577,)

    monkeypatch.setattr(probe.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1024 ** 3))
    with pytest.raises(ValueError, match='budget exhausted'):
        asyncio.run(probe.check_budget(Connection(), args, report, schema='owned_probe'))
    assert report['catalog_bytes'] == 1048577
    assert report['output_filesystem_free_bytes'] == 1024 ** 3


def test_low_disk_probe_never_connects_and_removes_owned_objects(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root, project_id, _, _ = legacy_journey(tmp_path)
    args = probe.arguments(['--template-copy', str(root), '--project-id', project_id, '--synthetic-template',
        '--output', str(tmp_path / 'low-disk.json')])
    monkeypatch.setenv(args.database_url_env, 'postgresql://127.0.0.1/unused')
    monkeypatch.setattr(probe.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match='reserve is unavailable'):
        asyncio.run(probe.execute(args))
    report = json.loads(args.output.read_text(encoding='utf-8'))
    assert report['status'] == 'failed' and report['owned_objects_removed']
    assert not Path(report['physical_object_root']).exists()
    assert 'owned_schema_removed' not in report


def test_plan_summary_retains_loops_removed_rows_and_child_index():
    result = probe._nodes({'Node Type': 'Limit', 'Actual Rows': 50, 'Actual Loops': 1,
        'Plans': [{'Node Type': 'Index Scan', 'Index Name': 'k05_records_page', 'Actual Rows': 50,
                   'Actual Loops': 1, 'Rows Removed by Filter': 2}]})
    assert result['children'][0]['Index Name'] == 'k05_records_page'
    assert result['children'][0]['Rows Removed by Filter'] == 2


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'), reason='Explicit K-05 PostgreSQL test URL required')
def test_real_query_probe_counts_plans_shared_objects_and_own_cleanup(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    report_path = tmp_path / 'plans.json'
    args = probe.arguments(['--template-copy', str(root), '--project-id', project_id, '--synthetic-template',
        '--output', str(report_path), '--copies', '10', '--timeout-seconds', '60'])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        report = runner.run(probe.execute(args))
    assert report['counts']['snapshot'] == 22
    assert report['counts']['result'] == 22
    assert not report['target_imported']
    assert report['owned_schema_removed']
    assert report['owned_objects_removed'] and not Path(report['physical_object_root']).exists()
    assert report['copies_committed'] == 10
    assert report_path.is_file()
    assert len(report['plans']['large']) == len(report['plans']['small']) == 8
    expected_queries = ('project_page', 'snapshot_page', 'result_page', 'logical_result_page',
        'logical_workspace_result_page', 'snapshot_result_page', 'record_and_object', 'parents')
    for stratum in ('large', 'small'):
        assert tuple(item['query'] for item in report['plans'][stratum]) == expected_queries
    assert all('not_run' in item['cold_run'] for item in report['plans']['large'])
    assert all(item['data_statements_per_execution'] == 1 for item in report['plans']['large'])


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'), reason='Explicit K-05 PostgreSQL test URL required')
def test_actual_authorized_catalog_reads_share_probe_cardinality_and_cleanup(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    args = probe.arguments(['--template-copy', str(root), '--project-id', project_id, '--synthetic-template',
        '--output', str(tmp_path / 'authorized-plans.json'), '--copies', '2', '--timeout-seconds', '60',
        '--authorized-read-probe'])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        report = runner.run(probe.execute(args))
    actual = report['authorized_reads']
    assert actual['status'] == 'passed' and actual['catalog_counts_observed_after_reads'] == report['counts']
    assert actual['selected_projection_rows'] == 1 and actual['canonical_object_reads_after_backfill'] == 0
    assert actual['outside_workspace_denied']
    assert {'projects', 'results', 'header', 'claim_pages', 'evidence_pages', 'graph_pages'} <= actual['reads'].keys()
    assert all(row['model_validated'] and row['serialized_bytes'] <= 1048576
               for values in actual['reads'].values() for row in values)
    assert report['owned_schema_removed'] and report['owned_objects_removed']
    assert len(report['plans']['large']) == len(report['plans']['small']) == 8


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'), reason='Explicit K-05 PostgreSQL test URL required')
def test_failed_query_probe_preserves_failure_checkpoint_and_cleanup_evidence(tmp_path, monkeypatch):
    root, project_id, _, _ = legacy_journey(tmp_path)
    report_path = tmp_path / 'failed-plans.json'
    args = probe.arguments(['--template-copy', str(root), '--project-id', project_id, '--synthetic-template',
        '--output', str(report_path), '--copies', '10', '--timeout-seconds', '60'])
    original = probe.seed

    async def interrupted(*values, **options):
        await original(*values, **options)
        raise TimeoutError('Injected failure before batch commit')

    monkeypatch.setattr(probe, 'seed', interrupted)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner, pytest.raises(TimeoutError):
        runner.run(probe.execute(args))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['status'] == 'failed' and report['failure_category'] == 'TimeoutError'
    assert report['owned_schema_removed'] and not report['target_imported']
    assert report['owned_objects_removed'] and not Path(report['physical_object_root']).exists()
    assert report['failure_checkpoint']['projects_committed'] == 0
    assert report['failure_checkpoint']['one_tenant_counts_committed'] == {}
    assert report['elapsed_seconds'] > 0


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'), reason='Explicit K-05 PostgreSQL test URL required')
def test_query_probe_budget_stop_retains_only_committed_progress(tmp_path, monkeypatch):
    root, project_id, _, _ = legacy_journey(tmp_path)
    args = probe.arguments(['--template-copy', str(root), '--project-id', project_id, '--synthetic-template',
        '--output', str(tmp_path / 'budget-stop.json'), '--copies', '101', '--timeout-seconds', '60'])
    original = probe.check_budget

    async def stop_after_commit(conn, args, report, *, schema):
        await original(conn, args, report, schema=schema)
        if report.get('copies_committed'):
            raise ValueError('Injected storage budget stop after commit')

    monkeypatch.setattr(probe, 'check_budget', stop_after_commit)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner, pytest.raises(ValueError):
        runner.run(probe.execute(args))
    report = json.loads(args.output.read_text(encoding='utf-8'))
    assert report['status'] == 'failed' and not report['target_imported']
    assert report['copies_committed'] == report['failure_checkpoint']['projects_committed'] == 100
    assert report['owned_schema_removed'] and report['owned_objects_removed']
    assert not Path(report['physical_object_root']).exists()
