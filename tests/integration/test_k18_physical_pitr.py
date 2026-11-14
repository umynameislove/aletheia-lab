"""Opt-in real basebackup/WAL point-in-time recovery on two owned native clusters.

No Docker or existing database is touched. This intentionally does not run from
K18_TEST_DATABASE_URL: opt-in, explicit PG16 binaries and disk space are required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from test_k05_legacy_import_postgres import _source
from test_k05_native_results_postgres import _case, _initial, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import verify
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.recovery_checkpoint import apply_checkpoint, capture_checkpoint
from aletheia_lab.hosted.recovery_points import file_digest
from aletheia_lab.hosted.retention import PostgresRetention
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("K18_TEST_PITR") != "1"
        or not os.environ.get("K18_TEST_PG_BIN")
        or sys.platform == "win32",
        reason="Explicit opt-in private native PG16 PITR fixture and binaries required",
    ),
]

_CAP = 4 * 1024**3
_PORTS = (50814, 50815)


def _quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


class _Clusters:
    """Only owns its fresh UUID directory and matching postmaster PID files."""

    def __init__(self, parent: Path, pg_bin: Path):
        self.root = parent / ("k18-pitr-" + uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.bin = pg_bin.resolve(strict=True)
        self.archive = self.root / "wal-archive"
        self.archive.mkdir(mode=0o700)
        self.source = self.root / "source"
        self.base = self.root / "base-backup"
        self.restored = self.root / "restored"
        self.password = secrets.token_urlsafe(32)
        self.pwfile = self.root / "init-password"
        self.pwfile.write_text(self.password + "\n")
        self.pwfile.chmod(0o600)
        self.pgpass = self.root / "pgpass"
        self.pgpass.write_text(
            "".join(f"127.0.0.1:{port}:*:k18_owner:{self.password}\n" for port in _PORTS)
        )
        self.pgpass.chmod(0o600)
        self.commands: list[dict[str, object]] = []
        self.started: set[Path] = set()
        self.peak_bytes = 0

    def budget(self) -> None:
        allocated = sum(
            path.stat().st_size
            for path in self.root.parent.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        self.peak_bytes = max(self.peak_bytes, allocated)
        if allocated > _CAP or shutil.disk_usage(self.root).free < _CAP:
            raise RuntimeError("Owned PITR fixture disk budget or free-space reserve exceeded.")

    def env(self) -> dict[str, str]:
        env = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
        return dict(env, PGPASSFILE=str(self.pgpass), PGCONNECT_TIMEOUT="5", LC_ALL="C", LANG="C")

    def command(self, binary: str, args: list[str], *, timeout: float = 120) -> None:
        started = time.monotonic()
        with (self.root / "commands.log").open("ab") as log:
            result = subprocess.run(
                [str(self.bin / binary), *args],
                env=self.env(),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        self.commands.append(
            {
                "binary": binary,
                "returncode": result.returncode,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        if result.returncode:
            raise RuntimeError(
                "Private PITR PostgreSQL command failed; inspect owned evidence logs."
            )

    def initialize(self) -> None:
        self.budget()
        for port in _PORTS:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        self.command(
            "initdb",
            [
                "-D",
                str(self.source),
                "--username=k18_owner",
                "--auth-host=scram-sha-256",
                "--auth-local=scram-sha-256",
                "--pwfile=" + str(self.pwfile),
                "--encoding=UTF8",
                "--locale=C",
            ],
        )
        archive_helper = Path(__file__).with_name("k18_wal_copy.py").resolve()
        archive_command = " ".join(
            (
                shlex.quote(sys.executable),
                shlex.quote(str(archive_helper)),
                '"%p"',
                shlex.quote(str(self.archive)),
                '"%f"',
            )
        )
        config = (
            "\nlisten_addresses='127.0.0.1'\nport=50814\nunix_socket_directories=''\n"
            "shared_buffers='32MB'\nmax_connections=12\nwal_level=replica\nmax_wal_senders=3\n"
            "max_wal_size='128MB'\nmin_wal_size='32MB'\narchive_mode=on\n"
            "archive_timeout=60\nlogging_collector=off\nlog_min_messages=warning\n"
            "archive_command=" + _quoted(archive_command) + "\n"
        )
        with (self.source / "postgresql.conf").open("a") as handle:
            handle.write(config)
        self.start(self.source)
        self.budget()

    def start(self, data: Path) -> None:
        if (
            data not in (self.source, self.restored)
            or not (data / "PG_VERSION").read_text().strip() == "16"
        ):
            raise RuntimeError("Only owned PG16 data directories may start.")
        self.command(
            "pg_ctl",
            [
                "-D",
                str(data),
                "-l",
                str(self.root / (data.name + ".log")),
                "-w",
                "-t",
                "45",
                "start",
            ],
            timeout=60,
        )
        self.started.add(data)
        self._owned_pid(data)

    def _owned_pid(self, data: Path) -> None:
        rows = (data / "postmaster.pid").read_text().splitlines()
        expected = 50814 if data == self.source else 50815
        if (
            len(rows) < 4
            or Path(rows[1]).resolve() != data.resolve()
            or int(rows[3]) != expected
            or int(rows[0]) <= 1
        ):
            raise RuntimeError("Private cluster PID/data-directory/port ownership did not match.")

    def dsn(self, port: int) -> str:
        value = f"postgresql://k18_owner@127.0.0.1:{port}/postgres?" + urlencode(
            {"passfile": str(self.pgpass), "connect_timeout": "5"}
        )
        path = self.root / ("dsn-" + str(port))
        path.write_text(value)
        path.chmod(0o600)
        return value

    def basebackup(self) -> None:
        self.budget()
        self.command(
            "pg_basebackup",
            [
                "-D",
                str(self.base),
                "-h",
                "127.0.0.1",
                "-p",
                "50814",
                "-U",
                "k18_owner",
                "-w",
                "-X",
                "stream",
                "--checkpoint=fast",
                "--no-password",
            ],
            timeout=180,
        )
        self.budget()

    def recover(self, lsn: str) -> None:
        if (
            not all(part and all(c in "0123456789ABCDEF" for c in part) for part in lsn.split("/"))
            or len(lsn.split("/")) != 2
        ):
            raise RuntimeError("Unexpected trusted PostgreSQL LSN.")
        shutil.copytree(self.base, self.restored)
        restore_command = "cp " + shlex.quote(str(self.archive)) + '/%f "%p"'
        # Appended values override the copied source configuration. Never connect
        # to a primary; recovery must stop at the requested local WAL position.
        with (self.restored / "postgresql.auto.conf").open("a") as handle:
            handle.write(
                "\nport=50815\narchive_mode=off\narchive_command=''\n"
                "restore_command="
                + _quoted(restore_command)
                + "\nrecovery_target_lsn="
                + _quoted(lsn)
                + "\n"
                "recovery_target_inclusive=true\nrecovery_target_action=promote\n"
            )
        (self.restored / "recovery.signal").touch(mode=0o600)
        self.start(self.restored)
        self.budget()

    def cleanup(self) -> None:
        failures = []
        for data in (self.restored, self.source):
            if data in self.started or (data / "postmaster.pid").exists():
                try:
                    self._owned_pid(data)
                    self.command(
                        "pg_ctl",
                        ["-D", str(data), "-m", "fast", "-w", "-t", "30", "stop"],
                        timeout=45,
                    )
                except Exception:
                    failures.append(data.name)
        if failures:
            raise RuntimeError("Owned PITR cluster stop failed; private directory preserved.")
        self.started.clear()


async def _wait_archive(cluster: _Clusters, wal_name: str) -> None:
    async with asyncio.timeout(45):
        while not (cluster.archive / wal_name).is_file():
            cluster.budget()
            await asyncio.sleep(0.1)


async def _fresh_fixture_session(pool, user: str, context: str) -> SecretStr:
    # Synthetic native fixture provisioning after checkpoint invalidation, never
    # represented as an actual IdP login or a production session-issue workflow.
    digest = hashlib.sha256((context + "-synthetic").encode()).hexdigest()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',"
            "statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
            "statement_timestamp()+interval '28000 seconds',0,0)",
            (context, user),
        )
        await conn.execute(
            "INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)", (digest, context)
        )
    return SecretStr(digest)


def test_physical_basebackup_wal_target_preserves_acknowledged_delete_job_and_latest_authority(
    tmp_path, monkeypatch
):
    cluster = _Clusters(tmp_path, Path(os.environ["K18_TEST_PG_BIN"]))
    evidence = Path(os.environ["K18_EVIDENCE_DIR"]) if os.environ.get("K18_EVIDENCE_DIR") else None
    report: dict[str, object] = {
        "format": "k18-native-physical-pitr/v1",
        "completed": False,
        "scope": "PG16 native basebackup + archived WAL LSN recovery + object closure + latest authenticated authority/deletions. No power-loss, real IdP, Docker or production capacity claim.",
    }
    try:
        cluster.initialize()
        source_dsn = cluster.dsn(50814)
        restored_dsn = cluster.dsn(50815)
        monkeypatch.setenv("K05_TEST_DATABASE_URL", source_dsn)
        (tmp_path / "business").mkdir()
        (tmp_path / "legacy").mkdir()
        legacy, original, _ = _source(tmp_path / "legacy")
        key = b"synthetic-k18-physical-checkpoint-signing-key-not-production"

        async def scenario():
            async with _case(tmp_path / "business") as case:
                _, native_result = await _initial(case)
                importer = LegacyImporter(case.pool, case.objects)
                for workspace, credential, owner in [
                    ("a", case.a, "owner_a"),
                    ("b", case.b, "owner_b"),
                ]:
                    await importer.import_project(
                        legacy,
                        ImportTarget(legacy.project_id, workspace, owner),
                        credential_digest=credential,
                        batch_id="k18-pitr-shared",
                    )
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                    await migrate_reviews(conn, schema)
                    await conn.execute("CREATE TABLE k18_pitr_witness(value text PRIMARY KEY)")
                    revision = (
                        await (
                            await conn.execute(
                                "SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s",
                                (legacy.project_id,),
                            )
                        ).fetchone()
                    )[0]
                async with case.pool.connection() as conn:
                    base_hashes = {
                        row[0]
                        for row in await (
                            await conn.execute("SELECT DISTINCT object_sha256 FROM k05_records")
                        ).fetchall()
                    }
                await asyncio.to_thread(cluster.basebackup)
                receipt = await PostgresProjectMutations(case.pool, case.catalog).delete(
                    credential_digest=case.a,
                    workspace_id="a",
                    project_id=legacy.project_id,
                    expected_revision=revision,
                )
                async with case.pool.connection() as conn:
                    tombstone_digest = (
                        await (
                            await conn.execute(
                                "SELECT object_sha256 FROM k05_records WHERE workspace_id='a' AND record_id=%s",
                                (receipt["tombstone_id"],),
                            )
                        ).fetchone()
                    )[0]
                assert tombstone_digest not in base_hashes
                jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog, execution=True)
                job = await jobs.enqueue(
                    credential_digest=case.a,
                    workspace_id="a",
                    operation="preview",
                    request_key=SecretStr("k18-physical-acknowledged-job"),
                    request={"source_id": case.receipt.source_id},
                )
                lease = SecretStr("d" * 64)
                claim = await jobs.claim(
                    lease_digest=lease, lease_seconds=30, operations=("preview",)
                )
                assert claim and claim.job_id == job.job_id
                assert await jobs.complete(
                    claim=claim, lease_digest=lease, result={"synthetic_preview_ack": True}
                )
                assert (
                    await jobs.status(credential_digest=case.a, workspace_id="a", job_id=job.job_id)
                ).status == "succeeded"
                async with case.pool.connection() as conn:
                    await conn.execute(
                        "UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'"
                    )
                retention = PostgresRetention(case.pool, case.catalog)
                checkpoint_path = tmp_path / "latest-checkpoint"
                checkpoint = await capture_checkpoint(
                    retention, checkpoint_path, schema=schema, signing_key=key
                )
                # Freeze an exact recovery cut only AFTER both acknowledged effects.
                async with case.pool.connection() as conn:
                    target_lsn = (
                        await (
                            await conn.execute(
                                "SELECT pg_create_restore_point('k18_acknowledged_business_cut')"
                            )
                        ).fetchone()
                    )[0]
                    target_wal = (
                        await (
                            await conn.execute("SELECT pg_walfile_name(%s::pg_lsn)", (target_lsn,))
                        ).fetchone()
                    )[0]
                async with case.pool.connection() as conn:
                    await conn.execute(
                        "INSERT INTO k18_pitr_witness VALUES('later_than_requested_target')"
                    )
                async with case.pool.connection() as conn:
                    await conn.execute("SELECT pg_switch_wal()")
                await _wait_archive(cluster, target_wal)
                # The object archive must include objects committed after basebackup.
                # Hold the same GC/backup closure barrier while validating and copying.
                from aletheia_lab.hosted.recovery_points import BACKUP_ADVISORY_KEY

                restored_store_root = tmp_path / "recovered-objects"
                archive_store_root = tmp_path / "object-archive"
                async with case.pool.connection() as conn:
                    await conn.execute("SELECT pg_advisory_lock(%s)", (BACKUP_ADVISORY_KEY,))
                    try:
                        rows = await (
                            await conn.execute(
                                "SELECT DISTINCT object_sha256 FROM k05_records UNION SELECT DISTINCT sha256 FROM k05_object_references"
                            )
                        ).fetchall()
                        hashes = {
                            row[0]: file_digest(case.objects.store.path(row[0])) for row in rows
                        }
                        assert all(expected == actual for expected, actual in hashes.items())
                        assert hashes[tombstone_digest] == tombstone_digest
                        await asyncio.to_thread(
                            shutil.copytree, case.objects.store.root, archive_store_root
                        )
                    finally:
                        await conn.execute("SELECT pg_advisory_unlock(%s)", (BACKUP_ADVISORY_KEY,))
                start = time.monotonic()
                await asyncio.to_thread(shutil.copytree, archive_store_root, restored_store_root)
                restored_store = FilesystemObjectStore(restored_store_root)
                assert all(file_digest(restored_store.path(digest)) == digest for digest in hashes)
                await asyncio.to_thread(cluster.recover, str(target_lsn))
                scoped = make_conninfo(restored_dsn, options="-csearch_path=" + schema)
                async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                    await pool.wait(timeout=10)
                    catalog = PostgresCatalog(pool, PostgresObjects(pool, restored_store))
                    async with pool.connection() as conn:
                        assert await verify(conn, schema)
                        # Hot standby can accept read-only connections before promotion.
                        async with asyncio.timeout(30):
                            while await (
                                await conn.execute("SELECT pg_is_in_recovery()")
                            ).fetchone() != (False,):
                                await asyncio.sleep(0.1)
                        assert await (
                            await conn.execute("SELECT count(*) FROM k18_pitr_witness")
                        ).fetchone() == (0,)
                        assert await (
                            await conn.execute(
                                "SELECT state,result FROM j01_jobs WHERE workspace_id='a' AND job_id=%s",
                                (job.job_id,),
                            )
                        ).fetchone() == ("succeeded", {"synthetic_preview_ack": True})
                        assert await (
                            await conn.execute(
                                "SELECT deletion_tombstone_id FROM k05_projects WHERE workspace_id='a' AND project_id=%s",
                                (legacy.project_id,),
                            )
                        ).fetchone() == (receipt["tombstone_id"],)
                        closure = await (
                            await conn.execute(
                                "SELECT DISTINCT object_sha256 FROM k05_records UNION SELECT DISTINCT sha256 FROM k05_object_references"
                            )
                        ).fetchall()
                        assert all(
                            file_digest(restored_store.path(digest)) == digest
                            for (digest,) in closure
                        )
                    outcome = await apply_checkpoint(
                        PostgresRetention(pool, catalog),
                        checkpoint_path,
                        schema=schema,
                        signing_key=key,
                        expected_manifest_sha256=checkpoint["manifest_sha256"],
                    )
                    assert outcome["restored_sessions_revoked"]
                    async with pool.connection() as conn:
                        assert await (
                            await conn.execute(
                                "SELECT active FROM j01_memberships WHERE workspace_id='a' AND user_id='owner_b'"
                            )
                        ).fetchone() == (False,)
                    with pytest.raises(AuthorizationDenied):
                        await catalog.read_record(
                            credential_digest=case.a,
                            workspace_id="a",
                            project_id=legacy.project_id,
                            record_id=original["result"]["id"],
                        )
                    with pytest.raises(AuthorizationDenied):
                        await catalog.read_record(
                            credential_digest=case.a,
                            workspace_id="a",
                            project_id=native_result.project_id,
                            record_id=native_result.record_id,
                        )
                    fresh_a = await _fresh_fixture_session(pool, "owner_a", "k18-fresh-a")
                    fresh_b = await _fresh_fixture_session(pool, "owner_b", "k18-fresh-b")
                    with pytest.raises(AuthorizationDenied):
                        await catalog.read_record(
                            credential_digest=fresh_a,
                            workspace_id="a",
                            project_id=legacy.project_id,
                            record_id=original["result"]["id"],
                        )
                    shared = await catalog.read_record(
                        credential_digest=fresh_b,
                        workspace_id="b",
                        project_id=legacy.project_id,
                        record_id=original["result"]["id"],
                    )
                    assert shared.record_id == original["result"]["id"]
                    live = await catalog.read_record(
                        credential_digest=fresh_a,
                        workspace_id="a",
                        project_id=native_result.project_id,
                        record_id=native_result.record_id,
                    )
                    assert live == case.initial
                    from aletheia_lab.hosted.postgres_sources import PostgresSources

                    restored_sources = PostgresSources(
                        pool,
                        maximum_upload_bytes=10000,
                        maximum_expanded_bytes=20000,
                        maximum_entries=10,
                    )
                    assert (
                        await restored_sources.read(
                            credential_digest=fresh_a,
                            workspace_id="a",
                            source_id=case.receipt.source_id,
                        )
                        == case.payload
                    )
                    with pytest.raises(AuthorizationDenied):
                        await restored_sources.read(
                            credential_digest=fresh_b,
                            workspace_id="a",
                            source_id=case.receipt.source_id,
                        )
                    elapsed = time.monotonic() - start
                    assert elapsed < 1800
                report.update(
                    completed=True,
                    source_postgresql_major=16,
                    restore_target_lsn=str(target_lsn),
                    latest_checkpoint_sha256=checkpoint["manifest_sha256"],
                    object_count=len(hashes),
                    post_base_tombstone_in_object_closure=True,
                    acknowledged_effects_checked=2,
                    acknowledged_effects_lost=0,
                    rpo_seconds=0,
                    rpo_scope="No loss of the two acknowledged delete/job commits before the explicitly selected local LSN. Not a continuous production RPO claim.",
                    restore_through_authenticated_checkpoint_and_actual_consumer_seconds=elapsed,
                    later_witness_absent=True,
                    old_sessions_denied=True,
                    latest_membership_revocation_preserved=True,
                    shared_project_and_raw_source_preserved=True,
                    synthetic_fresh_fixture_sessions=True,
                    runtime_grants_issued=False,
                    object_sha256_inventory=hashes,
                )

        _run(scenario())
    finally:
        cluster.cleanup()
        cluster.budget()
        report["commands"] = cluster.commands
        report["peak_owned_cluster_bytes"] = cluster.peak_bytes
        report["native_clusters_stopped"] = True
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "physical-pitr.json").write_text(json.dumps(report, indent=2) + "\n")
            for name in ("commands.log", "source.log", "restored.log"):
                if (cluster.root / name).is_file():
                    shutil.copyfile(cluster.root / name, evidence / ("physical-" + name))
        shutil.rmtree(cluster.root)
