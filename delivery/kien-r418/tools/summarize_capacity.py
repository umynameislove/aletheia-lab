"""Derive internal capacity tables from immutable raw measurements, without changing outcomes."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    for argument in sys.argv[1:]:
        folder = Path(argument)
        raw = json.loads((folder / "calibration100-result.json").read_bytes())
        profile = json.loads((folder / "calibration100-profile.json").read_bytes())
        attempt = json.loads((folder / "attempt.json").read_bytes())
        qualification = json.loads((folder / "result.json").read_bytes())
        positive = raw["measured_positive"]
        assert sum(row["offered"] for row in raw["strata"]) == raw["counts"]["measured_offered"]
        assert sum(row["offered"] for row in raw["strata"] if row["expectation"] == "positive") == positive["offered"]
        completed = sum(row["outcomes"].get("completed", 0) for row in raw["strata"] if row["expectation"] == "positive")
        assert completed == positive["correct_by_deadline"]
        assert abs(completed / positive["offered"] - positive["success_fraction"]) < 1e-12
        assert raw["reconciliation"]["offered_equals_terminal_plus_pending"]
        assert raw["reconciliation"]["terminal_outcomes_match"]
        outcomes = defaultdict(Counter)
        for row in raw["strata"]:
            outcomes[row["operation"]].update(row["outcomes"])
        raw_files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())
                     if p.is_file() and p.name not in {"observed-summary.json", "observed-strata.csv"}}
        summary = {key: raw[key] for key in (
            "elapsed_seconds", "started_at", "finished_at", "principal_count", "counts", "http",
            "measured_positive", "fatal_correctness_code", "first_unexpected_http", "native_topology",
            "actual_actor_size_counts", "jobs_pending_terminal", "peak_action_inflight",
            "generator_resources", "active_window_30s", "histogram_note", "open_gates",
            "postrun_reconciliation")}
        summary.update(
            source_identity=attempt["source_identity"], runtime_identity=attempt["runtime_identity"],
            mode=profile["mode"], duration_seconds=profile["duration_seconds"],
            warmup_seconds=profile["warmup_seconds"], drain_seconds=profile["drain_seconds"],
            arrivals_per_second=profile["arrivals_per_second"], think_seconds=profile["think_seconds"],
            max_inflight=profile["max_inflight"], max_pending=profile["max_pending"],
            poll_seconds=profile["poll_seconds"], seed=profile["seed"],
            outcomes_by_journey={k: dict(v) for k, v in sorted(outcomes.items())},
            raw_files=raw_files, qualified=qualification["qualified"],
            acceptance_result="observed_local_subset_target_met" if qualification["qualified"] else "failed_local_calibration",
            measurement_scope="100 synthetic principals, actual TLS/API/PostgreSQL/native workers on one host; not a primary50k or staging certificate.",
        )
        if attempt.get("diagnostic_not_capacity_acceptance"):
            assert not qualification["qualified"]
            summary["acceptance_result"] = "diagnostic_only_not_qualification"
            summary["measurement_scope"] = "Write contention diagnostic with altered journey weights and allowlisted exception logging; not capacity acceptance."
        headers = ["journey", "size", "expectation", "offered", "completed", "denied", "failed", "rejected", "timeout", "dropped", "pending",
                   "deadline_seconds", "target_p95_s", "target_p99_s", "latency_n", "mean_s", "p95_upper_s", "p99_upper_s", "p99_precision_sufficient",
                   "response_n", "response_mean_s", "response_p95_upper_s", "response_p99_upper_s"]
        with (folder / "observed-strata.csv").open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            for row in raw["strata"]:
                deadline, response, targets = row["deadline_inclusive"], row["completed_response"], row["slo_targets"]
                cells = dict(journey=row["operation"], size=row["size"], expectation=row["expectation"], offered=row["offered"],
                             pending=row["pending"], deadline_seconds=row["deadline_seconds"],
                             target_p95_s=targets["p95_seconds"], target_p99_s=targets["p99_seconds"],
                             latency_n=deadline["n"], mean_s=deadline["mean_seconds"],
                             p95_upper_s=deadline["p95_upper_seconds"], p99_upper_s=deadline["p99_upper_seconds"],
                             p99_precision_sufficient=deadline["p99_precision_sufficient"],
                             response_n=response["n"], response_mean_s=response["mean_seconds"],
                             response_p95_upper_s=response["p95_upper_seconds"], response_p99_upper_s=response["p99_upper_seconds"])
                cells.update({key: row["outcomes"].get(key, 0) for key in ("completed", "denied", "failed", "rejected", "timeout", "dropped")})
                writer.writerow(cells)
        (folder / "observed-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(directory=folder.name, positive=positive, qualified=qualification["qualified"])))


if __name__ == "__main__":
    main()
