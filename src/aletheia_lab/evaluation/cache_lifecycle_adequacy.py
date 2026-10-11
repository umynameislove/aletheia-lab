"""Additive development analysis: source capabilities and query-specific audit cost.

Native execution is never repeated. Observations already used in development
support scope refinement, not another prospective/held-out validation claim.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_analysis import reference, retained_records
from aletheia_lab.evaluation.cache_lifecycle_certificate import (
    certificate_answers,
    lifecycle_answers,
)
from aletheia_lab.evaluation.cache_lifecycle_materialization import (
    CANDIDATES,
    _verify_digest,
    materialize,
    verify_materialization,
)
from aletheia_lab.evaluation.cache_lifecycle_native_control import (
    client_projection,
    infer_client_contract,
)
from aletheia_lab.evaluation.cache_lifecycle_source import SUFFICIENT
from aletheia_lab.evaluation.cache_lifecycle_study import check_bindings, read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

TIERS = {"interval": (False, False), "cache_history": (True, False), "driver_premise": (True, True)}


def scores(truth: dict[str, Any], answers: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        denominator=len(truth["truth"]), correct=0, false=0, unknown=0, delivered=0
    )
    for token, verdict in truth["truth"].items():
        answer = answers.get(token, {})
        expected = (verdict, truth["producers"][token], truth["closure"][token])
        observed = (answer.get("verdict"), answer.get("producer"), answer.get("closure"))
        if observed == expected:
            counts["correct"] += 1
        elif answer.get("verdict") in {"compliant", "violation"}:
            counts["false"] += 1
        else:
            counts["unknown"] += 1
        counts["delivered"] += int(answer.get("delivery") == "observed")
    return dict(counts)


def native_comparison(source: dict[str, Any]) -> dict[str, Any]:
    client = client_projection(source)
    answers = {
        name: infer_client_contract(client, cache_history=history, driver_barrier=driver)
        for name, (history, driver) in TIERS.items()
    }
    # Only after all comparator predictions: raw arithmetic/state oracle enters.
    truth = reference(source)
    output = {}
    inference = [row for row in source["rows"] if row["route"] == "/infer"]
    for name, values in answers.items():
        result: Counter[str] = Counter(
            denominator=len(inference), correct=0, false=0, unknown=0, conflict=0
        )
        for row, answer in zip(inference, values, strict=True):
            result["unknown"] += int(answer == "unknown")
            result["conflict"] += int(answer == "conflict")
            if answer in {"compliant", "violation"}:
                actual = truth["truth"][row["token"]]
                result["correct" if answer == actual else "false"] += 1
                result["correct_violation"] += int(answer == actual == "violation")
        output[name] = dict(result)
    return {"scores": output, "client_logical_bytes": len(encode(client).encode())}


def dependency_comparison(records: list[dict[str, Any]], truth: dict[str, Any]) -> dict[str, Any]:
    # Observational deletion is an offline service ablation, never capture loss.
    return {
        kind: scores(truth, certificate_answers([r for r in records if r["kind"] != kind]))
        for kind in (
            "wrapper_return",
            "compute_return",
            "load_return",
            "handler_terminal",
            "response",
            "publish",
            "load_failure",
        )
    }


def _read_sources(
    root: Path, directory: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    plan, original = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    check_bindings(root, plan)
    if (
        len(original["executions"]) != len(plan["cells"])
        or original["plan_sha256"] != plan["sha256"]
    ):
        raise ValueError("original complete-census binding differs")
    sources = []
    for index, (cell, execution) in enumerate(
        zip(plan["cells"], original["executions"], strict=True)
    ):
        target = directory / f"cell-{index:03}"
        path = target / "source.json"
        if (
            path.is_symlink()
            or content_sha256(path.read_bytes()) != execution["source_sha256"]
            or cell != execution["config"]
            or execution["index"] != index
        ):
            raise ValueError("original source identity differs")
        source = json.loads(path.read_bytes())
        if source["terminal"] != "complete" or execution["returncode"] != 0:
            raise ValueError("this extension requires the previously complete native census")
        reference(source)
        retained_records(target, source)
        if cell["evidence"] != "none":
            _verify_digest(
                target / "provenance", content_sha256((target / "audit.sqlite").read_bytes())
            )
        sources.append(source)
    return plan, original, sources


def _measure_cell(
    index: int, source: dict[str, Any], directory: Path, binding: dict[str, Any]
) -> list[dict[str, Any]]:
    records = [row for row in source["audit_query"]["records"] if row["kind"] in SUFFICIENT]
    truth = reference(source)
    expected_reload = lifecycle_answers(records)
    candidates = list(CANDIDATES)
    random.Random(20261007 + index).shuffle(candidates)
    results = []
    for candidate in candidates:
        target = directory / f"cell-{index:03}" / candidate
        try:
            measured = materialize(records, candidate, target, binding)
            scored = scores(truth, measured["answers"])
            if (
                scored["correct"] != 72
                or scored["false"]
                or scored["delivered"] != 72
                or measured["reload_ledger"] != expected_reload
            ):
                raise ValueError("ordinary projection changed offered audit service")
            verify_materialization(
                target, measured, expected_records=records, expected_binding=binding
            )
            results.append(
                {
                    "cell": index,
                    "source_config": source["config"],
                    "verification": "pass",
                    "service": scored,
                    **measured,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "cell": index,
                    "candidate": candidate,
                    "verification": "fail",
                    "error_type": type(exc).__name__,
                    "service": {
                        "denominator": 72,
                        "correct": 0,
                        "false": 0,
                        "unknown": 72,
                        "delivered": 0,
                    },
                }
            )
    return results


def summarize(cells: list[dict[str, Any]], materials: list[dict[str, Any]]) -> dict[str, Any]:
    native = {
        name: dict(
            Counter(
                {
                    key: sum(cell["native"]["scores"][name].get(key, 0) for cell in cells)
                    for key in (
                        "denominator",
                        "correct",
                        "false",
                        "unknown",
                        "conflict",
                        "correct_violation",
                    )
                }
            )
        )
        for name in TIERS
    }
    costs = []
    for candidate in CANDIDATES:
        group = [row for row in materials if row["candidate"] == candidate]
        passed = [row for row in group if row["verification"] == "pass"]
        costs.append(
            {
                "candidate": candidate,
                "planned_cells": len(group),
                "successful_cells": len(passed),
                "correct": sum(row["service"]["correct"] for row in group),
                "unknown": sum(row["service"]["unknown"] for row in group),
                "medians": {
                    key: median(row[key] for row in passed) if passed else None
                    for key in (
                        "records",
                        "commits",
                        "logical_bytes",
                        "physical_live_bytes",
                        "physical_closed_bytes",
                        "provenance_bytes",
                    )
                },
                "times_median_ns": {
                    key: median(row["times_ns"][key] for row in passed) if passed else None
                    for key in (
                        "encoding",
                        "persistence",
                        "query",
                        "reconstruction",
                        "hashing",
                        "signing_verification",
                    )
                },
                "durability": passed[0]["durability"] if passed else "unmeasured",
            }
        )
    ablations = {
        kind: {
            key: sum(cell["dependencies"].get(kind, {}).get(key, 0) for cell in cells)
            for key in ("denominator", "correct", "false", "unknown", "delivered")
        }
        for kind in (
            "wrapper_return",
            "compute_return",
            "load_return",
            "handler_terminal",
            "response",
            "publish",
            "load_failure",
        )
    }
    return {
        "native_generation_compliance": native,
        "dependency_ablation": ablations,
        "offline_cost": costs,
        "native_cells": len(cells),
        "materialized_cells": len(materials),
        "materialized_failures": [
            {"cell": r["cell"], "candidate": r["candidate"]}
            for r in materials
            if r["verification"] != "pass"
        ],
        "provider_calls": 0,
        "native_workload_reruns": 0,
        "scope": "Post-result development over the immutable trace; nested samples. Conditional generation service != materialized producer/closure/delivery certificate. Projection/batching are ordinary methods, not optimality/novelty. Offline IO is not serving latency; drain batching weakens prefix durability.",
    }


def run(root: Path, directory: Path, prototype: Path | None = None) -> dict[str, Any]:
    output = directory / "adequacy-development"
    if output.exists() or output.is_symlink():
        raise ValueError("analysis output already exists; verify instead of rerunning timing")
    plan, original, sources = _read_sources(root, directory)
    output.mkdir()
    cells, materials = [], []
    for index, source in enumerate(sources):
        truth = reference(source)
        records = source["audit_query"]["records"]
        cells.append(
            {
                "cell": index,
                "native": native_comparison(source),
                "certificate": scores(truth, certificate_answers(records)),
                "dependencies": dependency_comparison(records, truth) if records else {},
            }
        )
        if records:
            binding = {
                "plan_sha256": plan["sha256"],
                "source_sha256": original["executions"][index]["source_sha256"],
                "cell": index,
            }
            materials.extend(_measure_cell(index, source, output, binding))
    proof = None
    if prototype is not None:
        proof = json.loads((prototype / "result.json").read_bytes())
        for name in ("probe.py", "result.json"):
            write_new_file(output / "zero-refill-prototype" / name, (prototype / name).read_bytes())
        proof = {
            "result": proof,
            "code_sha256": content_sha256((prototype / "probe.py").read_bytes()),
            "result_sha256": content_sha256((prototype / "result.json").read_bytes()),
        }
    report = seal(
        {
            "schema": "cache-lifecycle-adequacy-development/v1",
            "original_results_sha256": original["sha256"],
            "plan_sha256": plan["sha256"],
            "code_bindings": code_bindings(),
            "cells": cells,
            "materializations": materials,
            "exploratory_zero_refill": proof,
            "analysis": summarize(cells, materials),
        }
    )
    write_new_file(output / "results.json", encode(report).encode())
    return {
        "verification": "pass" if not report["analysis"]["materialized_failures"] else "incomplete",
        "sha256": report["sha256"],
        "analysis": report["analysis"],
    }


def code_bindings() -> dict[str, str]:
    own = Path(__file__)
    return {
        name: content_sha256((own.parent / name).read_bytes())
        for name in (
            own.name,
            "cache_lifecycle_certificate.py",
            "cache_lifecycle_native_control.py",
            "cache_lifecycle_materialization.py",
            "cache_lifecycle_analysis.py",
            "cache_lifecycle_study.py",
            "litserve_evidence_provenance.py",
            "model_load_provenance.py",
        )
    }


def _check_census(report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    cells = report["cells"]
    if len(cells) != len(sources) or [row["cell"] for row in cells] != list(range(len(sources))):
        raise ValueError("additive cell census differs")
    expected = {
        (index, candidate)
        for index, source in enumerate(sources)
        if source["audit_query"]["records"]
        for candidate in CANDIDATES
    }
    observed = [(row["cell"], row["candidate"]) for row in report["materializations"]]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("ordinary materialization census differs")


def _verify_probe(output: Path, proof: dict[str, Any] | None) -> None:
    if proof is None:
        return
    folder = output / "zero-refill-prototype"
    code, result = folder / "probe.py", folder / "result.json"
    if (
        code.is_symlink()
        or result.is_symlink()
        or content_sha256(code.read_bytes()) != proof["code_sha256"]
        or content_sha256(result.read_bytes()) != proof["result_sha256"]
        or json.loads(result.read_bytes()) != proof["result"]
    ):
        raise ValueError("exploratory probe archive changed")


def verify(root: Path, directory: Path) -> dict[str, Any]:
    output = directory / "adequacy-development"
    plan, original, sources = _read_sources(root, directory)
    report = read_sealed(output / "results.json")
    if (
        report["original_results_sha256"] != original["sha256"]
        or report["plan_sha256"] != plan["sha256"]
        or report["code_bindings"] != code_bindings()
    ):
        raise ValueError("analysis code/source binding changed")
    _check_census(report, sources)
    for index, source in enumerate(sources):
        before = report["cells"][index]
        if (
            before["cell"] != index
            or before["native"] != native_comparison(source)
            or before["certificate"]
            != scores(reference(source), certificate_answers(source["audit_query"]["records"]))
            or before["dependencies"]
            != (
                dependency_comparison(source["audit_query"]["records"], reference(source))
                if source["audit_query"]["records"]
                else {}
            )
        ):
            raise ValueError("additive service replay changed")
    for row in report["materializations"]:
        if row["verification"] == "pass":
            index = row["cell"]
            records = [
                value
                for value in sources[index]["audit_query"]["records"]
                if value["kind"] in SUFFICIENT
            ]
            binding = {
                "plan_sha256": plan["sha256"],
                "source_sha256": original["executions"][index]["source_sha256"],
                "cell": index,
            }
            if row["source_config"] != sources[index]["config"] or row["service"] != scores(
                reference(sources[index]), row["answers"]
            ):
                raise ValueError("materialized service/cell binding differs")
            verify_materialization(
                output / f"cell-{index:03}" / row["candidate"],
                row,
                expected_records=records,
                expected_binding=binding,
            )
        elif row["verification"] != "fail":
            raise ValueError("materialization terminal status unavailable")
    _verify_probe(output, report["exploratory_zero_refill"])
    if summarize(report["cells"], report["materializations"]) != report["analysis"]:
        raise ValueError("cost/service aggregate changed")
    return {"verification": "pass", "sha256": report["sha256"], "analysis": report["analysis"]}
