"""Run only the two pending K15 native HTTPS cases in a private Docker fixture."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from k14_measure_missing import (
    IMAGE_ID,
    ROOT,
    SNAPSHOT_BOOTSTRAP,
    docker,
    fingerprints,
    owned_postgres,
    private_postgres,
)


def inputs():
    values = fingerprints()
    for candidate in [Path(__file__), *Path(__file__).parent.parent.joinpath("tests").glob("*.py")]:
        values[candidate.relative_to(ROOT).as_posix()] = hashlib.sha256(
            candidate.read_bytes()
        ).hexdigest()
    return values


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    before = inputs()
    (output / "inputs-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    owner = uuid4().hex
    name = "aletheia-k15-probe-" + owner
    start = time.monotonic()
    result = {"status": "failed", "image": IMAGE_ID, "committed": False, "pushed": False}
    try:
        with private_postgres() as pg:
            postgres = owned_postgres(pg)
            environment = dict(
                os.environ,
                K15_TEST_DATABASE_URL="postgresql://aletheia:"
                + pg.password.get_secret_value()
                + "@127.0.0.1:5432/aletheia",
            )
            args = [
                "docker",
                "run",
                "--detach",
                "--pull=never",
                "--name",
                name,
                "--label",
                "aletheia.k15.probe=" + owner,
                "--network",
                "container:" + postgres,
                "--pid",
                "container:" + postgres,
                "--cpus",
                "4",
                "--memory",
                "3g",
                "--pids-limit",
                "256",
                "--ulimit",
                "nofile=1024:1024",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=805306368,mode=1777",
                "--mount",
                "type=bind,source=" + str(ROOT) + ",target=/input,readonly",
                "--mount",
                "type=bind,source=" + str(output) + ",target=/evidence",
                "--env",
                "K15_TEST_DATABASE_URL",
                "--env",
                "K15_RECONNECT_ONLY",
                "--env",
                "PYTHONUTF8=1",
                IMAGE_ID,
                "-c",
                SNAPSHOT_BOOTSTRAP,
                "-q",
                "-ra",
                "-o",
                "addopts=",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                "--basetemp=/tmp/k15test",
                "--junitxml=/evidence/junit.xml",
                "delivery/kien-r418/tests/test_k15_staging_remaining.py",
            ]
            child = (
                subprocess.run(args, env=environment, capture_output=True, check=True)
                .stdout.decode()
                .strip()
            )
            try:
                code = int(docker("wait", child, timeout=340).decode())
                logs = subprocess.run(["docker", "logs", child], capture_output=True, check=True)
                assert pg.password.get_secret_value().encode() not in logs.stdout + logs.stderr
                (output / "pytest.log").write_bytes(logs.stdout + logs.stderr)
                oom = json.loads(docker("inspect", "--format", "{{json .State}}", child))[
                    "OOMKilled"
                ]
                result.update(
                    status="passed_native_https" if code == 0 else "failed",
                    exit_code=code,
                    oom=oom,
                    application_cores=4,
                    application_ram_bytes=3 * 1024**3,
                    pg_cores=1,
                    pg_ram_bytes=512 * 1024**2,
                    fd_limit=1024,
                )
            finally:
                row = json.loads(docker("inspect", child))[0]
                assert (
                    row["Id"] == child
                    and row["Name"] == "/" + name
                    and row["Config"]["Labels"]["aletheia.k15.probe"] == owner
                    and row["Image"] == IMAGE_ID
                )
                docker("rm", "--force", child)
            print("K15_PROBE_EXIT=" + str(code), flush=True)
    finally:
        after = inputs()
        result.update(
            inputs_unchanged=before == after,
            elapsed_seconds=time.monotonic() - start,
            head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        )
        (output / "inputs-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        assert before == after
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
