"""Public worker authority paths with real local seals and inert SDK replacements."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation import model_load_validation_replay as replay
from aletheia_lab.evaluation import model_load_validation_run as runner
from aletheia_lab.evaluation import model_load_validation_worker as worker
from aletheia_lab.evaluation.model_load_contract import receipt_checker
from aletheia_lab.evaluation.model_load_provenance import ProvenanceResult

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


@pytest.fixture
def worker_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "checkout"
    for relative in dict.fromkeys((*design.CODE_PATHS, *runner.EXTRA_CODE)):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    state = SimpleNamespace(
        root=root,
        plan=tmp_path / "design.json",
        study=tmp_path / "private-study",
        dirty=False,
        dispatched_slots=[],
        builds=0,
        synthetic_fits=0,
        synthetic_entries=0,
        after_build=None,
        after_load=None,
        lock=threading.Lock(),
        inventory={
            "python": "3.12.synthetic",
            "platform": "synthetic",
            "packages": {"onnxruntime": "1.23.2", "skops": "0.15.0"},
        },
        artifacts={
            backend: {name: f"inert-{backend}-{name}".encode() for name in ("A", "B")}
            for backend in ("onnxruntime", "skops")
        },
    )

    def git(path: Path, *arguments: str) -> str:
        assert path == root.resolve()
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(root)
        if arguments == ("rev-parse", "HEAD"):
            return HEAD
        if arguments == ("status", "--porcelain"):
            return " M bound-code.py" if state.dirty else ""
        assert arguments == ("merge-base", "--is-ancestor", HEAD, "HEAD")
        return ""

    def build() -> dict[str, dict[str, bytes]]:
        with state.lock:
            state.builds += 1
            state.synthetic_fits += 2
        if state.after_build is not None:
            state.after_build()
        return copy.deepcopy(state.artifacts)

    class SyntheticAdapter:
        def __init__(self, backend: str, before: Any, after: Any) -> None:
            self.before, self.after = before, after

        def load(self, payload: bytes) -> bytes:
            self.before(payload)
            state.synthetic_entries += 1
            self.after(True)
            if state.after_load is not None:
                state.after_load()
            return payload

        def reenter(self, model: bytes, payload: bytes) -> bytes:
            assert model is payload
            return self.load(payload)

    def dispatch(root_arg: Path, arguments: list[str], timeout: float, directory: Path) -> Any:
        assert root_arg == root and directory == state.study
        if arguments[0] == "_prepare":
            assert timeout == 60
            return worker.prepare_worker(root_arg, state.plan, state.study)
        assert arguments[0] == "_slot" and 0 < timeout <= 30
        index = int(arguments[arguments.index("--slot-index") + 1])
        state.dispatched_slots.append(index)
        return worker.slot_worker(root_arg, state.plan, state.study, index)

    monkeypatch.setattr(design, "_git", git)
    monkeypatch.setattr(worker, "environment", lambda: copy.deepcopy(state.inventory))
    monkeypatch.setattr(runner, "environment", lambda: copy.deepcopy(state.inventory))
    monkeypatch.setattr(worker, "build_artifacts", build)
    monkeypatch.setattr(worker, "NativeAdapter", SyntheticAdapter)
    monkeypatch.setattr(
        replay,
        "provenance_checker",
        lambda observed, directory: ProvenanceResult(receipt_checker(observed), 0, None),
    )
    monkeypatch.setattr(worker.os, "umask", lambda _: 0o077)
    monkeypatch.setattr(runner, "worker", dispatch)
    state.report = design.prepare(root, state.plan)
    state.protocol = design.load_protocol(root)
    state.slots = design.census(state.protocol)
    return state


def _authority(state: Any, **changes: Any) -> dict[str, Any]:
    state.study.mkdir(mode=0o700)
    value = runner.signed(
        {
            "schema_version": "model-load-preparation-authority/v1",
            "plan_sha256": state.report["plan_sha256"],
            "code_sha256": runner.code_hashes(state.root),
            "environment": state.inventory,
            "git_head": HEAD,
            "max_local_fits": 2,
            "native_entries_authorized": 0,
            **changes,
        },
        "request_sha256",
    )
    runner.publish(state.study / "preparation.json", value)
    return value


def _sealed(state: Any) -> dict[str, Any]:
    runner.prepare(state.root, state.plan, state.study, state.report["plan_sha256"])
    return runner.read_signed(state.study / "seal.json", "seal_sha256")


def _lease(state: Any, **changes: Any) -> dict[str, Any]:
    seal = _sealed(state)
    value = runner.signed(
        {
            "schema_version": "model-load-execution-lease/v1",
            "seal_sha256": seal["seal_sha256"],
            "maximum_reserved_native_entries": 72,
            "provider_calls": 0,
            **changes,
        },
        "lease_sha256",
    )
    runner.publish(state.study / "lease.json", value)
    return seal


def _stable_index(state: Any) -> int:
    return next(
        index
        for index, slot in enumerate(state.slots)
        if slot["schedule"] == "native-single-entry" and slot["branch"] == "observation"
    )


def test_public_preparation_requires_matching_authority_then_exactly_two_stub_fits(
    worker_state: Any,
) -> None:
    state = worker_state
    state.study.mkdir()
    with pytest.raises(ValueError, match="missing"):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert state.builds == state.synthetic_fits == state.synthetic_entries == 0
    state.study.rmdir()
    authority = _authority(state)
    outcome = worker.prepare_worker(state.root, state.plan, state.study)
    assert (
        authority["max_local_fits"] == outcome["local_fits_completed"] == state.synthetic_fits == 2
    )
    assert authority["native_entries_authorized"] == outcome["native_loader_entries"] == 0
    assert state.builds == 1 and state.synthetic_entries == 0
    assert set(path.name for path in (state.study / "artifacts").iterdir()) == {
        "onnxruntime-A.buffer",
        "onnxruntime-B.buffer",
        "skops-A.buffer",
        "skops-B.buffer",
    }
    with pytest.raises(FileExistsError):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert state.builds == 1 and state.synthetic_fits == 2


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "other"},
        {"plan_sha256": "b" * 64},
        {"code_sha256": {}},
        {"environment": {}},
        {"git_head": "b" * 40},
        {"max_local_fits": 1},
        {"max_local_fits": 3},
        {"native_entries_authorized": 1},
        {"native_entries_authorized": False},
        {"max_local_fits": 2.0},
        {"undeclared_authority": True},
    ],
)
def test_rehashed_wrong_preparation_authority_never_consumes_artifact_folder(
    worker_state: Any,
    change: dict[str, Any],
) -> None:
    state = worker_state
    _authority(state, **change)
    with pytest.raises(ValueError, match="scoped authority"):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert not (state.study / "artifacts").exists()
    assert state.builds == state.synthetic_fits == state.synthetic_entries == 0


def test_dirty_preparation_and_racing_folder_do_not_create_additional_fits(
    worker_state: Any,
) -> None:
    state = worker_state
    _authority(state)
    state.dirty = True
    with pytest.raises(ValueError, match="scoped authority"):
        worker.prepare_worker(state.root, state.plan, state.study)
    state.dirty = False
    barrier = threading.Barrier(2)

    def attempt(_: int) -> str:
        barrier.wait(timeout=5)
        try:
            worker.prepare_worker(state.root, state.plan, state.study)
        except FileExistsError:
            return "consumed"
        return "prepared"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["consumed", "prepared"]
    assert state.builds == 1 and state.synthetic_fits == 2 and state.synthetic_entries == 0


@pytest.mark.parametrize("failure", ["backend", "pair", "bytes", "empty", "oversized", "identical"])
def test_invalid_preparation_artifact_census_consumes_setup_without_retry(
    worker_state: Any,
    failure: str,
) -> None:
    state = worker_state
    _authority(state)
    if failure == "backend":
        state.artifacts.pop("skops")
    elif failure == "pair":
        state.artifacts["skops"]["extra"] = b"extra"
    elif failure == "bytes":
        state.artifacts["skops"]["A"] = bytearray(b"not immutable")
    elif failure == "empty":
        state.artifacts["skops"]["A"] = b""
    elif failure == "oversized":
        state.artifacts["skops"]["A"] = b"x" * 262145
    else:
        state.artifacts["skops"]["B"] = state.artifacts["skops"]["A"]
    with pytest.raises(ValueError):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert (state.study / "artifacts").is_dir()
    assert not (state.study / "seal.json").exists()
    with pytest.raises(FileExistsError):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert state.builds == 1 and state.synthetic_fits == 2 and state.synthetic_entries == 0


def test_code_drift_during_preparation_is_detected_after_actual_public_worker(
    worker_state: Any,
) -> None:
    state = worker_state
    _authority(state)
    bound = state.root / runner.EXTRA_CODE[1]
    state.after_build = lambda: bound.write_bytes(bound.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="bound code or environment"):
        worker.prepare_worker(state.root, state.plan, state.study)
    assert (state.study / "artifacts").is_dir() and state.builds == 1
    assert not (state.study / "seal.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "other"},
        {"seal_sha256": "b" * 64},
        {"maximum_reserved_native_entries": 73},
        {"provider_calls": 1},
        {"provider_calls": False},
        {"maximum_reserved_native_entries": 72.0},
        {"undeclared_authority": True},
    ],
)
def test_public_slot_rejects_wrong_rehashed_execution_lease_before_entry(
    worker_state: Any,
    change: dict[str, Any],
) -> None:
    state = worker_state
    _lease(state, **change)
    with pytest.raises(ValueError, match="native-execution authority"):
        worker.slot_worker(state.root, state.plan, state.study, _stable_index(state))
    assert state.synthetic_entries == 0
    assert not list(state.study.glob("work-*"))


@pytest.mark.parametrize("index", [-1, 48, True, 1.0])
def test_public_slot_index_is_strict_and_inside_48_slot_census(
    worker_state: Any, index: Any
) -> None:
    state = worker_state
    _lease(state)
    with pytest.raises(ValueError, match="fixed census"):
        worker.slot_worker(state.root, state.plan, state.study, index)
    assert state.synthetic_entries == 0


def test_public_slot_has_one_consumed_work_directory_and_immutable_result(
    worker_state: Any,
) -> None:
    state = worker_state
    _lease(state)
    index = _stable_index(state)
    outcome = worker.slot_worker(state.root, state.plan, state.study, index)
    assert outcome == {"status": "slot_worker_complete", "slot_id": state.slots[index]["slot_id"]}
    path = state.study / f"slot-{index:02d}.json"
    original = path.read_bytes()
    row = runner.read_signed(path, "row_sha256")
    assert row["slot"] == state.slots[index]
    assert len(row["ledger"]["target_entries"]) == row["ledger"]["sdk_completed_loads"] == 1
    with pytest.raises(FileExistsError):
        worker.slot_worker(state.root, state.plan, state.study, index)
    assert state.synthetic_entries == 1 and path.read_bytes() == original
    with pytest.raises(FileExistsError):
        runner.publish(path, row)
    assert path.read_bytes() == original


def test_public_slot_checks_actual_read_buffer_before_entering_sdk(
    worker_state: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = worker_state
    _lease(state)
    index = _stable_index(state)
    backend = state.slots[index]["backend"]
    artifact = state.study / "artifacts" / f"{backend}-A.buffer"
    original_read = Path.read_bytes

    def raced_read(path: Path) -> bytes:
        if path == artifact:
            return b"synthetic-buffer-changed-after-seal-check"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", raced_read)
    with pytest.raises(ValueError, match="sealed|seal|buffer"):
        worker.slot_worker(state.root, state.plan, state.study, index)
    assert state.synthetic_entries == 0
    assert not (state.study / f"work-{index:02d}").exists()


@pytest.mark.parametrize("drift", ["code", "environment"])
def test_public_slot_detects_postexecution_drift_without_publishing_or_retrying(
    worker_state: Any,
    drift: str,
) -> None:
    state = worker_state
    _lease(state)
    index = _stable_index(state)
    if drift == "code":
        bound = state.root / runner.EXTRA_CODE[1]
        state.after_load = lambda: bound.write_bytes(bound.read_bytes() + b"\n")
    else:
        state.after_load = lambda: state.inventory.update(platform="changed")
    with pytest.raises(ValueError, match="changed inside worker"):
        worker.slot_worker(state.root, state.plan, state.study, index)
    assert state.synthetic_entries == 1 and (state.study / f"work-{index:02d}").is_dir()
    assert not (state.study / f"slot-{index:02d}.json").exists()
    raw = runner.read_signed(state.study / f"slot-raw-{index:02d}.json", "row_sha256")
    assert len(raw["ledger"]["target_entries"]) == 1


def test_public_slot_preserves_raw_capture_when_provenance_fails(
    worker_state: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = worker_state
    _lease(state)
    index = _stable_index(state)

    def failed_provenance(*_: Any) -> Any:
        raise RuntimeError("synthetic signed-verifier failure")

    monkeypatch.setattr(replay, "provenance_checker", failed_provenance)
    with pytest.raises(RuntimeError, match="signed-verifier failure"):
        worker.slot_worker(state.root, state.plan, state.study, index)
    raw_path = state.study / f"slot-raw-{index:02d}.json"
    retained = raw_path.read_bytes()
    raw = runner.read_signed(raw_path, "row_sha256")
    assert len(raw["ledger"]["target_entries"]) == 1 and state.synthetic_entries == 1
    assert not (state.study / f"slot-{index:02d}.json").exists()
    with pytest.raises(FileExistsError):
        worker.slot_worker(state.root, state.plan, state.study, index)
    assert raw_path.read_bytes() == retained and state.synthetic_entries == 1


def test_real_public_workers_complete_fixed_census_under_runner_reservations(
    worker_state: Any,
) -> None:
    state = worker_state
    seal = _sealed(state)
    result = runner.execute(state.root, state.plan, state.study, seal["seal_sha256"])
    retained = runner.read_signed(state.study / "results.json", "results_sha256")
    assert result["reserved_native_entries"] == 72
    assert state.dispatched_slots == list(range(48))
    assert [row["slot"] for row in retained["rows"]] == state.slots
    assert len(retained["rows"]) == 48 and retained["analysis"]["status_counts"] == {
        "completed": 48
    }
    assert runner._private_budget(state.study) <= runner.MAX_OUTPUT
    assert state.builds == 1 and state.synthetic_fits == 2 and state.synthetic_entries <= 72
    assert runner.verify(state.root, state.plan, state.study)["verification"] == "pass"
    calls = state.synthetic_entries
    with pytest.raises(FileExistsError):
        runner.execute(state.root, state.plan, state.study, seal["seal_sha256"])
    assert state.synthetic_entries == calls


@pytest.mark.parametrize("limit", ["storage", "time"])
def test_resource_stop_before_launch_retains_all_48_slots_and_consumed_lease(
    worker_state: Any,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    state = worker_state
    seal = _sealed(state)
    if limit == "storage":
        monkeypatch.setattr(runner, "MAX_OUTPUT", 1_000_000)
    else:
        clock = iter([0.0, *([1801.0] * 48)])
        monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    result = runner.execute(state.root, state.plan, state.study, seal["seal_sha256"])
    retained = runner.read_signed(state.study / "results.json", "results_sha256")
    assert result["reserved_native_entries"] == 0
    assert state.dispatched_slots == [] and state.synthetic_entries == 0
    assert len(retained["rows"]) == 48
    assert all(
        row["status"] == "unexecuted" and row["error_type"] == "ResourceLimit"
        for row in retained["rows"]
    )
    assert runner._private_budget(state.study) <= runner.MAX_OUTPUT
    for methods in retained["analysis"]["comparisons"].values():
        assert all(
            counts["planned_denominator"] == counts["reference_unavailable"] == 22
            for counts in methods.values()
        )
    assert runner.preflight(state.root, state.plan, state.study)["attempt_consumed"] is True


def test_publish_enforces_cumulative_32_mib_before_creating_next_file(tmp_path: Path) -> None:
    retained = tmp_path / "retained-synthetic-result.bin"
    with retained.open("xb") as handle:
        handle.truncate(33_554_431)
    destination = tmp_path / "next-result.json"
    with pytest.raises(ValueError, match="publication would exceed"):
        runner.publish(destination, {"status": "synthetic terminal result"})
    assert not destination.exists() and retained.stat().st_size == 33_554_431


def test_cli_missing_slot_index_fails_before_worker_action(
    worker_state: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = worker_state
    specification = importlib.util.spec_from_file_location(
        "validation_worker_cli", ROOT / runner.CLI
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(ROOT / runner.CLI),
            "_slot",
            "--root",
            str(state.root),
            "--plan",
            str(state.plan),
            "--study-dir",
            str(state.study),
        ],
    )
    assert module.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "model_load_validation_failed_closed",
        "error_type": "ValueError",
    }
    assert state.synthetic_entries == state.builds == 0 and not state.study.exists()
