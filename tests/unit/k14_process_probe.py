"""Bind PostgreSQL process samples only when its local PID namespace is proven."""
from pathlib import Path


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
