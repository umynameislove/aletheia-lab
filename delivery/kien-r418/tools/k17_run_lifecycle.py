"""Run one dedicated lifecycle phase on the explicitly owned, running K17 fleet."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from k16_run_remaining import docker, mount

BASE = Path(__file__).resolve().parent.parent
PHASES = {"imports", "management", "logout", "cancel", "delete_integrity"}


def main(state_file: Path, trial: str, phase: str) -> int:
    assert state_file.parent.parent == BASE / "evidence/k17-remaining"
    assert re.fullmatch(r"[a-z][a-z0-9-]{1,63}", trial) and phase in PHASES
    evidence = state_file.parent.parent
    assert not (evidence / trial).exists()
    state = json.loads(state_file.read_text())
    owner, containers = state["run_id"], state["containers"]
    assert state["project"] == "aletheia-k14-stg-" + owner
    network = json.loads(docker("network", "inspect", state["network"]))[0]
    assert network["Internal"] and network["Labels"]["com.aletheia.k14.owner"] == owner
    for role in ("api0", "api1", "worker0", "worker1"):
        item = json.loads(docker("inspect", containers[role]))[0]
        assert item["Name"] == "/" + state["project"] + "-" + role
        assert item["Config"]["Labels"]["com.aletheia.k14.owner"] == owner
        assert item["Image"] == state["image_id"] and item["State"]["Running"]
    for role in ("configs", "clients"):
        volume = json.loads(docker("volume", "inspect", state["volumes"][role]))[0]
        assert volume["Labels"]["com.aletheia.k14.owner"] == owner
    suffix = uuid4().hex
    name = "aletheia-k17-lifecycle-" + suffix
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--label",
        "com.aletheia.k17.lifecycle=" + suffix,
        "--network",
        state["network"],
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,size=67108864",
        "--env",
        "PYTHONPATH=/opt/k14",
        *mount("bind", BASE / "staging/k14-local", "/opt/k14"),
        *mount("bind", BASE / "tools/k17_lifecycle_probe.py", "/opt/k17_lifecycle_probe.py"),
        *mount("bind", evidence, "/evidence", readonly=False),
        *mount("volume", state["volumes"]["configs"], "/run/config"),
        *mount("volume", state["volumes"]["clients"], "/run/load"),
        "--entrypoint",
        "/opt/venv/bin/python",
        state["image_id"],
        "-B",
        "/opt/k17_lifecycle_probe.py",
        "/evidence/" + trial,
        phase,
    ]
    stopped = False
    start = time.monotonic()
    try:
        if phase == "cancel":
            stopped = True
            docker("stop", "--time", "12", containers["worker0"], containers["worker1"])
        with (evidence / (trial + ".console.log")).open("xb") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=420)
        result = {
            "exit_code": completed.returncode,
            "phase": phase,
            "elapsed_seconds": time.monotonic() - start,
            "image_id": state["image_id"],
            "runtime_identity": state["release_identity"],
        }
        if (evidence / trial).is_dir():
            (evidence / trial / "driver.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        return completed.returncode
    finally:
        if stopped:
            docker("start", containers["worker0"], containers["worker1"])
        remaining = subprocess.run(["docker", "inspect", name], capture_output=True)
        if remaining.returncode == 0:
            item = json.loads(remaining.stdout)[0]
            assert item["Config"]["Labels"]["com.aletheia.k17.lifecycle"] == suffix
            docker("rm", "--force", item["Id"])


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]))
