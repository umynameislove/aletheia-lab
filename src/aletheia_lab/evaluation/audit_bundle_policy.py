"""Generic whole-bundle retention heuristics on a caller-owned current pool.

The caller supplies complete dependency/metadata costs and mandatory lease pins.
These policies cannot fetch discarded evidence or inspect future queries. Utility
is the declared surrogate ``sum(1 + past hits)``, not subsequent audit coverage.
Greedy and bounded exchange have no approximation or novelty claim.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from fractions import Fraction
from itertools import combinations
from typing import Any

Cost = Callable[[frozenset[str]], int]
Entries = dict[str, tuple[int, int, int]]
POLICIES = frozenset(
    {"static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange"}
)
_EXCHANGE_WIDTH = 12
_EXCHANGE_ROUNDS = 4


def _integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


def _entries(pool: dict[str, dict[str, Any]]) -> Entries:
    entries: Entries = {}
    for identifier, entry in pool.items():
        if not identifier:
            raise ValueError("bundle identifiers must be nonempty")
        if "ttl_age" in entry:
            _integer(entry["ttl_age"], "ttl_age")
        try:
            entries[identifier] = (
                _integer(entry["created"], "created"),
                _integer(entry["touch"], "touch"),
                _integer(entry["hits"], "hits"),
            )
        except KeyError as exc:
            raise ValueError("bundle lacks policy metadata") from exc
    return entries


def _measure(cost: Cost, selected: frozenset[str]) -> int:
    return _integer(cost(selected), "selection cost")


def _validate(
    pool: dict[str, dict[str, Any]], mandatory: frozenset[str], cost: Cost, budget: int
) -> tuple[Entries, int]:
    _integer(budget, "budget")
    if not mandatory.issubset(pool):
        raise ValueError("mandatory evidence is absent from the current pool")
    entries = _entries(pool)
    mandatory_cost = _measure(cost, mandatory)
    if mandatory_cost > budget:
        raise ValueError("mandatory lease union exceeds budget")
    return entries, mandatory_cost


def _utility(entries: Entries, selected: frozenset[str]) -> int:
    return sum(1 + entries[identifier][2] for identifier in selected)


def _ratio(weight: int, incremental_cost: int) -> Fraction:
    # A unit denominator keeps zero-cost shared additions finite. This scoring
    # heuristic never replaces the actual full-union feasibility check.
    return Fraction(weight, max(1, incremental_cost))


def _ttl_age(metadata: dict[str, Any], created: int, now: int) -> int:
    if "ttl_age" in metadata:
        return _integer(metadata["ttl_age"], "ttl_age")
    return now - created


def _ordered(
    policy: str, entries: Entries, mandatory: frozenset[str], cost: Cost, base_cost: int
) -> list[str]:
    optional = sorted(set(entries).difference(mandatory))
    if policy in {"static", "ttl"}:
        return sorted(optional, key=lambda key: (-entries[key][0], key))
    if policy == "lru":
        return sorted(optional, key=lambda key: (-entries[key][1], -entries[key][0], key))
    if policy == "lfu":
        return sorted(optional, key=lambda key: (-entries[key][2], -entries[key][1], key))
    return sorted(
        optional,
        key=lambda key: (
            -_ratio(1 + entries[key][2], _measure(cost, mandatory | {key}) - base_cost),
            key,
        ),
    )


def _fill(ordered: list[str], mandatory: frozenset[str], cost: Cost, budget: int) -> frozenset[str]:
    selected = mandatory
    for identifier in ordered:
        proposal = selected | {identifier}
        if _measure(cost, proposal) <= budget:
            selected = proposal
    return selected


def _density(
    entries: Entries, mandatory: frozenset[str], cost: Cost, budget: int
) -> frozenset[str]:
    selected = mandatory
    available = set(entries).difference(selected)
    while available:
        current_cost = _measure(cost, selected)
        ranked = sorted(
            available,
            key=lambda key: (
                -_ratio(1 + entries[key][2], _measure(cost, selected | {key}) - current_cost),
                key,
            ),
        )
        fitting = next((key for key in ranked if _measure(cost, selected | {key}) <= budget), None)
        if fitting is None:
            break
        selected = selected | {fitting}
        available.remove(fitting)
    return selected


def _shortlists(
    entries: Entries, selected: frozenset[str], mandatory: frozenset[str], cost: Cost
) -> tuple[list[str], list[str]]:
    current_cost = _measure(cost, selected)
    outgoing = sorted(
        selected.difference(mandatory),
        key=lambda key: (
            _ratio(1 + entries[key][2], current_cost - _measure(cost, selected - {key})),
            key,
        ),
    )
    incoming = sorted(
        set(entries).difference(selected),
        key=lambda key: (
            -_ratio(1 + entries[key][2], _measure(cost, selected | {key}) - current_cost),
            key,
        ),
    )
    return outgoing[:_EXCHANGE_WIDTH], incoming[:_EXCHANGE_WIDTH]


def _proposals(
    selected: frozenset[str], outgoing: list[str], incoming: list[str]
) -> Iterator[frozenset[str]]:
    for removed in outgoing:
        remainder = selected - {removed}
        for width in (1, 2):
            for added in combinations(incoming, width):
                yield remainder | frozenset(added)


def _best_exchange(
    entries: Entries,
    selected: frozenset[str],
    mandatory: frozenset[str],
    cost: Cost,
    budget: int,
) -> frozenset[str]:
    best = selected
    original = (_utility(entries, selected), -_measure(cost, selected))
    best_score = original
    outgoing, incoming = _shortlists(entries, selected, mandatory, cost)
    for proposal in _proposals(selected, outgoing, incoming):
        size = _measure(cost, proposal)
        score = (_utility(entries, proposal), -size)
        if size > budget or score <= original:
            continue
        if score > best_score or (score == best_score and sorted(proposal) < sorted(best)):
            best, best_score = proposal, score
    return best


def _exchange(
    entries: Entries,
    selected: frozenset[str],
    mandatory: frozenset[str],
    cost: Cost,
    budget: int,
) -> frozenset[str]:
    for _ in range(_EXCHANGE_ROUNDS):
        proposal = _best_exchange(entries, selected, mandatory, cost, budget)
        if proposal == selected:
            break
        selected = proposal
    return selected


def select(
    policy: str,
    pool: dict[str, dict[str, Any]],
    mandatory: frozenset[str],
    cost: Cost,
    budget: int,
    now: int,
) -> frozenset[str]:
    """Select existing whole bundles without weakening common mandatory pins.

    TTL uses an inclusive age of two steps for optional bundles. The caller may
    supply nonnegative ``ttl_age`` in its TTL clock's units, independently of the
    event clock used by ``created``, ``touch`` and ``now``; otherwise age is
    ``now - created``. Exchange first runs density, then at most four strictly
    improving 1-out/1-or-2-in rounds; each round considers up to twelve incoming
    and twelve removable bundles. Infeasible mandatory reservations fail before
    any optional selection.
    """
    if policy not in POLICIES:
        raise ValueError("unknown bundle retention policy")
    _integer(now, "now")
    entries, base_cost = _validate(pool, mandatory, cost, budget)
    if any(created > now or touch > now for created, touch, _ in entries.values()):
        raise ValueError("policy metadata contains future information")
    if policy in {"union_density", "union_exchange"}:
        selected = _density(entries, mandatory, cost, budget)
        return (
            _exchange(entries, selected, mandatory, cost, budget)
            if policy == "union_exchange"
            else selected
        )
    ordered = _ordered(policy, entries, mandatory, cost, base_cost)
    if policy == "ttl":
        ordered = [key for key in ordered if _ttl_age(pool[key], entries[key][0], now) <= 2]
    return _fill(ordered, mandatory, cost, budget)


def exact_snapshot(
    pool: dict[str, dict[str, Any]],
    mandatory: frozenset[str],
    cost: Cost,
    budget: int,
    max_optional: int = 14,
) -> dict[str, Any] | None:
    """Exact surrogate optimum for this current pool; never a future oracle.

    Return None when enumeration would exceed the declared optional-item cap.
    ``enumerated`` counts every subset, including infeasible ones. Deterministic
    ties prefer lower cost, then lexicographically smaller selected identifiers.
    """
    _integer(max_optional, "max_optional")
    entries, base_cost = _validate(pool, mandatory, cost, budget)
    optional = sorted(set(entries).difference(mandatory))
    if len(optional) > max_optional:
        return None
    best = mandatory
    best_cost = base_cost
    best_utility = _utility(entries, mandatory)
    enumerated = 0
    for width in range(len(optional) + 1):
        for added in combinations(optional, width):
            proposal = mandatory | frozenset(added)
            enumerated += 1
            size = _measure(cost, proposal)
            utility = _utility(entries, proposal)
            score, best_score = (utility, -size), (best_utility, -best_cost)
            if size > budget:
                continue
            if score > best_score or (score == best_score and sorted(proposal) < sorted(best)):
                best, best_cost, best_utility = proposal, size, utility
    return {"selected": best, "utility": best_utility, "cost": best_cost, "enumerated": enumerated}
