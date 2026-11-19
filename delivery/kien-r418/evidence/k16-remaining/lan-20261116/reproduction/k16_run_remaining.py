"""Check role isolation and rehearse K16 alert delivery on an owned Docker staging fleet."""

import json
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
CONTROL = TOOLS.parent / 'control'
FLEET = ("api0", "api1", "worker0", "worker1")


def docker(*args, data=None, timeout=60):
    run = subprocess.run(
        ["docker", *args],
        input=data,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if run.returncode:
        raise RuntimeError(f"Docker {args[0]} exited {run.returncode}")
    return run.stdout


def mount(kind, source, target, *, readonly=True):
    return ["--mount", f"type={kind},src={source},dst={target}" + (",readonly" if readonly else "")]


def execute(state, output):
    containers, volumes, run = state["containers"], state["volumes"], state["run_id"]
    network = json.loads(docker("network", "inspect", state["network"]))[0]
    assert network["Internal"] and network["Labels"]["com.aletheia.k14.owner"] == run
    assert state["project"] == "aletheia-k14-stg-" + run
    for volume in json.loads(docker("volume", "inspect", *volumes.values())):
        assert volume["Labels"]["com.aletheia.k14.owner"] == run
    protection = {}
    for source, item in zip(
        FLEET, json.loads(docker("inspect", *(containers[s] for s in FLEET))), strict=True
    ):
        assert item["Name"] == f"/{state['project']}-{source}"
        assert item["Config"]["Labels"]["com.aletheia.k14.owner"] == run
        assert item["Image"] == state["image_id"] and item["State"]["Running"]
        role = source.rstrip("01")
        settings = item["HostConfig"]
        assert settings["ReadonlyRootfs"] and settings["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in settings["SecurityOpt"]
        assert item["Config"]["User"] == "10001:10001"
        secret_mounts = {
            entry["Name"]: (entry["Destination"], entry["RW"])
            for entry in item["Mounts"]
            if entry.get("Name")
            in {volumes["api-secrets"], volumes["worker-secrets"], volumes["admin-secrets"]}
        }
        assert secret_mounts == {volumes[role + "-secrets"]: ("/run/secrets", False)}
        access = docker(
            "exec",
            containers[source],
            "/opt/venv/bin/python",
            "-B",
            "-c",
            "import json,os,pathlib,stat; "
            "p=pathlib.Path('/run/secrets'); "
            "print(json.dumps([os.geteuid(),{f.name:[stat.S_IMODE(f.stat().st_mode),"
            "f.stat().st_uid,os.access(f,os.R_OK),os.access(f,os.W_OK)] for f in p.iterdir()}]))",
        )
        uid, files = json.loads(access)
        expected = (
            {"database_worker"}
            if role == "worker"
            else {
                "database_api",
                "csrf_secret",
                "admission_secret",
                "login_proof_key",
                "tls_cert",
                "tls_key",
            }
        )
        assert uid == 10001 and set(files) == expected
        assert all(meta == [0o400, 10001, True, False] for meta in files.values())
        protection[source] = {"uid": uid, "secrets": files, "read_only_mount": True}
    (output / "runtime-permissions.json").write_text(json.dumps(protection, indent=2))

    schema = state["configuration"]["schema"]
    assert schema.startswith("k17_seed_") and schema.replace("_", "").isalnum()
    pg = containers["pg"]
    identity = json.loads(docker("inspect", pg))[0]
    assert identity["Name"] == f"/{state['project']}-pg"
    assert identity["Config"]["Labels"]["com.aletheia.k14.owner"] == run
    suffix = secrets.token_hex(5)
    db_role = "k16_collector_" + suffix
    password = secrets.token_hex(32)

    def psql(sql):
        return docker(
            "exec",
            "-i",
            pg,
            "psql",
            "-Xq",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "aletheia",
            data=sql,
        )

    psql(f"""BEGIN;
        CREATE ROLE {db_role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD '{password}';
        ALTER ROLE {db_role} SET default_transaction_read_only = on;
        GRANT USAGE ON SCHEMA {schema} TO {db_role};
        GRANT SELECT (operation,state,created_at,job_id) ON {schema}.j01_jobs TO {db_role};
        GRANT SELECT (expires_at) ON {schema}.k05_object_leases TO {db_role};
        GRANT SELECT (state,eligible_at) ON {schema}.k13_deletions TO {db_role};
        COMMIT;
    """)

    secret = (
        "postgresql://"
        + db_role
        + ":"
        + password
        + "@pg:5432/aletheia?"
        + urlencode(
            {
                "sslmode": "verify-full",
                "sslrootcert": "/run/config/ca.pem",
                "options": "-csearch_path=" + schema,
            }
        )
    )
    label = "com.aletheia.k16.rehearsal=" + suffix
    receiver, collector = ("aletheia-k16-" + suffix + "-" + s for s in ("receiver", "collector"))
    common = [
        "--pull=never",
        "--network",
        state["network"],
        "--label",
        label,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:rw,size=67108864",
        "--entrypoint",
        "/opt/venv/bin/python",
        *mount("bind", TOOLS, "/opt/k16"),
    ]
    receipts = output / "receiver"
    observations = output / "collector"
    workload = output / "traffic"
    for folder in (receipts, observations, workload):
        folder.mkdir()

    def traffic(phase, *, background=False):
        args = [
            "docker",
            "run",
            "--rm",
            *common,
            "--memory",
            "384m",
            "--cpus",
            "0.75",
            "--env",
            "PYTHONPATH=/opt/k14",
            *mount("bind", BASE / "staging/k14-local", "/opt/k14"),
            *mount("volume", volumes["configs"], "/run/config"),
            *mount("volume", volumes["clients"], "/run/load"),
            *mount("bind", workload, "/evidence", readonly=False),
            state["image_id"],
            "-B",
            "/opt/k16/k16_edge_probe.py",
            f"/evidence/{phase}.json",
            phase,
        ]
        log = (output / f"{phase}.log").open("wb")
        try:
            if background:
                return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=110, check=True)
        finally:
            log.close()

    monitor = None
    stopped = False
    allocated = []
    recovery = None
    try:
        with tempfile.TemporaryDirectory(dir=state["private_dir"], prefix="k16-collector-") as temp:
            token = Path(temp) / "token"
            token.write_text(secrets.token_hex(32))
            dsn = Path(temp) / "dsn"
            dsn.write_text(secret)
            config = Path(temp) / "stage.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": schema,
                        "release_identity": state["release_identity"],
                    }
                )
            )
            route = mount("bind", token, "/run/route/token")
            rc_id = docker(
                "run",
                "-d",
                "--name",
                receiver,
                "--network-alias",
                "k16-receiver",
                "--user",
                "10003:10003",
                "--memory",
                "96m",
                *common,
                *route,
                *mount("bind", receipts, "/evidence", readonly=False),
                state["image_id"],
                "-B",
                "/opt/k16/k16_collector.py",
                "receive",
                "/evidence/receiver.jsonl",
            ).strip()
            allocated.append(rc_id)
            for _ in range(35):
                if "K16_RECEIVER_READY" in docker("logs", rc_id):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Receiver failed to start")
            monitor = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "-i",
                    "--name",
                    collector,
                    "--user",
                    "10002:10002",
                    "--memory",
                    "160m",
                    *common,
                    *route,
                    *mount("bind", dsn, "/run/collector/dsn"),
                    *mount("bind", config, "/run/config/stage.json"),
                    *mount("bind", Path(state["private_dir"]) / "ca.pem", "/run/config/ca.pem"),
                    *mount("bind", observations, "/evidence", readonly=False),
                    state["image_id"],
                    "-B",
                    "/opt/k16/k16_collector.py",
                    "collect",
                    "/evidence/collector.jsonl",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=(output / "collector.log").open("w"),
                text=True,
                encoding="utf-8",
            )
            assert json.loads(monitor.stdout.readline()) == {"ready": True}
            allocated.append(docker("inspect", "--format", "{{.Id}}", collector).strip())
            seen = {}
            frames = []
            origin = datetime.now(UTC).isoformat()

            def observe(source, **extra):
                monitor.stdin.write(json.dumps({"source": source, **extra}) + "\n")
                monitor.stdin.flush()
                record = json.loads(monitor.stdout.readline())
                frames.append(record)
                return record

            def sample(phase):
                for source in FLEET:
                    logs = subprocess.run(
                        ["docker", "logs", "--since", origin, "--timestamps", containers[source]],
                        capture_output=True,
                        check=True,
                        timeout=15,
                    )
                    prefix = b'{"schema_version":"hosted-telemetry/v1",'
                    for line in (logs.stdout + logs.stderr).splitlines():
                        stamp, _, payload = line.partition(b" ")
                        if payload.startswith(prefix) and stamp > seen.get(source, b""):
                            observe(
                                source,
                                stage=phase,
                                dockertime=stamp.decode(),
                                snapshot=json.loads(payload),
                            )
                            seen[source] = stamp
                return observe("queue", stage=phase)

            def until(phase, predicate, duration):
                start = time.monotonic()
                while time.monotonic() - start < duration:
                    sample(phase)
                    if predicate():
                        return
                    time.sleep(2)
                raise AssertionError(f"expected observation missing during {phase}")

            def delivered(code, status):
                return any(
                    item["code"] == code and item["state"] == status
                    for frame in frames
                    for item in frame["delivered"]
                )

            traffic("preflight")
            assert json.loads((workload / "preflight.json").read_text())["verified_job"] == 1
            print("K16_COLLECTOR_READY_WAITING_FOR_OPERATOR", flush=True)
            deadline = time.monotonic() + 1800
            while not (CONTROL / "ready.json").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("operator did not join the exercise")
                time.sleep(1)
            until("baseline", lambda: len(seen) == 4, 35)
            (CONTROL / "outage.json").write_text(json.dumps({"utc": datetime.now(UTC).isoformat()}))
            stopped = True
            docker("stop", "--time", "12", *(containers[s] for s in FLEET[2:]))
            started = time.monotonic()
            traffic("overload")
            until(
                "overload",
                lambda: delivered("queue_age", "firing") and delivered("http_burn", "firing"),
                55,
            )
            print("K16_ALERTS_ACKNOWLEDGED", flush=True)
            (CONTROL / "may-recover.tmp").write_text(str(time.time() + 45))
            (CONTROL / "may-recover.tmp").replace(CONTROL / "may-recover")
            until("operator-review", lambda: (CONTROL / "recover.json").exists(), 45)
            operator = json.loads((CONTROL / "recover.json").read_text())
            assert operator["source_ip"] == "192.168.2.52" and operator["action"] == "recover"
            before_recovery = time.monotonic()
            docker("start", *(containers[s] for s in FLEET[2:]))
            stopped = False
            recovery = traffic("recovery", background=True)

            def recovered():
                code = recovery.poll()
                assert code in (None, 0), code
                required = {
                    (item["source"], item["code"])
                    for frame in frames
                    for item in frame["delivered"]
                    if item["state"] == "firing"
                }
                resolved = {
                    (item["source"], item["code"])
                    for frame in frames
                    for item in frame["delivered"]
                    if item["state"] == "resolved"
                }
                return code == 0 and required <= resolved

            until("recovery", recovered, 95)
            ended = time.monotonic()
            assert not any(
                item["state"] == "resolved" and item["code"] == "http_burn"
                for f in frames
                if f["stage"] == "overload"
                for item in f["delivered"]
            )
            expected = [item for frame in frames for item in frame["delivered"]]
            received = [
                json.loads(line) for line in (receipts / "receiver.jsonl").read_text().splitlines()
            ]
            assert len(expected) == len(received)
            for sent, acknowledged in zip(expected, received, strict=True):
                assert {k: v for k, v in sent.items() if k != "http_status"} == {
                    k: v for k, v in acknowledged.items() if k != "received_utc"
                }
            (output / "result.json").write_text(
                json.dumps(
                    {
                        "status": "passed_lan_operator_drill",
                        "overload_seconds_until_operator_action": before_recovery - started,
                        "recovery_observation_seconds": ended - before_recovery,
                        "snapshots": len(frames),
                        "receiver_acknowledged": len(received),
                        "release_id": state["release_identity"],
                        "image": state["image_id"],
                        "lan_operator_action": operator,
                        "previous_cardinality_evidence": "k16-remaining/alerts-05/traffic/cardinality.json",
                    },
                    indent=2,
                )
            )
            monitor.stdin.close()
            assert monitor.wait(timeout=15) == 0
    finally:
        if stopped:
            docker("start", *(containers[s] for s in FLEET[2:]))
        if recovery is not None and recovery.poll() is None:
            recovery.wait(timeout=110)
        probe = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", collector], capture_output=True, text=True
        )
        if probe.returncode == 0 and probe.stdout.strip() not in allocated:
            allocated.append(probe.stdout.strip())
        for identity in reversed(allocated):
            item = json.loads(docker("inspect", identity))[0]
            assert item["Config"]["Labels"]["com.aletheia.k16.rehearsal"] == suffix
            docker("rm", "--force", identity)
        if monitor is not None and monitor.poll() is None:
            monitor.wait(timeout=15)
        psql(f"DROP OWNED BY {db_role}; DROP ROLE {db_role};")


if __name__ == "__main__":
    state_file, output_dir = map(Path, sys.argv[1:])
    assert state_file.parent.parent.name == "k16-remaining", "isolated K16 state required"
    output_dir = output_dir.resolve()
    output_dir.mkdir(exist_ok=False)
    execute(json.loads(state_file.read_text()), output_dir)
