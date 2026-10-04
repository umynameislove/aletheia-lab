"""Bounded inert-producer pilot checks, never native SDK validation reruns."""

from __future__ import annotations

import sqlite3

import pytest

from aletheia_lab.evaluation import (
    model_load_retention_development as development,
)
from aletheia_lab.evaluation.model_load_contract import receipt_checker
from aletheia_lab.evaluation.model_load_retention import ReceiptStore
from aletheia_lab.evaluation.model_load_retention_development import make_episodes, run_pilot


@pytest.fixture(scope="module")
def pilot():
    return run_pilot(repeats=2)


def by_id(pilot):
    return {summary["configuration"]["id"]: summary for summary in pilot["summaries"]}


def test_pilot_is_bounded_collector_execution_with_one_shared_producer_tape(pilot):
    assert pilot["status"] == "collector_development_complete"
    assert pilot["episode_count"] == 10
    assert pilot["configuration_count"] == 10
    assert pilot["executed_episode_runs"] == 200
    assert pilot["planned_episode_runs"] == 200
    assert pilot["failed_configuration_runs"] == 0
    assert pilot["upstream_hash_calls"] == 16
    assert pilot["upstream_hash_input_bytes"] == 557056
    assert pilot["native_loader_entries"] == 0
    assert pilot["local_model_fits"] == 0
    assert pilot["provider_calls"] == 0
    assert not pilot["protected_validation_rerun"]
    assert not pilot["adaptive_method_promoted"]
    assert len(pilot["tape_sha256"]) == 64
    for summary in pilot["summaries"]:
        resources = summary["resources"]
        assert resources["capture_events"] == 50
        assert resources["capture_payload_bytes"] == pilot["common_capture_payload_bytes"]
        assert resources["sample_count"] == 118
        assert resources["bookkeeping_included"]
        assert resources["peak_bookkeeping_bytes"] > 0
        assert resources["bookkeeping_byte_steps"] > 0
        assert resources["raw_buffer_archive_bytes"] == 0
        assert resources["sql_reads"] > 0
        assert resources["sql_writes"] > 0
        assert resources["peak_database_bytes"] >= resources["final_database_bytes"] > 0
        assert resources["peak_journal_bytes"] > 0
        assert summary["timing_repeats"] == 2
        assert 0 < summary["collector_operation_ns_min"] <= summary["collector_operation_ns_median"]
        assert summary["collector_operation_ns_median"] <= summary["collector_operation_ns_max"]


def test_routing_and_root_lifetime_are_separate_delivery_capabilities(pilot):
    summaries = by_id(pilot)
    expected = {
        "routing-mailbox-root-0": 6,
        "routing-mailbox-root-1": 7,
        "routing-trusted_scope-root-0": 7,
        "routing-trusted_scope-root-1": 8,
    }
    for name, identified in expected.items():
        query = summaries[name]["queries"]["after_release"]
        assert query["correct_identified"] == identified
        assert query["planned_load_denominator"] == 9
        assert query["planned_denominator"] == 10
        assert query["correct_no_new_load"] == 1
        assert query["false_compliance"] == query["false_violation"] == 0


@pytest.mark.parametrize("horizon", [0, 2, 8])
def test_static_sufficient_selector_matches_full_verdicts_with_fewer_receipt_writes(pilot, horizon):
    summaries = by_id(pilot)
    full = summaries[f"full-horizon-{horizon}"]
    static = summaries[f"static_sufficient-horizon-{horizon}"]
    assert static["queries"] == full["queries"]
    assert static["retrospective_queries"] == full["retrospective_queries"]
    assert static["queries"]["after_release"]["correct_identified"] == 8
    assert static["queries"]["after_release"]["correct_no_new_load"] == 1
    assert static["queries"]["after_release"]["verdict_counts"]["unknown"] == 1
    full_resources, static_resources = full["resources"], static["resources"]
    for key in (
        "capture_events",
        "capture_payload_bytes",
        "sample_count",
        "bookkeeping_byte_steps",
    ):
        assert static_resources[key] == full_resources[key]
    assert static_resources["inserted_records"] < full_resources["inserted_records"]
    assert static_resources["written_payload_bytes"] < full_resources["written_payload_bytes"]
    assert static_resources["sql_writes"] < full_resources["sql_writes"]
    assert static_resources["live_payload_byte_steps"] < full_resources["live_payload_byte_steps"]
    assert static_resources["peak_live_payload_bytes"] < full_resources["peak_live_payload_bytes"]
    assert static_resources["duplicate_deliveries"] == full_resources["duplicate_deliveries"] == 1


def test_longer_audit_horizon_increases_live_occupancy_without_changing_capture(pilot):
    summaries = by_id(pilot)
    for selector in ("full", "static_sufficient"):
        resources = [summaries[f"{selector}-horizon-{h}"]["resources"] for h in (0, 2, 8)]
        assert len({row["capture_payload_bytes"] for row in resources}) == 1
        assert len({row["written_payload_bytes"] for row in resources}) == 1
        assert resources[0]["live_payload_byte_steps"] < resources[1]["live_payload_byte_steps"]
        assert resources[1]["live_payload_byte_steps"] < resources[2]["live_payload_byte_steps"]
        assert resources[0]["peak_bookkeeping_bytes"] < resources[2]["peak_bookkeeping_bytes"]


@pytest.mark.parametrize("horizon,available", [(0, 0), (2, 19), (8, 27)])
def test_retrospective_service_availability_matches_the_declared_horizon(pilot, horizon, available):
    for selector in ("full", "static_sufficient"):
        audits = by_id(pilot)[f"{selector}-horizon-{horizon}"]["retrospective_queries"]
        assert audits["planned_queries"] == 27
        assert audits["available"] == available
        assert audits["decision_preserved_when_available"] == available
        assert audits["unavailable"] == 27 - available
        assert audits["requested_ages_drained_requests"] == [0, 1, 2]


@pytest.mark.parametrize("selector", ["full", "static_sufficient"])
def test_genuine_foreign_scope_remains_unknown_even_with_trusted_scope_index(tmp_path, selector):
    episode = next(
        episode for episode in make_episodes() if episode.target.request == "foreign-scope"
    )
    store = ReceiptStore(tmp_path / "foreign.sqlite", selection=selector)
    try:
        for delivery in episode.deliveries:
            store.submit(delivery.record, episode.target, mailbox=delivery.mailbox)
        store.release(episode.target.request)
        decision = receipt_checker(store.read(episode.contract, episode.target))
        assert decision.verdict == "unknown"
        assert decision.reason == "missing_buffer_witness"
        assert episode.truth()["verdict"] == "violation"
    finally:
        store.close()


@pytest.mark.parametrize("repeats", [0, 6, True, 1.5])
def test_pilot_rejects_invalid_repetition_bounds(repeats):
    with pytest.raises(ValueError, match="between one and five"):
        run_pilot(repeats=repeats)


@pytest.mark.parametrize("failure", [RuntimeError, sqlite3.OperationalError])
def test_failed_configuration_run_remains_in_the_planned_census(monkeypatch, failure):
    original = development._run_configuration
    failed_id = "routing-mailbox-root-0"

    def fail_one(path, config, episodes):
        if config["id"] == failed_id:
            raise failure("synthetic collector failure")
        return original(path, config, episodes)

    monkeypatch.setattr(development, "_run_configuration", fail_one)
    report = development.run_pilot(repeats=1)
    assert report["status"] == "collector_development_incomplete"
    assert report["planned_episode_runs"] == 100
    assert report["executed_episode_runs"] == 90
    assert report["failed_configuration_runs"] == 1
    assert len(report["summaries"]) == 10
    failed = by_id(report)[failed_id]
    assert failed["status"] == "technical_failure"
    assert failed["planned_repeats"] == 1
    assert failed["retained_runs"] == [
        {
            "configuration": failed["configuration"],
            "status": "technical_failure",
            "error_type": failure.__name__,
            "planned_episode_count": 10,
        }
    ]
