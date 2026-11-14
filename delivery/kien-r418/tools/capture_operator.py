"""Opt-in read-only, bounded Docker/PG diagnostics. Never requests Config.Env or query text."""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading

MAXIMUM = 512 * 1024


def bounded(command: list[str], *, timeout: float = 20) -> dict:
    """Keep raw CLI bytes in bounded memory only, never in an output file."""
    chunks: list[bytes] = []
    truncated = threading.Event()
    count = 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={name: value for name, value in os.environ.items() if not name.upper().startswith('PG')})
    except OSError:
        return {'status': 'tool_unavailable'}
    assert process.stdout is not None

    def read() -> None:
        nonlocal count
        while block := process.stdout.read(16 * 1024):
            remaining = MAXIMUM - count
            chunks.append(block[:remaining])
            count += min(len(block), remaining)
            if len(block) > remaining or count >= MAXIMUM:
                truncated.set()
                process.kill()  # Only this read-only CLI process, never a container.
                break

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    reader.join(timeout=2)
    if reader.is_alive():
        process.stdout.close()
        return {'status': 'capture_incomplete'}
    process.stdout.close()
    value = b''.join(chunks).decode('utf-8', errors='replace')
    return {'status': 'captured' if process.returncode == 0 else 'command_failed',
            'exit_code': process.returncode, 'timed_out': timed_out,
            'truncated': truncated.is_set(), '_memory_only': value}


def selected_format(fields: dict[str, str]) -> str:
    return '{' + ','.join(json.dumps(name) + ':{{json ' + path + '}}' for name, path in fields.items()) + '}'


def selected_json(command: list[str]) -> dict:
    observed = bounded(command)
    raw = observed.pop('_memory_only', '')
    if observed['status'] == 'captured' and not observed.get('truncated'):
        try:
            observed['selected'] = json.loads(raw)
        except (ValueError, TypeError):
            observed['status'] = 'selected_json_unavailable'
    # Error text is deliberately omitted: it may echo a private context/credential.
    return observed


LOG_EVENTS = {
    'ready': r'database system is ready to accept connections',
    'starting': r'starting PostgreSQL',
    'checkpoint_started': r'checkpoint starting:',
    'checkpoint_completed': r'checkpoint complete:',
    'recovery_started': r'(?:automatic recovery in progress|redo starts at)',
    'recovery_completed': r'(?:database system is ready|redo done at)',
    'shutdown_requested': r'(?:received fast shutdown request|received smart shutdown request)',
    'shutdown_completed': r'database system is shut down',
    'statement_timeout': r'canceling statement due to statement timeout',
    'lock_timeout': r'canceling statement due to lock timeout',
    'client_cancel': r'canceling statement due to user request',
    'client_connection_error': r'(?:could not receive data from client|could not send data to client|connection reset by peer|broken pipe)',
    'deadlock': r'deadlock detected',
    'disk_full': r'(?:No space left on device|could not extend file)',
    'wal_io_failure': r'(?:could not fsync|could not write to file|PANIC:)',
}


def summarize_logs(raw: str) -> dict:
    """Persist fixed classifications, never original SQL/parameters/messages."""
    counts: collections.Counter[str] = collections.Counter()
    events = []
    for line in raw.splitlines():
        matched = [key for key, pattern in LOG_EVENTS.items() if re.search(pattern, line, re.I)]
        counts.update(matched)
        timestamp = re.match(r'^(\d{4}-\d{2}-\d{2}T[0-9:.]+Z)\s', line)
        for event in matched:
            events.append({'event': event, 'docker_timestamp': timestamp.group(1) if timestamp else None})
    return {'observed_lines': len(raw.splitlines()), 'fixed_event_counts': dict(counts),
            'events': events[:1000], 'raw_messages_saved': False,
            'scope': 'Bounded recent log sample; unmatched lines, SQL, values and exception text discarded. Not a complete log or peak resource record.'}


PG_QUERY = """SELECT json_build_object(
 'server_version',current_setting('server_version'),
 'server_version_num',current_setting('server_version_num'),
 'database_bytes',pg_database_size(current_database()),
 'settings',(SELECT json_agg(json_build_object('name',name,'setting',setting,'unit',unit,'source',source))
   FROM pg_settings WHERE name IN ('synchronous_commit','fsync','full_page_writes','wal_sync_method',
   'shared_buffers','work_mem','maintenance_work_mem','max_connections','max_wal_size','checkpoint_timeout',
   'checkpoint_completion_target','wal_compression','track_io_timing','track_wal_io_timing','statement_timeout',
   'lock_timeout','idle_in_transaction_session_timeout')),
 'activity_waits',(SELECT json_agg(t) FROM (SELECT backend_type,state,wait_event_type,wait_event,count(*) AS count
   FROM pg_stat_activity WHERE datname=current_database() GROUP BY backend_type,state,wait_event_type,wait_event) t),
 'database_stats',(SELECT json_build_object('numbackends',numbackends,'xact_commit',xact_commit,
   'xact_rollback',xact_rollback,'blks_read',blks_read,'blks_hit',blks_hit,'temp_bytes',temp_bytes,
   'deadlocks',deadlocks,'blk_read_time',blk_read_time,'blk_write_time',blk_write_time,
   'sessions_abandoned',sessions_abandoned,'sessions_fatal',sessions_fatal,'sessions_killed',sessions_killed)
   FROM pg_stat_database WHERE datname=current_database()),
 'wal_stats',(SELECT json_build_object('wal_records',wal_records,'wal_bytes',wal_bytes,'wal_buffers_full',wal_buffers_full,
   'wal_write',wal_write,'wal_sync',wal_sync,'wal_write_time',wal_write_time,'wal_sync_time',wal_sync_time) FROM pg_stat_wal))"""


def capture(container: str, user: str, database: str) -> dict:
    for value in (container, user, database):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
            raise ValueError('Use a simple fixture container/user/database identifier.')
    fields = {'id': '.Id', 'name': '.Name', 'image_id': '.Image', 'status': '.State.Status',
              'running': '.State.Running', 'oom_killed': '.State.OOMKilled', 'exit_code': '.State.ExitCode',
              'started_at': '.State.StartedAt', 'finished_at': '.State.FinishedAt', 'restart_count': '.RestartCount',
              'memory_bytes': '.HostConfig.Memory', 'memory_swap_bytes': '.HostConfig.MemorySwap',
              'memory_reservation_bytes': '.HostConfig.MemoryReservation', 'nano_cpus': '.HostConfig.NanoCpus',
              'cpu_quota': '.HostConfig.CpuQuota', 'cpu_period': '.HostConfig.CpuPeriod',
              'cpuset_cpus': '.HostConfig.CpusetCpus', 'pids_limit': '.HostConfig.PidsLimit',
              'shm_size': '.HostConfig.ShmSize', 'ulimits': '.HostConfig.Ulimits',
              'port_bindings': '.HostConfig.PortBindings', 'log_driver': '.HostConfig.LogConfig.Type'}
    inspected = selected_json(['docker', 'inspect', '--format', selected_format(fields), container])
    info = selected_json(['docker', 'info', '--format', selected_format({
        'os_type': '.OSType', 'operating_system': '.OperatingSystem', 'architecture': '.Architecture',
        'kernel': '.KernelVersion', 'docker_server_version': '.ServerVersion', 'vm_memory_total_bytes': '.MemTotal',
        'vm_cpus': '.NCPU', 'storage_driver': '.Driver', 'cgroup_driver': '.CgroupDriver', 'cgroup_version': '.CgroupVersion'})])
    stats = selected_json(['docker', 'stats', '--no-stream', '--format', '{{json .}}', container])
    image = {'status': 'container_identity_unavailable'}
    if isinstance(inspected.get('selected'), dict) and re.fullmatch(r'sha256:[0-9a-f]{64}', str(inspected['selected'].get('image_id'))):
        image = selected_json(['docker', 'image', 'inspect', '--format', selected_format({
            'id': '.Id', 'repo_digests': '.RepoDigests', 'repo_tags': '.RepoTags',
            'os': '.Os', 'architecture': '.Architecture'}), inspected['selected']['image_id']])
    pg = selected_json(['docker', 'exec', '--env', 'PGOPTIONS=-c statement_timeout=2000 -c lock_timeout=250 -c default_transaction_read_only=on',
        container, 'psql', '-X', '-q', '-A', '-t', '--no-password', '--username', user, '--dbname', database,
        '--set', 'ON_ERROR_STOP=1', '--command', PG_QUERY])
    logs = bounded(['docker', 'logs', '--timestamps', '--since', '20m', '--tail', '1000', container])
    raw_logs = logs.pop('_memory_only', '')
    logs['summary'] = summarize_logs(raw_logs)
    return {'schema_version': 'kien-operator-safe-diagnostics/v1', 'observed_utc': datetime.now(timezone.utc).isoformat(),
            'container': inspected, 'engine_vm': info, 'actual_image': image, 'one_snapshot_stats': stats,
            'postgres_read_only': pg, 'recent_docker_logs': logs,
            'scope': 'Selected read-only fixture diagnostics only. No Config.Env, DSN, password, SQL query text, parameters, private data or raw logs persisted. One snapshot is not peak usage. No Docker lifecycle operation.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--user', default='hosted_test')
    parser.add_argument('--database', default='hosted_test')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        result = capture(args.container, args.user, args.database)
        with (args.output / 'docker-pg-diagnostics.json').open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2)
            handle.write('\n')
        print(json.dumps({'output': str(args.output), 'status': 'captured_selected_diagnostics',
                          'missing_sections_are_not_passes': True}))
        return 0
    except Exception:
        print('Diagnostic capture failed; keep the unique output directory. No private command error text is printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
