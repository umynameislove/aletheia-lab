"""Receipt-based service and process-exit controls, not new native inferences."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.incident_audit_archive import POLICIES, IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def receipt(study: Path) -> dict[str, Any]:
    document = json.loads((study / "transfer-original.json").read_bytes())
    return cast(dict[str, Any], document["chain_rows"][0]["frame"])


def renamed(frame: dict[str, Any], token: str) -> dict[str, Any]:
    result = copy.deepcopy(frame)
    result["token"] = token
    for use in result["uses"]:
        use["token"] = use["batch"] = token
    return result


def service_controls(study: Path, base: dict[str, Any] | None = None) -> dict[str, Any]:
    base, results = base if base is not None else receipt(study), []
    for policy in POLICIES:
        for budget in (4096, 16384, 65536):
            stores = [
                cls(study / f"sensitivity-{policy}-{budget}-{name}.sqlite", policy, budget)
                for cls, name in (
                    (IncidentAuditArchive, "whole"),
                    (IncrementalAuditArchive, "incremental"),
                )
            ]
            for store in stores:
                assert store.reserve("pinned", now=0, until=100, bound=2048)
                assert store.put(renamed(base, "pinned"), now=1)
                for ordinal in range(24):
                    store.put(renamed(base, f"r{ordinal}"), now=2 + ordinal)
            assert stores[0].state == stores[1].state
            row: dict[str, Any] = {"policy": policy, "logical_budget": budget}
            for name, scopes in (
                ("pinned", ["pinned"]),
                ("late_burst", ["r0", "r23"]),
                ("overlap", ["pinned", "r23"]),
            ):
                pairs = []
                for store in stores:
                    accepted = store.demand(name, scopes, now=30, until=100)
                    pairs.append({"accepted": accepted, "answers": store.query(scopes, now=30)})
                assert pairs[0] == pairs[1]
                row[name] = pairs[0]
            for store in stores:
                store.drain("pinned", now=31)
                row["overlap_survives_independent_drain"] = (
                    store.state["leases"]["overlap"]["scopes"] == ["pinned", "r23"]
                    if row["overlap"]["accepted"]
                    else None
                )
                row["unavailable_recovery"] = store.query(["absent"], now=32)["absent"]
                row["retained_tier_restore"] = store.restore_union(
                    [renamed(base, "recovered")], now=33
                )
                row["recovered_answer"] = store.query(["recovered"], now=34)["recovered"]
            assert stores[0].state == stores[1].state
            row.update(
                same_service=True,
                evicted=stores[0].state["evicted"],
                recovery_receipt_bytes=len(encode(renamed(base, "recovered")).encode()),
            )
            for store in stores:
                store.close()
            results.append(row)
    return {
        "cells": results,
        "scope": "new native-receipt replay; authored quota/event horizons",
        "same_service_all": all(row["same_service"] for row in results),
    }


def crash_worker(study: Path, name: str, phase: str) -> None:
    if name not in {"whole", "incremental"} or phase not in {"before_commit", "after_ack"}:
        raise ValueError("unplanned child exit control")
    cls = IncrementalAuditArchive if name == "incremental" else IncidentAuditArchive
    directory = study / f"crash-{name}-{phase}"
    directory.mkdir()
    store = cls(directory / "archive.sqlite", "static", 16384)
    assert store.put(renamed(receipt(study), "before"), now=0)
    write_new_file(
        directory / "baseline.json",
        encode({"state_sha256": store.snapshot()["state_sha256"]}).encode(),
    )
    if phase == "before_commit":
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute("DELETE FROM fragments" if name == "incremental" else "DELETE FROM state")
        os._exit(23)
    assert store.put(renamed(receipt(study), "acknowledged"), now=1)
    write_new_file(
        directory / "ack.json", encode({"state_sha256": store.snapshot()["state_sha256"]}).encode()
    )
    os._exit(24)


def crash_controls(root: Path, study: Path) -> list[dict[str, Any]]:
    results = []
    for name, cls in (("whole", IncidentAuditArchive), ("incremental", IncrementalAuditArchive)):
        for phase, expected in (("before_commit", 23), ("after_ack", 24)):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/auditability_closeout.py"),
                    "crash-worker",
                    "--root",
                    str(root),
                    "--study-dir",
                    str(study),
                    "--mode",
                    name,
                    "--phase",
                    phase,
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
            directory = study / f"crash-{name}-{phase}"
            ack = directory / ("baseline.json" if phase == "before_commit" else "ack.json")
            store = cls(directory / "archive.sqlite", "static", 16384, reopen=True)
            matched = (
                store.snapshot()["state_sha256"] == json.loads(ack.read_bytes())["state_sha256"]
            )
            store.close()
            results.append(
                {
                    "implementation": name,
                    "phase": phase,
                    "returncode": completed.returncode,
                    "pass": completed.returncode == expected and matched,
                    "scope": "abrupt child exit; not power loss",
                }
            )
    return results
