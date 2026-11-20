"""Start one native PostgreSQL cluster owned by this WSL test attempt."""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    kit = Path(__file__).resolve().parents[1]
    receipt = kit / "evidence/linux-postgres-fixture.json"
    if receipt.exists():
        raise RuntimeError("A native fixture receipt already exists; inspect it before continuing.")
    package = Path((kit / "evidence/linux-pg16-path.txt").read_text().strip())
    binary = package / "root/usr/lib/postgresql/16/bin"
    cluster = Path(tempfile.mkdtemp(prefix="aletheia-r4-postgres-"))
    private = kit / ".private"
    password = secrets.token_urlsafe(32)
    pwfile = cluster / "init-password"
    pwfile.write_text(password + "\n")
    pwfile.chmod(0o600)
    environment = dict(os.environ, LD_LIBRARY_PATH=str(package / "root/usr/lib/x86_64-linux-gnu"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    data = cluster / "data"
    with (cluster / "setup.log").open("w") as log:
        subprocess.run([str(binary / "initdb"), "-D", str(data), "--username=k06", "--auth-host=scram-sha-256",
                        "--auth-local=peer", "--pwfile=" + str(pwfile)], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        with (data / "postgresql.conf").open("a") as config:
            config.write("\nlisten_addresses='127.0.0.1'\nport=" + str(port) + "\nshared_buffers='128MB'\nmax_connections=100\n")
        subprocess.run([str(binary / "pg_ctl"), "-D", str(data), "-l", str(cluster / "postgres.log"), "-o", "-k " + str(cluster), "-w", "start"],
                       env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        subprocess.run([str(binary / "createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "k06", "k06_test"],
                       env=dict(environment, PGPASSWORD=password), stdout=log, stderr=subprocess.STDOUT, check=True, timeout=20)
    database_file = private / "linux-database-url.txt"
    database_file.write_text(f"postgresql://k06:{password}@127.0.0.1:{port}/k06_test\n", encoding="utf-8")
    public = {"data_directory": str(data), "binary_directory": str(binary), "package_root": str(package),
              "port": port, "database_file": str(database_file), "pid": int((data / "postmaster.pid").read_text().splitlines()[0]),
              "bind": "127.0.0.1", "ready": True, "owned_temporary_cluster": True, "existing_clusters_modified": False}
    receipt.write_text(json.dumps(public, indent=2), encoding="utf-8")
    print(json.dumps({"ready": True, "receipt": str(receipt)}))


if __name__ == "__main__":
    main()
