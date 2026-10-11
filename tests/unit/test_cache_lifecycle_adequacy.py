"""SDK-free service distinctions and resealed-report integration counterexamples."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import cache_lifecycle_adequacy as adequacy
from aletheia_lab.project.identity import content_sha256


def truth(count: int = 1) -> dict[str, Any]:
    return {
        "truth": {f"r{i}": "compliant" for i in range(count)},
        "producers": {f"r{i}": "producer" for i in range(count)},
        "closure": {f"r{i}": "closed" for i in range(count)},
    }


def answers(count: int = 1) -> dict[str, dict[str, Any]]:
    return {
        f"r{i}": {
            "verdict": "compliant",
            "producer": "producer",
            "closure": "closed",
            "delivery": "observed",
        }
        for i in range(count)
    }


@pytest.mark.parametrize("field,value", [("producer", "other"), ("closure", "unknown")])
def test_joint_service_rejects_correct_verdict_with_wrong_producer_or_closure(
    field: str, value: str
) -> None:
    offered = answers()
    offered["r0"][field] = value
    result = adequacy.scores(truth(), offered)
    assert result == {"denominator": 1, "correct": 0, "false": 1, "unknown": 0, "delivered": 1}


def test_delivery_is_separate_from_joint_certificate_and_missing_answers_keep_denominator() -> None:
    offered = answers()
    offered["r0"]["delivery"] = "conflict"
    result = adequacy.scores(truth(2), offered)
    assert result == {"denominator": 2, "correct": 1, "false": 0, "unknown": 1, "delivered": 0}


def test_native_comparison_reports_stronger_capability_without_reference_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def client_row(
        start: int, end: int, route: str, body: dict[str, Any], response: str
    ) -> dict[str, Any]:
        return {
            "token": f"r{start}",
            "route": route,
            "body": body,
            "status": 200,
            "raw_response": response,
            "completion_ns": end,
            "elapsed_ns": end - start,
        }

    source = {
        "config": {"arm": "input_key"},
        "events": object(),  # Comparator must not inspect execution hooks.
        "artifacts": {
            "A": {"coefficient": 1, "intercept": 0},
            "B": {"coefficient": 2, "intercept": 0},
        },
        "rows": [
            client_row(0, 10, "/infer", {"x": 0}, '{"y":0}'),
            client_row(11, 19, "/infer", {"x": 0}, '{"y":0}'),
            client_row(20, 60, "/infer", {"x": 1}, '{"y":1}'),
            client_row(30, 40, "/reload", {"artifact": "B"}, '{"loaded_generation":"B"}'),
            client_row(41, 45, "/infer", {"x": 0}, '{"y":0}'),
            client_row(61, 70, "/infer", {"x": 1}, '{"y":1}'),
        ],
    }
    expected = {
        "r0": "compliant",
        "r11": "compliant",
        "r20": "compliant",
        "r41": "violation",
        "r61": "violation",
    }
    monkeypatch.setattr(adequacy, "reference", lambda value: {"truth": expected})
    result = adequacy.native_comparison(source)
    assert result["scores"]["interval"]["correct"] == 1
    assert result["scores"]["interval"]["unknown"] == 4
    assert result["scores"]["cache_history"]["correct"] == 5
    assert result["scores"]["driver_premise"]["false"] == 0


def materialization(candidate: str, *, passed: bool, cell: int = 1) -> dict[str, Any]:
    result: dict[str, Any] = {
        "cell": cell,
        "candidate": candidate,
        "verification": "pass" if passed else "fail",
        "service": {
            "denominator": 72,
            "correct": 72 if passed else 0,
            "false": 0,
            "unknown": 0 if passed else 72,
            "delivered": 72 if passed else 0,
        },
    }
    if passed:
        result["source_config"] = {"evidence": "sufficient"}
        result["answers"] = answers(72)
        result.update(
            {
                key: 1
                for key in (
                    "records",
                    "commits",
                    "logical_bytes",
                    "physical_live_bytes",
                    "physical_closed_bytes",
                    "provenance_bytes",
                )
            }
        )
        result["times_ns"] = {
            key: 1
            for key in (
                "encoding",
                "persistence",
                "query",
                "reconstruction",
                "hashing",
                "signing_verification",
            )
        }
        result["durability"] = "declared test boundary"
    return result


def test_summary_keeps_failed_materialization_in_planned_service_denominator() -> None:
    candidate = adequacy.CANDIDATES[0]
    result = adequacy.summarize(
        [],
        [materialization(candidate, passed=True), materialization(candidate, passed=False, cell=2)],
    )
    row = next(item for item in result["offline_cost"] if item["candidate"] == candidate)
    assert (row["planned_cells"], row["successful_cells"], row["correct"], row["unknown"]) == (
        2,
        1,
        72,
        72,
    )
    assert result["materialized_failures"] == [{"cell": 2, "candidate": candidate}]


@pytest.fixture
def report_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    sources = [
        {"config": {"evidence": "none"}, "audit_query": {"records": []}},
        {
            "config": {"evidence": "sufficient"},
            "audit_query": {"records": [{"kind": "load_failure", "sequence": 0}]},
        },
    ]
    plan = {"sha256": "plan", "cells": [s["config"] for s in sources]}
    original = {
        "sha256": "original",
        "executions": [{"source_sha256": "source0"}, {"source_sha256": "source1"}],
    }
    native = {
        "scores": {
            name: {"denominator": 72, "correct": 72, "false": 0, "unknown": 0, "conflict": 0}
            for name in adequacy.TIERS
        }
    }
    dependencies = {
        "load_failure": {
            "denominator": 72,
            "correct": 72,
            "false": 0,
            "unknown": 0,
            "delivered": 72,
        }
    }
    monkeypatch.setattr(adequacy, "_read_sources", lambda *args: (plan, original, sources))
    monkeypatch.setattr(adequacy, "code_bindings", lambda: {"code": "bound"})
    monkeypatch.setattr(adequacy, "reference", lambda source: truth(72))
    monkeypatch.setattr(
        adequacy, "certificate_answers", lambda records: answers(72) if records else {}
    )
    monkeypatch.setattr(adequacy, "native_comparison", lambda source: deepcopy(native))
    monkeypatch.setattr(
        adequacy, "dependency_comparison", lambda records, expected: deepcopy(dependencies)
    )
    verified_inputs: list[dict[str, Any]] = []

    def verify_materialization(*args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
        verified_inputs.append(kwargs)
        return answers(72)

    monkeypatch.setattr(adequacy, "verify_materialization", verify_materialization)
    cells = [
        {
            "cell": index,
            "native": deepcopy(native),
            "certificate": adequacy.scores(truth(72), answers(72) if index else {}),
            "dependencies": deepcopy(dependencies) if index else {},
        }
        for index in range(2)
    ]
    materials = [materialization(candidate, passed=True) for candidate in adequacy.CANDIDATES]
    report = {
        "original_results_sha256": "original",
        "plan_sha256": "plan",
        "code_bindings": {"code": "bound"},
        "cells": cells,
        "materializations": materials,
        "exploratory_zero_refill": None,
    }
    monkeypatch.setattr(
        adequacy,
        "read_sealed",
        lambda path: adequacy.seal(
            {**report, "analysis": adequacy.summarize(report["cells"], report["materializations"])}
        ),
    )
    assert adequacy.verify(tmp_path, tmp_path)["verification"] == "pass"
    assert verified_inputs == [
        {
            "expected_records": sources[1]["audit_query"]["records"],
            "expected_binding": {"plan_sha256": "plan", "source_sha256": "source1", "cell": 1},
        }
    ] * len(adequacy.CANDIDATES)
    return report, tmp_path


@pytest.mark.parametrize("scope", ["cell", "materialization", "duplicate_materialization"])
def test_resealed_report_cannot_drop_or_duplicate_planned_census(
    report_fixture: tuple[dict[str, Any], Path], scope: str
) -> None:
    report, directory = report_fixture
    if scope == "cell":
        report["cells"].pop()
    elif scope == "materialization":
        report["materializations"].pop()
    else:
        report["materializations"].append(deepcopy(report["materializations"][0]))
    with pytest.raises(ValueError, match="census"):
        adequacy.verify(directory, directory)


def test_resealed_dependency_counts_are_recomputed(
    report_fixture: tuple[dict[str, Any], Path],
) -> None:
    report, directory = report_fixture
    report["cells"][1]["dependencies"]["load_failure"]["correct"] = 71
    with pytest.raises(ValueError, match="replay|dependenc"):
        adequacy.verify(directory, directory)


def test_resealed_materialization_cannot_claim_another_source_cell(
    report_fixture: tuple[dict[str, Any], Path],
) -> None:
    report, directory = report_fixture
    report["materializations"][0]["source_config"] = {"evidence": "none"}
    with pytest.raises(ValueError, match="binding"):
        adequacy.verify(directory, directory)


@pytest.mark.parametrize("changed", ["code", "result", "embedded_result"])
def test_resealed_prototype_must_bind_archived_code_and_result_bytes(
    report_fixture: tuple[dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    report, directory = report_fixture
    code = b"print('bounded fixture')\n"
    result = b'{"counterexample":true}'
    report["exploratory_zero_refill"] = {
        "result": json.loads(result),
        "code_sha256": content_sha256(code),
        "result_sha256": content_sha256(result),
    }

    def archived_bytes(path: Path) -> bytes:
        return code if path.name == "probe.py" else result

    monkeypatch.setattr(Path, "read_bytes", archived_bytes)
    assert adequacy.verify(directory, directory)["verification"] == "pass"
    if changed == "code":
        code = b"print('altered fixture')\n"
    elif changed == "result":
        result = b'{"counterexample":false}'
    else:
        report["exploratory_zero_refill"]["result"] = {"counterexample": False}
    with pytest.raises(ValueError, match="probe|prototype"):
        adequacy.verify(directory, directory)
