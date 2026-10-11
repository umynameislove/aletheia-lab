"""Matched multi-attempt evidence, query horizon and failure census checks."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from aletheia_lab.evaluation import model_load_retention_concurrency as development


@pytest.fixture(scope="module")
def report():
    return development.run_concurrency()


def find_run(report, selection, service, horizon):
    return next(
        row
        for row in report["runs"]
        if (row["selection"], row["service"], row["horizon"]) == (selection, service, horizon)
    )


def test_bounded_threaded_tape_runs_the_complete_configuration_census(report):
    assert report["status"] == "concurrency_development_complete"
    assert report["producer_threads"] == 2
    assert report["primary_requests"] == 2
    assert report["primary_attempts"] == 6
    assert report["planned_configuration_runs"] == report["executed_configuration_runs"] == 12
    assert report["failed_configuration_runs"] == 0
    assert report["planned_primary_request_runs"] == report["executed_primary_request_runs"] == 24
    assert report["common_tape_events"] == 26
    assert report["common_receipt_deliveries"] == 20
    assert (
        report["native_loader_entries"]
        == report["local_model_fits"]
        == report["provider_calls"]
        == 0
    )
    assert not report["protected_validation_rerun"]
    assert not report["throughput_measured"]
    assert not report["timing_measured"]
    assert len(report["tape_sha256"]) == 64
    json.dumps(report, allow_nan=False)


def test_threaded_tape_is_reproducible_with_root_closure_before_children():
    first, second = development._thread_tape(), development._thread_tape()
    assert first == second
    for request in development.REQUESTS:
        settled = [
            event.scope.attempt
            for event in first
            if event.owner == request and event.record is None
        ]
        assert settled == [0, 1, 2]
    assert any(
        event.delayed and event.record and event.record.kind == "selection" for event in first
    )
    assert any(event.record and event.record.scope.request == "actual-foreign" for event in first)


def test_full_and_static_share_decisions_captures_and_measurement_steps(report):
    assert len(report["comparisons"]) == 6
    for pair in report["comparisons"]:
        assert pair["comparison_available"]
        assert pair["matched_immediate_decisions"]
        assert pair["matched_audit_decisions_and_availability"]
        assert pair["matched_capture"]
        assert pair["matched_measurement_steps"]
        full = find_run(report, "full", pair["service"], pair["horizon"])["resources"]
        static = find_run(report, "static_sufficient", pair["service"], pair["horizon"])[
            "resources"
        ]
        assert full["duplicates"] == static["duplicates"] == 1
        assert full["bookkeeping_byte_steps"] == static["bookkeeping_byte_steps"]
        assert static["written_bytes"] < full["written_bytes"]
        assert static["peak_payload_bytes"] <= full["peak_payload_bytes"]
        assert static["inserts"] < full["inserts"]


@pytest.mark.parametrize("service,count", [("children_only", 4), ("root_and_children", 6)])
def test_late_conflicting_parent_and_real_foreign_scope_remain_distinct(report, service, count):
    run = find_run(report, "static_sufficient", service, 2)
    before = run["immediate_queries"]["before_release"]
    after = run["immediate_queries"]["after_release"]
    assert len(before) == len(after) == count
    assert all(len(row["admitted_record_sha256"]) == 64 for row in (*before, *after))
    by_scope = {
        (row["scope"]["request"], row["scope"]["attempt"]): row["decision"] for row in after
    }
    assert by_scope[("concurrent-a", 1)]["reason"] == "conflicting_parent_receipts"
    assert by_scope[("concurrent-a", 2)]["verdict"] == "conflict"
    assert by_scope[("concurrent-b", 1)]["verdict"] == "compliant"
    assert by_scope[("concurrent-b", 2)]["reason"] == "missing_buffer_witness"
    assert (
        next(row for row in before if row["scope"] == {"request": "concurrent-a", "attempt": 2})[
            "decision"
        ]["verdict"]
        == "violation"
    )


@pytest.mark.parametrize("horizon,factor", [(0, 0), (2, 4), (8, 6)])
@pytest.mark.parametrize("service,query_count", [("children_only", 2), ("root_and_children", 3)])
def test_audit_queries_have_matched_availability_and_preserve_allowed_decisions(
    report,
    horizon,
    factor,
    service,
    query_count,
):
    audit = find_run(report, "static_sufficient", service, horizon)["audit_queries"]
    assert len(audit) == 6 * query_count
    assert sum(row["available"] for row in audit) == factor * query_count
    assert all(row["preserved"] for row in audit if row["available"])
    assert all(row["decision"] is None for row in audit if not row["available"])
    assert all(len(row["admitted_record_sha256"]) == 64 for row in audit if row["available"])
    assert all(row["admitted_record_sha256"] is None for row in audit if not row["available"])
    assert {row["age"] for row in audit} == {0, 1, 2}


def test_early_root_delete_control_is_a_copied_observation_not_an_evidence_repair(report):
    for run in report["runs"]:
        (control,) = run["root_delete_controls"]
        assert control["before"]["verdict"] == "compliant"
        assert control["after"]["verdict"] == "unknown"
        assert control["after"]["reason"] == "missing_selection"
        assert not control["collector_mutated"]


def test_collector_failure_stays_in_all_planned_census(monkeypatch):
    original = development._replay

    def fail_one(path, tape, selection, service, horizon):
        if (selection, service, horizon) == ("full", "children_only", 0):
            raise RuntimeError("synthetic collector failure")
        return original(path, tape, selection, service, horizon)

    monkeypatch.setattr(development, "_replay", fail_one)
    result = development.run_concurrency()
    assert result["status"] == "concurrency_development_incomplete"
    assert result["planned_configuration_runs"] == 12
    assert result["executed_configuration_runs"] == 11
    assert result["failed_configuration_runs"] == 1
    assert len(result["runs"]) == 12
    assert result["planned_primary_request_runs"] == 24
    assert result["executed_primary_request_runs"] == 22
    assert not result["comparisons"][0]["comparison_available"]
    development.verify_concurrency(result)


def test_producer_failure_blocks_replay_without_shrinking_the_planned_census(monkeypatch):
    def fail():
        raise RuntimeError("synthetic producer failure")

    monkeypatch.setattr(development, "_thread_tape", fail)
    result = development.run_concurrency()
    assert result["status"] == "producer_tape_incomplete"
    assert result["planned_configuration_runs"] == result["failed_configuration_runs"] == 12
    assert result["executed_configuration_runs"] == 0
    assert result["provider_calls"] == 0
    development.verify_concurrency(result)


def test_report_verification_is_read_only_and_does_not_rerun_threads_or_collectors(
    report, monkeypatch
):
    before = deepcopy(report)

    def forbidden(*args, **kwargs):
        raise AssertionError("report verification must use retained evidence only")

    monkeypatch.setattr(development, "_thread_tape", forbidden)
    monkeypatch.setattr(development, "_replay", forbidden)
    monkeypatch.setattr(development, "AttemptReceiptStore", forbidden)
    development.verify_concurrency(report)
    assert report == before


@pytest.mark.parametrize(
    "tamper", ["tape", "decisions", "frame", "audit", "resource_pair", "comparison", "census"]
)
def test_rehashed_report_changes_cannot_hide_internal_inconsistency(report, tamper):
    changed = deepcopy(report)
    if tamper == "tape":
        changed["tape"][0]["owner"] = "other"
        changed["tape_sha256"] = development.document_digest(changed["tape"])
    elif tamper == "decisions":
        changed["runs"][0]["immediate_queries"]["after_release"][0]["decision"]["verdict"] = (
            "compliant"
        )
    elif tamper == "frame":
        changed["runs"][0]["immediate_queries"]["after_release"][0]["admitted_record_sha256"] = (
            "0" * 64
        )
    elif tamper == "audit":
        changed["runs"][0]["audit_queries"][0]["age"] = 1
    elif tamper == "resource_pair":
        changed["runs"][0]["resources"]["samples"] += 1
    elif tamper == "comparison":
        changed["comparisons"][0]["matched_capture"] = False
    else:
        changed["runs"].pop()
    with pytest.raises(ValueError):
        development.verify_concurrency(changed)
