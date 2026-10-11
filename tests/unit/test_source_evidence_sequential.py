from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from itertools import combinations, permutations, product
from pathlib import Path
from typing import Any

import pytest

import aletheia_lab.evaluation.source_evidence_acquisition as legacy
import aletheia_lab.evaluation.source_evidence_sequential as sequential
from aletheia_lab.evaluation.source_evidence_admission import PRODUCER, SourceDocument
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
A, B = "a" * 64, "b" * 64
REQUESTED, LOADED, BOTH = sequential.CATALOGUE
SINGLES = (REQUESTED, LOADED)
FIELDS = ("declared_artifact_sha256", "actual_loaded_sha256")
WORLD = tuple(product((A, B), repeat=2))
OBSERVATIONS = {REQUESTED: (0,), LOADED: (1,), BOTH: (0, 1)}
COSTS = {REQUESTED: 1, LOADED: 1, BOTH: 2}


def _source(tmp_path: Path, record: dict[str, Any] | None = None) -> legacy.QuerySource:
    path = tmp_path / legacy.STORES[1] / "load-trace.json"
    path.parent.mkdir(parents=True)
    value = record if record is not None else {FIELDS[0]: A, FIELDS[1]: B}
    raw = json.dumps({"events": [{**value, "case": "PRIVATE_CONTROL"}]}).encode()
    path.write_bytes(raw)
    return legacy.QuerySource(tmp_path, legacy.STORES[1], "/events/0", content_sha256(raw), "slot")


def _initial(source: legacy.QuerySource, view: str = "neither") -> list[SourceDocument]:
    record, _, _ = legacy._record(source)
    return [legacy._document(record, source=source, view=view)]


def _world_document(world: tuple[str, str], visible: tuple[int, ...]) -> SourceDocument:
    raw = json.dumps({FIELDS[index]: world[index] for index in visible}).encode()
    return SourceDocument(raw, "loader-event/v1", PRODUCER, "slot", "/frame")


def _guarantees_status(
    world: tuple[str, str], visible: tuple[int, ...], sequence: tuple[str, ...]
) -> bool:
    # Independent observation-partition oracle: no production resolver/role coverage.
    candidates = [item for item in WORLD if all(item[index] == world[index] for index in visible)]
    partitions: dict[tuple[str, ...], set[bool]] = {}
    for item in candidates:
        signature = tuple(item[index] for action in sequence for index in OBSERVATIONS[action])
        partitions.setdefault(signature, set()).add(item[0] == item[1])
    return all(len(statuses) == 1 for statuses in partitions.values())


def test_exact_planner_matches_independent_partition_oracle_without_source_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_read(*_: object, **__: object) -> bytes:
        raise AssertionError("planning cannot read hidden source bytes")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    catalogues = [items for size in range(4) for items in combinations(sequential.CATALOGUE, size)]
    counts = {"checked": 0, "solvable": 0, "one_step_gaps": 0}
    for world, visible, catalogue, budget in product(
        WORLD, ((0, 1), (0,), (1,), ()), catalogues, range(3)
    ):
        initial = [_world_document(world, visible)]
        feasible = [
            sequence
            for length in range(3)
            for sequence in permutations(catalogue, length)
            if sum(COSTS[action] for action in sequence) <= budget
            and _guarantees_status(world, visible, sequence)
        ]
        plan = sequential.choose_query_plan(
            initial, scope="slot", budget=budget, available=catalogue
        )
        counts["checked"] += 1
        if feasible:
            counts["solvable"] += 1
            minimum = min(sum(COSTS[action] for action in item) for item in feasible)
            assert plan in feasible
            assert sum(COSTS[action] for action in plan) == minimum
            assert len(plan) == min(
                len(item) for item in feasible if sum(COSTS[a] for a in item) == minimum
            )
        else:
            assert plan == ()
        if not visible and catalogue == SINGLES and budget == 2:
            assert (
                legacy.choose_query(initial, scope="slot", budget=budget, available=catalogue)
                is None
            )
            assert plan == (LOADED, REQUESTED)
            counts["one_step_gaps"] += 1
    assert counts == {"checked": 384, "solvable": 196, "one_step_gaps": 4}


@pytest.mark.parametrize("world", WORLD, ids=("AA", "AB", "BA", "BB"))
def test_two_actual_single_reads_resolve_all_worlds_and_both_orderings(
    tmp_path: Path, world: tuple[str, str]
) -> None:
    source = _source(tmp_path, dict(zip(FIELDS, world, strict=True)))
    initial = _initial(source)
    outcomes = [
        sequential.run_query_sequence(initial, source=source, available=SINGLES, actions=actions)
        for actions in (None, SINGLES, tuple(reversed(SINGLES)))
    ]
    expected = "no_binding_fault" if world[0] == world[1] else "binding_fault"
    for result in outcomes:
        assert result["after_compatible"] == [expected]
        assert result["newly_resolved"] is True
        assert result["cost_units"] == 2
        assert result["remaining_budget_units"] == 0
        assert len(result["ledgers"]) == 2
        assert result["source_bytes_read"] == 2 * source.path().stat().st_size
        assert result["ledgers"][0]["post_query_state"] == "ambiguous"
        assert result["ledgers"][1]["post_query_state"] == "identified"
        assert "PRIVATE_CONTROL" not in json.dumps(result)


def test_equal_cost_tie_prefers_combined_read_without_claiming_equal_physical_io(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    initial = _initial(source)
    combined = sequential.run_query_sequence(initial, source=source)
    singles = sequential.run_query_sequence(initial, source=source, available=SINGLES)
    assert sequential.choose_query_plan(
        initial, scope="slot", budget=2, available=sequential.CATALOGUE
    ) == (BOTH,)
    assert combined["cost_units"] == singles["cost_units"] == 2
    assert combined["source_bytes_read"] * 2 == singles["source_bytes_read"]
    assert len(combined["ledgers"]) == 1


@pytest.mark.parametrize("view", (REQUESTED, LOADED))
def test_one_missing_role_costs_one_and_full_initial_never_reads(tmp_path: Path, view: str) -> None:
    source = _source(tmp_path)
    result = sequential.run_query_sequence(_initial(source, view), source=source, budget=1)
    assert result["newly_resolved"] is True
    assert result["cost_units"] == len(result["ledgers"]) == 1
    full = sequential.run_query_sequence(_initial(source, "full"), source=source)
    assert full["stop_reason"] == "identified"
    assert full["cost_units"] == full["source_bytes_read"] == 0
    assert full["newly_resolved"] is False


@pytest.mark.parametrize("budget", (0, 1))
def test_no_affordable_resolving_plan_does_not_spend_or_commit(tmp_path: Path, budget: int) -> None:
    source = _source(tmp_path)
    result = sequential.run_query_sequence(_initial(source), source=source, budget=budget)
    assert result["after_state"] == "ambiguous"
    assert result["cost_units"] == 0
    assert result["remaining_budget_units"] == budget
    assert result["ledgers"] == []


@pytest.mark.parametrize("mode", ("unavailable", "missing-file", "snapshot-drift"))
def test_failed_attempt_is_charged_and_cannot_be_negative_evidence(
    tmp_path: Path, mode: str
) -> None:
    source = _source(tmp_path)
    initial = _initial(source)
    if mode == "missing-file":
        source.path().unlink()
    elif mode == "snapshot-drift":
        source.path().write_bytes(b"changed source")
    result = sequential.run_query_sequence(
        initial,
        source=source,
        available=() if mode == "unavailable" else SINGLES,
        actions=(REQUESTED, LOADED),
    )
    assert result["stop_reason"] == "query_failed"
    assert result["after_state"] == "ambiguous"
    assert result["cost_units"] == result["remaining_budget_units"] == 1
    assert len(result["ledgers"]) == 1
    assert result["newly_resolved"] is False
    assert result["source_bytes_read"] == (
        len(b"changed source") if mode == "snapshot-drift" else 0
    )


def test_observed_empty_projection_is_not_advertised_facts(tmp_path: Path) -> None:
    source = _source(tmp_path, {FIELDS[0]: A})
    result = sequential.run_query_sequence(_initial(source, REQUESTED), source=source)
    assert result["ledgers"][0]["status"] == "observed"
    assert result["ledgers"][0]["returned_fact_pointers"] == []
    assert result["after_state"] == "ambiguous"
    assert result["stop_reason"] == "no_information_gain"
    assert result["cost_units"] == 1


def test_partial_combined_return_is_retained_but_not_fabricated(tmp_path: Path) -> None:
    source = _source(tmp_path, {FIELDS[0]: A})
    result = sequential.run_query_sequence(_initial(source), source=source, available=(BOTH,))
    assert result["ledgers"][0]["status"] == "observed"
    assert len(result["ledgers"][0]["returned_fact_pointers"]) == 1
    assert result["after_state"] == "ambiguous"
    assert result["stop_reason"] == "budget_exhausted"
    assert result["cost_units"] == 2


def test_useless_supplied_read_uses_same_conservative_stop_rule(tmp_path: Path) -> None:
    source = _source(tmp_path)
    initial = _initial(source, LOADED)
    baseline = sequential.run_query_sequence(initial, source=source)
    supplied = sequential.run_query_sequence(initial, source=source, actions=(LOADED, REQUESTED))
    assert baseline["newly_resolved"] is True
    assert supplied["newly_resolved"] is False
    assert supplied["cost_units"] == 1
    assert supplied["stop_reason"] == "no_information_gain"
    assert len(supplied["ledgers"]) == 1


def test_missing_or_exhausted_proposal_is_not_a_baseline_fallback(tmp_path: Path) -> None:
    source = _source(tmp_path)
    empty = sequential.run_query_sequence(_initial(source), source=source, actions=())
    partial = sequential.run_query_sequence(_initial(source), source=source, actions=(REQUESTED,))
    assert empty["cost_units"] == 0
    assert partial["cost_units"] == 1
    assert empty["stop_reason"] == partial["stop_reason"] == "proposal_exhausted"
    assert empty["after_state"] == partial["after_state"] == "ambiguous"


def test_initial_and_acquired_conflicts_are_preserved(tmp_path: Path) -> None:
    source = _source(tmp_path, {FIELDS[0]: B, FIELDS[1]: B})
    actual = _initial(source, REQUESTED)[0]
    contrary = replace(actual, raw=json.dumps({FIELDS[0]: A}).encode())
    initial_conflict = sequential.run_query_sequence([actual, contrary], source=source)
    acquired_conflict = sequential.run_query_sequence([contrary], source=source, available=(BOTH,))
    assert initial_conflict["after_state"] == acquired_conflict["after_state"] == "conflict"
    assert initial_conflict["cost_units"] == 0
    assert acquired_conflict["cost_units"] == 2
    assert acquired_conflict["newly_resolved"] is False


def test_malformed_return_blocks_commit_without_dropping_it(tmp_path: Path) -> None:
    source = _source(tmp_path, {FIELDS[0]: A, FIELDS[1]: 123})
    result = sequential.run_query_sequence(_initial(source, REQUESTED), source=source)
    assert result["ledgers"][0]["status"] == "invalid_evidence"
    assert result["after_state"] == "admission_unresolved"
    assert result["newly_resolved"] is False
    assert result["cost_units"] == 1


@pytest.mark.parametrize(
    "actions", [(REQUESTED, REQUESTED), ("repair",), (BOTH, REQUESTED), [REQUESTED]]
)
def test_invalid_proposal_fails_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actions: Any
) -> None:
    source = _source(tmp_path)
    initial = _initial(source)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected source read"))
    with pytest.raises(ValueError, match="query sequence"):
        sequential.run_query_sequence(initial, source=source, actions=actions)


@pytest.mark.parametrize(
    "field,value",
    [("scope", "other"), ("producer", "report"), ("schema", "unknown"), ("pointer", "/different")],
)
def test_initial_metadata_is_checked_even_when_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    source = _source(tmp_path)
    initial = [replace(_initial(source, "full")[0], **{field: value})]
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected source read"))
    with pytest.raises(ValueError):
        sequential.run_query_sequence(initial, source=source)


@pytest.mark.parametrize("budget", [-1, 3, True, 1.0])
def test_budget_is_explicit_bounded_integer(tmp_path: Path, budget: Any) -> None:
    source = _source(tmp_path)
    with pytest.raises(ValueError):
        sequential.run_query_sequence(_initial(source), source=source, budget=budget)


def test_invalid_catalogue_and_document_capacity_fail_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    initial = _initial(source)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected source read"))
    for catalogue in ((REQUESTED, REQUESTED), ("repair",)):
        with pytest.raises(ValueError):
            sequential.run_query_sequence(initial, source=source, available=catalogue)
    with pytest.raises(ValueError, match="room"):
        sequential.run_query_sequence(initial * 11, source=source)


def _fixture_stores(tmp_path: Path) -> tuple[Path, Path]:
    path = Path(__file__).with_name("test_source_evidence_headroom.py")
    spec = importlib.util.spec_from_file_location("sequential_headroom_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, memory = module.synthetic_stores(tmp_path)
    return Path(root), Path(memory)


def test_native_replay_measures_actual_reads_without_claiming_natural_missingness(
    tmp_path: Path,
) -> None:
    root, memory = _fixture_stores(tmp_path)
    combined = sequential.replay_sequential_acquisition(root=root, memory_root=memory)
    singles = sequential.replay_sequential_acquisition(
        root=root, memory_root=memory, available=SINGLES
    )
    for report in (combined, singles):
        assert report["native_runtime_record_count"] == 36
        assert report["authored_view_count"] == report["correct_post_query_count"] == 144
        assert report["initial_identified_count"] == 36
        assert report["newly_resolved_count"] == 108
        assert report["query_cost_units"] == 144
        assert report["source_cluster_count"] == report["producer_family_count"] == 1
        assert report["sources_unchanged"] is True
        assert report["source_sha256_before"] == report["source_sha256_after"]
        assert (
            report["llm_planner_measured"] is report["naturally_missing_evidence_measured"] is False
        )
        assert report["provider_calls"] == 0
    assert combined["source_query_count"] == combined["observed_query_count"] == 108
    assert singles["source_query_count"] == singles["observed_query_count"] == 144
    limited = sequential.replay_sequential_acquisition(
        root=root, memory_root=memory, available=SINGLES, budget=1
    )
    assert limited["correct_post_query_count"] == 108
    assert limited["stop_reason_counts"] == {"identified": 108, "no_resolving_plan": 36}
    assert "unresolved is not a wrong commitment" in limited["post_query_count_meaning"]


def test_sequential_cli_is_hash_seed_stable_and_hides_private_rows(tmp_path: Path) -> None:
    root, memory = _fixture_stores(tmp_path)
    command = [
        sys.executable,
        str(ROOT / "scripts/replay_source_evidence_acquisition.py"),
        "--root",
        str(root),
        "--memory-root",
        str(memory),
        "--sequential",
        "--available",
        *SINGLES,
    ]
    outputs = []
    for seed in ("1", "104729"):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=90
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    report = json.loads(outputs[0])
    assert report["source_query_count"] == 144
    assert all(
        key not in report
        for key in ("rows", "source_sha256_before", "source_sha256_after", "executed_module_sha256")
    )
