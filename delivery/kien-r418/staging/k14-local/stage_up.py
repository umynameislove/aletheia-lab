"""Create only labelled, loopback-published local staging resources; no cloud account needed."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "delivery/kien-r418/evidence/k14-staging"
TOOLS = Path(__file__).resolve().parent


def docker(*args, env=None):
    result = subprocess.run(["docker", *args], env=env, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            "Docker staging operation failed: "
            + args[0]
            + "; "
            + result.stderr.decode(errors="replace")[-500:]
        )
    return result.stdout


def main(mode):
    state = json.loads((OUT / "state-public.json").read_text())
    private = Path(state["private_dir"])
    project, run = state["project"], state["run_id"]
    assert project == "aletheia-k14-stg-" + run and len(run) == 12
    assert (
        docker("image", "inspect", "--format", "{{.Id}}", state["image"]).decode().strip()
        == state["image_id"]
    )
    state.setdefault("containers", {})
    state.setdefault("volumes", {})

    def save(phase):
        state["phase"] = phase
        (OUT / "state-public.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (private / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        print("K14_STAGING_PHASE=" + phase, flush=True)

    def owned_container(label):
        identity = state["containers"][label]
        item = json.loads(docker("inspect", identity))[0]
        assert item["Name"] == "/" + project + "-" + label
        assert item["Config"]["Labels"]["com.aletheia.k14.owner"] == run
        return identity

    if mode in ("status", "stop", "resume"):
        order = ["pg", "idp", "api0", "api1", "worker0", "worker1", "ingress"]
        if mode == "stop":
            order.reverse()
        for component in order:
            identity = owned_container(component)
            status = docker("inspect", "--format", "{{.State.Status}}", identity).decode().strip()
            if mode == "stop" and status == "running":
                docker("stop", "--time", "20", identity)
                status = "stopped"
            if mode == "resume" and status != "running":
                docker("start", identity)
                status = "starting"
            print(component, status, flush=True)
        return

    def volume(key):
        return state["volumes"][key]

    def mount(key, destination, readonly=False):
        return [
            "--mount",
            "type=volume,src="
            + volume(key)
            + ",dst="
            + destination
            + (",readonly" if readonly else ""),
        ]

    def tool_mount():
        return ["--mount", "type=bind,src=" + str(TOOLS) + ",dst=/opt/k14,readonly"]

    if mode == "dependencies":
        assert state["phase"] == "image-built", "fresh image-built owned staging required"
        ports = []
        for candidate in range(18776, 18900):
            try:
                with socket.socket() as test:
                    test.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            ports.append(candidate)
            if len(ports) == 3:
                break
        assert len(ports) == 3
        app_port, idp_port, pg_port = ports
        idp_host = "idp-" + run + ".test"
        schema = "k17_seed_" + uuid.uuid4().hex
        subjects = [
            str(uuid.uuid5(uuid.NAMESPACE_URL, project + "/user/" + str(i))) for i in range(30)
        ]
        config = {
            "run_id": run,
            "schema": schema,
            "api_role": "api_" + run,
            "worker_role": "worker_" + run,
            "app_port": app_port,
            "idp_port": idp_port,
            "origin": f"https://localhost:{app_port}",
            "issuer": f"https://{idp_host}:{idp_port}/realms/k14",
            "idp_host": idp_host,
            "subjects": subjects,
            "release_identity": state["release_identity"],
        }
        state["configuration"] = config
        state["pg_port"] = pg_port
        material = {
            name: secrets.token_urlsafe(36)
            for name in (
                "pg_password",
                "api_password",
                "worker_password",
                "csrf_secret",
                "admission_secret",
                "idp_password",
                "user_password",
            )
        }
        material["proof_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
        state["network"] = (
            docker(
                "network",
                "create",
                "--internal",
                "--label",
                "com.aletheia.k14.owner=" + run,
                project + "-network",
            )
            .decode()
            .strip()
        )
        for key in (
            "configs",
            "api-secrets",
            "worker-secrets",
            "pg-secrets",
            "admin-secrets",
            "idp-secrets",
            "objects",
            "clients",
            "metrics",
            "pg-data",
            "idp-data",
        ):
            name = project + "-" + key
            docker("volume", "create", "--label", "com.aletheia.k14.owner=" + run, name)
            state["volumes"][key] = name
        save("resources-created")

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, project + " private CA")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
            .not_valid_after(datetime.now(UTC) + timedelta(days=14))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .sign(ca_key, hashes.SHA256())
        )

        def write(name, raw):
            (private / name).write_bytes(raw)

        write("ca.pem", ca.public_bytes(serialization.Encoding.PEM))
        write(
            "ca.key",
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        for name, hosts in {
            "api": ["localhost", "ingress", "api0", "api1"],
            "pg": ["localhost", "pg"],
            "idp": [idp_host, "idp"],
        }.items():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
                .issuer_name(ca_name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
                .not_valid_after(datetime.now(UTC) + timedelta(days=7))
                .add_extension(
                    x509.SubjectAlternativeName(
                        [
                            *[x509.DNSName(h) for h in hosts],
                            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        ]
                    ),
                    False,
                )
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
                .sign(ca_key, hashes.SHA256())
            )
            write(name + ".crt", cert.public_bytes(serialization.Encoding.PEM))
            write(
                name + ".key",
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            )
        params = {"sslmode": "verify-full", "sslrootcert": "/run/config/ca.pem"}
        owner_dsn = (
            "postgresql://postgres:"
            + quote(material["pg_password"], safe="")
            + "@pg:5432/aletheia?"
            + urlencode(params)
        )
        bootstrap = {**material, "owner_dsn": owner_dsn}
        write("bootstrap.json", json.dumps(bootstrap).encode())
        for role in ("api", "worker"):
            dsn = (
                "postgresql://"
                + config[role + "_role"]
                + ":"
                + quote(material[role + "_password"], safe="")
                + "@pg:5432/aletheia?"
                + urlencode({**params, "options": "-csearch_path=" + schema})
            )
            write("database_" + role, dsn.encode())
        for src, key in (
            ("csrf_secret", "csrf_secret"),
            ("admission_secret", "admission_secret"),
            ("login_proof_key", "proof_key"),
            ("pg-password", "pg_password"),
        ):
            write(src, material[key].encode())
        write("stage.json", json.dumps(config).encode())
        realm = {
            "realm": "k14",
            "enabled": True,
            "sslRequired": "all",
            "registrationAllowed": False,
            "clients": [
                {
                    "clientId": "k14-staging",
                    "enabled": True,
                    "publicClient": True,
                    "standardFlowEnabled": True,
                    "directAccessGrantsEnabled": False,
                    "redirectUris": [config["origin"] + "/v1/auth/callback"],
                    "webOrigins": [config["origin"]],
                    "attributes": {"pkce.code.challenge.method": "S256"},
                }
            ],
            "users": [
                {
                    "id": subjects[i],
                    "username": "u" + str(i),
                    "email": f"u{i}@aletheia.invalid",
                    "emailVerified": True,
                    "enabled": True,
                    "firstName": "Synthetic",
                    "lastName": "Staging",
                    "credentials": [
                        {"type": "password", "value": material["user_password"], "temporary": False}
                    ],
                }
                for i in range(30)
            ],
        }
        write("k14-realm.json", json.dumps(realm).encode())
        template = (ROOT / "configs/hosted/staging.example.toml").read_text()
        import re

        for role in ("api", "worker"):
            text = template
            for key, value in {
                "public_origin": config["origin"],
                "issuer": config["issuer"],
                "client_id": "k14-staging",
                "redirect_uri": config["origin"] + "/v1/auth/callback",
                "client_kind": "public",
                "expected_schema": schema,
                "expected_database_role": config[role + "_role"],
                "object_root": "/var/lib/aletheia",
            }.items():
                text = re.sub(
                    r"^" + key + r' = ".*"$', key + " = " + json.dumps(value), text, flags=re.M
                )
            text = text.replace('bind_host = "127.0.0.1"', 'bind_host = "0.0.0.0"')
            text = text.replace("database_pool_size = 8", "database_pool_size = 4")
            text = text.replace(
                "postgres_connection_budget = 16", "postgres_connection_budget = 20"
            )
            text = text.replace("admin_connection_reserve = 2", "admin_connection_reserve = 4")
            write(role + ".toml", text.encode())
        write(
            "auth.toml",
            (
                'default_return_to = "/"\nreturn_to_paths = ["/"]\nadmission_namespace = "'
                + schema
                + '"\n'
            ).encode(),
        )
        provision = {
            "volumes": {
                k: (
                    [999, 999]
                    if k.startswith("pg-")
                    else [1000, 0]
                    if k.startswith("idp-")
                    else [10001, 10001]
                )
                for k in state["volumes"]
            },
            "files": [],
        }

        def file(source, destination, owner=(10001, 10001), mode=0o400):
            provision["files"].append(
                dict(source=source, destination=destination, owner=owner, mode=mode)
            )

        for name in ("api.toml", "worker.toml", "auth.toml", "stage.json", "ca.pem"):
            file(name, "configs/" + name, mode=0o444)
        file("ca.pem", "configs/idp-ca.pem", mode=0o444)
        for name in ("database_api", "csrf_secret", "admission_secret", "login_proof_key"):
            file(name, "api-secrets/" + name)
        file("api.crt", "api-secrets/tls_cert")
        file("api.key", "api-secrets/tls_key")
        file("database_worker", "worker-secrets/database_worker")
        file("bootstrap.json", "admin-secrets/bootstrap.json")
        file("pg.crt", "pg-secrets/pg.crt", (999, 999))
        file("pg.key", "pg-secrets/pg.key", (999, 999))
        file("pg-password", "pg-secrets/password", (999, 999))
        file("idp.crt", "idp-secrets/idp.crt", (1000, 0))
        file("idp.key", "idp-secrets/idp.key", (1000, 0))
        file("k14-realm.json", "idp-secrets/k14-realm.json", (1000, 0))
        write("provision.json", json.dumps(provision).encode())
        provision_args = [
            "run",
            "--rm",
            "--pull=never",
            "--user",
            "0:0",
            "--memory",
            "256m",
            "--network",
            "none",
            "--entrypoint",
            "/opt/venv/bin/python",
            *tool_mount(),
            "--mount",
            "type=bind,src=" + str(private) + ",dst=/private,readonly",
        ]
        for key in state["volumes"]:
            provision_args += mount(key, "/volumes/" + key)
        docker(*provision_args, state["image_id"], "/opt/k14/stage_node.py", "provision")
        save("private-config-provisioned")
        pg_image = docker("image", "inspect", "--format", "{{.Id}}", "postgres:16").decode().strip()
        assert pg_image == "sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94"
        state["pg_image"] = pg_image
        args = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            project + "-pg",
            "--label",
            "com.aletheia.k14.owner=" + run,
            "--label",
            "com.aletheia.k14.component=pg",
            "--network",
            state["network"],
            "--network-alias",
            "pg",
            "--memory",
            "640m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "-p",
            f"127.0.0.1:{pg_port}:5432",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/pg-secrets/password",
            "-e",
            "POSTGRES_DB=aletheia",
            *mount("pg-secrets", "/run/pg-secrets", True),
            *mount("pg-data", "/var/lib/postgresql/data"),
            pg_image,
            "postgres",
            "-c",
            "ssl=on",
            "-c",
            "ssl_cert_file=/run/pg-secrets/pg.crt",
            "-c",
            "ssl_key_file=/run/pg-secrets/pg.key",
            "-c",
            "max_connections=24",
            "-c",
            "shared_buffers=32MB",
            "-c",
            "work_mem=2MB",
        ]
        state["containers"]["pg"] = docker(*args).decode().strip()
        save("postgres-started")
        idp_image = (
            docker("image", "inspect", "--format", "{{.Id}}", "quay.io/keycloak/keycloak:26.8.2")
            .decode()
            .strip()
        )
        idp_env = {
            **os.environ,
            "KC_BOOTSTRAP_ADMIN_USERNAME": "k14-admin",
            "KC_BOOTSTRAP_ADMIN_PASSWORD": material["idp_password"],
        }
        args = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            project + "-idp",
            "--label",
            "com.aletheia.k14.owner=" + run,
            "--label",
            "com.aletheia.k14.component=idp",
            "--network",
            state["network"],
            "--network-alias",
            idp_host,
            "--network-alias",
            "idp",
            "--memory",
            "640m",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "-p",
            f"127.0.0.1:{idp_port}:{idp_port}",
            "-e",
            "KC_BOOTSTRAP_ADMIN_USERNAME",
            "-e",
            "KC_BOOTSTRAP_ADMIN_PASSWORD",
            "-e",
            "JAVA_OPTS_APPEND=-Xms64m -Xmx256m -XX:MaxMetaspaceSize=192m -XX:MaxDirectMemorySize=32m",
            *mount("idp-data", "/opt/keycloak/data"),
            *mount("idp-secrets", "/run/keycloak", True),
            *mount("idp-secrets", "/opt/keycloak/data/import", True),
            idp_image,
            "start-dev",
            "--http-enabled=false",
            "--hostname=" + config["issuer"].split("/realms/")[0],
            "--hostname-strict=true",
            "--https-port=" + str(idp_port),
            "--https-certificate-file=/run/keycloak/idp.crt",
            "--https-certificate-key-file=/run/keycloak/idp.key",
            "--import-realm",
        ]
        state["containers"]["idp"] = docker(*args, env=idp_env).decode().strip()
        state["idp_image"] = idp_image
        save("dependencies-started")
        return

    if mode == "start-runtime":
        assert state["phase"] == "dependencies-started"
        args = [
            "run",
            "--rm",
            "--pull=never",
            "--name",
            project + "-initialize",
            "--label",
            "com.aletheia.k14.owner=" + run,
            "--network",
            state["network"],
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=134217728",
            "--entrypoint",
            "/opt/venv/bin/python",
            *tool_mount(),
            *mount("configs", "/run/config", True),
            *mount("admin-secrets", "/run/admin", True),
            *mount("objects", "/var/lib/aletheia"),
            *mount("clients", "/run/load"),
            "--mount",
            "type=bind,src=" + str(OUT) + ",dst=/evidence",
            state["image_id"],
            "/opt/k14/stage_node.py",
            "initialize",
        ]
        (OUT / "initialize.log").write_bytes(docker(*args))
        save("data-initialized")
        for role in ("api", "worker"):
            for index in range(2):
                label = role + str(index)
                args = [
                    "--memory",
                    "512m" if role == "api" else "640m",
                    "--cpus",
                    "0.75" if role == "api" else "1",
                    "--pids-limit",
                    "192",
                    "--ulimit",
                    "nofile=512:512",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/tmp:rw,size=134217728",
                    "--entrypoint",
                    "/opt/venv/bin/python",
                    "-e",
                    "K14_COMPONENT=" + label,
                    *tool_mount(),
                    *mount("configs", "/run/config", True),
                    *mount(role + "-secrets", "/run/secrets", True),
                    *mount("objects", "/var/lib/aletheia"),
                    *mount("metrics", "/run/metrics"),
                ]
                command = [
                    "run",
                    "-d",
                    "--pull=never",
                    "--name",
                    project + "-" + label,
                    "--label",
                    "com.aletheia.k14.owner=" + run,
                    "--label",
                    "com.aletheia.k14.component=" + label,
                    "--network",
                    state["network"],
                    "--network-alias",
                    label,
                    *args,
                    state["image_id"],
                    "/opt/k14/stage_node.py",
                    role,
                ]
                state["containers"][label] = docker(*command).decode().strip()
                save("started-" + label)
        command = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            project + "-ingress",
            "--label",
            "com.aletheia.k14.owner=" + run,
            "--label",
            "com.aletheia.k14.component=ingress",
            "--network",
            state["network"],
            "--network-alias",
            "ingress",
            "--memory",
            "96m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "32",
            "--ulimit",
            "nofile=128:128",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,size=16777216",
            "-p",
            f"127.0.0.1:{state['configuration']['app_port']}:{state['configuration']['app_port']}",
            "--entrypoint",
            "/opt/venv/bin/python",
            "-e",
            "K14_COMPONENT=ingress",
            *tool_mount(),
            *mount("configs", "/run/config", True),
            *mount("metrics", "/run/metrics"),
            state["image_id"],
            "/opt/k14/stage_node.py",
            "ingress",
        ]
        state["containers"]["ingress"] = docker(*command).decode().strip()
        save("runtime-started")
        return
    raise ValueError("Explicit dependency/runtime phase required")


if __name__ == "__main__":
    main(sys.argv[1])
