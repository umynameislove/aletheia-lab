"""Run current K17 tests in native Linux with explicit disposable PostgreSQL."""

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
    for path in (ROOT / "scripts").glob("k17_*.py"):
        values[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    edge = ROOT / "delivery/kien-r418/tests/k15_tls_edge.py"
    values[edge.relative_to(ROOT).as_posix()] = hashlib.sha256(edge.read_bytes()).hexdigest()
    return values


def postgres_cpu_stat(postgres):
    return docker(
        "exec",
        postgres,
        "sh",
        "-c",
        "for path in /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/cpu/cpu.stat; do "
        'if test -r "$path"; then exec cat "$path"; fi; done; printf "unavailable\n"',
    ).decode()


def main(output: Path, mode: str, pg_cores: int = 1) -> int:
    assert pg_cores in (1, 2, 4)
    targets = {
        "unit": [
            "tests/unit/test_k17_capacity.py",
            "tests/unit/test_k17_profiles.py",
            "tests/unit/test_k17_capacity_cli.py",
            "tests/unit/test_k17_capacity_preflight.py",
            "tests/unit/test_k17_capacity_sessions.py",
            "tests/unit/test_k17_native_seed.py",
        ],
        "faults": ["tests/integration/test_k17_fault_load_postgres.py"],
        "seed": ["tests/integration/test_k17_native_seed_postgres.py"],
        "closed": [
            "tests/integration/test_k17_capacity_postgres.py::test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports"
        ],
        "open": [
            "tests/integration/test_k17_capacity_postgres.py::test_actual_tls_100_principal_calibration_with_native_jobs_and_whole_exports"
        ],
        "rotation": [
            "tests/integration/test_k17_capacity_postgres.py::test_actual_native_rotation_retains_actor_context_and_old_cookie_is_denied"
        ],
    }
    targets["remaining"] = targets["unit"] + targets["seed"] + targets["rotation"]
    targets["cli"] = ["tests/unit/test_k17_capacity_cli.py"]
    targets["open2"] = targets["open"]
    targets["closed2"] = targets["closed"]
    targets["window"] = ["tests/integration/test_k17_window_pipeline_postgres.py"]
    targets["catalog"] = targets["seed"]
    targets["catalog-policy"] = [
        "tests/integration/test_k17_native_seed_postgres.py::test_window_catalog_pilot_verifies_native_multi_generation_results[small]",
        "tests/integration/test_k17_native_seed_postgres.py::test_window_seed_preserves_live_owner_and_replaces_expired_session",
        "tests/integration/test_k17_native_seed_postgres.py::test_window_catalog_hot_memberships_keep_distinct_principals",
        "tests/integration/test_k17_native_seed_postgres.py::test_small_streamed_native_seed_has_real_scoped_records_role_mix_and_independent_physical_sizes",
        "tests/unit/test_k17_capacity_cli.py",
    ]
    tests = targets[mode]
    output.mkdir(parents=True, exist_ok=False)
    before = inputs()
    (output / "inputs-before.json").write_text(json.dumps(before, indent=2))
    identity = uuid4().hex
    started = time.monotonic()
    receipt = {"status": "failed", "mode": mode, "primary_pass": False}
    try:
        with private_postgres() as pg:
            postgres = owned_postgres(pg)
            if pg_cores != 1:
                docker("update", "--cpus", str(pg_cores), postgres)
            assert (
                int(docker("inspect", "--format", "{{.HostConfig.NanoCpus}}", postgres))
                == pg_cores * 10**9
            )
            receipt["postgres_cores"] = pg_cores
            receipt["postgres_cpu_stat_before_test"] = postgres_cpu_stat(postgres)
            environment = {
                **os.environ,
                "K17_TEST_DATABASE_URL": "postgresql://aletheia:"
                + pg.password.get_secret_value()
                + "@127.0.0.1:5432/aletheia",
                "K17_EVIDENCE_DIR": "/evidence",
                "K17_FAULT_EVIDENCE_DIR": "/evidence",
                "K17_CALIBRATION_MODE": mode.removesuffix("2")
                if mode in ("closed", "open", "closed2", "open2")
                else "closed",
                "K17_API_REPLICAS": "2" if mode in ("closed2", "open2") else "1",
                "K17_CALIBRATION_DURATION_SECONDS": "40",
                "K17_CALIBRATION_WARMUP_SECONDS": "5",
                "K17_CALIBRATION_ARRIVALS_PER_SECOND": "20",
            }
            arguments = [
                "run",
                "-d",
                "--pull=never",
                "--name",
                "aletheia-k17-verify-" + identity,
                "--label",
                "com.aletheia.k17.verify=" + identity,
                "--network",
                "container:" + postgres,
                "--pid",
                "container:" + postgres,
                "--cpus",
                "4",
                "--memory",
                "2g",
                "--pids-limit",
                "256",
                "--ulimit",
                "nofile=1024:1024",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=1610612736,mode=1777",
                "--mount",
                f"type=bind,src={ROOT},dst=/input,readonly",
                "--mount",
                f"type=bind,src={output},dst=/evidence",
                "--env",
                "PYTHONUTF8=1",
            ]
            for key in environment:
                if key.startswith("K17_"):
                    arguments.extend(["--env", key])
            arguments.extend(
                [
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
                    "--basetemp=/tmp/k17test",
                    "--junitxml=/evidence/junit.xml",
                    *tests,
                ]
            )
            child = docker(*arguments, env=environment).decode().strip()
            try:
                code = int(docker("wait", child, timeout=780 if mode in ("window", "catalog") else 480).decode())
                logs = subprocess.run(["docker", "logs", child], capture_output=True, check=True)
                assert pg.password.get_secret_value().encode() not in logs.stdout + logs.stderr
                (output / "pytest.log").write_bytes(logs.stdout + logs.stderr)
                state = json.loads(docker("inspect", "--format", "{{json .State}}", child))
                assert not state["OOMKilled"]
                receipt["postgres_cpu_stat_after_test"] = postgres_cpu_stat(postgres)
                receipt.update(
                    status="passed" if code == 0 else "failed", exit_code=code, oom=False
                )
                measured = output / "calibration100-result.json"
                if code == 0 and mode in ("closed", "open", "closed2", "open2"):
                    capacity = json.loads(measured.read_text())
                    slo_pass = capacity["measured_positive"]["observed_subset_success_target_met"]
                    receipt.update(
                        functional_checks_passed=True,
                        measured_positive_slo_passed=slo_pass,
                        status="passed_local_subset" if slo_pass else "completed_with_slo_failures",
                    )
            finally:
                item = json.loads(docker("inspect", child))[0]
                assert item["Config"]["Labels"]["com.aletheia.k17.verify"] == identity
                assert item["Image"] == IMAGE_ID
                docker("rm", "--force", child)
    finally:
        assert before == inputs()
        receipt.update(
            elapsed_seconds=time.monotonic() - started,
            inputs_unchanged=True,
            git_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            test_image=IMAGE_ID,
        )
        (output / "result.json").write_text(json.dumps(receipt, indent=2))
    return receipt["exit_code"]


if __name__ == "__main__":
    raise SystemExit(
        main(Path(sys.argv[1]).resolve(), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    )
