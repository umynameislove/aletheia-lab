"""Generate per-step cost rows and align existing physical NIC samples to the LAN pilot."""

import csv
import json
from pathlib import Path

out = Path(__file__).resolve().parent
result = json.loads((out / "result.json").read_text(encoding="utf-8"))
assert result["status"] == "passed_isolated_steps"
assert len(result["batches"]) == 27 and len(result["fixture_actions"]) == 15


def matched_aux_role(role, before, after):
    points = [
        tuple(map(float, line.split()))
        for line in (out / (role + "-resource.tsv")).read_text().splitlines()
    ]
    lo = min(points, key=lambda point: abs(point[0] - before))
    hi = min(points, key=lambda point: abs(point[0] - after))
    if lo[0] >= hi[0] or abs(lo[0] - before) > 0.6 or abs(hi[0] - after) > 0.6:
        return None
    return (
        (hi[1] - lo[1]) / 1000,
        hi[0] - lo[0],
        max(p[2] for p in points if lo[0] <= p[0] <= hi[0]),
        None,
    )


rows = []
for batch in result["batches"]:
    start, end = batch["before"], batch["after"]
    samples = {}
    for role, after in end.items():
        before = start[role]
        samples[role] = (
            (after["cpu"]["usage_usec"] - before["cpu"]["usage_usec"]) / 1000,
            after["monotonic"] - before["monotonic"],
            max(before["memory_current"], after["memory_current"]),
            max(
                proc["fds"] for entry in (before, after) for proc in entry["processes"]
            ),
        )
    for role in ("pg", "idp"):
        samples[role] = matched_aux_role(
            role, start["generator"]["epoch"], end["generator"]["epoch"]
        )
    for role, observation in samples.items():
        wall = batch["latency"]["mean_ms"] / 1000
        rate = batch["count"] / batch["window_seconds"]
        if observation is None:
            cpu_per_request = duration = memory = fds = None
        else:
            cpu, duration, memory, fds = observation
            assert cpu >= 0 and duration > 0 and memory > 0
            cpu_per_request = cpu / batch["count"]
        rows.append(
            {
                "journey": batch["journey"],
                "operation": batch["operation"],
                "size": batch["size"],
                "role": role,
                "completed": batch["count"],
                "wall_mean_ms": batch["latency"]["mean_ms"],
                "p95_ms": batch["latency"]["p95_observed_ms"],
                "gross_cpu_ms_per_request": cpu_per_request,
                "gross_cores_at_batch_rate": None
                if cpu_per_request is None
                else rate * cpu_per_request / 1000,
                "cpu_sample_seconds": duration,
                "memory_sample_bytes": memory,
                "fd_sample_max": fds,
                "rate_in_batch_per_second": rate,
                "mean_inflight_at_batch_rate": rate * wall,
            }
        )
assert len(rows) == 216 and all(row["completed"] == 12 for row in rows)
with (out / "cpu-per-http-step.csv").open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

pilot = Path("C:/t/k14-lan-load-20261116")
run = json.loads((pilot / "kien-mixed-90s.json").read_text())
start, end = run["mixed_start_epoch"], run["mixed_end_epoch"]
samples = [
    json.loads(line) for line in (pilot / "host-wifi.jsonl").read_text().splitlines()
]
samples = [row for row in samples if start <= row["epoch"] <= end]
first, last = samples[0], samples[-1]
duration = last["epoch"] - first["epoch"]
assert len(samples) >= 40 and 85 < duration < end - start
deltas = {key: last[key] - first[key] for key in first if key != "epoch"}
assert all(value >= 0 for value in deltas.values())
(out / "pilot-physical-nic.json").write_text(
    json.dumps(
        {
            "scope": "Physical Wi-Fi whole-interface traffic during the existing same-host pilot. No workload-only attribution.",
            "pilot_workload_start_epoch": start,
            "pilot_workload_end_epoch": end,
            "capture_start_epoch": first["epoch"],
            "capture_end_epoch": last["epoch"],
            "samples": len(samples),
            "capture_seconds": duration,
            "counter_deltas": deltas,
            "rx_mbit_per_second": deltas["rx_bytes"] * 8 / duration / 1_000_000,
            "tx_mbit_per_second": deltas["tx_bytes"] * 8 / duration / 1_000_000,
        },
        indent=2,
    ),
    encoding="utf-8",
)
