"""Summarize the measured J06.02 waterfall and one retained browser trace."""
import ast
import csv
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_csv(path, rows):
    assert rows
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def dumped(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mean(values):
    return round(statistics.mean(values), 6)


def group(rows, *keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    return groups


def redact_route(route):
    path = route.split("?", 1)[0]
    path = re.sub(r"/workspaces/[^/]+", "/workspaces/{workspace}", path)
    path = re.sub(r"/jobs/[^/]+", "/jobs/{job}", path)
    path = re.sub(r"/uploads/[^/]+", "/uploads/{upload}", path)
    path = re.sub(r"/imports/(?!preview(?:/|$))[^/]+", "/imports/{preview}", path)
    path = re.sub(r"/results/[^/]+", "/results/{result}", path)
    return re.sub(r"/parts/\d+", "/parts/{part}", path)


def trace_time(value):
    stamp, offset = value.rsplit(" ", 1)
    return datetime.fromisoformat(stamp + offset + ":00")


def browser(source):
    inputs = {
        "a-upload-preview.json": (("upload_at", "upload_preview"),),
        "b-mapping-analyze-reload.json": (("confirm_at", "confirm"), ("analyze_at", "analyze"),
                                         ("reload_at", "reload_and_revalidation")),
        "c-layout.json": (),
    }
    traces, hashes = [], {}
    for name, boundaries in inputs.items():
        path = source / name
        data = read_json(path)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for observation in data["api"]:
            start = trace_time(observation["at"])
            phase = "layout" if not boundaries else "before_action"
            for marker, name_for_phase in boundaries:
                if start >= trace_time(data[marker]):
                    phase = name_for_phase
            traces.append({"window": phase, "method": observation["m"],
                "path_template": redact_route(observation["path"]),
                "status": observation.get("status") or "no_response", "ms": observation.get("ms")})
    assert len(traces) == 50
    assert sum(row["status"] == "no_response" for row in traces) == 4
    output = []
    for (window, method, path, status), values in group(traces, "window", "method", "path_template", "status").items():
        observed = [item["ms"] for item in values if isinstance(item["ms"], (int, float))]
        output.append({"window": window, "method": method, "path_template": path,
                       "status": status, "requests": len(values), "mean_http_ms": mean(observed) if observed else ""})
    return output, hashes


def large_failure(out, proof):
    receipt = out / "j06-waterfall-l-final.xml"
    element = ElementTree.parse(receipt).find(".//failure")
    assert element is not None
    message = element.attrib["message"].splitlines()[0]
    state = ast.literal_eval(message.removeprefix("AssertionError: "))
    assert state["operation"] == "confirm" and state["status"] == "failed"
    pending = [row for row in proof["requests"] if row["sample"] == 2]
    assert len(proof["actions"]) == 1 and proof["actions"][0]["action"] == "upload_preview"
    assert pending[0]["operation"] == "P09.confirm" and pending[0]["status"] == 202
    assert all(row["operation"] == "P20.status" and row["status"] == 200 for row in pending[1:])
    attempts = []
    telemetry = out / "backend-l-final/worker.safe.jsonl"
    for line in telemetry.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("event") == "job.execution" and event.get("job_id") == state["job_id"]:
            attempts.append({key: event[key] for key in ("outcome", "duration_ms")})
    assert len(attempts) == state["attempt"] == state["max_attempts"]
    assert all(attempt["outcome"] == "error" for attempt in attempts)
    terminal = datetime.fromisoformat(state["terminal_at"]) - datetime.fromisoformat(state["created_at"])
    return {
        "size": "L", "rows": 100000, "action": "confirm_snapshot", "terminal": state["status"],
        "worker_attempts": attempts, "server_created_to_terminal_seconds": terminal.total_seconds(),
        "job_ack_http_seconds": pending[0]["seconds"], "http_requests": len(pending),
        "status_polls_included": len(pending) - 1,
        "request_body_bytes": sum(row["request_body_bytes"] for row in pending),
        "response_body_bytes": sum(row["response_body_bytes"] for row in pending),
        "api_sql_calls_during_http": sum(row["api"]["statements"] for row in pending),
        "worker_timeout_seconds_in_fixture": 90,
        "scope": "One observed failed confirm. API SQL here covers each HTTP request. No worker delta for this failed journey. Later L actions were blocked by confirm.",
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (receipt, telemetry)},
    }


def consolidate(root, j01):
    out = root / "evidence"
    backend = out / "backend/waterfall.json"
    runs = {"SM": read_json(backend)}
    large = out / "backend-l/waterfall.json"
    partial = out / "backend-l-final/waterfall.partial.json"
    failure = None
    if large.is_file():
        runs["L"] = read_json(large)
    elif partial.is_file():
        proof = read_json(partial)
        failure = large_failure(out, proof)
        runs["L_partial"] = {**proof, "requests": [row for row in proof["requests"] if row["sample"] == 1]}
        dumped(out / "waterfall-l-confirm-failed.json", failure)
    actions, requests = [], []
    for run_id, proof in runs.items():
        offset = len(actions)
        actions.extend({**row, "sample": row["sample"] + offset} for row in proof["actions"])
        requests.extend({**row, "sample": row["sample"] + offset} for row in proof["requests"])
    assert len(actions) == (106 if "L" in runs else 72 + bool(failure))
    assert len(requests) == sum(row["http_requests"] for row in actions)
    assert Counter(row["sample"] for row in requests) == {row["sample"]: row["http_requests"] for row in actions}
    ack_seconds_by_sample = {row["sample"]: row["seconds"] for row in requests if row["status"] == 202}
    jobs = sum(row["jobs"] for row in actions)
    assert jobs == (11 if "L" in runs else 8 + bool(failure))
    assert set(ack_seconds_by_sample) == {row["sample"] for row in actions if row["jobs"]}
    assert all(row.get("terminal") == "succeeded" for row in actions if row["jobs"])
    downloads = sum(bool(row.get("download_hash_verified")) for row in actions)
    assert downloads == (5 if "L" in runs else 4)
    assert all(row["status"] in (200, 201, 202) for row in requests)

    waterfall = []
    for (size, action), values in group(actions, "size", "action").items():
        ack_seconds = [ack_seconds_by_sample[v["sample"]] for v in values if v["jobs"]]
        waterfall.append({"size": size, "action": action, "n": len(values),
            "http_per_action": mean(v["http_requests"] for v in values),
            "polls_per_action_included": mean(v["status_polls"] for v in values),
            "job_ack_per_action": mean(v["jobs"] for v in values),
            "job_ack_http_mean_s": mean(ack_seconds) if ack_seconds else "",
            "action_mean_s": mean(v["seconds"] for v in values),
            "sum_http_wall_mean_s": mean(v["http_wall_seconds_sum"] for v in values),
            "request_body_bytes_mean": mean(v["request_body_bytes"] for v in values),
            "response_body_bytes_mean": mean(v["response_body_bytes"] for v in values),
            "api_sql_calls_mean": mean(v["api"]["statements"] for v in values),
            "api_sql_exec_ms_mean": mean(v["api"]["sql_exec_ms"] for v in values),
            "api_wal_bytes_mean": mean(v["api"]["wal_bytes"] for v in values),
            "api_cpu_ms_mean": mean(v["api"]["cpu_seconds"] * 1000 for v in values),
            "worker_sql_calls_gross_mean": mean(v["worker_gross"]["statements"] for v in values),
            "worker_sql_exec_ms_gross_mean": mean(v["worker_gross"]["sql_exec_ms"] for v in values),
            "worker_wal_bytes_gross_mean": mean(v["worker_gross"]["wal_bytes"] for v in values),
            "worker_cpu_ms_gross_mean": mean(v["worker_gross"]["cpu_seconds"] * 1000 for v in values)})
    dump_csv(out / "waterfall-actions.csv", waterfall)

    aliases = {"P08.preview": "jobs.preview", "P09.confirm": "jobs.confirm", "P13.analyze": "jobs.analyze"}
    http = []
    for (size, code, method, status), values in group(requests, "size", "operation", "method", "status").items():
        http.append({"size": size, "operation": aliases.get(code, code), "method": method, "status": status,
            "n": len(values), "http_mean_ms": mean(v["seconds"] * 1000 for v in values),
            "request_body_bytes_mean": mean(v["request_body_bytes"] for v in values),
            "response_body_bytes_mean": mean(v["response_body_bytes"] for v in values),
            "api_sql_calls_mean": mean(v["api"]["statements"] for v in values),
            "api_sql_exec_ms_mean": mean(v["api"]["sql_exec_ms"] for v in values)})
    dump_csv(out / "waterfall-http.csv", http)
    browser_rows, browser_hashes = browser(j01)
    dump_csv(out / "j01-browser-waterfall.csv", browser_rows)

    # Illustrative application of the measured S/M means, with no certified load rate.
    example = [row for row in waterfall if row["action"] == "analysis_to_result" and row["size"] in ("S", "M")]
    model = {
        "source_sha": "c70d8c726908f9bf3443b9e08f6f00a16048526f",
        "tests": {"actions": len(actions), "http_requests": len(requests),
                  "status_polls": sum(row["status_polls"] for row in actions), "completed_jobs": jobs,
                  "hash_verified_downloads": downloads, "runs": list(runs)},
        "idle_control_per_run": {name: run["idle_control"] for name, run in runs.items()},
        "resources_sampled_peak": {role: {k: max(row["sampled_resources"][role][k] for row in actions)
                                    for k in ("rss_bytes", "fds", "open_connections")}
                                   for role in ("api", "worker")},
        "configured_pool": {"api": 4, "worker": 2, "worker_slots": 2, "db_max_connections": 24},
        "browser_trace_sha256": browser_hashes,
        "reference_load": {"principals": 50000, "business_actions_per_user_second": 0.1,
            "business_actions_per_second": 5000, "provisional_http_requests_per_action": 3,
            "provisional_http_rps_includes_status": 15000, "reference_status_rps_inside_total": 900,
            "j01_old_foreground_probe_requests_per_minute": 65,
            "j01_old_foreground_probe_rps_if_all_50000_tabs": round(50000*65/60, 3)},
        "example_only_one_S_and_one_M_analysis_per_second": {
            "assumed_action_rps_per_size": 1,
            "http_rps_including_job_polls": sum(row["http_per_action"] for row in example),
            "job_poll_rps_already_in_http_total": sum(row["polls_per_action_included"] for row in example),
            "db_api_calls_per_second": sum(row["api_sql_calls_mean"] for row in example),
            "worker_excluded_from_projection": "Gross worker counters include scheduler idle work; summing across concurrent jobs would count it more than once.",
            "request_body_bytes_per_second": sum(row["request_body_bytes_mean"] for row in example),
            "response_body_bytes_per_second": sum(row["response_body_bytes_mean"] for row in example),
            "mean_outstanding_actions": round(sum(row["action_mean_s"] for row in example), 6),
            "mean_http_in_flight": round(sum(row["sum_http_wall_mean_s"] for row in example), 6),
        },
    }
    if failure:
        model["observed_failed_large_confirm"] = failure
        model["tests"].update(failed_actions=1, failed_jobs=1,
            total_http_requests_including_failed_action=len(requests) + failure["http_requests"],
            total_status_polls_including_failed_action=model["tests"]["status_polls"] + failure["status_polls_included"])
    dumped(out / "j06-sizing.json", model)
    print(json.dumps(model["tests"], ensure_ascii=False))


if __name__ == "__main__":
    consolidate(Path(sys.argv[1]), Path(sys.argv[2]))
