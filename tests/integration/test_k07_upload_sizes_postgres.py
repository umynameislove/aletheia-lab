"""S/M/L physical transfer-byte checks, distinct from scientific row/claim capacity."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

import pytest
from test_k07_upload_staging_postgres import _database, _digest, _manifest, _run

from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.transactions import TransactionOutcomeUnknown

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


def _physical_bundle(byte_size):
    # ZIP_STORED provides real physical transfer bytes rather than a tiny bomb.
    def make(padding):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
            archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
            archive.writestr("config.json", '{"synthetic":true}')
            archive.writestr("source.txt", padding)
        return output.getvalue()
    overhead = len(make(b""))
    line = b"synthetic source text\n"
    remaining = byte_size - overhead
    result = make(line * (remaining // len(line)) + b"x" * (remaining % len(line)))
    assert len(result) == byte_size
    return result


@pytest.mark.parametrize("mib", [1, 20, 200], ids=["S-1MiB", "M-20MiB", "L-200MiB"])
def test_real_physical_resumable_transfer_to_verified_source(mib):
    async def run():
        from pydantic import SecretStr

        async with _database() as (pool, _):
            payload = _physical_bundle(mib * 1024 * 1024)
            manifest, part_copies = _manifest(payload, part_size=4 * 1024 * 1024)
            part_count = len(part_copies)
            del part_copies
            started = time.monotonic()
            stage_times = {}
            progress = []
            resumed_received_bytes = None
            phase = {"name": "database_info", "part_number": None}
            query_timings, connection_timings, part_timings, waits, observer_errors = [], [], [], [], []
            database_info = {}
            executing = {"pid": None, "last_pid": None, "transaction_phase": "idle"}
            done = asyncio.Event()

            class TimedPool:
                @asynccontextmanager
                async def connection(self):
                    context_started = time.monotonic()
                    acquired = body_ended = None
                    body_returned = False
                    error_type = None
                    context_phase = dict(phase)
                    executing["transaction_phase"] = "pool_acquire"
                    try:
                        async with pool.connection() as conn:
                            acquired = time.monotonic()
                            executing.update(last_pid=conn.info.backend_pid, transaction_phase="body")
                            class Connection:
                                autocommit = False

                                async def execute(self, query, params=(), **kwargs):
                                    query_started = time.monotonic()
                                    query_error = None
                                    executing["pid"] = conn.info.backend_pid
                                    executing["transaction_phase"] = "sql"
                                    try:
                                        return await conn.execute(query, params, **kwargs)
                                    except BaseException as error:
                                        query_error = type(error).__name__
                                        raise
                                    finally:
                                        query_timings.append({**context_phase,
                                            "query": query.split("VALUES")[0][:110],
                                            "error_type": query_error,
                                            "binary": bool(kwargs.get("binary", False)),
                                            "source_publication": "INSERT INTO j01_sources" in query,
                                            "bound_binary_bytes": sum(len(value) for value in params
                                                if isinstance(value, bytes | bytearray | memoryview)),
                                            "bound_parameter_bytes": sum(len(value) if isinstance(value, bytes | bytearray | memoryview)
                                                else len(str(value).encode("utf-8")) for value in params),
                                            "seconds": time.monotonic() - query_started})
                                        executing["pid"] = None
                                        executing["transaction_phase"] = "body"
                            try:
                                yield Connection()
                                body_returned = True
                            finally:
                                body_ended = time.monotonic()
                                executing["pid"] = conn.info.backend_pid  # Observe COMMIT/rollback and pool return too.
                                executing["transaction_phase"] = "context_exit"
                    except BaseException as error:
                        error_type = type(error).__name__
                        raise
                    finally:
                        ended = time.monotonic()
                        executing["pid"] = None
                        connection_timings.append({**context_phase,
                            "pool_wait_seconds": (acquired or ended) - context_started,
                            "body_seconds": None if acquired is None or body_ended is None else body_ended - acquired,
                            "context_exit_seconds": None if body_ended is None else ended - body_ended,
                            "body_returned": body_returned, "context_returned": error_type is None,
                            "error_type": error_type, "last_transaction_phase": executing["transaction_phase"],
                            "seconds": ended - context_started})
                        executing["transaction_phase"] = "idle"

            async def observe():
                while not done.is_set():
                    if executing["pid"] is not None:
                        observed_phase, pid = dict(phase), executing["pid"]
                        transaction_phase = executing["transaction_phase"]
                        try:
                            async with asyncio.timeout(1), pool.connection() as observer:
                                row = await (await observer.execute("SELECT wait_event_type,wait_event,state,cardinality(pg_blocking_pids(pid)) "
                                    "FROM pg_stat_activity WHERE pid=%s", (pid,))).fetchone()
                                if row is not None:
                                    waits.append({**observed_phase, "transaction_phase": transaction_phase,
                                                  "elapsed": time.monotonic() - started, "wait_type": row[0],
                                                  "wait": row[1], "state": row[2], "blockers": row[3]})
                        except Exception as error:
                            observer_errors.append({**observed_phase, "error_type": type(error).__name__})
                    try:
                        async with asyncio.timeout(1):
                            await done.wait()
                    except TimeoutError:
                        pass

            observer = asyncio.create_task(observe())
            timed_pool = TimedPool()
            sources = PostgresSources(timed_pool, maximum_upload_bytes=200 * 1024 * 1024,
                maximum_expanded_bytes=512 * 1024 * 1024, maximum_entries=100000)
            receipt = None

            def pool_stats():
                names = ('pool_min', 'pool_max', 'pool_size', 'pool_available', 'requests_waiting',
                         'requests_num', 'requests_errors', 'connections_lost', 'returns_bad')
                return {name: value for name, value in pool.get_stats().items()
                        if name in names and isinstance(value, int | float)}

            async def failure_metadata():
                """Fixture-owner observation only: no FOR UPDATE, recovery or writes."""
                result = {"scope": "Read-only fixture-owner committed metadata observation; not a business ACK.",
                          "original_failure_retained": True, "automatic_replay": False}
                if receipt is None:
                    return {**result, "available": False, "reason": "upload_receipt_not_received"}
                try:
                    async with asyncio.timeout(1), pool.connection(timeout=.25) as conn:
                        await conn.execute('SET TRANSACTION READ ONLY')
                        await conn.execute("SET LOCAL statement_timeout='500ms'")
                        await conn.execute("SET LOCAL lock_timeout='100ms'")
                        diagnostic_pid = conn.info.backend_pid
                        row = await (await conn.execute('SELECT state,source_id FROM k07_uploads '
                            'WHERE workspace_id=%s AND upload_id=%s', ('a', receipt.upload_id))).fetchone()
                        progress_row = await (await conn.execute('SELECT count(*),COALESCE(sum(byte_size),0) '
                            'FROM k07_upload_parts WHERE workspace_id=%s AND upload_id=%s',
                            ('a', receipt.upload_id))).fetchone()
                        part_row = None
                        if phase['part_number'] is not None:
                            part_row = await (await conn.execute('SELECT byte_size,sha256 FROM k07_upload_parts '
                                'WHERE workspace_id=%s AND upload_id=%s AND part_number=%s',
                                ('a', receipt.upload_id, phase['part_number']))).fetchone()
                        backend = await (await conn.execute('SELECT state,wait_event_type,wait_event,cardinality(pg_blocking_pids(pid)) '
                            'FROM pg_stat_activity WHERE pid=%s', (executing['last_pid'],))).fetchone()
                    return {**result, "available": True, "upload_state": None if row is None else row[0],
                        "source_published_observed": row is not None and row[1] is not None,
                        "received_part_count_observed": progress_row[0], "received_bytes_observed": progress_row[1],
                        "target_part_checked": phase['part_number'] is not None,
                        "target_part_present_observed": None if phase['part_number'] is None else part_row is not None,
                        "target_part_bytes_observed": None if part_row is None else part_row[0],
                        "target_part_sha256_observed": None if part_row is None else part_row[1],
                        "original_backend_present": backend is not None,
                        "original_backend_reused_by_diagnostic": diagnostic_pid == executing['last_pid'],
                        "original_backend_state": None if backend is None else backend[0],
                        "original_backend_wait_type": None if backend is None else backend[1],
                        "original_backend_wait": None if backend is None else backend[2],
                        "original_backend_blockers": None if backend is None else backend[3],
                        "pool_stats_after_observation": pool_stats(),
                        "absence_is_not_a_rollback_or_commit_certificate": True}
                except Exception as error:
                    return {**result, "available": False, "error_type": type(error).__name__,
                            "outcome_remains_unresolved": True}

            def observations():
                return {"schema_version": "k07-transfer-observation/v2", "mib": mib,
                    "byte_size": len(payload), "part_count": part_count,
                    "phase": dict(phase), "confirmed_progress": progress, "resume_received_bytes": resumed_received_bytes,
                    "last_acknowledged_bytes": progress[-1] if progress else 0,
                    "current_part": part_timings[-1] if phase['part_number'] is not None and part_timings else None,
                    "stage_seconds": stage_times, "elapsed_seconds": time.monotonic() - started,
                    "part_timings": part_timings, "query_timings": query_timings,
                    "connection_timings": connection_timings, "wait_observations": waits,
                    "observer_errors": observer_errors, "database_info": database_info,
                    "pool_stats": pool_stats(),
                    "recovered_or_retried_unknown": False}

            def emit(suffix, report):
                report = {**report, "observation_kind": {"": "completed", "_READY": "ready", "_FAILURE": "failure"}[suffix]}
                print("K07_TRANSFER" + suffix + " " + json.dumps(report, sort_keys=True))
                if evidence := os.environ.get("K07_EVIDENCE_DIR"):
                    destination = Path(evidence)
                    destination.mkdir(parents=True, exist_ok=True)
                    with (destination / f"transfer-{mib}MiB{suffix.lower()}-result.json").open('x') as handle:
                        json.dump(report, handle, indent=2)
                        handle.write('\n')

            try:
                database_info.update(python_version=sys.version.split()[0], platform=sys.platform,
                    psycopg_version=version('psycopg'), psycopg_pool_version=version('psycopg-pool'))
                async with pool.connection() as conn:
                    names = ('server_version', 'synchronous_commit', 'fsync', 'full_page_writes', 'wal_sync_method', 'max_wal_size')
                    values = await (await conn.execute("SELECT current_setting('server_version'),current_setting('synchronous_commit'),"
                        "current_setting('fsync'),current_setting('full_page_writes'),current_setting('wal_sync_method'),current_setting('max_wal_size')")).fetchone()
                    database_info.update(zip(names, values, strict=True))
                phase.update(name="create", part_number=None)
                receipt = await sources.create_upload(credential_digest=_digest(), workspace_id="a",
                    request_key=SecretStr("synthetic-size-key-" + str(mib)), manifest=manifest)
                stage_times["create_seconds"] = time.monotonic() - started
                parts_started = time.monotonic()
                for index in range(part_count):
                    phase.update(name="part", part_number=index + 1)
                    part = payload[index * 4 * 1024 * 1024:(index + 1) * 4 * 1024 * 1024]
                    part_started = time.monotonic()
                    acknowledged = False
                    try:
                        status = await sources.put_upload_part(credential_digest=_digest(), workspace_id="a",
                            upload_id=receipt.upload_id, part_number=index + 1, payload=part)
                        assert status.received_bytes == min((index + 1) * 4 * 1024 * 1024, len(payload))
                        progress.append(status.received_bytes)
                        acknowledged = True
                    finally:
                        part_timings.append({"part_number": index + 1, "bytes": len(part),
                            "sha256": hashlib.sha256(part).hexdigest(), "expected_sha256": manifest.parts[index].sha256,
                            "seconds": time.monotonic() - part_started, "acknowledged": acknowledged})
                    if index == part_count // 2:
                        phase.update(name="resume", part_number=index + 1)
                        # Reconstructed repository proves progress is committed in PostgreSQL.
                        restarted = PostgresSources(timed_pool, maximum_upload_bytes=200 * 1024 * 1024,
                            maximum_expanded_bytes=512 * 1024 * 1024, maximum_entries=100000)
                        resumed = await restarted.upload_status(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
                        assert resumed == status
                        resumed_received_bytes = resumed.received_bytes
                stage_times["parts_seconds"] = time.monotonic() - parts_started
                phase.update(name="complete", part_number=None)
                complete_started = time.monotonic()
                ready = await sources.complete_upload(credential_digest=_digest(), workspace_id="a", upload_id=receipt.upload_id)
                stage_times["complete_seconds"] = time.monotonic() - complete_started
                assert ready.state == "ready" and ready.sha256 == hashlib.sha256(payload).hexdigest()
                emit("_READY", {**observations(), "sha256": ready.sha256, "state": ready.state})
                phase.update(name="read", part_number=None)
                read_started = time.monotonic()
                saved = await sources.read(credential_digest=_digest(), workspace_id="a", source_id=ready.source_id)
                assert len(saved) == len(payload) and hashlib.sha256(saved).hexdigest() == ready.sha256
                byte_reads = [query for query in query_timings if query["query"].startswith((
                    "SELECT part_number,byte_size,sha256,payload", "SELECT payload,sha256"))]
                assert len(byte_reads) == 2 and all(query["binary"] for query in byte_reads)
                publications = [query for query in query_timings if query["source_publication"]]
                assert len(publications) == 1 and publications[0]["bound_binary_bytes"] == 0
                assert publications[0]["bound_parameter_bytes"] < 64 * 1024
                stage_times["read_seconds"] = time.monotonic() - read_started
                emit("", {**observations(), "sha256": ready.sha256, "stored_sha256": hashlib.sha256(saved).hexdigest(),
                    "ready": ready.state == "ready", "total_seconds": time.monotonic() - started})
            except BaseException as error:
                try:
                    emit("_FAILURE", {**observations(), "error_type": type(error).__name__,
                        "transaction_outcome_unknown": isinstance(error, TransactionOutcomeUnknown)
                            or 'transaction_outcome_unknown' in getattr(error, '__notes__', ()),
                        "read_only_failure_metadata": await failure_metadata()})
                except Exception as evidence_error:
                    print("K07_TRANSFER_EVIDENCE_FAILURE " + json.dumps({"error_type": type(evidence_error).__name__}))
                raise
            finally:
                done.set()
                await observer
    _run(run())
