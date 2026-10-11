"""Independent replay falsifiers on synthetic receipt stores; zero model loads."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_serving_analysis as analysis
from aletheia_lab.evaluation.model_load_serving_store import ServingStore
from aletheia_lab.evaluation.model_load_serving_workload import decide

A, B = "a" * 64, "b" * 64


@pytest.fixture
def synthetic(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    store = ServingStore(tmp_path / "test.sqlite", "static", 8)
    rows, audits, targets = [], [], []
    generation, resident_truth = None, None
    for step in range(12):
        expected = A if step % 2 == 0 else B
        failed = step in (3, 7, 11)
        observed = [] if failed else [B if step in (2, 6, 10) else expected]
        scope = f"load-{step}"
        verdict = None if failed else "violation" if expected != observed[0] else "compliant"
        if not failed:
            generation, resident_truth = scope, verdict
        for kind, index in [("load", -1), *[("infer", i) for i in range(16)]]:
            name = scope if kind == "load" else f"slot-{step}-infer-{index}"
            values = observed if kind == "load" else []
            frame = {
                "scope": name,
                "kind": kind,
                "step": step,
                "domain": [A, B],
                "expected": expected if kind == "load" else None,
                "observed": values,
                "count": len(values),
                "closed": True,
                "status": 422 if failed and kind == "load" else 200,
                "generation": generation,
            }
            truth = {
                "verdict": verdict if kind == "load" else None,
                "eligibility": "load" if kind == "load" and not failed else "no_new_load",
                "resident": resident_truth if kind == "infer" else None,
            }
            events = [
                {
                    "digest": digest,
                    "completed": True,
                    "bytes": 1800,
                    "read_ns": 1,
                    "hash_ns": 2,
                    "reconstruct_ns": 3,
                }
                for digest in values
            ]
            row = {
                "scope": name,
                "step": step,
                "kind": kind,
                "frame": frame,
                "truth": truth,
                "status": frame["status"],
                "error": None,
                "events": events,
                "rest_ns": 10,
                "write_ns": 3,
                "end_to_end_ns": 13,
            }
            rows.append(row)
            store.append(name, step, frame, generation if kind == "infer" else None, {})
            store.keep_parent(generation)
            if kind == "load" or index == 15:
                targets.append(row)
        for target in targets:
            age = step - target["step"]
            if age in (0, 2, 8):
                snapshot = store.query(target["scope"])
                assert snapshot is not None
                audits.append(
                    {
                        "scope": target["scope"],
                        "age": age,
                        "kind": target["kind"],
                        "truth": target["truth"],
                        "snapshot": snapshot,
                        "available": True,
                        "decision": decide(snapshot["frame"], snapshot["parent_frame"]),
                        "query_ns": 1,
                        "verify_ns": 2,
                    }
                )
        store.retire(step)
    stats = store.stats()
    store.close()
    result = {
        "status": "complete",
        "config": {"depth": 2, "repeat": 0, "arm": "static", "horizon": 8, "inferences": 16},
        "rows": rows,
        "audits": audits,
        "store": stats,
        **dict.fromkeys(
            (
                "setup_ns",
                "reference_ns",
                "retire_ns",
                "close_ns",
                "collector_init_ns",
                "monitor_ns",
                "cpu_ns",
                "peak_process_rss_bytes",
            ),
            1,
        ),
    }
    return result, {"bands": {"2": {"A": {"digest": A}, "B": {"digest": B}}}}


def test_complete_worker_replay_and_frontier(synthetic: Any) -> None:
    worker, models = synthetic
    analysis.validate_worker(worker, models)
    summary = analysis.worker_summary(worker)
    assert summary["operation_count"] == 204 and summary["http_failures"] == 3
    assert summary["captured_reconstructions"] == 9
    assert summary["audit"]["8"]["correct"] == 8
    assert summary["latency"]["successful_load"]["rest"]["n"] == 9
    workers = [deepcopy(worker) for _ in range(3)]
    for repeat, item in enumerate(workers):
        item["config"]["repeat"] = repeat
    report = analysis.analyze(workers, models)
    assert report["frontier"][1]["cheapest_measured_key"] == "d2-i16-h8-static"
    assert report["cells"][0]["audit"]["0"]["denominator"] == 72


@pytest.mark.parametrize(
    "mutation",
    [
        "denominator",
        "status",
        "reference",
        "generation",
        "capture",
        "duration",
        "parent",
        "availability",
        "decision",
        "closure",
    ],
)
def test_resealed_semantic_tampering_cannot_pass(synthetic: Any, mutation: str) -> None:
    worker, models = synthetic
    if mutation == "denominator":
        worker["rows"].pop()
    elif mutation == "status":
        worker["rows"][0]["status"] = 500
    elif mutation == "reference":
        worker["rows"][0]["truth"]["verdict"] = "violation"
    elif mutation == "generation":
        worker["rows"][1]["frame"]["generation"] = "invented"
    elif mutation == "capture":
        worker["rows"][0]["events"][0]["completed"] = False
    elif mutation == "duration":
        worker["rows"][0]["rest_ns"] = -1
    elif mutation == "parent":
        worker["audits"][1]["snapshot"]["parent"] = None
    elif mutation == "availability":
        worker["audits"][0].update(
            snapshot=None,
            available=False,
            decision={"verdict": "unknown", "eligibility": "undetermined", "resident": None},
        )
    elif mutation == "decision":
        worker["audits"][0]["decision"]["verdict"] = "violation"
    else:
        worker["rows"][0]["frame"]["count"] = 2
    with pytest.raises(ValueError):
        analysis.validate_worker(worker, models)


def test_duplicate_audit_and_changed_snapshot_rejected(synthetic: Any) -> None:
    worker, models = synthetic
    original = deepcopy(worker)
    worker["audits"].append(deepcopy(worker["audits"][0]))
    with pytest.raises(ValueError, match="audit census"):
        analysis.validate_worker(worker, models)
    original["audits"][0]["snapshot"]["frame"]["expected"] = B
    with pytest.raises(ValueError, match="durable frame"):
        analysis.validate_worker(original, models)
