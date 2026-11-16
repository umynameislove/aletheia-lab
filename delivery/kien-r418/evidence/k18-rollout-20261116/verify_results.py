"""Validate the raw rollout receipts and report measured resource peaks."""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "attempt-05"


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8-sig"))


def size(text):
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    for suffix in ("GiB", "MiB", "KiB", "B"):
        if text.endswith(suffix):
            return float(text.removesuffix(suffix)) * units[suffix]
    raise ValueError(text)


def main():
    assert read("controller-result.json")["status"] == "passed"
    traffic = read("traffic-result.json")
    assert traffic["status"] == "passed" and traffic["pg_peak_connections"] <= 20
    events = [
        json.loads(row)
        for row in (OUT / "controller-events.jsonl").read_text().splitlines()
    ]
    exits = [row for row in events if row["event"] == "drained_exit"]
    canaries = [row for row in events if row["event"] == "candidate_ready_and_routed"]
    assert len(exits) == 8 and all(row["exit_code"] in (0, 241) for row in exits)
    assert len(canaries) == 4
    for canary in canaries:
        alias = canary["alias"]
        assert canary["before"]["checks"][alias] is False
        assert (
            alias not in canary["before"]["ready"] and alias in canary["after"]["ready"]
        )
        assert canary["after"]["routed"][alias] > canary["before"]["routed"].get(
            alias, 0
        )
    for phase in ("upgrade", "rollback"):
        value = read(phase + "-result.json")
        signal = read(phase + "-api-signal.json")
        assert value["acknowledged"] == len(value["successful"]) == 6
        assert all(
            row["attempts"] == 1 and row["state"] == "succeeded"
            for row in value["successful"].values()
        )
        assert value["download"]["bytes"] == read("driver-ready.json")["download_bytes"]
        assert (
            value["download"]["completed_epoch"] > signal["epoch"]
            and signal["active_at_drain"] > 0
        )
        assert value["queued_at_cutover"] > 0 and len(value["running_at_cutover"]) == 2
        assert all(
            value["successful"][job]["terminal_epoch"]
            > value["controller"]["worker_signal_epoch"]
            for job in value["running_at_cutover"]
        )
    fence = read("fence-result.json")
    assert fence["stale_heartbeat_rejected"] and fence["stale_publish_rejected"]
    assert fence["attempts"] == [[1, "lease_lost"], [2, "succeeded"]]

    resource = [
        json.loads(line)
        for line in (OUT / "docker-resources.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
    ]
    rows = []
    for sample in resource:
        for entry in sample["containers"]:
            used, limit = entry["MemUsage"].split(" / ")
            rows.append(
                {
                    "name": entry["Name"],
                    "cpu_cores": float(entry["CPUPerc"].removesuffix("%")) / 100,
                    "memory_bytes": size(used),
                    "limit_bytes": size(limit),
                    "pids": int(entry["PIDs"]),
                }
            )
    assert len(resource) >= 10 and all(
        row["memory_bytes"] <= row["limit_bytes"] for row in rows
    )
    groups = {}
    for role, token in (
        ("api", "-api"),
        ("worker", "-worker"),
        ("pg", "-pg"),
        ("idp", "-idp"),
        ("edge", "-edge"),
        ("driver", "-driver"),
    ):
        selected = [
            row for row in rows if token in row["name"] and row["limit_bytes"] > 0
        ]
        assert selected, role
        groups[role] = {
            "cpu_peak_sample_cores": max(row["cpu_cores"] for row in selected),
            "memory_peak_sample_mib": max(row["memory_bytes"] for row in selected)
            / 1024**2,
            "memory_limit_mib": min(row["limit_bytes"] for row in selected) / 1024**2,
            "pids_peak_sample": max(row["pids"] for row in selected),
        }
    summary = {
        "status": "passed_k18_03_staging",
        "attempt": OUT.name,
        "images": read("controller-result.json")["images"],
        "successful_jobs_during_rollout": 12,
        "verified_inflight_downloads": 2,
        "fence_recovered_jobs": 1,
        "canary_replicas": 4,
        "drained_processes": 8,
        "probes": traffic["probes"],
        "pg_peak_connections": traffic["pg_peak_connections"],
        "rollout_configured_connection_bound": 20,
        "postgres_max_connections": 24,
        "resource_samples": len(resource),
        "resources_by_role": groups,
        "first_resource_utc": resource[0]["time"],
        "last_resource_utc": resource[-1]["time"],
        "verified_utc": datetime.now().astimezone().isoformat(),
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in ("images", "verified_utc")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
