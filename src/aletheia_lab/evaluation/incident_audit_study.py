"""Real CPU source calls, ordinary online audit service and private receipts.

This unprotected development study does not consume historical attempts. The
same captured producer tape is fed online to every matched archive. Component
costs are measured; the combined observer workload is not production overhead.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_service import RecoveryTier, offer, summarize
from aletheia_lab.evaluation.incident_audit_source import (
    InitializerWorkload,
    identity,
    source_summary,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

PROTOCOL = "configs/evaluation/incident_audit_development_protocol.json"
FILES = (
    PROTOCOL,
    "scripts/incident_audit_development.py",
    *(
        f"src/aletheia_lab/evaluation/{name}.py"
        for name in (
            "incident_audit_archive",
            "incident_audit_service",
            "incident_audit_source",
            "incident_audit_study",
            "incident_audit_verification",
            "incident_audit_controls",
            "request_model_audit",
            "request_model_retention",
            "audit_bundle_policy",
            "model_load_retention",
            "cache_lifecycle_study",
        )
    ),
    "src/aletheia_lab/project/identity.py",
    "src/aletheia_lab/filesystem.py",
)


def memory_sample() -> dict[str, Any]:
    try:
        resource = importlib.import_module("resource")
    except ImportError:
        return {"peak_process_rss_bytes": None, "status": "unavailable_on_platform"}
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "peak_process_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024),
        "status": "process_high_water_not_per_policy",
    }


class ServiceWorkload:
    def __init__(self, config: dict[str, Any], directory: Path) -> None:
        self.config, self.directory = config, directory
        self.source = InitializerWorkload()
        self.archives: dict[str, IncidentAuditArchive] = {}
        for budget in config["logical_capacity_bytes"]:
            for policy in config["policies"]:
                for tier in config["recovery_tiers"]:
                    name = f"{budget}-{policy}-{tier}"
                    self.archives[name] = IncidentAuditArchive(
                        directory / f"{name}.sqlite", policy, budget
                    )
        self.archives["full-reference"] = IncidentAuditArchive(
            directory / "full-reference.sqlite", "static", config["full_reference_capacity_bytes"]
        )
        self.offers: dict[str, list[dict[str, Any]]] = {name: [] for name in self.archives}
        self.prospective: dict[str, list[dict[str, Any]]] = {name: [] for name in self.archives}
        self.secondary = RecoveryTier(
            directory / "secondary.sqlite", config["secondary_logical_capacity_bytes"]
        )
        self.rows: list[dict[str, Any]] = []
        self.schedule: list[dict[str, Any]] = []

    def service_late(self, item: dict[str, Any]) -> None:
        for name, archive in self.archives.items():
            tier = next(
                (tier for tier in self.config["recovery_tiers"] if name.endswith(f"-{tier}")),
                "none",
            )
            self.offers[name].append(
                offer(archive, tier, self.secondary, item, self.config["response_budget_ns"])
            )

    def service_known(self, now: int) -> None:
        for name, admissions in self.prospective.items():
            archive = self.archives[name]
            for item in admissions:
                if item["deadline"] != now:
                    continue
                from time import perf_counter_ns

                started = perf_counter_ns()
                item["answers"] = archive.query(item["scopes"], now=now)
                item["witness"] = archive.witness(item["scopes"])
                identifier = f"pre:{item['scopes'][0]}"
                if identifier in archive.state["leases"]:
                    archive.drain(identifier, now=now)
                item["queried_at"] = now
                item["query_wall_ns"] = perf_counter_ns() - started
                item["refetched"], item["refetch_failed"] = [], []

    def ingest(self, ordinal: int) -> None:
        token = f"request-{ordinal:03d}"
        if ordinal % self.config["preknown_obligation_every_calls"] == 0:
            until = ordinal + self.config["preknown_audit_delay_ticks"]
            for name, archive in self.archives.items():
                accepted = archive.reserve(
                    token,
                    now=ordinal,
                    until=until,
                    bound=self.config["future_frame_growth_bound_bytes"],
                )
                self.prospective[name].append(
                    {
                        "id": f"pre:{token}",
                        "kind": "preknown",
                        "scopes": [token],
                        "offered_at": ordinal,
                        "deadline": until,
                        "accepted": accepted,
                        "response_budget_ns": self.config["response_budget_ns"],
                    }
                )
        row = self.source.call(
            ordinal,
            captured=self.config["source_arm"] == "complete_capture"
            or ordinal != self.config["capture_omission_ordinal"],
            route_repair=self.config["source_arm"] == "route_repair",
        )
        self.rows.append(row)
        self.secondary.put(row["frame"])
        for archive in self.archives.values():
            archive.put(row["frame"], now=ordinal)
        if row["native_error"] or ordinal % 8 == 7:
            start = max(0, ordinal - self.config["incident_window_calls"] + 1)
            arrived = ordinal + self.config["incident_audit_delay_ticks"]
            self.schedule.append(
                {
                    "id": f"incident-{ordinal}",
                    "kind": "incident",
                    "scopes": [f"request-{i:03d}" for i in range(start, ordinal + 1)],
                    "offered_at": arrived,
                    "deadline": arrived + 4,
                }
            )
        for item in self.schedule:
            if item["offered_at"] == ordinal:
                self.service_late(item)
        self.service_known(ordinal)

    def execute(self) -> dict[str, Any]:
        for ordinal in range(self.config["native_calls_per_process"]):
            self.ingest(ordinal)
        end = self.config["native_calls_per_process"]
        start = max(0, end - self.config["retrospective_window_calls"])
        arrived = end + self.config["retrospective_audit_delay_ticks"]
        self.schedule.append(
            {
                "id": "retrospective-final",
                "kind": "retrospective",
                "scopes": [f"request-{i:03d}" for i in range(start, end)],
                "offered_at": arrived,
                "deadline": arrived + 8,
            }
        )
        future = {i["offered_at"] for i in self.schedule if i["offered_at"] >= end}
        future.update(
            i["deadline"] for i in next(iter(self.prospective.values())) if i["deadline"] >= end
        )
        for now in sorted(future):
            for item in self.schedule:
                if item["offered_at"] == now:
                    self.service_late(item)
            self.service_known(now)
        reports = {}
        for name, archive in self.archives.items():
            known = self.prospective[name]
            combined = known + self.offers[name]
            snapshot = archive.snapshot()
            archive.close()
            reports[name] = {
                "offers": self.offers[name],
                "prospective_admissions": known,
                "summary": summarize(combined, self.rows),
                "late_summary": summarize(self.offers[name], self.rows),
                "preknown_summary": summarize(known, self.rows),
                "storage": snapshot,
                "closed_db_bytes": archive.path.stat().st_size,
                "db_sha256": content_sha256(archive.path.read_bytes()),
            }
        return {
            "status": "development_executed",
            "source_arm": self.config["source_arm"],
            "source_identity": self.source.identity,
            "source_summary": source_summary(self.rows),
            "source_load_ns": self.source.load_ns,
            "rows": self.rows,
            "schedule": self.schedule,
            "archives": reports,
            "secondary": self.secondary.finish(),
            "memory": memory_sample(),
            "provider_calls": 0,
            "limitations": "authored event schedule/development response budgets; common instrumented CPU producer; no field deployment, throughput, physical quota, hostile host or novel method",
        }


def worker(config: dict[str, Any], directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink():
        raise ValueError("fresh owned worker directory required")
    directory.mkdir()
    return ServiceWorkload(config, directory).execute()


def native_control(config: dict[str, Any]) -> dict[str, Any]:
    source = InitializerWorkload()
    return {
        "status": "native_control_executed",
        "source_identity": source.identity,
        "source_load_ns": source.load_ns,
        "rows": [source.native_floor(i) for i in range(config["native_calls_per_process"])],
        "memory": memory_sample(),
        "provider_calls": 0,
    }


def _child(root: Path, directory: Path, arm: str, replicate: int) -> dict[str, Any]:
    report = directory / f"{arm}-{replicate}.json"
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}
    }
    env.update(
        PYTHONPATH=str(root / "src"),
        PYTHONHASHSEED=str(replicate + 1),
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        OTEL_SDK_DISABLED="true",
    )
    command = [
        sys.executable,
        str(root / "scripts/incident_audit_development.py"),
        "worker",
        "--root",
        str(root),
        "--study-dir",
        str(directory / f"{arm}-{replicate}"),
        "--source-arm",
        arm,
        "--output",
        str(report),
    ]
    failure = None
    try:
        completed = subprocess.run(command, env=env, capture_output=True, timeout=120, check=False)
        if completed.returncode:
            failure = {"status": "worker_failed", "returncode": completed.returncode}
            stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        failure = {"status": "worker_timeout", "timeout_seconds": 120}
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
    if failure is not None:
        write_new_file(directory / f"{arm}-{replicate}.stderr", stderr)
        write_new_file(directory / f"{arm}-{replicate}.stdout", stdout)
        failure.update(stderr_sha256=content_sha256(stderr), stdout_sha256=content_sha256(stdout))
        if report.exists():
            raise ValueError("failed child unexpectedly acknowledged a report")
        write_new_file(report, encode(seal(failure)).encode())
    return read_sealed(report)


def run(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private output outside repository required")
    config = json.loads((root / PROTOCOL).read_bytes())
    plan = seal(
        {
            "schema": "incident-audit-development-plan/v1",
            "protocol": config,
            "source_identity": identity(),
            "code": {p: content_sha256((root / p).read_bytes()) for p in FILES},
        }
    )
    directory.mkdir(parents=True)
    for name in FILES:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    reports = []
    for arm in (*config["source_arms"], "native_floor"):
        for replicate in range(config["process_replicates"]):
            reports.append(_child(root, directory, arm, replicate))
    result = seal(
        {
            "schema": "incident-audit-development-results/v1",
            "plan_sha256": plan["sha256"],
            "processes": reports,
            "summary": aggregate(reports),
        }
    )
    write_new_file(directory / "results.json", encode(result).encode())
    return dict(result["summary"])


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [r for r in reports if r["status"] == "development_executed"]
    controls = [r for r in reports if r["status"] == "native_control_executed"]
    counts: Counter[str] = Counter()
    per_archive: dict[str, Counter[str]] = {}
    source_arms: dict[str, Counter[str]] = {}
    for report in successful:
        stats = source_arms.setdefault(report["source_arm"], Counter())
        for key, value in report["source_summary"].items():
            if type(value) is int and key != "source_cluster_count":
                stats[key] += value
        for key in (
            "offered_native_calls",
            "native_failures",
            "captured_correct",
            "captured_unknown",
            "captured_false",
        ):
            counts[key] += report["source_summary"][key]
        for name, arm in report["archives"].items():
            per_archive.setdefault(name, Counter()).update(arm["summary"])
    return {
        "planned_processes": len(reports),
        "successful_processes": len(successful),
        "native_control_processes": len(controls),
        "failed_processes": len(reports) - len(successful) - len(controls),
        **dict(counts),
        "native_control_calls": sum(len(r["rows"]) for r in controls),
        "source_cluster_count": 1,
        "archives": {name: dict(v) for name, v in sorted(per_archive.items())},
        "source_arms": {arm: dict(v) for arm, v in source_arms.items()},
        "A_disposition": "development_only_external_operator_and_unused_transfer_pending",
        "B_disposition": "no_method_admission_from_authored_quota_and_development_response_budgets",
        "provider_calls": 0,
    }
