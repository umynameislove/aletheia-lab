from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import audit_bundle_screen as screen
from aletheia_lab.evaluation.audit_bundle_screen import offers, replay

A, B = "a" * 64, "b" * 64
CAPTURE = screen._capture


def rows(fanout: int = 1) -> list[dict[str, Any]]:
    result = []
    for step in range(12):
        scope = f"load-{step}"
        frame = {
            "scope": scope,
            "kind": "load",
            "step": step,
            "domain": [A, B],
            "expected": A,
            "observed": [A],
            "count": 1,
            "closed": True,
            "status": 200,
            "generation": scope,
        }
        result.append(
            {
                "scope": scope,
                "kind": "load",
                "step": step,
                "frame": frame,
                "truth": {"verdict": "compliant", "eligibility": "load", "resident": None},
            }
        )
        for index in range(fanout):
            child = f"slot-{step}-infer-{index}"
            frame = {
                **frame,
                "scope": child,
                "kind": "infer",
                "expected": None,
                "observed": [],
                "count": 0,
            }
            result.append(
                {
                    "scope": child,
                    "kind": "infer",
                    "step": step,
                    "frame": frame,
                    "truth": {
                        "verdict": None,
                        "eligibility": "no_new_load",
                        "resident": "compliant",
                    },
                }
            )
    return result


def test_offered_census_includes_full_delayed_tail() -> None:
    tape = rows(6)
    first, last = offers(tape, "first_child"), offers(tape, "last_child")
    assert len(first) == len(last) == 132
    assert max(query["due"] for query in first) == 19
    assert {query["scope"] for query in first if query["age"] == 8} != {
        query["scope"] for query in last if query["age"] == 8
    }


@pytest.mark.parametrize(
    "policy", ["static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange"]
)
def test_small_screen_preserves_denominator_and_hard_safety(tmp_path: Path, policy: str) -> None:
    result = replay(tmp_path / "archive.sqlite", rows(), policy, 4096, "last_child")
    assert result["status"] == "complete"
    assert result["offered_queries"] == result["processed_queries"] == 72
    assert result["hard_lease_offers"] == 24
    assert result["false"] == result["hard_failures"] == 0
    assert result["correct"] + result["unknown_or_unprocessed"] == 72
    assert result["metrics"]["peak_logical_bytes"] <= 4096
    assert result["extension"]["retained_before"] == result["extension"]["retained_after"]
    assert len(result["oracle_snapshots"]) == 3


def test_too_small_mandatory_capacity_is_failure_not_free_unknown(tmp_path: Path) -> None:
    result = replay(tmp_path / "archive.sqlite", rows(), "static", 512, "last_child")
    assert result["status"] == "service_failure"
    assert result["offered_queries"] == 72
    assert result["hard_refused_or_unprocessed"] == 24
    assert result["unknown_or_unprocessed"] == 72


@pytest.fixture(scope="module")
def completed_screen(tmp_path_factory: pytest.TempPathFactory) -> Any:
    base = tmp_path_factory.mktemp("screen-orchestration")
    root, directory = base / "root", base / "study"
    root.mkdir()
    captures = [
        {
            "status": "complete",
            "rows": rows(fanout),
            "config": {"repeat": 0, "depth": 6, "inferences": fanout, "arm": "hash", "horizon": 0},
        }
        for fanout in (1, 6)
    ]
    # Isolate orchestration; strict native/reference/byte-cost validation has its
    # own independently shaped fixture in test_audit_bundle_verify.py.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(screen, "design", lambda _: {"development_only": True, "code": "synthetic"})
        patch.setattr(screen, "prepare_models", lambda _: {"synthetic": True})
        patch.setattr(screen, "_capture", lambda _root, _directory, index, _fanout: captures[index])
        patch.setattr(screen, "validate_worker", lambda *_: None)
        # Subset enumeration is checked against native-shaped snapshots in the
        # independent-verifier tests, not duplicated in this CLI/file-binding fixture.
        patch.setattr(screen, "exact_snapshot", lambda *_args, **_kwargs: None)
        import aletheia_lab.evaluation.audit_bundle_verify as verification

        checked: list[int] = []
        patch.setattr(verification, "validate_records", lambda _n, _m, r: checked.append(len(r)))
        result = screen.run(root, directory)
        yield root, directory, base, result, checked


def test_orchestration_binds_complete_matrix_and_reads_without_writing(
    completed_screen: Any,
) -> None:
    root, directory, _, result, checked = completed_screen
    assert result["policy_runs"] == 112 and result["native_operations"] == 108
    before = (directory / "results.json").read_bytes()
    verified = screen.verify(root, directory)
    assert verified["status"] == "development_reference_and_aggregate_replay_pass"
    assert verified["native_calls_executed"] == verified["provider_calls"] == 0
    assert checked[-1] == 112 and (directory / "results.json").read_bytes() == before


def test_reused_capture_makes_separate_revision_without_fitting(
    completed_screen: Any, monkeypatch: Any
) -> None:
    root, original, base, _, _ = completed_screen

    def forbidden(*_: Any) -> Any:
        raise AssertionError("replay must not fit/capture")

    monkeypatch.setattr(screen, "prepare_models", forbidden)
    monkeypatch.setattr(screen, "_capture", forbidden)
    revised = base / "revised"
    result = screen.run(root, revised, original)
    assert result["native_capture_reused"] is True and result["native_calls_executed"] == 0
    assert (revised / "native-0.json").read_bytes() == (original / "native-0.json").read_bytes()
    assert screen.verify(root, revised)["analysis"] == result["analysis"]


@pytest.mark.parametrize("changed", ["plan", "results", "native-0", "models"])
def test_hash_bindings_reject_changed_artifacts(completed_screen: Any, changed: str) -> None:
    root, directory, _, _, _ = completed_screen
    path = directory / f"{changed}.json"
    before = path.read_bytes()
    payload = json.loads(before)
    payload["unexpected"] = True
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            screen.verify(root, directory)
    finally:
        path.write_bytes(before)


def test_rehashed_aggregate_cannot_change_raw_totals(completed_screen: Any) -> None:
    root, directory, _, _, _ = completed_screen
    path = directory / "results.json"
    before = path.read_bytes()
    payload = json.loads(before)
    payload.pop("results_sha256")
    payload["analysis"] = deepcopy(payload["analysis"])
    payload["analysis"]["disposition"] = "fabricated_gain"
    payload["results_sha256"] = screen._hash(payload)
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="aggregate"):
            screen.verify(root, directory)
    finally:
        path.write_bytes(before)


@pytest.mark.parametrize("case", ["timeout", "nonzero", "bad_json", "valid"])
def test_capture_envelope_preserves_native_failure(
    tmp_path: Path, monkeypatch: Any, case: str
) -> None:
    def invoke(*args: Any, **kwargs: Any) -> Any:
        if case == "timeout":
            raise subprocess.TimeoutExpired("synthetic", 90)
        return subprocess.CompletedProcess(
            args,
            1 if case == "nonzero" else 0,
            stdout="invalid" if case == "bad_json" else '{"rows":[]}',
            stderr="",
        )

    (tmp_path / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(screen.subprocess, "run", invoke)
    monkeypatch.setattr(screen, "child_environment", lambda _: {})
    monkeypatch.setattr(screen, "validate_worker", lambda *_: None)
    observed = CAPTURE(tmp_path, tmp_path, 0, 1)
    assert (
        observed["status"]
        == {
            "timeout": "timeout",
            "nonzero": "native_failure",
            "bad_json": "invalid_native_result",
            "valid": "complete",
        }[case]
    )


def test_cli_error_is_hash_only_and_never_exposes_private_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/audit_bundle_screen.py"),
            "verify",
            "--root",
            str(root),
            "--study-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 1
    assert str(tmp_path) not in completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "audit_bundle_screen_failed_closed"
