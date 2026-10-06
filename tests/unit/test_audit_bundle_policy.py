"""Independent finite costs check pins, sharing, greedy traps and no future tape."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from aletheia_lab.evaluation.audit_bundle_policy import POLICIES, exact_snapshot, select


def entry(created: int = 0, touch: int = 0, hits: int = 0) -> dict[str, Any]:
    return {"created": created, "touch": touch, "hits": hits}


def union_cost(
    dependencies: dict[str, frozenset[str]], sizes: dict[str, int], metadata: int = 0
) -> Callable[[frozenset[str]], int]:
    def measure(selected: frozenset[str]) -> int:
        witnesses: set[str] = set()
        for key in selected:
            witnesses.update(dependencies[key])
        return sum(sizes[key] for key in witnesses) + metadata * len(selected)

    return measure


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_all_policies_keep_old_pin_and_charge_shared_union(policy: str) -> None:
    pool = {"pin": entry(), "x": entry(3, 3), "y": entry(4, 4)}
    cost = union_cost(
        {"pin": frozenset({"root"}), "x": frozenset({"root", "x"}), "y": frozenset({"root", "y"})},
        {"root": 7, "x": 2, "y": 2},
        metadata=1,
    )
    result = select(policy, pool, frozenset({"pin"}), cost, 11, 4)
    assert "pin" in result and result.issubset(pool)
    assert len(result) == 2 and cost(result) == 11
    assert pool == {"pin": entry(), "x": entry(3, 3), "y": entry(4, 4)}


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("static", "new"),
        ("ttl", "new"),
        ("lru", "touched"),
        ("lfu", "frequent"),
        ("size_cost", "frequent"),
    ],
)
def test_baselines_have_declared_online_priorities(policy: str, expected: str) -> None:
    pool = {"new": entry(5, 5), "touched": entry(1, 6), "frequent": entry(0, 3, 7)}
    assert select(policy, pool, frozenset(), len, 1, 6) == frozenset({expected})


def test_ttl_age_is_inclusive_and_never_expires_mandatory() -> None:
    pool = {"pin": entry(), "age-two": entry(3, 3), "age-three": entry(2, 2)}
    assert select("ttl", pool, frozenset({"pin"}), len, 3, 5) == frozenset({"pin", "age-two"})


def test_ttl_slot_age_is_independent_of_event_clock() -> None:
    pool = {
        "pin": {**entry(), "ttl_age": 20},
        "age-two": {**entry(100, 101), "ttl_age": 2},
        "age-three": {**entry(110, 110), "ttl_age": 3},
        "fallback-recent": entry(199, 199),
    }
    assert select("ttl", pool, frozenset({"pin"}), len, 4, 200) == frozenset(
        {"pin", "age-two", "fallback-recent"}
    )
    assert select("static", pool, frozenset({"pin"}), len, 4, 200) == frozenset(pool)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_invalid_ttl_age_is_rejected(value: object) -> None:
    pool = {"a": {**entry(), "ttl_age": value}}
    with pytest.raises(ValueError, match="ttl_age.*integer"):
        select("ttl", pool, frozenset(), len, 1, 0)
    with pytest.raises(ValueError, match="ttl_age.*integer"):
        exact_snapshot(pool, frozenset(), len, 1)


def test_density_recomputes_shared_cost_after_each_addition() -> None:
    pool = {"a": entry(hits=2), "b": entry(), "c": entry()}
    cost = union_cost(
        {"a": frozenset({"parent", "a"}), "b": frozenset({"parent", "b"}), "c": frozenset({"c"})},
        {"parent": 4, "a": 1, "b": 1, "c": 2},
    )
    assert select("size_cost", pool, frozenset(), cost, 7, 0) == frozenset({"a", "c"})
    assert select("union_density", pool, frozenset(), cost, 7, 0) == frozenset({"a", "b"})


def test_exchange_escapes_one_out_two_in_greedy_trap() -> None:
    pool = {"greedy": entry(hits=3), "left": entry(hits=2), "right": entry(hits=2)}
    cost = union_cost(
        {
            "greedy": frozenset({"greedy"}),
            "left": frozenset({"left"}),
            "right": frozenset({"right"}),
        },
        {"greedy": 4, "left": 3, "right": 3},
    )
    assert select("union_density", pool, frozenset(), cost, 6, 0) == frozenset({"greedy"})
    exchanged = select("union_exchange", pool, frozenset(), cost, 6, 0)
    assert exchanged == frozenset({"left", "right"})
    assert exact_snapshot(pool, frozenset(), cost, 6) == {
        "selected": exchanged,
        "utility": 6,
        "cost": 6,
        "enumerated": 8,
    }


def test_exchange_can_improve_cost_without_claiming_future_coverage() -> None:
    pool = {"a-expensive": entry(hits=1), "b-cheap": entry(), "c-cheap": entry()}
    cost = union_cost(
        {
            "a-expensive": frozenset({"a"}),
            "b-cheap": frozenset({"shared"}),
            "c-cheap": frozenset({"shared"}),
        },
        {"a": 3, "shared": 2},
    )
    assert select("union_density", pool, frozenset(), cost, 3, 0) == frozenset({"a-expensive"})
    result = select("union_exchange", pool, frozenset(), cost, 3, 0)
    assert result == frozenset({"b-cheap", "c-cheap"})
    assert cost(result) == 2


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_snapshot_upper_bounds_policy_surrogate_with_metadata(policy: str) -> None:
    pool = {"pin": entry(), "a": entry(1, 1, 2), "b": entry(2, 2), "c": entry(3, 3, 1)}
    cost = union_cost(
        {
            "pin": frozenset({"root"}),
            "a": frozenset({"root", "a"}),
            "b": frozenset({"b"}),
            "c": frozenset({"root", "c"}),
        },
        {"root": 3, "a": 2, "b": 2, "c": 2},
        metadata=1,
    )
    result = select(policy, pool, frozenset({"pin"}), cost, 10, 3)
    optimum = exact_snapshot(pool, frozenset({"pin"}), cost, 10)
    assert optimum is not None and optimum["enumerated"] == 8
    assert optimum["utility"] >= sum(1 + pool[key]["hits"] for key in result)
    assert optimum["cost"] == cost(optimum["selected"]) <= 10


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_deterministic_across_insertion_order_and_no_missing_evidence(policy: str) -> None:
    pool = {"z": entry(3, 3), "a": entry(3, 3), "m": entry(3, 3)}
    assert select(policy, pool, frozenset(), len, 1, 3) == frozenset({"a"})
    assert select(policy, dict(reversed(list(pool.items()))), frozenset(), len, 1, 3) == frozenset(
        {"a"}
    )
    assert select(policy, {"z": entry(3, 3)}, frozenset(), len, 1, 3) == frozenset({"z"})


def test_exact_ties_skip_cap_and_count_infeasible_subsets() -> None:
    pool = {"z": entry(), "a": entry()}
    assert exact_snapshot(pool, frozenset(), len, 1) == {
        "selected": frozenset({"a"}),
        "utility": 1,
        "cost": 1,
        "enumerated": 4,
    }
    assert exact_snapshot(pool, frozenset(), len, 1, max_optional=1) is None
    assert exact_snapshot(pool, frozenset(pool), len, 2, max_optional=0) == {
        "selected": frozenset(pool),
        "utility": 2,
        "cost": 2,
        "enumerated": 1,
    }


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_infeasible_or_missing_mandatory_fails_before_optional_selection(policy: str) -> None:
    with pytest.raises(ValueError, match="absent"):
        select(policy, {"available": entry()}, frozenset({"lost"}), len, 2, 0)
    with pytest.raises(ValueError, match="exceeds"):
        select(policy, {"pin": entry()}, frozenset({"pin"}), len, 0, 0)


@pytest.mark.parametrize("policy", ["ttl", "union_density"])
@pytest.mark.parametrize("field", ["created", "touch"])
def test_future_metadata_is_rejected(policy: str, field: str) -> None:
    value = {**entry(), "ttl_age": 0}
    value[field] = 2
    with pytest.raises(ValueError, match="future"):
        select(policy, {"a": value}, frozenset(), len, 1, 1)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_invalid_hits_are_rejected(value: object) -> None:
    metadata = entry()
    metadata["hits"] = value
    with pytest.raises(ValueError, match="integer"):
        select("static", {"a": metadata}, frozenset(), len, 1, 0)


def test_unknown_policy_bad_cost_and_empty_pool() -> None:
    with pytest.raises(ValueError, match="unknown"):
        select("future", {}, frozenset(), len, 0, 0)
    with pytest.raises(ValueError, match="cost"):
        exact_snapshot({}, frozenset(), lambda _: -1, 1)
    assert select("union_exchange", {}, frozenset(), len, 0, 0) == frozenset()
