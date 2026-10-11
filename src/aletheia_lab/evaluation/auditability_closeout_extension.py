"""Technical correction followed by sealed unused-data transfer, not old-cost reruns."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.auditability_closeout import analyze, check_plan, read, sealed
from aletheia_lab.evaluation.auditability_closeout_controls import crash_controls, service_controls
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.neighbors_audit_transfer import ARMS
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def run_extension(root: Path, study: Path) -> dict[str, Any]:
    plan = check_plan(root, study)
    if (study / "execution-census.json").exists():
        raise ValueError("extension already attempted")
    write_new_file(study / "execution-census.json", encode(sealed({"status": "started"})).encode())
    census = []
    for seed in (0, *plan["validation_seed_variants"]):
        for arm in ARMS:
            name = f"transfer-{arm}" if seed == 0 else f"fresh-{seed}-{arm}"
            command = [
                sys.executable,
                str(root / "scripts/auditability_closeout.py"),
                "worker",
                "--root",
                str(root),
                "--study-dir",
                str(study),
                "--output",
                str(study / f"{name}.json"),
                "--kind",
                "transfer",
                "--arm",
                arm,
                "--seed",
                str(seed),
            ]
            try:
                result = subprocess.run(
                    command,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(root / "src"),
                        "OMP_NUM_THREADS": "1",
                        "OPENBLAS_NUM_THREADS": "1",
                    },
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
                row = {
                    "worker": name,
                    "returncode": result.returncode,
                    "stderr_sha256": content_sha256(result.stderr),
                }
                write_new_file(study / f"{name}.stderr", result.stderr)
            except subprocess.TimeoutExpired:
                row = {"worker": name, "returncode": None, "failure": "timeout"}
            census.append(row)
            print(f"closeout extension {len(census)}/21 returncode={row['returncode']}", flush=True)
    write_new_file(study / "completed-census.json", encode(sealed({"workers": census})).encode())
    control = service_controls(study)
    write_new_file(study / "service-controls.json", encode(sealed(control)).encode())
    exits = crash_controls(root, study)
    write_new_file(study / "crash-controls.json", encode(sealed({"rows": exits})).encode())
    predictions = []
    for seed in (0, *plan["validation_seed_variants"]):
        for arm in ARMS:
            name = f"transfer-{arm}" if seed == 0 else f"fresh-{seed}-{arm}"
            path = study / f"{name}.json"
            if not path.exists():
                predictions.append({"seed": seed, "arm": arm, "status": "insufficient_evidence"})
                continue
            document = read(path)
            status, verdict = plan["transfer_forecasts"][arm]
            rows = document["chain_rows"]
            supported = (
                len(rows) == 4
                and document["source_status"] == status
                and rows[-1]["resolution"] == verdict
            )
            predictions.append(
                {
                    "seed": seed,
                    "arm": arm,
                    "classification": "technical replication"
                    if seed == 0
                    else "prospective unused-data source-informed frame",
                    "status": "supported" if supported else "contradicted",
                    "chain_rows": len(rows),
                    "native_calls": document["attempted_native_predictions"],
                }
            )
    previous, corrected = analyze(study.parent), analyze(study)
    document = sealed(
        {
            "plan_sha256": plan["sha256"],
            "predictions": predictions,
            "source_family_count": 1,
            "old_transfer_forecasts": previous["predictions"],
            "costs": previous["costs"],
            "cost_origin": "immutable parent 30-worker frame",
            "corrected_seed_zero_service": corrected["offers"],
            "corrected_false_conclusive": corrected["false_conclusive"],
            "corrected_accepted_but_unserved": corrected["accepted_but_unserved"],
            "service_controls": control,
            "process_exit_controls": exits,
            "measurement_correction": "v1 stale numeric object-id enrollment; owner references fix it",
            "A_disposition": "bounded source-informed evidence/repair transfer and cost envelope; "
            "not a new checker or natural deployment",
            "B_disposition": "NARROW: no algorithm superiority demonstrated",
            "parent_status": "In progress: operator audit SLO/resource grounding unavailable",
            "provider_calls": 0,
        }
    )
    write_new_file(study / "analysis.json", encode(document).encode())
    return {
        "status": "closeout_extension_executed",
        "workers": len(census),
        "failed_workers": sum(row["returncode"] != 0 for row in census),
        "supported_predictions": sum(row["status"] == "supported" for row in predictions),
        "analysis_sha256": document["sha256"],
        "provider_calls": 0,
    }


def service_census(document: dict[str, Any]) -> dict[str, int]:
    """Count every offered obligation, including missing native/capture scopes."""
    truth = {row["frame"]["token"]: row["reference"] for row in document["chain_rows"]}
    counts = dict.fromkeys(("offered", "accepted", "refused", "complete", "unserved", "false"), 0)
    for offer in document["offers"]:
        answers = offer["answers"]
        complete = (
            bool(offer["scope"])
            and set(answers) == set(offer["scope"])
            and all(
                answer in {"compliant", "violation"} and answer == truth.get(token)
                for token, answer in answers.items()
            )
        )
        false = any(
            answer in {"compliant", "violation"} and answer != truth.get(token)
            for token, answer in answers.items()
        )
        accepted = offer["admitted"]
        counts["offered"] += 1
        counts["accepted"] += accepted
        counts["refused"] += not accepted
        counts["complete"] += accepted and complete
        counts["unserved"] += accepted and not complete
        counts["false"] += false
    return counts


def _transfer_census(study: Path, seeds: tuple[int, ...]) -> dict[str, Any]:
    rows = []
    for seed in seeds:
        for arm in ARMS:
            name = f"transfer-{arm}" if seed == 0 else f"fresh-{seed}-{arm}"
            document = read(study / f"{name}.json")
            reached = len(document["chain_rows"]) == 4
            rows.append(
                {
                    "seed": seed,
                    "arm": arm,
                    "native_calls": document["attempted_native_predictions"],
                    "chain_rows": len(document["chain_rows"]),
                    "fourth_intervention_reached": reached,
                    "fourth_intervention_status": "evaluated"
                    if reached
                    else "insufficient_evidence",
                    "source_status": document["source_status"],
                    **service_census(document),
                }
            )
    return {
        "rows": rows,
        "totals": {
            key: sum(row[key] for row in rows)
            for key in (
                "native_calls",
                "chain_rows",
                "offered",
                "accepted",
                "refused",
                "complete",
                "unserved",
                "false",
            )
        },
        "planned_workers": len(seeds) * len(ARMS),
        "planned_native_calls": len(seeds) * len(ARMS) * 8,
        "deadline_basis": "authored event horizon; no measured operator wall-clock SLO",
    }


def scientific_closeout(study: Path) -> dict[str, Any]:
    """Read-only additive synthesis; no native execution or changes to old receipts."""
    corrected = study / "correction-v2"
    original_plan, corrected_plan = read(study / "plan.json"), read(corrected / "plan.json")
    for directory, plan in ((study, original_plan), (corrected, corrected_plan)):
        for name, expected in plan["code"].items():
            if content_sha256((directory / "code-snapshot" / name).read_bytes()) != expected:
                raise ValueError("executed source snapshot differs")
    legacy = read(corrected / "analysis.json")
    graph = read(corrected / "graph-repair-results.json")
    graph_binding = read(corrected / "graph-repair-code-binding.json")
    if (
        graph["plan"] != graph_binding
        or content_sha256((corrected / "graph-repair-plan.json").read_bytes())
        != graph_binding["plan_sha256"]
    ):
        raise ValueError("graph repair binding differs")
    graph_code = corrected / "code-snapshot/src/aletheia_lab/evaluation/neighbors_graph_repair.py"
    if content_sha256(graph_code.read_bytes()) != graph_binding["code_sha256"]:
        raise ValueError("graph repair implementation differs")
    if analyze(study)["costs"] != legacy["costs"]:
        raise ValueError("immutable cost summary differs from worker receipts")
    graph_rows = [
        {
            key: row[key]
            for key in (
                "seed",
                "repair",
                "phase",
                "equivalent",
                "native_predictions",
                "additional_inspection_transforms",
                "missing_radius_neighbors",
            )
        }
        for row in graph["rows"]
    ]
    census = read(corrected / "completed-census.json")
    controls, crashes = (
        read(corrected / "service-controls.json"),
        read(corrected / "crash-controls.json"),
    )
    return sealed(
        {
            "schema": "auditability-scientific-closeout/additive-v1",
            "original_plan_sha256": original_plan["sha256"],
            "corrected_plan_sha256": corrected_plan["sha256"],
            "immutable_analysis_sha256": legacy["sha256"],
            "corrected_and_fresh": _transfer_census(corrected, (0, 59, 61)),
            "fresh_only": _transfer_census(corrected, (59, 61)),
            "failed_worker_processes": sum(row["returncode"] != 0 for row in census["workers"]),
            "qualification_falsification": "seed59 original numerical equivalence fails at pair2; "
            "all seven dependent arms share that qualification failure, not seven independent faults; "
            "their fourth intervention remains unattempted",
            "graph_repair": {"results_sha256": graph["sha256"], "rows": graph_rows},
            "costs": legacy["costs"],
            "cost_scope": "30 fresh processes; fixed-read sufficient storage service, "
            "not admission/reservation/refetch equivalence or causal production overhead; "
            "raw fastest observed; incremental lower SQL payload than whole rewrite, no dominance",
            "service_controls": {
                "cells": len(controls["cells"]),
                "same_service": controls["same_service_all"],
                "scope": "authored quota/horizon sensitivity; supplied renamed receipt "
                "installation is not independently charged historical refetch",
            },
            "crash_controls": crashes,
            "preserved_failures": [
                "initial stale numeric object-id enrollment and extra p4 row",
                "initial four-KiB overlap control assumption failure",
                "all seed59 source qualification failures",
                "historical33accepted-unserved unchanged",
            ],
            "A_disposition": "bounded empirical/system contribution; numerical correctness, fitted-state "
            "identity, capture and service are distinct; source-informed new-data transfer only",
            "B_disposition": "NARROW: no same-service residual algorithm advantage demonstrated",
            "parent_status": "In progress: source-grounded operator audit requirements unavailable",
            "excluded_claims": [
                "natural deployment",
                "hostile-host completeness",
                "arbitrary native state",
                "power-loss guarantee",
                "global optimum",
                "Q1 guarantee",
            ],
            "provider_calls": 0,
        }
    )
