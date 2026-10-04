"""Single-attempt runner checks with inert buffers and synthetic worker entries."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation import model_load_validation_replay as replay
from aletheia_lab.evaluation import model_load_validation_run as runner
from aletheia_lab.evaluation.model_load_contract import receipt_checker
from aletheia_lab.evaluation.model_load_provenance import ProvenanceResult
from aletheia_lab.evaluation.model_load_validation_lifecycle import run_slot
from aletheia_lab.filesystem import publish_immutable_file

ROOT = Path(__file__).resolve().parents[2]


class SyntheticAdapter:
    def __init__(self, backend: str, before: Any, after: Any) -> None:
        self.before, self.after = before, after

    def load(self, payload: bytes) -> bytes:
        self.before(payload)
        self.after(True)
        return payload

    def reenter(self, model: bytes, payload: bytes) -> bytes:
        assert model is payload
        return self.load(payload)


class FailedEntryAdapter(SyntheticAdapter):
    def load(self, payload: bytes) -> bytes:
        self.before(payload)
        self.after(False)
        raise RuntimeError("synthetic native entry failed")


@pytest.fixture
def synthetic_study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    protocol = design.load_protocol(ROOT)
    slots = design.census(protocol)
    root = tmp_path / "checkout"
    root.mkdir()
    plan = tmp_path / "unchanged-plan.json"
    plan.write_text('{"synthetic_design_plan":"immutable"}\n')
    study = tmp_path / "private-study"
    report = {"plan_sha256": "a" * 64, "protocol_sha256": design.PROTOCOL_SHA256}
    state = SimpleNamespace(
        root=root,
        plan=plan,
        study=study,
        report=report,
        protocol=protocol,
        slots=slots,
        code={"synthetic-code.py": "c" * 64},
        inventory={
            "python": "3.12.synthetic",
            "platform": "synthetic",
            "packages": {"onnxruntime": "1.23.2", "skops": "0.15.0"},
        },
        calls=[],
        context_clean=[],
        prepare_status="artifacts_prepared_without_native_load",
        after_prepare=None,
        fail_index=None,
        fail_status="worker_failure",
        fail_error="SyntheticFailure",
        technical_index=None,
        wrong_row_index=None,
    )
    state.artifacts = {
        backend: {name: f"inert-{backend}-{name}".encode() for name in ("A", "B")}
        for backend in ("onnxruntime", "skops")
    }

    def context(root_arg: Path, plan_arg: Path, study_arg: Path, *, clean: bool = True) -> Any:
        assert (root_arg, plan_arg, study_arg) == (root, plan, study)
        state.context_clean.append(clean)
        return root, study, copy.deepcopy(report)

    def git(root_arg: Path, *args: str) -> str:
        assert root_arg == root
        if args == ("rev-parse", "HEAD"):
            return "d" * 40
        assert args == ("merge-base", "--is-ancestor", "d" * 40, "HEAD")
        return ""

    def worker(root_arg: Path, arguments: list[str], timeout: float, directory: Path) -> Any:
        assert root_arg == root and directory == study and 0 < timeout <= 60
        state.calls.append(arguments)
        if arguments[0] == "_prepare":
            if state.prepare_status == "artifacts_prepared_without_native_load":
                for backend, pair in state.artifacts.items():
                    for name, payload in pair.items():
                        publish_immutable_file(
                            study / "artifacts" / f"{backend}-{name}.buffer", payload
                        )
            if state.after_prepare is not None:
                state.after_prepare()
            return {"status": state.prepare_status}
        assert arguments[0] == "_slot"
        index = int(arguments[arguments.index("--slot-index") + 1])
        if state.fail_index == index:
            return {"status": state.fail_status, "error_type": state.fail_error}
        slot = slots[index]
        schedule = next(s for s in protocol["schedules"] if s["id"] == slot["schedule"])
        adapter = FailedEntryAdapter if state.technical_index == index else SyntheticAdapter
        row = run_slot(
            study / f"synthetic-work-{index:02d}",
            slot,
            schedule,
            state.artifacts[slot["backend"]],
            state.inventory["packages"][slot["backend"]],
            adapter,
        )
        runner.publish(study / f"slot-raw-{index:02d}.json", row)
        digests = {
            name: runner.artifact_inventory(study)[slot["backend"]][name]["sha256"]
            for name in ("A", "B")
        }
        row = replay.annotate(row, digests, study / f"provenance-{index:02d}")
        if state.wrong_row_index == index:
            row["slot"] = slots[(index + 1) % len(slots)]
            row = runner.signed({k: v for k, v in row.items() if k != "row_sha256"}, "row_sha256")
        runner.publish(study / f"slot-{index:02d}.json", row)
        return {"status": "slot_worker_complete", "slot_id": slot["slot_id"]}

    # P is deliberately synthetic here: these tests concern runner authority,
    # retained census and replay, not the optional signed in-toto implementation.
    monkeypatch.setattr(
        replay,
        "provenance_checker",
        lambda observed, directory: ProvenanceResult(receipt_checker(observed), 0, None),
    )
    monkeypatch.setattr(runner, "context", context)
    monkeypatch.setattr(runner, "environment", lambda: copy.deepcopy(state.inventory))
    monkeypatch.setattr(runner, "code_hashes", lambda _: copy.deepcopy(state.code))
    monkeypatch.setattr(runner, "worker", worker)
    monkeypatch.setattr(design, "_git", git)
    monkeypatch.setattr(design, "load_protocol", lambda _: copy.deepcopy(protocol))
    return state


def _prepare(state: Any) -> dict[str, Any]:
    return runner.prepare(state.root, state.plan, state.study, state.report["plan_sha256"])


def _execute(state: Any) -> dict[str, Any]:
    seal = _prepare(state)
    return runner.execute(state.root, state.plan, state.study, seal["seal_sha256"])


def _result(state: Any) -> dict[str, Any]:
    return runner.read_signed(state.study / "results.json", "results_sha256")


def _resign(path: Path, value: dict[str, Any], field: str) -> None:
    unsigned = {k: v for k, v in value.items() if k != field}
    path.write_text(json.dumps(runner.signed(unsigned, field)))


def _coverage(analysis: dict[str, Any], unavailable: int) -> None:
    for cutoff in ("before", "after"):
        for method in ("S", "T", "P"):
            counts = analysis["comparisons"][cutoff][method]
            assert counts["planned_denominator"] == 22
            assert counts["reference_unavailable"] == unavailable
            assert counts["reference_assessable_denominator"] == 22 - unavailable


def test_complete_synthetic_run_retains_all_48_and_original_plan(synthetic_study: Any) -> None:
    state = synthetic_study
    original_plan = state.plan.read_bytes()
    preparation = _prepare(state)
    assert preparation["status"] == "artifacts_sealed_no_native_load"
    assert preparation["native_loader_entries"] == 0
    preflight = runner.preflight(state.root, state.plan, state.study)
    assert preflight["execution_ready"] is True and preflight["attempt_consumed"] is False
    report = runner.execute(state.root, state.plan, state.study, preparation["seal_sha256"])
    assert report["reserved_native_entries"] == 72
    result = _result(state)
    assert [row["slot"] for row in result["rows"]] == state.slots
    assert len(result["rows"]) == 48
    assert result["analysis"]["status_counts"] == {"completed": 48}
    assert result["analysis"]["planned_slots"] == 48
    assert result["analysis"]["planned_load_slots"] == 44
    assert result["analysis"]["planned_cache_slots"] == 4
    assert result["analysis"]["retained_native_entries"] <= 72
    _coverage(result["analysis"], 0)
    for index, row in enumerate(result["rows"]):
        path = state.study / f"analysis-{index:02d}.json"
        assert runner.read_signed(path, "row_sha256") == row
        with pytest.raises(FileExistsError, match="single-attempt output"):
            runner.publish(path, row)
    verification = runner.verify(state.root, state.plan, state.study)
    assert verification["verification"] == "pass"
    assert verification["native_loads_replayed"] == 0
    assert verification["analysis"] == report["analysis"]
    assert state.context_clean[-1] is False
    assert state.plan.read_bytes() == original_plan
    assert len(state.calls) == 49
    consumed = runner.preflight(state.root, state.plan, state.study)
    assert consumed["execution_ready"] is False and consumed["attempt_consumed"] is True
    with pytest.raises(FileExistsError):
        runner.execute(state.root, state.plan, state.study, preparation["seal_sha256"])
    assert len(state.calls) == 49


def test_wrong_preparation_confirmation_does_not_consume_worker_or_directory(
    synthetic_study: Any,
) -> None:
    state = synthetic_study
    with pytest.raises(ValueError, match="preparation confirmation"):
        runner.prepare(state.root, state.plan, state.study, "wrong-confirmation")
    assert state.calls == [] and not state.study.exists()


def test_wrong_execution_confirmation_does_not_consume_lease_or_worker(
    synthetic_study: Any,
) -> None:
    state = synthetic_study
    _prepare(state)
    with pytest.raises(ValueError, match="seal confirmation"):
        runner.execute(state.root, state.plan, state.study, "wrong-confirmation")
    assert len(state.calls) == 1
    assert not (state.study / "lease.json").exists()


def test_failed_preparation_is_retained_and_cannot_be_retried(synthetic_study: Any) -> None:
    state = synthetic_study
    state.prepare_status = "slot_timeout"
    assert _prepare(state)["status"] == "preparation_failed_closed"
    assert (state.study / "preparation.json").is_file()
    assert (
        json.loads((state.study / "preparation-failure.json").read_text())["status"]
        == "slot_timeout"
    )
    assert not (state.study / "seal.json").exists()
    with pytest.raises(FileExistsError, match="cannot be replaced"):
        _prepare(state)
    assert len(state.calls) == 1


@pytest.mark.parametrize("drift", ["code", "environment"])
def test_drift_during_preparation_consumes_setup_without_creating_seal(
    synthetic_study: Any, drift: str
) -> None:
    state = synthetic_study

    def change() -> None:
        if drift == "code":
            state.code["synthetic-code.py"] = "e" * 64
        else:
            state.inventory["platform"] = "drifted-platform"

    state.after_prepare = change
    with pytest.raises(ValueError, match="code or environment changed"):
        _prepare(state)
    assert (state.study / "preparation.json").is_file()
    assert not (state.study / "seal.json").exists()
    with pytest.raises(FileExistsError):
        _prepare(state)
    assert len(state.calls) == 1


@pytest.mark.parametrize("drift", ["code", "environment", "artifact", "plan", "seal"])
def test_seal_drift_refuses_execution_before_lease_and_worker(
    synthetic_study: Any, drift: str
) -> None:
    state = synthetic_study
    sealed = _prepare(state)
    if drift == "code":
        state.code["synthetic-code.py"] = "e" * 64
    elif drift == "environment":
        state.inventory["platform"] = "changed-platform"
    elif drift == "artifact":
        (state.study / "artifacts" / "skops-A.buffer").write_bytes(b"changed-inert-buffer")
    elif drift == "plan":
        state.report["plan_sha256"] = "f" * 64
    else:
        path = state.study / "seal.json"
        value = runner.read_signed(path, "seal_sha256")
        value["native_loader_entries"] = 1
        _resign(path, value, "seal_sha256")
    with pytest.raises(ValueError, match="seal no longer matches"):
        runner.execute(state.root, state.plan, state.study, sealed["seal_sha256"])
    assert len(state.calls) == 1
    assert not (state.study / "lease.json").exists()


def test_context_refuses_dirty_preparation_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan, study = tmp_path / "root", tmp_path / "plan", tmp_path / "study"
    monkeypatch.setattr(design, "_root", lambda _: root)
    monkeypatch.setattr(design, "_private", lambda path, _: path)
    monkeypatch.setattr(design, "preflight", lambda *_: {"plan_sha256": "a" * 64})
    monkeypatch.setattr(design, "_git", lambda *_: " M code.py")
    with pytest.raises(ValueError, match="commit and merge"):
        runner.context(root, plan, study)
    assert runner.context(root, plan, study, clean=False)[:2] == (root, study)


@pytest.mark.parametrize("status", ["worker_failure", "slot_timeout", "invalid_worker_output"])
def test_unknown_native_census_stops_remaining_slots_but_retains_fixed_denominator(
    synthetic_study: Any, status: str
) -> None:
    state = synthetic_study
    state.fail_index, state.fail_status = 6, status  # Includes a reserved auxiliary entry.
    report = _execute(state)
    result = _result(state)
    assert len(result["rows"]) == 48
    assert result["rows"][6]["status"] == status
    assert all(row["status"] == "unexecuted" for row in result["rows"][7:])
    assert all(row["error_type"] == "UnavailableNativeCensus" for row in result["rows"][7:])
    expected = sum(
        s["planned_target_entries"] + s["planned_auxiliary_entries"] for s in state.slots[:7]
    )
    assert report["reserved_native_entries"] == expected <= 72
    assert result["analysis"]["slots_with_unavailable_native_census"] == 1
    assert result["analysis"]["status_counts"] == {"completed": 6, status: 1, "unexecuted": 41}
    _coverage(result["analysis"], 19)
    assert len(state.calls) == 8
    assert runner.verify(state.root, state.plan, state.study)["verification"] == "pass"
    with pytest.raises(FileExistsError):
        runner.execute(state.root, state.plan, state.study, result["seal_sha256"])
    assert len(state.calls) == 8


def test_known_failed_native_entry_keeps_full_reservation_and_does_not_drop_slot(
    synthetic_study: Any,
) -> None:
    state = synthetic_study
    state.technical_index = 0
    _execute(state)
    result = _result(state)
    assert result["reserved_native_entries"] == 72
    assert result["analysis"]["status_counts"] == {"technical_failure": 1, "completed": 47}
    failed = result["rows"][0]
    assert failed["ledger"]["target_entries"][0]["completed"] is False
    assert failed["ledger"]["sdk_completed_loads"] == 0
    assert result["analysis"]["slots_with_unavailable_native_census"] == 0
    _coverage(result["analysis"], 1)
    assert runner.verify(state.root, state.plan, state.study)["verification"] == "pass"


def test_worker_signed_row_for_another_slot_stops_without_repairing_identity(
    synthetic_study: Any,
) -> None:
    state = synthetic_study
    state.wrong_row_index = 0
    _execute(state)
    result = _result(state)
    assert result["rows"][0]["slot"] == state.slots[0]
    assert result["rows"][0]["status"] == "invalid_worker_output"
    assert result["analysis"]["status_counts"] == {"invalid_worker_output": 1, "unexecuted": 47}
    assert result["reserved_native_entries"] == 1
    _coverage(result["analysis"], 22)


def test_orphan_lease_is_consumed_and_never_resumes(synthetic_study: Any) -> None:
    state = synthetic_study
    sealed = _prepare(state)
    lease = runner.signed(
        {
            "schema_version": "model-load-execution-lease/v1",
            "seal_sha256": sealed["seal_sha256"],
            "maximum_reserved_native_entries": 72,
            "provider_calls": 0,
        },
        "lease_sha256",
    )
    runner.publish(state.study / "lease.json", lease)
    assert runner.preflight(state.root, state.plan, state.study)["attempt_consumed"] is True
    with pytest.raises(FileExistsError):
        runner.execute(state.root, state.plan, state.study, sealed["seal_sha256"])
    with pytest.raises(ValueError, match="missing"):
        runner.verify(state.root, state.plan, state.study)
    assert len(state.calls) == 1
    assert not (state.study / "results.json").exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "census",
        "denominator",
        "reservation",
        "retained_row",
        "typed_reservation",
        "provider_type",
        "analysis_type",
        "extra",
    ],
)
def test_hash_resigned_result_tampering_is_rejected(synthetic_study: Any, tamper: str) -> None:
    state = synthetic_study
    _execute(state)
    value = _result(state)
    path = state.study / "results.json"
    if tamper == "census":
        value["rows"].pop()
    elif tamper == "denominator":
        value["analysis"]["comparisons"]["before"]["S"]["planned_denominator"] = 21
    elif tamper == "reservation":
        value["reserved_native_entries"] = 73
    elif tamper == "typed_reservation":
        value["reserved_native_entries"] = float(value["reserved_native_entries"])
    elif tamper == "provider_type":
        value["provider_calls"] = False
    elif tamper == "analysis_type":
        value["analysis"]["planned_slots"] = 48.0
    elif tamper == "extra":
        value["undeclared_authority"] = True
    else:
        path = state.study / "analysis-00.json"
        value = runner.read_signed(path, "row_sha256")
        value["extra_field"] = "changed-retained-analysis"
        _resign(path, value, "row_sha256")
    if tamper != "retained_row":
        _resign(path, value, "results_sha256")
    with pytest.raises(ValueError):
        runner.verify(state.root, state.plan, state.study)


def test_worker_timeout_kills_and_reaps_inert_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "inert-cli-root"
    script = root / runner.CLI
    script.parent.mkdir(parents=True)
    script.write_text("import time\ntime.sleep(2)\nprint('{}')\n")
    processes = []
    original_popen = runner.subprocess.Popen

    def spawn(*args: Any, **kwargs: Any) -> Any:
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    output = runner.worker(root, [], 0.1, tmp_path)
    assert output == {"status": "slot_timeout", "error_type": "TimeoutExpired"}
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].returncode != 0


@pytest.mark.parametrize("failure_at", ["reader", "finish"])
def test_worker_capture_failure_is_a_terminal_envelope_and_cleans_both_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    root = tmp_path / "capture-failure-root"
    script = root / runner.CLI
    script.parent.mkdir(parents=True)
    script.write_text("import time\nprint('{}', flush=True)\ntime.sleep(0.1)\n")
    captures, processes = [], []
    original_capture, original_spawn = runner.BoundedCapture, runner.subprocess.Popen

    def capture(limit: int) -> Any:
        value = original_capture(limit)
        if not captures:
            if failure_at == "reader":
                value.failed.set()
            else:
                finish = value.finish

                def fail_finish() -> None:
                    finish()
                    raise ValueError("synthetic capture finish failure")

                monkeypatch.setattr(value, "finish", fail_finish)
        captures.append(value)
        return value

    def spawn(*args: Any, **kwargs: Any) -> Any:
        value = original_spawn(*args, **kwargs)
        processes.append(value)
        return value

    monkeypatch.setattr(runner, "BoundedCapture", capture)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    assert runner.worker(root, [], 5, tmp_path) == {
        "status": "invalid_worker_output",
        "error_type": "CaptureFailure",
    }
    assert len(processes) == 1 and processes[0].poll() is not None
    assert len(captures) == 2 and all(not value.thread.is_alive() for value in captures)


@pytest.mark.parametrize("descriptor,limit", [(1, 4096), (2, 262144)])
def test_noisy_worker_is_killed_with_bounded_capture_and_no_disk_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor: int,
    limit: int,
) -> None:
    root = tmp_path / "noisy-cli-root"
    script = root / runner.CLI
    script.parent.mkdir(parents=True)
    script.write_text(f"import os\nwhile True:\n    os.write({descriptor}, b'x' * 4096)\n")
    directory = tmp_path / "capture"
    directory.mkdir()
    captures, processes = [], []
    original_capture, original_popen = runner.BoundedCapture, runner.subprocess.Popen

    def capture(bound: int) -> Any:
        value = original_capture(bound)
        captures.append(value)
        return value

    def spawn(*args: Any, **kwargs: Any) -> Any:
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runner, "BoundedCapture", capture)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    outcome = runner.worker(root, [], 5, directory)
    assert outcome == {"status": "invalid_worker_output", "error_type": "OutputLimit"}
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].returncode != 0
    assert len(captures) == 2
    assert len(captures[0].data) <= 4096 and len(captures[1].data) <= 262144
    assert captures[descriptor - 1].exceeded.is_set()
    assert len(captures[descriptor - 1].data) <= limit
    assert list(directory.iterdir()) == []


def test_output_limit_stops_run_and_retains_all_planned_slots(synthetic_study: Any) -> None:
    state = synthetic_study
    state.fail_index = 6
    state.fail_status, state.fail_error = "invalid_worker_output", "OutputLimit"
    report = _execute(state)
    result = _result(state)
    assert len(result["rows"]) == 48
    assert result["rows"][6]["error_type"] == "OutputLimit"
    assert result["analysis"]["status_counts"] == {
        "completed": 6,
        "invalid_worker_output": 1,
        "unexecuted": 41,
    }
    assert all(row["error_type"] == "UnavailableNativeCensus" for row in result["rows"][7:])
    assert report["reserved_native_entries"] == sum(
        slot["planned_target_entries"] + slot["planned_auxiliary_entries"]
        for slot in state.slots[:7]
    )
    _coverage(result["analysis"], 19)
    assert runner.verify(state.root, state.plan, state.study)["verification"] == "pass"
