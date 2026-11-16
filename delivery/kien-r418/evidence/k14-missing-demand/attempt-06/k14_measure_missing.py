"""Run only supplemental K14 measurements in owned disposable Docker resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/unit")]
from test_k03_postgres_environment import _inspect, private_postgres  # noqa: E402

IMAGE = "aletheia-k14-test:queue-pressure-3"
IMAGE_ID = "sha256:6aed97ec4d6691527a72667ecdac67e4964ddcd6c1e4077aab9d8073a249b797"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, **kwargs).stdout


def owned_postgres(pg):
    identities = (
        docker("ps", "-q", "--filter", "label=aletheia.owner=k03-private-postgres")
        .decode()
        .splitlines()
    )
    matches = []
    for identity in identities:
        row = json.loads(docker("inspect", identity))[0]
        if row["NetworkSettings"]["Ports"].get("5432/tcp") != [
            {"HostIp": "127.0.0.1", "HostPort": str(pg.port)}
        ]:
            continue
        name = row["Name"].lstrip("/")
        owner = row["Config"]["Labels"]["aletheia.run_id"]
        assert name == "aletheia-k03-postgres-" + owner
        _inspect(row["Id"], name, owner, row["Image"], expected_port=pg.port)
        matches.append(row["Id"])
    assert len(matches) == 1
    return matches[0]


def fingerprints():
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("src", "tests", "configs", "dashboard/web/src")
        for p in sorted((ROOT / folder).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assert docker("image", "inspect", "--format", "{{.Id}}", IMAGE).decode().strip() == IMAGE_ID
    before = fingerprints()
    for p in (ROOT / "delivery/kien-r418").glob("**/*k14*missing*.py"):
        before[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    (output / "inputs-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    owner = uuid4().hex
    name = "aletheia-k14-demand-" + owner
    identity = None
    result = {
        "status": "not_completed",
        "scope": "K14.01/K14.05 missing measurements only",
        "test_image": IMAGE_ID,
        "committed": False,
        "pushed": False,
        "full_k14_deployment_pass": False,
        "production_source_modified": False,
    }
    start = time.monotonic()

    def cleanup_child():
        nonlocal identity
        if identity is None:
            return
        row = json.loads(docker("inspect", identity))[0]
        assert (
            row["Name"] == "/" + name
            and row["Config"]["Labels"].get("aletheia.k14.demand") == owner
            and row["Image"] == IMAGE_ID
        )
        docker("rm", "--force", identity)
        identity = None

    try:
        with ExitStack() as stack:
            pg = stack.enter_context(private_postgres())
            stack.callback(cleanup_child)
            postgres = owned_postgres(pg)
            docker("update", "--cpus", "2", "--memory", "1g", postgres)
            environment = dict(
                os.environ,
                K14_TEST_DATABASE_URL=(
                    "postgresql://aletheia:"
                    + pg.password.get_secret_value()
                    + "@127.0.0.1:5432/aletheia"
                ),
            )
            command = [
                "docker",
                "run",
                "--detach",
                "--pull=never",
                "--name",
                name,
                "--label",
                "aletheia.k14.demand=" + owner,
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
                "nofile=512:512",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=1610612736,mode=1777",
                "--mount",
                "type=bind,source=" + str(ROOT) + ",target=/workspace,readonly",
                "--mount",
                "type=bind,source=" + str(output) + ",target=/evidence",
                "--env",
                "K14_TEST_DATABASE_URL",
                "--env",
                "K14_RUN_MISSING=1",
                "--env",
                "K14_EVIDENCE_DIR=/evidence",
                "--env",
                "PYTHONUTF8=1",
                "--env",
                "PYTHONPATH=/workspace/src:/workspace/tests/unit:/workspace/tests/integration:/opt/test-deps",
                IMAGE_ID,
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "-q",
                "-ra",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "--basetemp=/tmp/k14-demand",
                "--junitxml=/evidence/junit.xml",
                "delivery/kien-r418/tests/test_k14_missing_demand.py",
            ]
            run = subprocess.run(command, env=environment, check=True, capture_output=True)
            identity = run.stdout.decode().strip()
            code = int(docker("wait", identity).decode().strip())
            # Docker keeps stderr separate, including the pytest startup diagnostics.
            raw = subprocess.run(["docker", "logs", identity], capture_output=True)
            assert pg.password.get_secret_value().encode() not in raw.stdout + raw.stderr
            (output / "pytest.log").write_bytes(raw.stdout + raw.stderr)
            row = json.loads(docker("inspect", identity))[0]
            result.update(
                status="passed_local_supplement" if code == 0 else "failed",
                exit_code=code,
                oom_killed=row["State"]["OOMKilled"],
                app_cpu_quota_cores=4,
                app_memory_limit_bytes=3 * 1024**3,
                postgres_cpu_quota_cores=2,
                postgres_memory_limit_bytes=1024**3,
                note="Shared network/PID namespace is verified against the owned PG. Separate cgroups, loopback TLS application traffic. Not team staging or primary50k.",
            )
            print("K14_SUPPLEMENT_EXIT=" + str(code), flush=True)
    finally:
        cleanup_child()
        after = fingerprints()
        for p in (ROOT / "delivery/kien-r418").glob("**/*k14*missing*.py"):
            after[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        (output / "inputs-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
        result.update(
            elapsed_seconds=time.monotonic() - start,
            inputs_unchanged=before == after,
            head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        )
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        assert before == after
    return 0 if result["status"] == "passed_local_supplement" else 1


if __name__ == "__main__":
    raise SystemExit(main())
