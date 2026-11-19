"""Create one bounded, loopback-only PostgreSQL fixture; preserve existing resources."""
from __future__ import annotations

import json
import secrets
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

KIT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:16-bookworm@sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca"


def command(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Owned PostgreSQL fixture command failed; no existing resource was changed.")
    return result.stdout.strip()


def main() -> None:
    private = KIT / ".private"
    private.mkdir(exist_ok=True)
    receipt = KIT / "evidence" / "postgres-fixture.json"
    if receipt.exists():
        raise RuntimeError("A fixture receipt already exists; inspect it instead of creating another.")
    command("image", "inspect", IMAGE, "--format", "{{.Id}}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run_id = uuid4().hex
    name = "aletheia-r4-" + run_id
    volume = "aletheia_r4_" + run_id
    password = secrets.token_urlsafe(32)
    env_file = private / "postgres.env"
    env_file.write_text("POSTGRES_USER=k06\nPOSTGRES_DB=k06_test\nPOSTGRES_PASSWORD=" + password + "\n", encoding="utf-8")
    database_file = private / "database-url.txt"
    database_file.write_text(f"postgresql://k06:{password}@127.0.0.1:{port}/k06_test\n", encoding="utf-8")
    command("volume", "create", "--label", "codex.task=aletheia-r4", volume)
    identity = command("run", "--detach", "--pull=never", "--name", name,
                       "--label", "codex.task=aletheia-r4", "--cpus", "2", "--memory", "1g",
                       "--pids-limit", "192", "--publish", f"127.0.0.1:{port}:5432",
                       "--env-file", str(env_file), "--mount", f"type=volume,src={volume},dst=/var/lib/postgresql/data", IMAGE)
    public = {"container_id": identity, "container_name": name, "volume_name": volume,
              "requested_image": IMAGE, "port": port, "cpus": 2, "memory_bytes": 1073741824,
              "dedicated_synthetic_test_only": True, "existing_resources_modified": False,
              "automatic_delete": False, "database_file": str(database_file), "ready": False}
    receipt.write_text(json.dumps(public, indent=2), encoding="utf-8")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        check = subprocess.run(["docker", "exec", identity, "pg_isready", "-U", "k06", "-d", "k06_test"], capture_output=True, timeout=10)
        if check.returncode == 0:
            public["ready"] = True
            public["actual_image_id"] = command("inspect", identity, "--format", "{{.Image}}")
            receipt.write_text(json.dumps(public, indent=2), encoding="utf-8")
            print(json.dumps({"ready": True, "receipt": str(receipt), "database_file": str(database_file)}))
            return
        time.sleep(1)
    raise RuntimeError("Owned fixture did not become ready; retain its receipt and volume for inspection.")


if __name__ == "__main__":
    main()
