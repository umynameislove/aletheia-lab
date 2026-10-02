from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import aletheia_lab.evaluation.source_evidence_acquisition as acquisition
from aletheia_lab.evaluation.source_evidence_admission import SourceDocument
from aletheia_lab.project.identity import content_sha256

A, B = "a" * 64, "b" * 64
CATALOGUE = ("requested_endpoint", "loaded_endpoint", "both_endpoints")
ROOT = Path(__file__).resolve().parents[2]


def native_source(tmp_path: Path, *, pointer: str = "/events/0") -> acquisition.QuerySource:
    path = tmp_path / acquisition.STORES[1] / "load-trace.json"
    path.parent.mkdir(parents=True)
    events = [
        {"declared_artifact_sha256": A, "actual_loaded_sha256": A, "case": "SECRET_CONTROL"},
        {"declared_artifact_sha256": A, "actual_loaded_sha256": B, "case": "SECRET_CONTROL"},
    ]
    raw = json.dumps({"events": events}).encode()
    path.write_bytes(raw)
    return acquisition.QuerySource(
        tmp_path, acquisition.STORES[1], pointer, content_sha256(raw), "slot"
    )


def initial(source: acquisition.QuerySource, view: str) -> list[SourceDocument]:
    record, _, _ = acquisition._record(source)
    return [acquisition._document(record, source=source, view=view)]


@pytest.mark.parametrize(
    "pointer,gold", [("/events/0", "no_binding_fault"), ("/events/1", "binding_fault")]
)
@pytest.mark.parametrize(
    "view,action",
    [
        ("full", None),
        ("requested_endpoint", "loaded_endpoint"),
        ("loaded_endpoint", "requested_endpoint"),
        ("neither", "both_endpoints"),
    ],
)
def test_executed_query_closes_only_missing_endpoint_information(
    tmp_path: Path, pointer: str, gold: str, view: str, action: str | None
) -> None:
    source = native_source(tmp_path, pointer=pointer)
    inputs = initial(source, view)
    assert acquisition.choose_query(inputs, scope="slot", budget=2, available=CATALOGUE) == action
    no_query = acquisition.run_query(inputs, source=source, action=None)
    queried = acquisition.run_query(inputs, source=source, action=action)
    assert queried["after_compatible"] == [gold]
    assert no_query["after_state"] == ("identified" if view == "full" else "ambiguous")
    assert queried["newly_resolved"] is (view != "full")
    if action:
        ledger = queried["ledger"]
        assert ledger["status"] == "observed"
        assert ledger["source_read_executed"] is True
        assert ledger["source_bytes_read"] == source.path().stat().st_size
        assert all(item.startswith(pointer + "/") for item in ledger["returned_fact_pointers"])
        assert "SECRET_CONTROL" not in json.dumps(queried)
    else:
        assert queried["ledger"] is None


def test_same_loaded_witness_has_zero_gain_but_intention_distinguishes(tmp_path: Path) -> None:
    source = native_source(tmp_path, pointer="/events/1")
    inputs = initial(source, "loaded_endpoint")
    useless = acquisition.run_query(inputs, source=source, action="loaded_endpoint")
    useful = acquisition.run_query(inputs, source=source, action="requested_endpoint")
    assert useless["ledger"]["status"] == useful["ledger"]["status"] == "observed"
    assert useless["cost_units"] == useful["cost_units"] == 1
    assert useless["newly_resolved"] is False
    assert useless["after_state"] == "ambiguous"
    assert useful["after_compatible"] == ["binding_fault"]


def test_planner_falls_back_to_combined_capability_without_peeking(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    inputs = initial(source, "loaded_endpoint")
    assert (
        acquisition.choose_query(inputs, scope="slot", budget=2, available=("both_endpoints",))
        == "both_endpoints"
    )
    assert (
        acquisition.choose_query(inputs, scope="slot", budget=1, available=("both_endpoints",))
        is None
    )
    assert acquisition.choose_query(inputs, scope="slot", budget=0, available=CATALOGUE) is None
    assert (
        acquisition.choose_query(
            initial(source, "neither"), scope="slot", budget=1, available=CATALOGUE
        )
        is None
    )


def test_unavailable_and_read_failure_are_charged_not_observations(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    inputs = initial(source, "requested_endpoint")
    unavailable = acquisition.run_query(
        inputs, source=source, action="loaded_endpoint", available=()
    )
    assert unavailable["cost_units"] == 1
    assert unavailable["ledger"]["status"] == "unavailable"
    assert unavailable["ledger"]["source_read_executed"] is False
    source.path().unlink()
    error = acquisition.run_query(inputs, source=source, action="loaded_endpoint")
    assert error["cost_units"] == 1
    assert error["ledger"]["status"] == "source_error"
    assert error["after_state"] == "ambiguous"
    assert error["newly_resolved"] is False


def test_changed_snapshot_fails_after_read_and_reports_actual_bytes(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    inputs = initial(source, "requested_endpoint")
    source.path().write_bytes(b"changed native bytes")
    result = acquisition.run_query(inputs, source=source, action="loaded_endpoint")
    assert result["ledger"]["status"] == "source_error"
    assert result["ledger"]["source_read_executed"] is True
    assert result["ledger"]["source_bytes_read"] == len(b"changed native bytes")
    assert result["cost_units"] == 1
    assert result["newly_resolved"] is False


def test_different_record_or_snapshot_is_not_joined_even_with_equal_scope(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    other = replace(source, pointer="/events/1")
    with pytest.raises(ValueError, match="different origins"):
        acquisition.run_query(
            initial(other, "requested_endpoint"), source=source, action="loaded_endpoint"
        )
    different_snapshot = replace(source, container_sha256="f" * 64)
    with pytest.raises(ValueError, match="different origins"):
        acquisition.run_query(
            initial(source, "requested_endpoint"),
            source=different_snapshot,
            action="loaded_endpoint",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "other"),
        ("producer", "report"),
        ("schema", "unverified"),
        ("pointer", "/invented"),
    ],
)
def test_initial_capability_metadata_is_caller_bound(
    tmp_path: Path, field: str, value: str
) -> None:
    source = native_source(tmp_path)
    document = replace(initial(source, "requested_endpoint")[0], **{field: value})
    with pytest.raises(ValueError):
        acquisition.run_query([document], source=source, action=None)


@pytest.mark.parametrize(
    "store,pointer",
    [
        ("../protected", "/events/0"),
        (acquisition.STORES[0], "/paths/faulty"),
        (acquisition.STORES[1], "/events/6"),
        (acquisition.STORES[1], "/events/01"),
        (acquisition.STORES[2], "/doses/999/events/0"),
    ],
)
def test_path_and_pointer_allowlist_cannot_expand_from_a_proposal(
    tmp_path: Path, store: str, pointer: str
) -> None:
    source = acquisition.QuerySource(tmp_path, store, pointer, A, "slot")
    with pytest.raises(ValueError):
        source.path()


def test_symlink_is_not_a_read_capability(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    target = tmp_path / "outside.json"
    target.write_bytes(source.path().read_bytes())
    source.path().unlink()
    try:
        source.path().symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable on this host")
    with pytest.raises(ValueError):
        source.path()


@pytest.mark.parametrize("budget", [-1, 3, True, 1.0])
def test_query_budget_is_explicit_bounded_integer(tmp_path: Path, budget: Any) -> None:
    source = native_source(tmp_path)
    with pytest.raises(ValueError):
        acquisition.run_query(initial(source, "neither"), source=source, action=None, budget=budget)


def test_invalid_or_overbudget_queries_do_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = native_source(tmp_path)
    inputs = initial(source, "neither")

    def tripwire(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected source read")

    monkeypatch.setattr(Path, "read_bytes", tripwire)
    for action, budget in (("repair_model", 2), ("both_endpoints", 1)):
        with pytest.raises(ValueError):
            acquisition.run_query(inputs, source=source, action=action, budget=budget)


def test_new_measurement_never_overwrites_an_existing_conflict(tmp_path: Path) -> None:
    source = native_source(tmp_path)
    first = initial(source, "full")[0]
    contrary = replace(first, raw=json.dumps({"actual_loaded_sha256": B}).encode())
    assert (
        acquisition.choose_query([first, contrary], scope="slot", budget=2, available=CATALOGUE)
        is None
    )
    result = acquisition.run_query([first, contrary], source=source, action="both_endpoints")
    assert result["after_state"] == "conflict"
    assert result["newly_resolved"] is False


def _fixture_stores(tmp_path: Path) -> tuple[Path, Path]:
    # Reuse the existing synthetic receipt-chain fixture by path, not by relying
    # on tests/scripts being importable packages on CI.
    path = Path(__file__).with_name("test_source_evidence_headroom.py")
    spec = importlib.util.spec_from_file_location("source_headroom_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, memory = module.synthetic_stores(tmp_path)
    return Path(root), Path(memory)


def test_complete_native_replay_excludes_aggregates_and_preserves_all_sources(
    tmp_path: Path,
) -> None:
    root, memory = _fixture_stores(tmp_path)
    result = acquisition.replay_acquisition(root=root, memory_root=memory)
    assert result["native_runtime_record_count"] == 36
    assert result["authored_view_count"] == result["correct_post_query_count"] == 144
    assert result["initial_identified_count"] == 36
    assert (
        result["newly_resolved_count"]
        == result["source_query_count"]
        == result["observed_query_count"]
        == 108
    )
    assert result["query_cost_units"] == 144
    assert result["source_cluster_count"] == result["producer_family_count"] == 1
    assert result["llm_planner_measured"] is result["naturally_missing_evidence_measured"] is False
    assert result["provider_calls"] == 0
    assert result["validation_preparation"]["execution_authorized"] is False


def test_audit_to_acquisition_snapshot_gap_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, memory = _fixture_stores(tmp_path)
    observed = acquisition.audit_development_sources(root=root, memory_root=memory)
    path = memory / acquisition.STORES[1] / "load-trace.json"
    native = json.loads(path.read_bytes())
    native["events"][0]["actual_loaded_sha256"] = "f" * 64
    path.write_text(json.dumps(native), encoding="utf-8")
    monkeypatch.setattr(acquisition, "audit_development_sources", lambda **_: observed)
    with pytest.raises(ValueError, match="snapshot changed"):
        acquisition.replay_acquisition(root=root, memory_root=memory)


def test_cli_is_hash_seed_stable_offline_and_sanitizes_failed_sources(tmp_path: Path) -> None:
    root, memory = _fixture_stores(tmp_path)
    script = ROOT / "scripts/replay_source_evidence_acquisition.py"
    results = []
    for seed in ("1", "104729"):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--memory-root", str(memory)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        results.append(completed.stdout)
    assert results[0] == results[1]
    assert "rows" not in json.loads(results[0])
    failed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "--memory-root",
            str(memory / "PRIVATE_MISSING"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert failed.returncode == 1
    assert "PRIVATE_MISSING" not in failed.stdout + failed.stderr
    assert json.loads(failed.stdout)["status"] == "source_acquisition_failed_closed"
