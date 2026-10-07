"""Abrupt owner-exit controls for prospective admission persistence.

These are authored storage controls, not another deployment family. SQLite FULL
is tested across a process exit, not power loss, hostile storage or concurrency.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import seal
from aletheia_lab.evaluation.incident_audit_archive import POLICIES, IncidentAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

PHASES = ("before_commit", "after_ack")


def crash_worker(path: Path, policy: str, phase: str) -> None:
    if phase not in PHASES:
        raise ValueError("explicit commit boundary required")
    store = IncidentAuditArchive(path, policy, 8192)
    if phase == "before_commit":
        store.state["pending"]["future"] = {"until": 10, "bound": 4096}
        store.state["accepted"] = 1
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(
            "INSERT OR REPLACE INTO state VALUES(1,?)", (store._document(store.state),)
        )
        print(encode({"acknowledged": False}), flush=True)
    else:
        if not store.reserve("future", now=0, until=10, bound=4096):
            raise ValueError("controlled admission did not fit")
        print(
            encode({"acknowledged": True, "state_sha256": store.snapshot()["state_sha256"]}),
            flush=True,
        )
    os._exit(23)  # Deliberate child termination, no connection cleanup/checkpoint.


def run_controls(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private controls directory required")
    directory.mkdir(parents=True)
    config = {"policies": POLICIES, "phases": PHASES, "quota": 8192, "growth_bound": 4096}
    write_new_file(directory / "plan.json", encode(seal(config)).encode())
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP"}
    }
    env["PYTHONPATH"] = str(root / "src")
    rows = []
    for policy in POLICIES:
        for phase in PHASES:
            path = directory / f"{policy}-{phase}.sqlite"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/incident_audit_development.py"),
                    "crash-worker",
                    "--root",
                    str(root),
                    "--study-dir",
                    str(path),
                    "--policy",
                    policy,
                    "--phase",
                    phase,
                ],
                env=env,
                capture_output=True,
                timeout=30,
                check=False,
            )
            stream = {
                "stdout_sha256": content_sha256(completed.stdout),
                "stderr_sha256": content_sha256(completed.stderr),
            }
            write_new_file(directory / f"{policy}-{phase}.stdout", completed.stdout)
            write_new_file(directory / f"{policy}-{phase}.stderr", completed.stderr)
            row: dict[str, Any] = {
                "policy": policy,
                "phase": phase,
                "returncode": completed.returncode,
                **stream,
            }
            if completed.returncode != 23:
                row["status"] = "control_worker_failed"
            else:
                receipt = json.loads(completed.stdout)
                store = IncidentAuditArchive(path, policy, 8192, reopen=True)
                pending = "future" in store.state["pending"]
                snapshot = store.snapshot()
                if phase == "after_ack":
                    passed = pending and snapshot["state_sha256"] == receipt["state_sha256"]
                else:
                    passed = not pending and snapshot["accepted"] == 0
                store.close()
                row.update(
                    status="pass" if passed else "control_recovery_failed",
                    acknowledged=receipt["acknowledged"],
                    recovered_pending=pending,
                    state_sha256=snapshot["state_sha256"],
                    db_sha256=content_sha256(path.read_bytes()),
                )
            rows.append(row)
    result = seal(
        {
            "schema": "incident-audit-process-exit-controls/v1",
            "rows": rows,
            "planned": len(POLICIES) * len(PHASES),
            "passed": sum(row["status"] == "pass" for row in rows),
            "provider_calls": 0,
            "claim": "abrupt process exit only; no power-loss or machine crash guarantee",
        }
    )
    write_new_file(directory / "results.json", encode(result).encode())
    return result
