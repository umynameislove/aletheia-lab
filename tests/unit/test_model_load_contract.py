from __future__ import annotations

from dataclasses import replace
from itertools import permutations, product

import pytest

from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
    compatible_completions,
    completion_monitor,
    receipt_checker,
)

A, B, C = "a" * 64, "b" * 64, "c" * 64
ROOT = Scope("request", 0)
CONTRACT = LoadContract("pin_at_acceptance", (A, B))
SELECT = Record("selected", ROOT, "selection", A, "pin", 0, "pin_at_acceptance")
LOAD = Record("loaded", ROOT, "load", A, "pin")
CLOSE = Record("closed", ROOT, "closure", load_count=1)


def _check(
    records: tuple[Record, ...],
    verdict: str | None,
    *,
    scope: Scope = ROOT,
    contract: LoadContract | None = CONTRACT,
    eligibility: str | None = None,
) -> None:
    observation = Observation(contract, scope, records)
    for checker in (receipt_checker, completion_monitor):
        decision = checker(observation)
        assert decision.verdict == verdict
        if eligibility is not None:
            assert decision.eligibility == eligibility


def test_complete_legal_and_wrong_actual_buffer_have_opposite_verdicts() -> None:
    _check((SELECT, LOAD, CLOSE), "compliant")
    _check((SELECT, replace(LOAD, digest=B), CLOSE), "violation")
    _check((SELECT, replace(LOAD, digest=B)), "violation")
    _check((SELECT, LOAD), "unknown")  # An unseen extra load remains possible.


@pytest.mark.parametrize(
    "records",
    [(), (SELECT,), (LOAD,), (CLOSE,), (LOAD, CLOSE), (SELECT, CLOSE)],
    ids=("empty", "selection", "load", "closure", "missing-selection", "missing-buffer"),
)
def test_incomplete_evidence_is_not_converted_to_compliance(records: tuple[Record, ...]) -> None:
    _check(records, "unknown")


def test_native_alias_and_other_attempt_are_not_consumed_buffer_evidence() -> None:
    alias = Record("alias", ROOT, "registry", digest=B, revision=1)
    foreign = replace(LOAD, scope=Scope("request", 1))
    _check((alias, foreign, SELECT, CLOSE), "unknown")
    _check((SELECT, LOAD, CLOSE, alias, foreign), "compliant")
    _check((SELECT, LOAD, CLOSE), "unknown", contract=None, eligibility="undetermined")


@pytest.mark.parametrize(
    "records",
    [
        (SELECT, LOAD, replace(LOAD, digest=B), CLOSE),
        (SELECT, LOAD, replace(CLOSE, load_count=0)),
        (SELECT, replace(LOAD, selection="other"), CLOSE),
        (SELECT, replace(LOAD, digest=C), CLOSE),
        (SELECT, LOAD, replace(CLOSE, load_count=3)),
        (SELECT, LOAD, CLOSE, replace(CLOSE, identifier="closed-again", load_count=2)),
        (SELECT, replace(SELECT, identifier="selected-again", parent_selection="different")),
    ],
    ids=("identity", "closure", "token", "domain", "bound", "two-closures", "parent-binding"),
)
def test_inconsistent_observations_are_conflicts(records: tuple[Record, ...]) -> None:
    _check(records, "conflict")


def test_extra_load_and_wrong_boundary_stay_in_violating_completions() -> None:
    extra = replace(LOAD, identifier="loaded-again")
    _check((SELECT, LOAD, extra), "violation")
    wrong_phase = replace(SELECT, phase="resolve_at_load")
    _check((wrong_phase, LOAD, CLOSE), "violation")
    _check((wrong_phase,), "unknown")
    _check((wrong_phase, replace(CLOSE, load_count=0)), None, eligibility="no_new_load")
    worlds = compatible_completions(Observation(CONTRACT, ROOT, (SELECT, LOAD)))
    assert any(not item.violation for item in worlds)
    assert any(item.violation and len(item.loads) == 2 for item in worlds)
    assert compatible_completions(Observation(None, ROOT, ())) == ()


def test_cache_only_is_a_separate_endpoint_even_under_reload_policy() -> None:
    hit = Record("cache", ROOT, "cache_hit", digest=A)
    _check((hit, replace(CLOSE, load_count=0)), None, eligibility="no_new_load")
    _check((hit,), "unknown", eligibility="undetermined")
    _check(
        (hit, replace(CLOSE, load_count=0)),
        None,
        contract=replace(CONTRACT, cache="reload"),
        eligibility="no_new_load",
    )


def test_retry_requires_an_authoritative_root_binding_not_an_inherit_word() -> None:
    child = Scope("request", 1)
    selected = Record(
        "child-pin",
        child,
        "selection",
        A,
        "child-token",
        0,
        "inherit",
        parent_scope=ROOT,
        parent_selection="pin",
    )
    loaded = Record("child-load", child, "load", A, "child-token")
    closed = Record("child-closed", child, "closure", load_count=1)
    evidence = (selected, loaded, closed)
    _check(evidence, "unknown", scope=child)
    _check((*evidence, SELECT), "compliant", scope=child)
    _check((*evidence, replace(SELECT, phase="resolve_at_load")), "unknown", scope=child)
    _check(
        (replace(selected, digest=B), replace(loaded, digest=B), closed, SELECT),
        "violation",
        scope=child,
    )
    _check(
        (*evidence, SELECT, replace(SELECT, identifier="root-conflict", digest=B)),
        "conflict",
        scope=child,
    )
    _check((*evidence, replace(SELECT, digest=C)), "conflict", scope=child)
    for origin in (None, Scope("foreign", 0), child, Scope("request", 2)):
        _check(
            (replace(selected, parent_scope=origin), loaded, closed, SELECT), "unknown", scope=child
        )


def test_retry_reselection_is_explicitly_at_retry_load_boundary() -> None:
    child = Scope("request", 1)
    selected = Record("child-pin", child, "selection", B, "child-token", 1, "reselect")
    loaded = Record("child-load", child, "load", B, "child-token")
    closed = Record("child-closed", child, "closure", load_count=1)
    _check(
        (SELECT, selected, loaded, closed),
        "compliant",
        scope=child,
        contract=replace(CONTRACT, retry="reselect"),
    )


def test_four_world_partition_requires_both_selection_and_consumption() -> None:
    partitions: dict[str, dict[tuple[str, ...], set[bool]]] = {
        "selection": {},
        "load": {},
        "both": {},
    }
    for required, consumed in product((A, B), repeat=2):
        for view, signature in (
            ("selection", (required,)),
            ("load", (consumed,)),
            ("both", (required, consumed)),
        ):
            partitions[view].setdefault(signature, set()).add(required == consumed)
    assert all(len(statuses) == 2 for statuses in partitions["selection"].values())
    assert all(len(statuses) == 2 for statuses in partitions["load"].values())
    assert all(len(statuses) == 1 for statuses in partitions["both"].values())


def test_receipt_baseline_matches_independent_finite_truth_and_delivery_permutations() -> None:
    checked = 0
    for required, consumed, visible, closed in product((A, B), (A, B), range(4), (False, True)):
        selected = replace(SELECT, digest=required)
        loaded = replace(LOAD, digest=consumed)
        records = tuple(item for bit, item in ((1, selected), (2, loaded)) if visible & bit)
        records += (CLOSE,) if closed else ()
        # Independent truth predicate, not production admission/Completion.violation.
        worlds = [
            (r, loads)
            for r in (A, B)
            for count in ((1,) if closed else (0, 1, 2))
            for loads in product((A, B), repeat=count)
            if (not visible & 1 or r == required) and (not visible & 2 or consumed in loads)
        ]
        statuses = {len(loads) > 1 or any(value != r for value in loads) for r, loads in worlds}
        expected = (
            "unknown" if len(statuses) > 1 else "violation" if True in statuses else "compliant"
        )
        for ordering in permutations(records):
            _check(ordering, expected)
            if ordering:
                _check((*ordering, ordering[0]), expected)
            checked += 1
    assert checked == 64


def test_truthful_refinement_never_reverses_a_conclusive_verdict() -> None:
    for required, consumed in product((A, B), repeat=2):
        records = (replace(SELECT, digest=required), replace(LOAD, digest=consumed), CLOSE)
        for mask in range(8):
            prefix = tuple(record for index, record in enumerate(records) if mask & (1 << index))
            initial = receipt_checker(Observation(CONTRACT, ROOT, prefix))
            final = receipt_checker(Observation(CONTRACT, ROOT, records))
            if initial.verdict in {"compliant", "violation"}:
                assert initial.verdict == final.verdict


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Scope("", 0),
        lambda: Scope("x", -1),
        lambda: Scope("x", True),
        lambda: LoadContract("invalid", (A, B)),
        lambda: LoadContract("pin_at_acceptance", (A,)),
        lambda: LoadContract("pin_at_acceptance", (A, A)),
        lambda: LoadContract("pin_at_acceptance", (A, "bad")),
        lambda: LoadContract("pin_at_acceptance", (A, B), retry="bad"),
        lambda: Record("", ROOT, "load", A, "pin"),
        lambda: Record("x", ROOT, "load", A),
        lambda: Record("x", ROOT, "selection", A, "pin"),
        lambda: Record("x", ROOT, "closure"),
        lambda: Record("x", ROOT, "registry", "bad"),
        lambda: Record("x", ROOT, "closure", load_count=-1),
    ],
    ids=[f"invalid-{index}" for index in range(14)],
)
def test_malformed_contract_inputs_are_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()
