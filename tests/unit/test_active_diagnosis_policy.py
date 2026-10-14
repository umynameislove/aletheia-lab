"""Finite-model checks, information limits and fail-closed actual acquisition."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from aletheia_lab.diagnosis import active_policy
from aletheia_lab.diagnosis.active_policy import (
    Hypothesis,
    choose_probe,
    compatible,
    declared_catalogue,
    discriminating_gain,
    policy_payload,
    run_policy,
)

CATALOGUE = declared_catalogue()
ACTIONS = tuple(CATALOGUE[0].outcomes)
COSTS = dict.fromkeys(ACTIONS, 0.001)


def _run(policy="ig", *, name="input_sign", available=ACTIONS, **kwargs):
    hypothesis = next(h for h in CATALOGUE if h.name == name)

    def probe(action):
        return {
            "value": hypothesis.outcomes[action],
            "seconds": 0.0001,
            "evidence_sha256": "a" * 64,
        }

    return run_policy(
        probe=probe,
        harmful=hypothesis.harmful,
        available=available,
        costs=COSTS,
        budget_seconds=10,
        policy=policy,
        **kwargs,
    )


@pytest.mark.parametrize("name", [h.name for h in CATALOGUE])
@pytest.mark.parametrize("policy", ["full", "cheap", "ig", "ec2"])
def test_known_finite_patterns_resolve_without_an_oracle(policy, name):
    result = _run(policy, name=name)
    assert result["identified"] and result["compatible"] == [name]
    assert result["on_budget"]
    assert result["scope"] == "catalogue_relative_not_global_cause_uniqueness"


def test_static_cannot_separate_two_prediction_equivalent_fault_sites():
    assert _run("static")["compatible"] == ["input_sign", "resident_sign", "target_roll"]
    assert not _run("static")["identified"]


def test_actual_call_ledger_and_full_baseline_share_the_same_interface():
    full, selected = _run("full"), _run("ec2")
    assert len(full["ledger"]) == 6
    assert len(selected["ledger"]) < len(full["ledger"])
    assert full["observations"].items() >= selected["observations"].items()


def test_restoration_only_cannot_distinguish_training_from_resident():
    result = _run("ec2", name="target_roll", available=ACTIONS[3:])
    assert not result["identified"]
    assert result["compatible"] == ["resident_sign", "target_roll"]


def test_policy_view_is_allowlisted_and_contains_no_fixture_truth():
    view = policy_payload(CATALOGUE, {"read_input": "changed"}, ACTIONS, 5)
    assert set(view) == {
        "catalogue_scope",
        "hypotheses",
        "observations",
        "available_actions",
        "cost_estimates_seconds",
        "remaining_seconds",
    }
    assert not {"condition", "case", "source", "seed", "reference", "repair_residual"} & set(view)


def test_outside_model_outcome_conflicts_instead_of_becoming_a_negative_fact():
    observations = {"read_input": "changed", "read_model": "same", "read_training": "changed"}
    assert compatible(CATALOGUE, observations) == ()


@pytest.mark.parametrize("rule", ["ig", "ec2"])
def test_nondiscriminating_query_has_zero_gain(rule):
    rivals = [h for h in CATALOGUE if h.name in {"resident_sign", "target_roll"}]
    assert discriminating_gain(rivals, "restore_model", rule=rule) == pytest.approx(0)
    assert discriminating_gain(rivals, "read_training", rule=rule) > 0


def test_ec2_objective_is_not_silently_replaced_by_entropy():
    hypotheses = tuple(
        Hypothesis(str(i), str(i), True, {"a": "x" if i < 3 else "y"}) for i in range(4)
    )
    assert discriminating_gain(hypotheses, "a", rule="ec2") == pytest.approx(0.234375)
    assert discriminating_gain(hypotheses, "a", rule="ig") == pytest.approx(0.8112781244591328)


def test_no_available_measurement_retains_ambiguity():
    result = _run(available=())
    assert not result["identified"] and not result["ledger"]


@pytest.mark.parametrize("bad_cost", [0, -1, math.nan, math.inf])
def test_invalid_cost_cannot_select_a_favorable_probe(bad_cost):
    with pytest.raises(ValueError):
        choose_probe(CATALOGUE, ["read_input"], {"read_input": bad_cost}, rule="ig")


def test_model_invalid_action_does_not_execute_arbitrary_code():
    calls = []
    result = run_policy(
        probe=lambda action: calls.append(action),
        harmful=True,
        available=ACTIONS,
        costs=COSTS,
        budget_seconds=10,
        policy="llm",
        selector=lambda _: ["run_shell"],
    )
    assert result["stop"] == "invalid_model_action" and not calls


def test_hybrid_selects_classically_from_model_proposals():
    seen = []

    def propose(view):
        seen.append(view)
        return ["read_training", "read_input"]

    result = _run("hybrid", selector=propose)
    assert seen and result["identified"]
    assert result["ledger"][0]["action"] == "read_input"


def test_probe_projection_cannot_leak_hidden_truth():
    result = run_policy(
        probe=lambda _: {
            "value": "changed",
            "seconds": 0,
            "evidence_sha256": "a" * 64,
            "condition": "input_sign",
        },
        harmful=True,
        available=ACTIONS,
        costs=COSTS,
        budget_seconds=10,
        policy="ig",
    )
    assert result["stop"] == "probe_failure" and not result["identified"]
    assert result["ledger"][0]["status"] == "probe_failure"


def test_failed_probe_is_preserved_and_not_treated_as_same():
    def fail(_):
        raise OSError("unavailable")

    result = run_policy(
        probe=fail, harmful=True, available=ACTIONS, costs=COSTS, budget_seconds=10, policy="ig"
    )
    assert result["stop"] == "probe_failure" and not result["observations"]


def test_selection_time_is_charged_to_the_same_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(active_policy, "time", SimpleNamespace(perf_counter=lambda: clock[0]))

    def slow(_):
        clock[0] += 0.01
        return ["read_input"]

    result = run_policy(
        probe=lambda _: pytest.fail("must not execute"),
        harmful=True,
        available=ACTIONS,
        costs=COSTS,
        budget_seconds=0.005,
        policy="llm",
        selector=slow,
    )
    assert result["stop"] == "selection_exhausted_budget" and not result["on_budget"]
    assert result["selection_seconds"] == result["total_seconds"] == 0.01


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf])
def test_invalid_budget_rejected(bad):
    with pytest.raises(ValueError):
        run_policy(
            probe=lambda _: {},
            harmful=True,
            available=ACTIONS,
            costs=COSTS,
            budget_seconds=bad,
            policy="static",
        )


def test_misspecified_catalogue_alias_does_not_establish_mechanism_uniqueness():
    # A different operator can yield exactly these same coarse measurements.
    # Success here is only identification relative to the advertised catalogue.
    result = _run("full", name="resident_sign")
    assert result["compatible"] == ["resident_sign"]
    assert "global_cause_uniqueness" in result["scope"]
