"""Sealed, bounded scientific closeout with explicit unresolved acceptance.

Only new owned local experiments run here. Historical reports are hash-bound,
never replayed through their native runners. No manuscript or provider execution.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from statistics import median
from typing import Any, cast
from unittest.mock import patch

from aletheia_lab.evaluation.auditability_closeout_cost import MODES, run_cost
from aletheia_lab.evaluation.auditability_model import check_model
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.neighbors_audit_transfer import (
    ARMS,
    SOURCE_SHA,
    execute,
    native_counterpair,
    source_path,
)
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

FILES = (
    "scripts/auditability_closeout.py",
    "src/aletheia_lab/evaluation/auditability_closeout.py",
    "src/aletheia_lab/evaluation/auditability_closeout_cost.py",
    "src/aletheia_lab/evaluation/auditability_closeout_controls.py",
    "src/aletheia_lab/evaluation/auditability_closeout_extension.py",
    "src/aletheia_lab/evaluation/auditability_model.py",
    "src/aletheia_lab/evaluation/neighbors_audit_transfer.py",
    "src/aletheia_lab/evaluation/incident_audit_incremental.py",
    "src/aletheia_lab/evaluation/incident_audit_archive.py",
    "src/aletheia_lab/evaluation/audit_bundle_policy.py",
    "src/aletheia_lab/evaluation/request_model_audit.py",
    "src/aletheia_lab/evaluation/request_model_retention.py",
)


def sha(path: Path) -> str:
    return content_sha256(path.read_bytes())


def sealed(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "sha256": content_sha256(encode(document).encode())}


def read(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_bytes())
    expected = document.pop("sha256")
    if sealed(document)["sha256"] != expected:
        raise ValueError("sealed document differs")
    return {**document, "sha256": expected}


def prepare(root: Path, study: Path) -> dict[str, Any]:
    if sys.flags.optimize:
        raise ValueError("scientific controls require assertions enabled")
    if study.exists() or study.is_symlink() or not study.parent.is_dir():
        raise ValueError("fresh owned study required")
    if sha(source_path()) != SOURCE_SHA:
        raise ValueError("pinned installed source differs")
    sources = {name: sha(root / name) for name in FILES}
    upstream = {str(source_path().relative_to(source_path().parents[3])): SOURCE_SHA}
    sklearn = import_module("sklearn")

    for name in (
        "pipeline.py",
        "neighbors/_graph.py",
        "neighbors/_regression.py",
        "neighbors/_base.py",
    ):
        upstream[name] = sha(Path(cast(str, sklearn.__file__)).parent / name)
    plan = sealed(
        {
            "schema": "auditability-scientific-closeout/v1",
            "code": sources,
            "upstream": upstream,
            "source_version": sklearn.__version__,
            "transfer_arms": list(ARMS),
            "cost_modes": list(MODES),
            "counts": [16, 64],
            "repeats": [0, 1, 2],
            "cost_seed": "1701 + repeat",
            "validation_seed_variants": [59, 61],
            "receipt_service_controls": {
                "policies": ["static", "union_density", "lru", "size_cost"],
                "budgets": [4096, 16384, 65536],
                "ingress": 24,
                "source": "first original transfer receipt",
            },
            "child_exit_controls": ["whole/incremental", "before_commit/after_ack"],
            "source_clusters": 1,
            "transfer_forecasts": {
                "original": ["pass", "compliant"],
                "wrong_labels": ["source_failure", "violation"],
                "restore_labels": ["pass", "compliant"],
                "missing_use": ["pass", "unknown"],
                "missing_closure": ["pass", "unknown"],
                "misjoin": ["pass", "conflict"],
                "native_failure": ["source_failure", "unknown"],
            },
            "service": {
                "quota": 65536,
                "reservation": 4096,
                "until": 100,
                "single_and_burst_offers": 5,
                "demand_basis": "source paired equivalence checkpoints; authored audit extension",
            },
            "cost_forecasts": [
                "equal service among raw/whole/incremental",
                "lower submitted SQL payload for incremental",
                "no forecast of lower total latency or physical DB",
            ],
            "premises": [
                "honest host/hook",
                "no in-call concurrent state mutation",
                "only fitted-label intervention; other native fields unchanged",
                "SQLite FULL/WAL local process-crash scope",
            ],
            "unresolved": [
                "natural deployment packet",
                "operator audit SLO/resource basis",
                "hostile-host capture completeness",
                "power-loss durability",
                "global cost optimum",
            ],
            "provider_calls": 0,
            "outcomes_observed": False,
        }
    )
    study.mkdir()
    write_new_file(study / "plan.json", encode(plan).encode())
    return {
        "status": "new_frame_prediction_blind_plan_sealed",
        "plan_sha256": plan["sha256"],
        "transfer_workers": len(ARMS),
        "cost_workers": 30,
        "provider_calls": 0,
    }


def check_plan(root: Path, study: Path) -> dict[str, Any]:
    if sys.flags.optimize:
        raise ValueError("scientific controls require assertions enabled")
    plan = read(study / "plan.json")
    if any(sha(root / name) != expected for name, expected in plan["code"].items()):
        raise ValueError("executed implementation differs from seal")
    if sha(source_path()) != SOURCE_SHA:
        raise ValueError("upstream differs")
    sklearn = import_module("sklearn")

    for name, expected in plan["upstream"].items():
        if name.startswith("sklearn/"):
            continue
        if sha(Path(cast(str, sklearn.__file__)).parent / name) != expected:
            raise ValueError("native dependency differs")
    return plan


def _reserve_all(stores: list[IncidentAuditArchive]) -> None:
    for store in stores:
        for ordinal in range(4):
            if not store.reserve(f"p{ordinal}", now=0, until=100, bound=4096):
                raise ValueError("frozen reservation refused")


def transfer_worker(directory: Path, arm: str, seed: int = 0) -> dict[str, Any]:
    directory.mkdir()
    stores = [
        IncidentAuditArchive(directory / "whole.sqlite", "static", 65536),
        IncrementalAuditArchive(directory / "incremental.sqlite", "static", 65536),
    ]
    _reserve_all(stores)

    def persist(row: dict[str, Any]) -> None:
        for store in stores:
            if not store.put(row["frame"], now=row["ordinal"] + 1):
                raise ValueError("frozen growth promise failed")

    result = execute(arm, persist, seed)
    offers = []
    scopes = [[f"p{i}"] for i in range(4)] + [[f"p{i}" for i in range(4)]]
    for ordinal, tokens in enumerate(scopes):
        now, identifier = 10 + ordinal, f"audit-{ordinal}"
        pair = []
        for store in stores:
            admitted = store.demand(identifier, tokens, now=now, until=100)
            answers = store.query(tokens, now=now)
            pair.append(
                {"admitted": admitted, "answers": answers, "witness": store.witness(tokens)}
            )
        if pair[0] != pair[1]:
            raise ValueError("physical baseline changed logical service")
        offers.append({"scope": tokens, **pair[0]})
    result["offers"] = offers
    result["same_service"] = True
    result["frontier"] = [store.snapshot() for store in stores]
    for store in stores:
        store.close()
    result["closed_database_bytes"] = [
        path.stat().st_size
        for path in (directory / "whole.sqlite", directory / "incremental.sqlite")
    ]
    return result


def worker(
    root: Path,
    study: Path,
    output: Path,
    kind: str,
    arm: str,
    mode: str,
    count: int,
    repeat: int,
    seed: int = 0,
) -> dict[str, Any]:
    check_plan(root, study)
    if output.parent != study or output.exists() or output.is_symlink():
        raise ValueError("fresh owned worker output required")

    def deny(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("local audit study cannot use sockets")

    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
    ):
        result = (
            transfer_worker(study / output.stem, arm, seed)
            if kind == "transfer"
            else run_cost(study / output.stem, mode, count, repeat)
        )
    try:
        import resource

        result["process_peak_rss_native_units"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["rss_units"] = "bytes" if sys.platform == "darwin" else "KiB"
    except ImportError:
        result["process_peak_rss_native_units"] = None
    write_new_file(output, encode(sealed(result)).encode())
    return {"status": "owned_worker_complete", "provider_calls": 0}


def analyze(study: Path) -> dict[str, Any]:
    plan = read(study / "plan.json")
    predictions: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    for arm in ARMS:
        path = study / f"transfer-{arm}.json"
        if not path.exists():
            predictions.append({"arm": arm, "status": "insufficient_evidence"})
            continue
        document = read(path)
        rows = document["chain_rows"]
        expected_status, expected_resolution = plan["transfer_forecasts"][arm]
        supported = (
            document["source_status"] == expected_status
            and len(rows) == 4
            and rows[-1]["resolution"] == expected_resolution
        )
        predictions.append(
            {
                "arm": arm,
                "status": "supported" if supported else "contradicted",
                "source_status": document["source_status"],
                "last_resolution": rows[-1]["resolution"] if rows else None,
                "native_predictions": document["attempted_native_predictions"],
            }
        )
        truth = {row["frame"]["token"]: row["reference"] for row in rows}
        for offer in document["offers"]:
            complete = (
                bool(offer["scope"])
                and set(offer["answers"]) == set(offer["scope"])
                and all(
                    answer in {"compliant", "violation"} and answer == truth.get(token)
                    for token, answer in offer["answers"].items()
                )
            )
            false = any(
                answer in {"compliant", "violation"} and answer != truth.get(token)
                for token, answer in offer["answers"].items()
            )
            offers.append(
                {
                    "arm": arm,
                    "scopes": offer["scope"],
                    "accepted": offer["admitted"],
                    "complete_correct": complete,
                    "false_conclusive": false,
                    "accepted_but_unserved": offer["admitted"] and not complete,
                    "deadline_basis": "event horizon, not an operator wall SLO",
                }
            )
    costs = []
    for count in (16, 64):
        for mode in MODES:
            documents = [
                read(study / f"cost-{mode}-{count}-{repeat}.json")
                for repeat in (0, 1, 2)
                if (study / f"cost-{mode}-{count}-{repeat}.json").exists()
            ]
            costs.append(
                {
                    "mode": mode,
                    "count": count,
                    "completed_workers": len(documents),
                    **{
                        key: median(d[key] for d in documents) if documents else None
                        for key in (
                            "serving_stage_ns",
                            "query_ns",
                            "verify_ns",
                            "sql_submitted_payload_bytes",
                            "closed_db_bytes",
                            "peak_db_wal_shm_bytes",
                        )
                    },
                    "correct_audit_count": [
                        sum(a == "compliant" for a in d["answers"].values()) for d in documents
                    ],
                    "numerical_reference_pass": bool(documents)
                    and all(d["numerical_correct"] for d in documents),
                }
            )
    return sealed(
        {
            "plan_sha256": plan["sha256"],
            "predictions": predictions,
            "offers": offers,
            "costs": costs,
            "accepted_but_unserved": sum(o["accepted_but_unserved"] for o in offers),
            "false_conclusive": sum(o["false_conclusive"] for o in offers),
            "A_disposition": "bounded scientific envelope; no natural-deployment claim",
            "B_disposition": "narrow unless same-service residual gap established",
            "parent_status": "In progress: operational demand/SLO grounding unavailable",
            "provider_calls": 0,
        }
    )


def run(root: Path, study: Path) -> dict[str, Any]:
    check_plan(root, study)
    if (study / "execution-census.json").exists():
        raise ValueError("new-frame execution already attempted")
    write_new_file(study / "execution-census.json", encode(sealed({"status": "started"})).encode())
    write_new_file(study / "model-check.json", encode(sealed(check_model())).encode())
    write_new_file(study / "native-counterpair.json", encode(sealed(native_counterpair())).encode())
    jobs = [(f"transfer-{arm}", ["--kind", "transfer", "--arm", arm]) for arm in ARMS]
    jobs += [
        (
            f"cost-{mode}-{count}-{repeat}",
            ["--kind", "cost", "--mode", mode, "--count", str(count), "--repeat", str(repeat)],
        )
        for count in (16, 64)
        for repeat in (0, 1, 2)
        for mode in MODES
    ]
    census = []
    for name, args in jobs:
        environment = {
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        }
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
            *args,
        ]
        try:
            completed = subprocess.run(
                command, env=environment, capture_output=True, timeout=120, check=False
            )
            row = {
                "worker": name,
                "returncode": completed.returncode,
                "stdout_sha256": content_sha256(completed.stdout),
                "stderr_sha256": content_sha256(completed.stderr),
            }
            write_new_file(study / f"{name}.stderr", completed.stderr)
        except subprocess.TimeoutExpired as exc:
            row = {
                "worker": name,
                "returncode": None,
                "failure": "timeout",
                "stdout_sha256": content_sha256(exc.stdout or b""),
            }
        census.append(row)
        print(
            json.dumps(
                {
                    "status": "closeout_progress",
                    "completed": len(census),
                    "planned": len(jobs),
                    "returncode": row["returncode"],
                }
            ),
            flush=True,
        )
    write_new_file(study / "completed-census.json", encode(sealed({"workers": census})).encode())
    from aletheia_lab.evaluation.auditability_closeout_controls import (
        crash_controls,
        service_controls,
    )

    write_new_file(
        study / "service-controls.json", encode(sealed(service_controls(study))).encode()
    )
    write_new_file(
        study / "crash-controls.json",
        encode(sealed({"rows": crash_controls(root, study)})).encode(),
    )
    result = analyze(study)
    write_new_file(study / "analysis.json", encode(result).encode())
    return {
        "status": "new_frame_executed",
        "workers": len(census),
        "failed_workers": sum(row["returncode"] != 0 for row in census),
        "analysis_sha256": result["sha256"],
        "provider_calls": 0,
    }
