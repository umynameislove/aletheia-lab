"""Read-only validation of development capture, accounting and snapshot optima.

The subset enumerator and offered-query census are independent of the online
selector. A snapshot optimum bounds its saved current-pool surrogate only, not
future audit coverage. This does not rerun a runtime or establish hostile-host
authenticity, historical policy transitions, physical capacity or latency.
"""

from __future__ import annotations

import base64
import json
import zlib
from itertools import product
from typing import Any, cast

from aletheia_lab.evaluation.model_load_serving_analysis import validate_worker
from aletheia_lab.project.identity import content_sha256

_POLICIES = ("static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange")
_BUDGETS = (2048, 4096, 8192, 16384)
_SCHEDULES = ("last_child", "first_child")
_SNAPSHOTS = (2, 5, 8)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _integer(value: Any, reason: str) -> int:
    _require(type(value) is int and value >= 0, reason)
    return int(value)


def _hex(value: Any) -> int:
    _require(type(value) is str and len(value) == 8, "invalid bounded service counter")
    parsed = int(value, 16)
    _require(f"{parsed:08x}" == value, "noncanonical service counter")
    return parsed


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return content_sha256(_encode(value))


def _native(captures: list[dict[str, Any]], models: dict[str, Any]) -> dict[int, list[Any]]:
    _require(len(captures) == 2, "native capture denominator changed")
    tapes: dict[int, list[Any]] = {}
    for capture in captures:
        config = capture["config"]
        fanout = config["inferences"]
        _require(type(fanout) is int and fanout in (1, 6), "unexpected native fanout")
        expected = {"repeat": 0, "depth": 6, "inferences": fanout, "arm": "hash", "horizon": 0}
        _require(config == expected and fanout not in tapes, "native configuration census changed")
        _require(capture["status"] == "complete", "incomplete native capture")
        _require(capture["source_cluster_count"] == 1, "native source-cluster count changed")
        validate_worker(capture, models)
        for row in capture["rows"]:
            _native_frame(row)
        _require(capture["captured_reconstructions"] == 9, "native reconstruction count changed")
        tapes[fanout] = capture["rows"]
    return tapes


def _native_frame(row: dict[str, Any]) -> None:
    frame = row["frame"]
    _require(frame["closed"] is True, "native frame was not completed")
    _require(
        all(frame[key] == row[key] for key in ("scope", "kind", "step", "status")),
        "native frame occurrence binding changed",
    )
    _require(all(event["scope"] == row["scope"] for event in row["events"]), "event scope changed")


def _offers(rows: list[Any], schedule: str) -> list[dict[str, Any]]:
    queries = [{"scope": row["scope"], "due": row["step"], "age": 0} for row in rows]
    for step in range(12):
        batch = [row for row in rows if row["step"] == step]
        load = next(row for row in batch if row["kind"] == "load")
        children = [row for row in batch if row["kind"] == "infer"]
        child = children[0] if schedule == "first_child" else children[-1]
        for target, age in product((load, child), (2, 8)):
            queries.append({"scope": target["scope"], "due": step + age, "age": age})
    return sorted(queries, key=lambda query: (query["due"], query["scope"], query["age"]))


def _failure(result: dict[str, Any], rows: list[Any]) -> tuple[int, int]:
    if result["status"] == "complete":
        _require(
            result["failure_at"] is None and result["error_type"] is None, "false failure marker"
        )
        return len(rows), 20
    _require(result["status"] == "service_failure", "unknown replay status")
    failure = result["failure_at"]
    sequence = _integer(failure["sequence"], "invalid failure sequence")
    now = _integer(failure["now"], "invalid failure slot")
    _require(sequence <= len(rows) and now < 20, "failure outside replay boundary")
    _require(
        type(failure["reason_code"]) is str and bool(failure["reason_code"]),
        "failure reason absent",
    )
    _require(
        type(result["error_type"]) is str and bool(result["error_type"]), "failure type absent"
    )
    _require(not sequence or rows[sequence - 1]["step"] <= now, "failure precedes accepted ingress")
    _require(sequence == len(rows) or rows[sequence]["step"] >= now, "failure skips ingress")
    return sequence, now


def _queries(result: dict[str, Any], rows: list[Any], now: int) -> None:
    offered = _offers(rows, result["schedule"])
    queries = result["queries"]
    coordinates = [{key: query[key] for key in ("scope", "due", "age")} for query in queries]
    _require(
        coordinates == offered[: len(queries)], "offered queries are not a chronological prefix"
    )
    _require(all(query["due"] <= now for query in queries), "query occurs after failure")
    past = sum(query["due"] < now for query in offered)
    _require(len(queries) >= past, "earlier offered queries were omitted")
    truth = {row["scope"]: row["truth"] for row in rows}
    correct = false = 0
    for query in queries:
        good = query["decision"] == truth[query["scope"]]
        bad = query["decision"] is not None and not good
        _require(
            query["correct"] is good and query["false"] is bad,
            "query score differs from native truth",
        )
        correct += good
        false += bad
    _require(result["offered_queries"] == len(offered), "offered-query denominator changed")
    _require(result["processed_queries"] == len(queries), "processed-query denominator changed")
    _require(result["correct"] == correct and result["false"] == false, "query totals changed")
    _require(
        result["unknown_or_unprocessed"] == len(offered) - correct - false, "missing audits hidden"
    )


def _leases(result: dict[str, Any], rows: list[Any], sequence: int, now: int) -> None:
    arrivals = rows[:sequence]
    planned = [
        (row["scope"], row["step"] + 2)
        for row in arrivals
        if row["kind"] == "load" or row["scope"].endswith("infer-0")
    ]
    leases = result["leases"]
    _require(
        [(lease["scope"], lease["due"]) for lease in leases] == planned,
        "hard-lease ingress census changed",
    )
    truth = {row["scope"]: row["truth"] for row in rows}
    for lease in leases:
        _lease(lease, truth, now)
    accepted = sum(lease["accepted"] is True for lease in leases)
    failed = sum(
        lease["accepted"] is True and not lease.get("fulfilled", False) for lease in leases
    )
    _require(result["hard_lease_offers"] == 24, "planned hard-lease denominator changed")
    _require(result["hard_accepted"] == accepted, "accepted hard-lease count changed")
    _require(result["hard_refused_or_unprocessed"] == 24 - accepted, "refused hard leases hidden")
    _require(result["hard_failures"] == failed, "accepted hard-lease failures hidden")


def _lease(lease: dict[str, Any], truth: dict[str, Any], now: int) -> None:
    _require(type(lease["accepted"]) is bool, "nonboolean lease admission")
    answered = "decision" in lease
    _require(answered == ("fulfilled" in lease), "lease answer/score evidence is incomplete")
    if not lease["accepted"]:
        _require(not answered, "refused lease was reported fulfilled")
        return
    _require(answered or lease["due"] >= now, "prior accepted deadline was not accounted")
    if answered:
        _require(lease["due"] <= now, "lease answer uses future information")
        _require(
            lease["fulfilled"] is (lease["decision"] == truth[lease["scope"]]),
            "lease score differs from native truth",
        )


def _metrics(result: dict[str, Any]) -> None:
    metrics, extension = result["metrics"], result["extension"]
    for value in metrics.values():
        _integer(value, "invalid archive diagnostic")
    _integer(result["replay_wall_ns"], "invalid replay duration")
    _require(metrics["logical_budget_bytes"] == result["budget"], "reported capacity changed")
    _require(metrics["peak_logical_bytes"] <= result["budget"], "logical archive cap exceeded")
    complete = result["status"] == "complete"
    _require(type(extension["accepted"]) is bool, "nonboolean lease extension")
    if complete:
        _require(
            extension["retained_before"] == extension["retained_after"],
            "late extension resurrected evidence",
        )
        _integer(extension["retained_before"], "invalid retained-bundle count")
    else:
        _require(
            not extension["accepted"] and extension["not_attempted_after_failure"] is True,
            "failure extension changed",
        )
    offered = len(result["leases"]) + int(complete)
    accepted = result["hard_accepted"] + int(extension["accepted"])
    _require(metrics["lease_offers"] == offered, "actual lease-offer count changed")
    _require(metrics["accepted_leases"] == accepted, "actual lease acceptance changed")
    _require(metrics["refused_leases"] == offered - accepted, "actual lease refusal changed")


def _atom(key: str, value: str) -> dict[str, Any]:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(value, validate=True), 8193)
    _require(decoder.eof and not decoder.unused_data and len(raw) <= 8192, "invalid snapshot atom")
    frame = json.loads(raw)
    _require(type(frame) is dict and _encode(frame) == raw, "snapshot atom is noncanonical")
    _require(content_sha256(raw) == key, "snapshot atom digest changed")
    return cast(dict[str, Any], frame)


def _basis(snapshot: dict[str, Any], rows: list[Any]) -> dict[str, Any]:
    basis, pool = snapshot["cost_basis"], snapshot["pool"]
    sequence, now = snapshot["sequence"], snapshot["now"]
    _require(basis["schema"] == "audit-bundle-archive/v2", "unexpected snapshot schema")
    _require(
        _hex(basis["sequence"]) == sequence + 1 and _hex(basis["now"]) == now,
        "snapshot counters changed",
    )
    clock = _hex(basis["clock"])
    _require(set(pool) == set(basis["entries"]), "snapshot pool differs from charged manifests")
    prefix = {row["scope"]: row["frame"] for row in rows[: sequence + 1]}
    frames = {key: _atom(key, value) for key, value in basis["atoms"].items()}
    for key, entry in basis["entries"].items():
        _basis_entry(key, entry, pool[key], prefix, frames, clock)
    needed = {
        atom
        for entry in basis["entries"].values()
        for atom in (entry["target"], entry["parent"])
        if atom is not None
    }
    _require(needed == set(frames), "snapshot dependency union changed")
    return cast(dict[str, Any], basis)


def _basis_entry(key: str, entry: Any, stats: Any, prefix: Any, frames: Any, clock: int) -> None:
    _require(key in prefix and entry["scope"] == key, "snapshot evidence is foreign or future")
    _require(
        frames[entry["target"]] == prefix[key], "snapshot target differs from native occurrence"
    )
    frame = prefix[key]
    parent = _digest(prefix[frame["generation"]]) if frame["kind"] == "infer" else None
    _require(entry["parent"] == parent, "snapshot resident-generation dependency changed")
    _require(
        entry["seal"] == _digest([key, entry["target"], parent]), "snapshot manifest seal changed"
    )
    _require(_hex(entry["slot"]) == frame["step"], "snapshot admission slot changed")
    for field in ("created", "touch", "hits"):
        value = _integer(stats[field], "invalid snapshot policy statistic")
        _require(value == _hex(entry[field]), "snapshot statistics differ from charged metadata")
        _require(field == "hits" or value <= clock, "snapshot policy sees future clock")


def _snapshot_pins(
    snapshot: dict[str, Any], result: dict[str, Any], rows: list[Any], basis: Any
) -> None:
    indices = {row["scope"]: index for index, row in enumerate(rows)}
    live = {
        lease["scope"]: lease["due"]
        for lease in result["leases"]
        if lease["accepted"]
        and indices[lease["scope"]] <= snapshot["sequence"]
        and lease["due"] >= snapshot["now"]
    }
    _require(
        {key: _hex(value) for key, value in basis["leases"].items()} == live,
        "snapshot accepted leases changed",
    )
    resident = rows[snapshot["sequence"]]["frame"]["generation"]
    _require(basis["resident"] == resident, "snapshot resident pin changed")
    mandatory = set(live) | {resident}
    _require(snapshot["mandatory"] == sorted(mandatory), "snapshot mandatory union changed")
    _require(mandatory <= set(snapshot["pool"]), "snapshot dropped a mandatory bundle")


def _cost(basis: Any, selected: set[str]) -> int:
    entries = {key: basis["entries"][key] for key in selected}
    needed = {
        atom
        for entry in entries.values()
        for atom in (entry["target"], entry["parent"])
        if atom is not None
    }
    document = {**basis, "entries": entries, "atoms": {key: basis["atoms"][key] for key in needed}}
    return len(_encode(document))


def _objective(pool: Any, selected: set[str]) -> int:
    return sum(1 + pool[key]["hits"] for key in selected)


def _optimum(pool: Any, mandatory: set[str], basis: Any, budget: int) -> dict[str, Any]:
    optional = sorted(set(pool) - mandatory)
    best: tuple[Any, ...] | None = None
    winner: dict[str, Any] = {}
    for bits in product((False, True), repeat=len(optional)):
        selected = mandatory | {key for key, bit in zip(optional, bits, strict=True) if bit}
        cost = _cost(basis, selected)
        utility = _objective(pool, selected)
        score = (-utility, cost, sorted(selected))
        if cost <= budget and (best is None or score < best):
            best = score
            winner = {
                "selected": sorted(selected),
                "utility": utility,
                "cost": cost,
                "enumerated": 2 ** len(optional),
            }
    _require(best is not None, "snapshot mandatory union is infeasible")
    return winner


def _snapshots(result: dict[str, Any], rows: list[Any], sequence: int) -> None:
    snapshots = result["oracle_snapshots"]
    expected = [index for index in _SNAPSHOTS if index < sequence]
    _require(
        [snapshot["sequence"] for snapshot in snapshots] == expected, "snapshot census changed"
    )
    for snapshot in snapshots:
        _require(snapshot["now"] == rows[snapshot["sequence"]]["step"], "snapshot slot changed")
        basis = _basis(snapshot, rows)
        _snapshot_pins(snapshot, result, rows, basis)
        mandatory, selected = set(snapshot["mandatory"]), set(snapshot["selected"])
        _require(
            snapshot["selected"] == sorted(selected),
            "selected snapshot identifiers are noncanonical",
        )
        _require(
            mandatory <= selected <= set(snapshot["pool"]),
            "selected snapshot drops pins or invents evidence",
        )
        cost, utility = _cost(basis, selected), _objective(snapshot["pool"], selected)
        _require(
            cost <= result["budget"] and snapshot["selected_cost"] == cost,
            "selected snapshot cost changed",
        )
        _require(snapshot["selected_utility"] == utility, "selected surrogate utility changed")
        _snapshot_oracle(snapshot, mandatory, basis, result["budget"])


def _snapshot_oracle(snapshot: Any, mandatory: set[str], basis: Any, budget: int) -> None:
    optional_count = len(snapshot["pool"]) - len(mandatory)
    _require(snapshot["optional_count"] == optional_count, "snapshot optional count changed")
    if optional_count > 10:
        _require(snapshot["oracle"] is None, "snapshot enumeration exceeded fixed cap")
        return
    optimum = _optimum(snapshot["pool"], mandatory, basis, budget)
    _require(
        snapshot["oracle"] == optimum, "saved snapshot optimum differs from independent enumeration"
    )
    _require(
        snapshot["selected_utility"] <= optimum["utility"],
        "selected surrogate exceeds exact optimum",
    )


def validate_records(
    captures: list[dict[str, Any]], models: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    """Validate saved raw records without mutation, provider calls or runtime reruns."""
    try:
        tapes = _native(captures, models)
        expected = set(product((1, 6), _BUDGETS, _SCHEDULES, _POLICIES))
        keys = [
            (result["fanout"], result["budget"], result["schedule"], result["policy"])
            for result in results
        ]
        _require(
            len(keys) == len(expected) and set(keys) == expected,
            "112-cell development census changed",
        )
        for result in results:
            rows = tapes[result["fanout"]]
            _require(
                result["native_operation_count"] == len(rows),
                "native-operation denominator changed",
            )
            sequence, now = _failure(result, rows)
            _queries(result, rows, now)
            _leases(result, rows, sequence, now)
            _metrics(result)
            _snapshots(result, rows, sequence)
    except (KeyError, TypeError, IndexError, zlib.error) as exc:
        raise ValueError("malformed development evidence") from exc
