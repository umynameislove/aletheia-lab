"""Equal-projection ordinary SQLite materialization and process-exit recovery."""

import argparse
import hashlib
import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("static_amortized", "compact", "full")
CONTROL, MISSING = "__uncommitted_control__", "__never_captured__"


def encode(v):
    return json.dumps(
        v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def write_new(p, v):
    with p.open("x", encoding="utf-8") as out:
        out.write(json.dumps(v, ensure_ascii=False, indent=2) + "\n")


def emit(v):
    print(json.dumps(v, ensure_ascii=False), flush=True)


def storage(db):
    files = {}
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            s = p.stat()
            files[p.name] = {"logical": s.st_size, "allocated": s.st_blocks * 512}
    return {
        "files": files,
        **{k: sum(v[k] for v in files.values()) for k in ("logical", "allocated")},
    }


def read_inputs(p):
    with p.open("rb") as stream:
        raw = stream.read(4 * 1024**2 + 1)
    if len(raw) > 4 * 1024**2:
        raise ValueError("input exceeds bound")
    rows = json.loads(raw)["records"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty record list required")
    if any(
        not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] for r in rows
    ):
        raise ValueError("string IDs required")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("nonempty unique IDs required")
    for r in rows:
        if set(r) != {"id", "state", "record"} or r["id"] in (CONTROL, MISSING):
            raise ValueError("invalid envelope")
        if len(encode(r)) > 256 * 1024:
            raise ValueError("record exceeds bound")
    return raw, rows


def writer(arm, inputs, db):
    import resource

    start = time.monotonic_ns()
    raw, rows = read_inputs(inputs)
    input_done = time.monotonic_ns()
    if db.exists():
        raise ValueError("never overwrite a study DB")
    conn = sqlite3.connect(db)
    journal = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA fullfsync=ON")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    if journal != "wal" or conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
        raise ValueError("config unavailable")
    conn.execute(
        "CREATE TABLE states (key TEXT PRIMARY KEY, body BLOB NOT NULL, checksum TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE requests (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, state_key TEXT, body BLOB NOT NULL, checksum TEXT NOT NULL)"
    )
    conn.commit()
    initialized = time.monotonic_ns()
    prepare_ns = write_ns = 0
    for seq, row in enumerate(rows):
        t = time.monotonic_ns()
        whole = encode(row)
        if arm == "full":
            key, state, body = None, None, whole
        else:
            state = encode(row["state"])
            key = sha(state)
            body = encode({"id": row["id"], "record": row["record"]})
            if arm == "compact":
                state, body = zlib.compress(state), zlib.compress(body)
        checksum = sha(whole)
        prepared = time.monotonic_ns()
        if state is not None:
            found = conn.execute("SELECT body FROM states WHERE key=?", (key,)).fetchone()
            if found is not None and found[0] != state:
                raise ValueError("identity collision")
            conn.execute("INSERT OR IGNORE INTO states VALUES (?,?,?)", (key, state, sha(state)))
        conn.execute(
            "INSERT INTO requests VALUES (?,?,?,?,?)", (seq, row["id"], key, body, checksum)
        )
        conn.commit()
        committed = time.monotonic_ns()
        prepare_ns += prepared - t
        write_ns += committed - prepared
        emit({"event": "ack", "id": row["id"], "checksum": checksum, "commit_ns": committed})
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO requests VALUES (?,?,?,?,?)",
        (len(rows), CONTROL, None, b"not committed", "not committed"),
    )
    r = resource.getrusage(resource.RUSAGE_SELF)
    emit(
        {
            "event": "ready_for_kill",
            "input_sha256": sha(raw),
            "count": len(rows),
            "input_ns": input_done - start,
            "initialization_ns": initialized - input_done,
            "prepare_ns": prepare_ns,
            "write_commit_ns": write_ns,
            "writer_to_ready_ns": time.monotonic_ns() - start,
            "child_cpu_seconds": r.ru_utime + r.ru_stime,
            "child_maxrss_platform_units": r.ru_maxrss,
            "sqlite": sqlite3.sqlite_version,
            "journal_mode": journal,
            "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
            "fullfsync": conn.execute("PRAGMA fullfsync").fetchone()[0],
            "states": conn.execute("SELECT count(*) FROM states").fetchone()[0],
        }
    )
    while True:
        signal.pause()  # Parent kills; no clean close/checkpoint.


def recover(arm, db):
    start = time.monotonic_ns()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    output = []
    for _seq, rid, key, body, checksum in conn.execute("SELECT * FROM requests ORDER BY seq"):
        if arm == "full":
            row = json.loads(body)
        else:
            state, state_checksum = conn.execute(
                "SELECT body,checksum FROM states WHERE key=?", (key,)
            ).fetchone()
            if sha(state) != state_checksum:
                raise ValueError("state checksum")
            if arm == "compact":
                state, body = zlib.decompress(state), zlib.decompress(body)
            if sha(state) != key:
                raise ValueError("decoded state identity")
            row = {**json.loads(body), "state": json.loads(state)}
        if row["id"] != rid or sha(encode(row)) != checksum:
            raise ValueError("reconstruction checksum")
        output.append(row)
    missing = {
        i: conn.execute("SELECT id FROM requests WHERE id=?", (i,)).fetchone() is None
        for i in (CONTROL, MISSING)
    }
    conn.close()
    emit(
        {
            "integrity": integrity,
            "records": output,
            "missing_queries": missing,
            "child_recovery_ns": time.monotonic_ns() - start,
        }
    )


def execute(inputs, destination):
    checkout = HERE.parents[1]
    if destination.resolve() == checkout or checkout in destination.resolve().parents:
        raise ValueError("Keep generated DBs and logs outside the checkout")
    if os.name != "posix":
        raise RuntimeError("Owned SIGKILL recovery study requires POSIX")
    raw, rows = read_inputs(inputs)
    destination.mkdir()
    write_new(
        destination / "execution-inputs.json",
        {
            "input_path": str(inputs),
            "input_sha256": sha(raw),
            "input_bytes": len(raw),
            "script_sha256": sha(Path(__file__).read_bytes()),
            "public_method_sha256": sha(
                (HERE.parents[1] / "docs/serving-recovery-study.md").read_bytes()
            ),
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
            "planned_blocks": 5,
            "planned_owner_processes": 15,
            "record_count": len(rows),
            "orders": [list(ARMS[b % 3 :] + ARMS[: b % 3]) for b in range(5)],
            "scope": "Offline trace materialization/owner-exit recovery, not native serving overhead",
        },
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "en_US.UTF-8",
    }
    results = []
    for block in range(5):
        for arm in ARMS[block % 3 :] + ARMS[: block % 3]:
            folder = destination / f"{block}-{arm}"
            folder.mkdir()
            db = (folder / "collector.sqlite").resolve()
            result = {"block": block, "arm": arm, "planned": len(rows), "status": "failed"}
            acks = []
            with (folder / "writer.stderr").open("xb") as err:
                started = time.monotonic_ns()
                p = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        __file__,
                        "writer",
                        "--arm",
                        arm,
                        "--input",
                        str(inputs.resolve()),
                        "--db",
                        str(db),
                    ],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=err,
                )
                try:
                    ready = None
                    deadline = time.monotonic() + 30
                    pending = b""
                    fd = p.stdout.fileno()
                    os.set_blocking(fd, False)
                    while ready is None:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError("writer ready deadline")
                        if not select.select([fd], [], [], min(left, 1))[0]:
                            if p.poll() is not None:
                                raise RuntimeError("writer exited before ready")
                            continue
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            raise RuntimeError("writer output ended before ready")
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            item = json.loads(line)
                            if item["event"] == "ack":
                                acks.append({**item, "parent_received_ns": time.monotonic_ns()})
                            elif item["event"] == "ready_for_kill":
                                ready = item
                    result.update(
                        {
                            "acknowledgements": acks,
                            "writer": ready,
                            "parent_launch_to_ready_ns": time.monotonic_ns() - started,
                            "live_storage": storage(db),
                        }
                    )
                    if [a["id"] for a in acks] != [r["id"] for r in rows]:
                        raise ValueError("ACK census/order")
                    killed = time.monotonic_ns()
                    p.kill()
                    result["exit_code"] = p.wait(timeout=5)
                    p.stdout.close()
                    result["kill_wait_ns"] = time.monotonic_ns() - killed
                    if result["exit_code"] != -signal.SIGKILL:
                        raise ValueError("not SIGKILL")
                    result["after_kill_storage"] = storage(db)
                    restart = time.monotonic_ns()
                    child = subprocess.run(
                        [sys.executable, "-B", __file__, "recover", "--arm", arm, "--db", str(db)],
                        env=env,
                        capture_output=True,
                        timeout=30,
                    )
                    elapsed = time.monotonic_ns() - restart
                    (folder / "recovery.stdout").write_bytes(child.stdout)
                    (folder / "recovery.stderr").write_bytes(child.stderr)
                    if child.returncode:
                        raise RuntimeError("recovery failed")
                    answer = json.loads(child.stdout)
                    checking = time.monotonic_ns()
                    equal = len(answer["records"]) == len(rows) and all(
                        encode(a) == encode(b)
                        for a, b in zip(answer["records"], rows, strict=False)
                    )
                    if (
                        not equal
                        or answer["integrity"] != "ok"
                        or not all(answer["missing_queries"].values())
                    ):
                        raise ValueError("payload/integrity/negative mismatch")
                    result.update(
                        {
                            "status": "complete",
                            "recovered": len(rows),
                            "correct": len(rows),
                            "late": elapsed > 2_000_000_000,
                            "parent_recovery_ns": elapsed,
                            "child_recovery_ns": answer["child_recovery_ns"],
                            "reference_comparison_ns": time.monotonic_ns() - checking,
                            "negative_queries_missing": answer["missing_queries"],
                            "after_recovery_storage": storage(db),
                            "recovered_projection_sha256": sha(encode(answer["records"])),
                        }
                    )
                except Exception as exc:
                    result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
                    result.setdefault("acknowledgements", acks)
                finally:
                    if p.poll() is None:
                        p.kill()
                    p.wait(timeout=5)
                    p.stdout.close()
            write_new(folder / "result.json", result)
            results.append(result)
    summaries = []
    for arm in ARMS:
        group = [r for r in results if r["arm"] == arm]
        complete = [r for r in group if r["status"] == "complete"]
        accepted = sum(len(r.get("acknowledgements", [])) for r in group)
        on_time = sum(r["correct"] for r in complete if not r["late"])
        summaries.append(
            {
                "arm": arm,
                "planned_processes": 5,
                "completed_processes": len(complete),
                "offered": 5 * len(rows),
                "refused_or_not_admitted": 5 * len(rows) - accepted,
                "accepted": accepted,
                "accepted_but_unserved": accepted - on_time,
                "correct_on_time": on_time,
                "correct_but_late": sum(r["correct"] for r in complete if r["late"]),
                "parent_recovery_ns": [r["parent_recovery_ns"] for r in complete],
                "collector_bytes_after_kill": [
                    r["after_kill_storage"]["logical"] for r in complete
                ],
                "allocated_bytes_after_kill": [
                    r["after_kill_storage"]["allocated"] for r in complete
                ],
                "launch_to_ready_ns": [r["parent_launch_to_ready_ns"] for r in complete],
                "new_dataset_or_native_replications": 0,
            }
        )
    write_new(
        destination / "analysis.json",
        {
            "results": results,
            "summaries": summaries,
            "planned": 15,
            "complete": sum(r["status"] == "complete" for r in results),
            "input_reference_bytes_common": len(raw),
            "source_projection_sha256": sha(encode(rows)),
            "not_measured": [
                "native capture/serving/update/restart overhead",
                "manual preparation",
                "host/powerloss",
                "coldcache",
                "productiondurability",
            ],
        },
    )
    emit(
        {
            "complete": sum(r["status"] == "complete" for r in results),
            "planned": 15,
            "summaries": summaries,
        }
    )
    return all(r["status"] == "complete" and not r["late"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("writer", "recover", "execute"))
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode in ("writer", "recover") and (args.arm is None or args.db is None):
        parser.error("worker modes require --arm and --db")
    if args.mode == "writer":
        if args.input is None:
            parser.error("writer requires --input")
        writer(args.arm, args.input, args.db)
    elif args.mode == "recover":
        recover(args.arm, args.db)
    else:
        if args.input is None or args.output is None:
            parser.error("execute requires --input and --output")
        raise SystemExit(0 if execute(args.input, args.output) else 1)
