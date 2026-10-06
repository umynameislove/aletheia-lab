from __future__ import annotations

from copy import deepcopy
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.audit_bundle_screen import replay
from aletheia_lab.evaluation.audit_bundle_verify import validate_records

A, B = "a" * 64, "b" * 64
POLICIES = ("static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange")
MODELS = {"bands": {"6": {"A": {"digest": A}, "B": {"digest": B}}}}


def native(fanout: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    generation, resident = None, None
    for step in range(12):
        failed = step in (3, 7, 11)
        expected = A if step % 2 == 0 else B
        delivered = B if step in (2, 6, 10) else expected
        scope = f"load-{step}"
        if not failed:
            generation = scope
            resident = "compliant" if delivered == expected else "violation"
        frame = {
            "scope": scope,
            "kind": "load",
            "step": step,
            "domain": [A, B],
            "expected": expected,
            "observed": [] if failed else [delivered],
            "count": 0 if failed else 1,
            "closed": True,
            "status": 422 if failed else 200,
            "generation": generation,
        }
        truth = {
            "eligibility": "no_new_load" if failed else "load",
            "verdict": None if failed else resident,
            "resident": None,
        }
        rows.append(_row(frame, truth))
        for index in range(fanout):
            child = {
                **frame,
                "scope": f"slot-{step}-infer-{index}",
                "kind": "infer",
                "expected": None,
                "observed": [],
                "count": 0,
                "status": 200,
            }
            rows.append(
                _row(child, {"eligibility": "no_new_load", "verdict": None, "resident": resident})
            )
    audits = [
        {
            "scope": row["scope"],
            "kind": "load",
            "age": age,
            "truth": row["truth"],
            "snapshot": None,
            "available": False,
            "decision": {"eligibility": "undetermined", "verdict": "unknown", "resident": None},
        }
        for row, age in product(rows, (0, 2, 8))
        if row["kind"] == "load" and row["step"] + age < 12
    ]
    return {
        "status": "complete",
        "source_cluster_count": 1,
        "captured_reconstructions": 9,
        "config": {"repeat": 0, "depth": 6, "inferences": fanout, "arm": "hash", "horizon": 0},
        "rows": rows,
        "audits": audits,
    }


def _row(frame: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: frame[key] for key in ("scope", "kind", "step", "status")},
        "error": None,
        "frame": frame,
        "truth": truth,
        "events": [
            {"digest": value, "completed": True, "scope": frame["scope"]}
            for value in frame["observed"]
        ],
        "rest_ns": 1,
        "write_ns": 0,
        "end_to_end_ns": 1,
    }


@pytest.fixture(scope="module")
def evidence(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[Any], list[Any]]:
    directory = tmp_path_factory.mktemp("audit-verify")
    captures = [native(1), native(6)]
    results = []
    for capture, budget, schedule, policy in product(
        captures, (2048, 4096, 8192, 16384), ("last_child", "first_child"), POLICIES
    ):
        fanout = capture["config"]["inferences"]
        path: Path = directory / f"{fanout}-{budget}-{schedule}-{policy}.sqlite"
        result = replay(path, capture["rows"], policy, budget, schedule)
        results.append({**result, "fanout": fanout})
    return captures, results


def _complete(results: list[Any]) -> dict[str, Any]:
    return next(result for result in results if result["status"] == "complete")


def test_raw_accounting_and_independent_snapshot_optimum(
    evidence: tuple[list[Any], list[Any]],
) -> None:
    captures, results = evidence
    before = deepcopy((captures, results))
    validate_records(captures, MODELS, results)
    assert (captures, results) == before
    assert len(results) == 112
    assert any(result["status"] == "service_failure" for result in results)
    assert all(result["hard_failures"] == 0 for result in results if result["status"] == "complete")


@pytest.mark.parametrize("field", ["offered_queries", "correct", "hard_accepted", "hard_failures"])
def test_changed_totals_are_rejected(evidence: tuple[list[Any], list[Any]], field: str) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    _complete(results)[field] += 1
    with pytest.raises(ValueError):
        validate_records(captures, MODELS, results)


def test_missing_duplicate_cell_and_hidden_tail_are_rejected(
    evidence: tuple[list[Any], list[Any]],
) -> None:
    captures, saved = evidence
    with pytest.raises(ValueError, match="census"):
        validate_records(captures, MODELS, saved[:-1])
    results = deepcopy(saved)
    results[-1] = deepcopy(results[0])
    with pytest.raises(ValueError, match="census"):
        validate_records(captures, MODELS, results)
    results = deepcopy(saved)
    _complete(results)["queries"].pop()
    with pytest.raises(ValueError, match="omitted"):
        validate_records(captures, MODELS, results)


@pytest.mark.parametrize("field", ["utility", "cost", "enumerated"])
def test_fabricated_snapshot_optimum_is_rejected(
    evidence: tuple[list[Any], list[Any]], field: str
) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    _complete(results)["oracle_snapshots"][0]["oracle"][field] += 1
    with pytest.raises(ValueError, match="enumeration"):
        validate_records(captures, MODELS, results)


@pytest.mark.parametrize("field", ["selected_cost", "selected_utility", "optional_count"])
def test_snapshot_selected_score_is_recomputed(
    evidence: tuple[list[Any], list[Any]], field: str
) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    _complete(results)["oracle_snapshots"][0][field] += 1
    with pytest.raises(ValueError):
        validate_records(captures, MODELS, results)


def test_snapshot_cannot_drop_pin_or_change_charged_statistics(
    evidence: tuple[list[Any], list[Any]],
) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    snapshot = _complete(results)["oracle_snapshots"][0]
    snapshot["selected"] = []
    with pytest.raises(ValueError, match="pins"):
        validate_records(captures, MODELS, results)
    results = deepcopy(saved)
    snapshot = _complete(results)["oracle_snapshots"][0]
    scope = next(iter(snapshot["pool"]))
    snapshot["pool"][scope]["hits"] += 1
    with pytest.raises(ValueError, match="metadata"):
        validate_records(captures, MODELS, results)


def test_lease_answer_not_boolean_alone_is_verified(evidence: tuple[list[Any], list[Any]]) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    lease = next(lease for lease in _complete(results)["leases"] if lease["accepted"])
    lease["decision"] = None
    with pytest.raises(ValueError, match="lease score"):
        validate_records(captures, MODELS, results)
    results = deepcopy(saved)
    lease = next(lease for lease in _complete(results)["leases"] if lease["accepted"])
    del lease["decision"]
    with pytest.raises(ValueError, match="incomplete"):
        validate_records(captures, MODELS, results)


def test_failure_denominator_and_physical_cap_are_not_conflated(
    evidence: tuple[list[Any], list[Any]],
) -> None:
    captures, saved = evidence
    results = deepcopy(saved)
    failed = next(result for result in results if result["status"] == "service_failure")
    failed["failure_at"]["sequence"] += 1
    with pytest.raises(ValueError):
        validate_records(captures, MODELS, results)
    results = deepcopy(saved)
    _complete(results)["metrics"]["peak_logical_bytes"] = 1_000_000
    with pytest.raises(ValueError, match="cap exceeded"):
        validate_records(captures, MODELS, results)
    results = deepcopy(saved)
    _complete(results)["metrics"]["peak_sqlite_wal_shm_bytes"] = 1_000_000
    validate_records(captures, MODELS, results)


def test_native_occurrence_binding_is_checked(evidence: tuple[list[Any], list[Any]]) -> None:
    captures, results = evidence
    changed = deepcopy(captures)
    changed[0]["rows"][0]["frame"]["closed"] = False
    with pytest.raises(ValueError, match="completed"):
        validate_records(changed, MODELS, results)
    changed = deepcopy(captures)
    changed[0]["rows"][0]["events"][0]["scope"] = "foreign"
    with pytest.raises(ValueError, match="scope"):
        validate_records(changed, MODELS, results)
