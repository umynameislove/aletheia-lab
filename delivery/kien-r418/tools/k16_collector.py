"""Private collector and acknowledged test receiver for the K16 operator exercise."""

import asyncio
import hmac
import json
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aletheia_lab.hosted.operational_alerts import AlertEvaluator

_LABEL_FIELDS = ("kind", "operation", "method", "status", "outcome")
_SOURCES = {"api0", "api1", "worker0", "worker1", "queue"}
_ALERTS = {"queue_age", "pool_wait", "event_loop_lag", "http_burn", "worker_error", "log_drops"}
_SECRET = Path("/run/route/token")
_RECEIVER = "http://k16-receiver:18080/alert"


def write_record(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")


def receive(output):
    token = "Bearer " + _SECRET.read_text().strip()
    seen = set()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(3)
            if self.path != "/alert" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), token
            ):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024:
                    raise ValueError
                value = json.loads(self.rfile.read(size))
                if (
                    set(value) != {"code", "state", "source", "owner", "sequence"}
                    or value["source"] not in _SOURCES
                    or value["code"] not in _ALERTS
                    or value["state"] not in {"firing", "resolved"}
                    or value["owner"] != "kien-k16-staging"
                    or not isinstance(value["sequence"], int)
                ):
                    raise ValueError
            except (TypeError, ValueError, TimeoutError):
                self.send_error(400)
                return
            key = tuple(value[field] for field in sorted(value))
            if key not in seen:
                write_record(output, {**value, "received_utc": datetime.now(UTC).isoformat()})
                seen.add(key)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    with HTTPServer(("0.0.0.0", 18080), Receiver) as server:
        print("K16_RECEIVER_READY", flush=True)
        server.serve_forever()


async def collect(output):
    import psycopg

    from aletheia_lab.hosted.operator_observation import operator_snapshot

    forbidden = (
        "/run/secrets/database_api",
        "/run/secrets/database_worker",
        "/run/load/actors.jsonl",
        "/var/lib/aletheia/catalog",
        "/var/run/docker.sock",
    )
    assert not any(Path(path).exists() for path in forbidden)
    config = json.loads(Path("/run/config/stage.json").read_text())
    release, schema = config["release_identity"], config["schema"]
    dsn = Path("/run/collector/dsn").read_text().strip()
    token = "Bearer " + _SECRET.read_text().strip()
    try:
        urlopen(Request(_RECEIVER, data=b"{}", method="POST"), timeout=3)
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("test receiver accepted an unauthorized request")
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        attrs = await (
            await conn.execute(
                "SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,"
                "pg_has_role(current_user,'pg_read_all_stats','member'),"
                "current_setting('default_transaction_read_only') FROM pg_roles WHERE rolname=current_user"
            )
        ).fetchone()
        assert attrs == (False, False, False, False, False, "on")
        denied = {}
        for key, command in {
            "mutate_jobs": "DELETE FROM j01_jobs WHERE false",
            "read_job_payload": "SELECT * FROM j01_jobs LIMIT 0",
            "read_identity": "SELECT subject FROM hosted_identity_bindings LIMIT 0",
        }.items():
            try:
                async with conn.transaction():
                    await conn.execute("SET TRANSACTION READ WRITE")
                    await conn.execute(command)
            except psycopg.errors.InsufficientPrivilege as error:
                denied[key] = error.sqlstate
            else:
                raise AssertionError(f"unexpected collector permission: {key}")
        write_record(
            output, {"permissions": denied, "default_read_only": True, "receiver_denied": 403}
        )
        print(json.dumps({"ready": True}), flush=True)
        evaluators = {source: AlertEvaluator() for source in _SOURCES}
        delivered_active = {source: set() for source in _SOURCES}
        for sequence, line in enumerate(sys.stdin):
            frame = json.loads(line)
            observed = time.monotonic()
            if frame["source"] == "queue":
                async with conn.transaction():
                    snapshot = await operator_snapshot(conn, schema)
                gauges = {"queue_oldest_seconds": snapshot["queue_oldest_seconds"]}
                raw = {"process_id": "queue", "release_id": release, "gauges": gauges}
                frame["pending_count"] = snapshot["pending_count_lower_bound"]
                frame["pending_count_exact"] = snapshot["pending_count_exact"]
                frame["queue_oldest_seconds"] = gauges["queue_oldest_seconds"]
            else:
                raw = frame.pop("snapshot")
                assert raw["event"] == "metric.snapshot" and raw["release_id"] == release
                series = [tuple(item[key] for key in _LABEL_FIELDS) for item in raw["items"]]
                assert len(set(series)) == len(series)
                frame["series"] = series
                frame["gauges"] = raw["gauges"]
                frame["log_buffer"] = raw["log_buffer"]
            source = frame["source"]
            verdict = evaluators[source].evaluate(
                raw, observed_at=observed, log_dropped=raw.get("log_buffer", {}).get("dropped", 0)
            )
            active = {item["code"] for item in verdict["active"]}
            previous = delivered_active[source]
            if (
                verdict["window"]["http_requests"] < evaluators[source].policy.minimum_http_requests
                and "http_burn" in previous
            ):
                active.add("http_burn")
            delivered = []
            for code in sorted(active ^ previous):
                if code in previous and verdict["window"]["baseline_reset"]:
                    active.add(code)
                    continue
                transition = {"code": code, "state": "firing" if code in active else "resolved"}
                message = {
                    **transition,
                    "source": source,
                    "owner": "kien-k16-staging",
                    "sequence": sequence,
                }
                request = Request(
                    _RECEIVER,
                    data=json.dumps(message).encode(),
                    method="POST",
                    headers={"Authorization": token, "Content-Type": "application/json"},
                )
                with urlopen(request, timeout=3) as response:
                    assert response.status == 204
                    delivered.append({**message, "http_status": response.status})
            delivered_active[source] = active
            frame.update(verdict=verdict, delivered=delivered, utc=datetime.now(UTC).isoformat())
            write_record(output, frame)
            print(json.dumps(frame), flush=True)


if __name__ == "__main__":
    mode, filename = sys.argv[1:]
    if mode == "receive":
        receive(Path(filename))
    elif mode == "collect":
        asyncio.run(collect(Path(filename)))
    else:
        raise SystemExit("explicit K16 receiver or collector mode required")
