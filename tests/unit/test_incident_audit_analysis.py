"""Service-matched accounting and read-only native-floor receipt checks."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aletheia_lab.evaluation.incident_audit_analysis import envelope, point
from aletheia_lab.evaluation.incident_audit_service import summarize
from aletheia_lab.evaluation.incident_audit_verification import verify_native_floor


def _item(
    identity: str,
    scope: str,
    answer: str | None,
    *,
    accepted: bool = True,
    kind: str = "incident",
) -> dict[str, Any]:
    return {
        "id": identity,
        "kind": kind,
        "scopes": [scope],
        "answers": {scope: answer},
        "accepted": accepted,
        "queried_at": 1,
        "deadline": 2,
        "query_wall_ns": 10,
        "response_budget_ns": 50,
        "refetched": [],
        "refetch_failed": [],
        "recovery_meter": {"read_ns": 7},
    }


def _archive(
    items: list[dict[str, Any]], rows: list[dict[str, Any]], closed_bytes: int = 100
) -> dict[str, Any]:
    return {
        "prospective_admissions": [item for item in items if item["kind"] == "preknown"],
        "offers": [item for item in items if item["kind"] != "preknown"],
        "summary": summarize(items, rows),
        "closed_db_bytes": closed_bytes,
        "storage": {
            "peak_charge": 80,
            "logical_charge": 70,
            "peak_db_wal_shm_bytes": 300,
            "write_ns": 40,
        },
    }


def _report(
    name: str,
    candidate: list[dict[str, Any]],
    reference: list[dict[str, Any]] | None = None,
    *,
    primary_bytes: int = 100,
    secondary_bytes: int = 200,
    truth: dict[str, str] | None = None,
) -> dict[str, Any]:
    truth = truth or {"a": "compliant", "b": "unknown"}
    rows = [{"frame": {"token": scope}, "reference": value} for scope, value in truth.items()]
    return {
        "rows": rows,
        "archives": {
            name: _archive(candidate, rows, primary_bytes),
            "full-reference": _archive(
                reference if reference is not None else candidate, rows, 500
            ),
        },
        "secondary": {"closed_db_bytes": secondary_bytes},
    }


def test_same_full_frontier_with_unknown_is_not_complete_audit_coverage() -> None:
    name = "65536-static-none"
    items = [_item("one", "a", "compliant"), _item("two", "b", "unknown")]
    result = point(name, [_report(name, items)], [5, 50])
    assert result["same_full_frontier"] and result["same_full_service"]
    assert result["offered"] == result["accepted"] == 2
    assert result["correct_complete"] == result["on_time_correct"] == 1
    assert result["accepted_but_unserved"] == 1
    assert result["scope_census"]["unknown_scopes"] == 1
    assert result["scope_census"]["native_failure_scopes"] == 1
    assert result["response_budget_sensitivity"]["5"]["accepted_but_unserved"] == 2
    chosen = envelope([result])
    assert chosen["status"] == "ordinary_candidate_matches_tested_full_service"
    assert chosen["correct_complete"] < chosen["offered"]
    assert "unknown" in chosen["qualification"]


@pytest.mark.parametrize("change", ["refused", "wall_deadline", "producer_deadline"])
def test_equal_answers_do_not_qualify_a_different_admission_or_deadline_service(
    change: str,
) -> None:
    name = "65536-static-none"
    reference = [_item("one", "a", "compliant")]
    candidate = copy.deepcopy(reference)
    if change == "refused":
        candidate[0]["accepted"] = False
    elif change == "wall_deadline":
        candidate[0]["query_wall_ns"] = 51
    else:
        candidate[0]["queried_at"] = 3
    result = point(name, [_report(name, candidate, reference)], [50])
    assert result["same_full_frontier"]
    assert not result["same_full_service"]
    assert envelope([result]) == {"status": "no_tested_ordinary_matched_service_candidate"}


def test_process_mismatches_cannot_cancel_in_aggregate_counts() -> None:
    name = "65536-static-none"
    accepted = [_item("one", "a", "compliant")]
    refused = [_item("one", "a", "compliant", accepted=False)]
    reports = [_report(name, refused, accepted), _report(name, accepted, refused)]
    result = point(name, reports, [50])
    assert result["accepted"] == result["refused"] == 1
    assert result["same_full_frontier"] and not result["same_full_service"]
    assert envelope([result])["status"] == "no_tested_ordinary_matched_service_candidate"


@pytest.mark.parametrize("change", ["admission", "deadline"])
def test_obligation_mismatches_cannot_cancel_within_one_process(change: str) -> None:
    name = "65536-static-none"
    reference = [_item("one", "a", "compliant"), _item("two", "a", "compliant")]
    if change == "admission":
        reference[0]["accepted"] = False
    else:
        reference[0]["query_wall_ns"] = 51
    candidate = copy.deepcopy(reference)
    field = "accepted" if change == "admission" else "query_wall_ns"
    candidate[0][field], candidate[1][field] = candidate[1][field], candidate[0][field]
    report = _report(name, candidate, reference)
    assert report["archives"][name]["summary"] == report["archives"]["full-reference"]["summary"]
    result = point(name, [report], [50])
    assert result["same_full_frontier"] and not result["same_full_service"]
    assert envelope([result])["status"] == "no_tested_ordinary_matched_service_candidate"


def test_storage_selection_excludes_a_cheaper_mismatched_service() -> None:
    items = [_item("one", "a", "compliant")]
    good = point(
        "65536-static-none", [_report("65536-static-none", items, primary_bytes=100)], [50]
    )
    altered = copy.deepcopy(items)
    altered[0]["accepted"] = False
    bad = point(
        "4096-static-none", [_report("4096-static-none", altered, items, primary_bytes=1)], [50]
    )
    chosen = envelope([bad, good])
    assert chosen["candidate"] == good["name"]
    assert chosen["lowest_measured_closed_storage"] == 100


@pytest.mark.parametrize("tier", ["durable_secondary", "secondary_unavailable"])
def test_enabled_secondary_is_charged_even_when_unavailable_and_never_used(tier: str) -> None:
    name = "65536-static-" + tier
    items = [_item("one", "a", "compliant")]
    items[0]["recovery_meter"]["read_ns"] = 0
    result = point(name, [_report(name, items)], [50])
    assert result["median_primary_closed_db_bytes"] == 100
    assert result["median_allocated_secondary_closed_db_bytes"] == 200
    assert result["median_matched_tier_closed_db_bytes"] == 300
    assert result["median_secondary_retrieval_ns"] == 0


def test_no_recovery_tier_does_not_charge_the_shared_experimental_secondary() -> None:
    name = "65536-static-none"
    result = point(name, [_report(name, [_item("one", "a", "compliant")])], [50])
    assert result["median_allocated_secondary_closed_db_bytes"] == 0
    assert result["median_matched_tier_closed_db_bytes"] == 100


def test_matched_storage_median_uses_each_process_pair_not_sum_of_medians() -> None:
    name = "65536-static-durable_secondary"
    items = [_item("one", "a", "compliant")]
    reports = [
        _report(name, items, primary_bytes=p, secondary_bytes=s)
        for p, s in [(10, 100), (20, 1000), (1000, 10)]
    ]
    result = point(name, reports, [50])
    assert result["median_primary_closed_db_bytes"] == 20
    assert result["median_allocated_secondary_closed_db_bytes"] == 100
    assert result["median_matched_tier_closed_db_bytes"] == 1010


def test_refusal_classes_describe_evidence_without_inventing_capacity_causality() -> None:
    name = "65536-static-none"
    items = [
        _item("lost", "a", None, accepted=False),
        _item("present", "a", "compliant", accepted=False),
        _item("pre:a", "a", None, accepted=False, kind="preknown"),
    ]
    result = point(name, [_report(name, items)], [50])
    assert result["refused"] == 3
    assert result["refusal_evidence_class"] == {
        "missing_or_unrecoverable_evidence": 1,
        "admission_rejected_with_present_closure": 1,
        "prospective_admission_refused_cause_not_recorded": 1,
    }
    assert not any("capacity" in label for label in result["refusal_evidence_class"])


def _native_floor_rows() -> list[dict[str, Any]]:
    rows = []
    for ordinal in range(16):
        values = [[1, 2], [3, 4], [5, 6]]
        if ordinal % 8 == 3 or ordinal == 15:
            values = [[0, 0], [0, 0], [0, 0]]
        weights = [[1, 2], [3, 4], [5, 6]] if ordinal % 2 == 0 else [[2, 1], [4, 3], [6, 5]]
        output = (
            None
            if ordinal == 11
            else [
                [x * w for x, w in zip(xs, ws, strict=True)]
                for xs, ws in zip(values, weights, strict=True)
            ]
        )
        rows.append(
            {
                "ordinal": ordinal,
                "output": output,
                "native_error": "expected native failure" if ordinal == 11 else None,
                "native_ns": 1,
            }
        )
    return rows


def test_native_floor_receipts_can_be_verified_without_rerunning_native_execution() -> None:
    verify_native_floor(_native_floor_rows(), 16)


@pytest.mark.parametrize(
    "tamper",
    ["output", "missing_failure", "extra_failure", "ordinal", "negative_timer", "boolean_timer"],
)
def test_native_floor_tampering_is_rejected_without_native_execution(tamper: str) -> None:
    rows = _native_floor_rows()
    if tamper == "output":
        rows[0]["output"] = [[0, 0]] * 3
    elif tamper == "missing_failure":
        rows[11]["native_error"] = None
    elif tamper == "extra_failure":
        rows[0]["native_error"] = "fabricated"
    elif tamper == "ordinal":
        rows[0]["ordinal"] = 1
    elif tamper == "negative_timer":
        rows[0]["native_ns"] = -1
    else:
        rows[0]["native_ns"] = True
    with pytest.raises(ValueError, match="native floor"):
        verify_native_floor(rows, 16)
