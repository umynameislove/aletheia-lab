"""Bind PostgreSQL process samples only when its local PID namespace is proven."""
import os
from pathlib import Path


def linux_processes(proc_root=Path('/proc')):
    ticks = os.sysconf('SC_CLK_TCK')
    page = os.sysconf('SC_PAGE_SIZE')
    rows = {}
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            fields = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
            values = {'parent': int(fields[1]), 'rss_bytes': int(fields[21]) * page,
                      'cpu_seconds': (int(fields[11]) + int(fields[12])) / ticks}
        except (OSError, ValueError, IndexError):
            continue
        try:
            values['open_fds'] = len(list((process / 'fd').iterdir()))
        except OSError:
            values['open_fds'] = None
        rows[int(process.name)] = values
    return rows


def local_postgres_root(backend_pid, data_directory, processes):
    if not isinstance(data_directory, str):
        return None
    try:
        lines = (Path(data_directory) / 'postmaster.pid').read_text().splitlines()
        root = int(lines[0])
    except (OSError, ValueError, IndexError):
        return None
    # A same-numbered PID in another namespace must never stand in for the DB.
    backend = processes.get(backend_pid)
    if root <= 0 or root not in processes or backend is None or backend['parent'] != root:
        return None
    return root
