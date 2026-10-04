from __future__ import annotations

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation import model_load_validation_lifecycle as lifecycle
from aletheia_lab.evaluation.model_load_contract import Scope, completion_monitor, receipt_checker
from aletheia_lab.evaluation.model_load_validation_lifecycle import (
    Guard,
    PreventionBlocked,
    native_output,
    observation,
    run_slot,
    selection,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = {"A": b"synthetic-A", "B": b"synthetic-B"}


class SyntheticAdapter:
    """Only invoke the supplied synthetic hooks; never import a selected SDK."""

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
        raise RuntimeError("synthetic native-entry failure")


class FailedInitializeAdapter(SyntheticAdapter):
    def load(self, payload: bytes) -> bytes:
        self.before(payload)
        self.after(True)
        raise RuntimeError("synthetic SDK initialization failure after native constructor")


@pytest.fixture
def protocol() -> dict[str, Any]:
    return design.load_protocol(ROOT)


def _run(
    directory: Path,
    protocol: dict[str, Any],
    schedule_name: str,
    branch: str,
    adapter: type[SyntheticAdapter] = SyntheticAdapter,
) -> dict[str, Any]:
    schedule = next(item for item in protocol["schedules"] if item["id"] == schedule_name)
    slot = next(
        item
        for item in design.census(protocol)
        if item["backend"] == "skops"
        and item["schedule"] == schedule_name
        and item["branch"] == branch
    )
    return run_slot(directory, slot, schedule, ARTIFACTS, "synthetic", adapter)


def _truth(row: dict[str, Any]) -> str | None:
    """Compute truth from actual ledger facts without authored schedule labels."""
    ledger = row["ledger"]
    scope, contract = ledger["scope"], ledger["contract"]
    chosen, root = ledger["selection"], ledger["root_binding"]
    inherited = scope["attempt"] > 0 and contract["retry"] == "inherit"
    required = root["digest"] if inherited else chosen["digest"]
    entered = [
        content_sha256(bytes.fromhex(entry["raw_hex"])) for entry in ledger["target_entries"]
    ]
    if not entered:
        return None
    phase = contract["retry"] if scope["attempt"] else contract["policy"]
    violation = (
        len(entered) > 1
        or any(digest != required for digest in entered)
        or chosen["phase"] != phase
        or (inherited and chosen["digest"] != root["digest"])
    )
    return "violation" if violation else "compliant"


def _verdicts(row: dict[str, Any], cutoff: str) -> tuple[str | None, str | None]:
    admitted = observation(row["observations"][cutoff])
    return receipt_checker(admitted).verdict, completion_monitor(admitted).verdict


def test_all_frozen_slots_have_independent_census_and_entry_caps(
    tmp_path: Path,
    protocol: dict[str, Any],
) -> None:
    schedules = {item["id"]: item for item in protocol["schedules"]}
    slots = design.census(protocol)
    assert len(slots) == len({slot["slot_id"] for slot in slots}) == 48
    assert Counter(slot["backend"] for slot in slots) == {"onnxruntime": 24, "skops": 24}
    assert sum(s["planned_target_entries"] > 0 for s in slots) == 44
    assert sum(s["planned_target_entries"] == 0 for s in slots) == 4
    assert (
        sum(s["branch"] == "observation" and s["planned_target_entries"] > 0 for s in slots) == 22
    )
    assert sum(s["planned_target_entries"] + s["planned_auxiliary_entries"] for s in slots) == 72
    rows = [
        run_slot(
            tmp_path / str(index),
            slot,
            schedules[slot["schedule"]],
            ARTIFACTS,
            "synthetic",
            SyntheticAdapter,
        )
        for index, slot in enumerate(slots)
    ]
    assert all(row["status"] == "completed" for row in rows)
    for row in rows:
        ledger, slot = row["ledger"], row["slot"]
        assert ledger["closed"] is True
        assert len(ledger["target_entries"]) <= slot["planned_target_entries"]
        assert len(ledger["auxiliary_entries"]) == slot["planned_auxiliary_entries"]
        assert ledger["sdk_completed_loads"] == len(ledger["target_entries"])
        assert ledger["sdk_completed_auxiliary_loads"] == len(ledger["auxiliary_entries"])
        assert set(row["path_frame"]) == set(protocol["capture"]["path_frame_fields"])
        if slot["branch"] == "prevention":
            assert len(ledger["target_entries"]) <= 1
            assert _truth(row) != "violation"
        else:
            assert not ledger["blocked"]
        if slot["schedule"] == "handoff-resolve-drift":
            assert _truth(row) == "compliant"
            assert _verdicts(row, "before") == _verdicts(row, "after") == ("compliant", "compliant")
    assert (
        sum(
            len(row["ledger"][key])
            for row in rows
            for key in ("target_entries", "auxiliary_entries")
        )
        <= 72
    )


def test_routing_and_expiry_do_not_repair_observer_from_reference(
    tmp_path: Path,
    protocol: dict[str, Any],
) -> None:
    for index, schedule in enumerate(("retry-inherited-foreign-mailbox", "retry-root-expired")):
        row = _run(tmp_path / str(index), protocol, schedule, "observation")
        assert _truth(row) == "violation"
        assert _verdicts(row, "before") == _verdicts(row, "after") == ("unknown", "unknown")
        assert row["ledger"]["root_binding"]  # Private reference keeps its actual root.
        if schedule == "retry-inherited-foreign-mailbox":
            assert row["transport"]["foreign"] == 1
            assert not any(
                r["kind"] == "load" and r["scope"]["attempt"] == 1
                for r in row["observations"]["after"]["records"]
            )
        else:
            assert row["transport"]["expired"] >= 1
            assert not any(
                r["scope"]["attempt"] == 0 for r in row["observations"]["after"]["records"]
            )


def test_delayed_delivery_changes_only_available_evidence(
    tmp_path: Path,
    protocol: dict[str, Any],
) -> None:
    row = _run(tmp_path / "delayed", protocol, "retry-reselect-legitimate", "observation")
    assert _truth(row) == "compliant"
    assert len(row["ledger"]["target_entries"]) == 1
    assert row["transport"]["delayed"] == 1
    assert _verdicts(row, "before") == ("unknown", "unknown")
    assert _verdicts(row, "after") == ("compliant", "compliant")


def test_native_occurrences_and_duplicate_delivery_are_different(
    tmp_path: Path,
    protocol: dict[str, Any],
) -> None:
    duplicate = _run(tmp_path / "duplicate", protocol, "delivery-duplicate-only", "observation")
    repeated = _run(tmp_path / "repeated", protocol, "native-same-buffer-reentry", "observation")
    prevented = _run(tmp_path / "prevented", protocol, "native-same-buffer-reentry", "prevention")
    assert len(duplicate["ledger"]["target_entries"]) == 1
    loads = [r for r in duplicate["observations"]["after"]["records"] if r["kind"] == "load"]
    assert len(loads) == 2 and loads[0] == loads[1]
    assert _verdicts(duplicate, "after") == ("compliant", "compliant")
    assert len(repeated["ledger"]["target_entries"]) == 2
    assert len({e["occurrence"] for e in repeated["ledger"]["target_entries"]}) == 2
    assert _truth(repeated) == "violation"
    assert _verdicts(repeated, "after") == ("violation", "violation")
    assert len(prevented["ledger"]["target_entries"]) == 1
    assert len(prevented["ledger"]["blocked"]) == 1
    assert prevented["ledger"]["blocked"][0]["prior_entries"] == 1
    assert _truth(prevented) == "compliant"


@pytest.mark.parametrize(
    "adapter,native_completed",
    [
        (FailedEntryAdapter, False),
        (FailedInitializeAdapter, True),
    ],
)
def test_failed_entry_counts_separately_from_successful_sdk_loads(
    tmp_path: Path,
    protocol: dict[str, Any],
    adapter: type[SyntheticAdapter],
    native_completed: bool,
) -> None:
    row = _run(tmp_path / "failed", protocol, "native-single-entry", "observation", adapter)
    assert row["status"] == "technical_failure"
    assert len(row["ledger"]["target_entries"]) == 1
    assert row["ledger"]["target_entries"][0]["completed"] is native_completed
    assert row["ledger"]["sdk_completed_loads"] == 0


def test_guard_is_atomic_root_attempt_bound_and_wrong_bytes_do_not_spend_token() -> None:
    scope = Scope("guard-race", 1)
    parent = selection(
        Scope(scope.request, 0), content_sha256(ARTIFACTS["A"]), 1, "pin_at_acceptance", None
    )
    selected = selection(scope, parent.digest, 1, "inherit", parent)
    guard = Guard(scope, selected, parent)
    with pytest.raises(PreventionBlocked, match="wrong_bound_buffer"):
        guard.permit(ARTIFACTS["B"])
    assert guard.used is False

    def permit(_: int) -> bool:
        try:
            guard.permit(ARTIFACTS["A"])
        except PreventionBlocked:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(permit, range(64)))
    assert results.count(True) == 1 and results.count(False) == 63
    foreign = selection(Scope("foreign", 0), parent.digest, 1, "pin_at_acceptance", None)
    with pytest.raises(PreventionBlocked, match="unavailable_root_binding"):
        Guard(scope, selected, foreign).permit(ARTIFACTS["A"])
    with pytest.raises(PreventionBlocked, match="wrong_bound_buffer"):
        Guard(Scope(scope.request, 2), selected, parent).permit(ARTIFACTS["A"])


def test_native_output_captures_os_descriptors_and_restores_them(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    # Isolated workers use ordinary Python streams attached to descriptors 1/2;
    # pytest's replacement stream would otherwise capture print independently.
    with capfd.disabled():
        before = [os.fstat(fd) for fd in (1, 2)]
        with native_output(tmp_path) as messages:
            print("python stdout", flush=True)
            os.write(1, b"native stdout\n")
            os.write(2, b"native stderr\n")
        assert messages == {"stdout": "python stdout\nnative stdout\n", "stderr": "native stderr\n"}
        assert [os.fstat(fd) for fd in (1, 2)] == before
        with pytest.raises(ValueError, match="native output exceeds"), native_output(tmp_path):
            os.write(1, b"x" * 262145)
        assert [os.fstat(fd) for fd in (1, 2)] == before


@pytest.mark.parametrize("descriptor,name", [(1, "stdout"), (2, "stderr")])
def test_noisy_native_output_has_bounded_capture_and_no_disk_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    descriptor: int,
    name: str,
) -> None:
    captures = []
    original_capture = lifecycle.BoundedCapture

    def capture(limit: int) -> Any:
        value = original_capture(limit)
        captures.append(value)
        return value

    monkeypatch.setattr(lifecycle, "BoundedCapture", capture)
    with capfd.disabled():
        before = [os.fstat(fd) for fd in (1, 2)]
        with (
            pytest.raises(ValueError, match="native output exceeds"),
            native_output(tmp_path) as messages,
        ):
            # Four MiB must be drained without growing the retained buffer
            # or a temporary file, even after the first overflow is known.
            for _ in range(1024):
                os.write(descriptor, b"x" * 4096)
        assert [os.fstat(fd) for fd in (1, 2)] == before
    assert len(captures) == 2
    assert all(len(value.data) <= 262144 for value in captures)
    assert captures[descriptor - 1].exceeded.is_set()
    assert name not in messages  # Overflow is never admitted as truncated evidence.
    assert list(tmp_path.iterdir()) == []


def test_output_reader_failure_cannot_succeed_with_empty_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_read(descriptor: int, size: int) -> bytes:
        raise OSError("synthetic capture reader failure")

    monkeypatch.setattr(lifecycle.os, "read", failed_read)
    capture = lifecycle.BoundedCapture(262144)
    with pytest.raises((ValueError, OSError)):
        capture.finish()
    assert not capture.thread.is_alive()
    assert len(capture.data) == 0
    for descriptor in (capture.reader, capture.writer):
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_native_output_restores_descriptors_when_final_flush_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    class FailingStream:
        calls = 0

        def flush(self) -> None:
            self.calls += 1
            if self.calls == 2:
                raise BrokenPipeError("synthetic final flush failure")

    with capfd.disabled():
        before = [os.fstat(fd) for fd in (1, 2)]
        with monkeypatch.context() as context:
            context.setattr(lifecycle.sys, "stdout", FailingStream())
            with (
                pytest.raises(BrokenPipeError, match="final flush failure"),
                native_output(tmp_path),
            ):
                os.write(1, b"bounded native message\n")
        assert [os.fstat(fd) for fd in (1, 2)] == before
