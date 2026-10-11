"""Offline replay controls use synthetic bytes and a fake native boundary only."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation import model_load_validation_replay as replay
from aletheia_lab.evaluation.model_load_contract import receipt_checker
from aletheia_lab.evaluation.model_load_provenance import ProvenanceResult, document_digest
from aletheia_lab.evaluation.model_load_validation_lifecycle import run_slot
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = {"A": b"synthetic-A", "B": b"synthetic-B"}
DIGESTS = {key: content_sha256(raw) for key, raw in ARTIFACTS.items()}


class FakeAdapter:
    def __init__(self, backend, before, after):
        self.before, self.after = before, after

    def load(self, payload):
        self.before(payload)
        self.after(True)
        return object()

    def reenter(self, model, payload):
        return self.load(payload)


@pytest.fixture
def fake_p(monkeypatch):
    # Actual signed P integration is covered separately; these tests isolate replay.
    monkeypatch.setattr(
        replay,
        "provenance_checker",
        lambda obs, path: ProvenanceResult(receipt_checker(obs), 0, None),
    )


def make_row(
    tmp_path, schedule_id="native-single-entry", branch="observation", adapter=FakeAdapter
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    protocol = design.load_protocol(ROOT)
    slot = next(
        s
        for s in design.census(protocol)
        if s["backend"] == "onnxruntime" and s["schedule"] == schedule_id and s["branch"] == branch
    )
    schedule = next(s for s in protocol["schedules"] if s["id"] == schedule_id)
    return run_slot(tmp_path / schedule_id, slot, schedule, ARTIFACTS, "1.23.2", adapter)


@pytest.mark.parametrize("field", ["phase", "digest"])
def test_reference_selection_rules_are_normative_without_candidate_or_schedule_labels(
    tmp_path, field
):
    row = make_row(tmp_path, "retry-inherited-foreign-mailbox")
    entry = row["ledger"]["target_entries"][0]
    entry.update(raw_hex=ARTIFACTS["A"].hex(), sha256=DIGESTS["A"])
    chosen = row["ledger"]["selection"]
    chosen[field] = "reselect" if field == "phase" else DIGESTS["B"]
    row["expected_status"] = "compliant"
    row["slot"]["schedule"] = "arbitrary-authored-label"
    truth = replay.reference(row, DIGESTS)
    assert truth["verdict"] == "violation" and truth["assessable"]


@pytest.mark.parametrize(
    "schedule_id",
    ["retry-inherited-foreign-mailbox", "retry-root-expired", "retry-reselect-legitimate"],
)
def test_frozen_transport_gaps_cannot_be_repaired_in_rehashed_observations(
    tmp_path, fake_p, schedule_id
):
    row = make_row(tmp_path, schedule_id)
    replay.validate_evidence(row, DIGESTS)
    if schedule_id == "retry-root-expired":
        repair = row["ledger"]["root_binding"]
    else:
        entry = row["ledger"]["target_entries"][0]
        repair = {
            "identifier": entry["occurrence"],
            "scope": entry["scope"],
            "kind": "load",
            "digest": entry["sha256"],
            "selection": row["ledger"]["selection"]["selection"],
            "revision": None,
            "phase": None,
            "load_count": None,
            "parent_scope": None,
            "parent_selection": None,
        }
    row["observations"]["before"]["records"] += (repair,)
    with pytest.raises(ValueError, match="actual frozen delivery"):
        replay.annotate(row, DIGESTS, tmp_path / "decisions")


@pytest.mark.parametrize(
    "field", ["selected_version", "post_path_sha256", "native_messages", "manifest", "extra_status"]
)
def test_full_frame_is_bound_to_actual_application_and_raw_capture(tmp_path, field):
    row = make_row(tmp_path)
    replay.validate_evidence(row, DIGESTS)
    row["path_frame"][field] = {"authored": "tampered"}
    with pytest.raises(ValueError, match="actual application snapshots"):
        replay.validate_evidence(row, DIGESTS)


def test_duplicate_delivery_remains_one_native_entry_and_exact_projection(tmp_path):
    row = make_row(tmp_path, "delivery-duplicate-only")
    replay.validate_evidence(row, DIGESTS)
    assert len(row["ledger"]["target_entries"]) == 1
    loads = [r for r in row["observations"]["after"]["records"] if r["kind"] == "load"]
    assert len(loads) == 2 and loads[0] == loads[1]
    assert replay.reference(row, DIGESTS)["verdict"] == "compliant"


def test_native_constructor_completion_is_not_sdk_load_completion(tmp_path, fake_p):
    class SDKFailure(FakeAdapter):
        def load(self, payload):
            super().load(payload)
            raise RuntimeError("synthetic SDK initialization failure")

    row = make_row(tmp_path, adapter=SDKFailure)
    retained = replay.annotate(row, DIGESTS, tmp_path / "decisions")
    assert retained["status"] == "technical_failure"
    assert retained["ledger"]["target_entries"][0]["completed"] is True
    assert retained["ledger"]["sdk_completed_loads"] == 0
    assert retained["reference"]["offered"] == [DIGESTS["A"]]
    assert not retained["reference"]["assessable"]
    assert all(
        c["verdict"] == "unknown"
        for outputs in retained["decisions"].values()
        for c in outputs.values()
    )
    retained["ledger"]["target_entries"][0]["raw_hex"] = ARTIFACTS["B"].hex()
    with pytest.raises(ValueError, match="native entry snapshot"):
        replay.reference(retained, DIGESTS)


def test_failed_native_entry_is_retained_without_claiming_completion(tmp_path, fake_p):
    class NativeFailure(FakeAdapter):
        def load(self, payload):
            self.before(payload)
            self.after(False)
            raise RuntimeError("synthetic native failure")

    row = replay.annotate(
        make_row(tmp_path, adapter=NativeFailure), DIGESTS, tmp_path / "decisions"
    )
    assert len(row["ledger"]["target_entries"]) == 1
    assert row["ledger"]["target_entries"][0]["completed"] is False
    assert row["reference"]["offered"] == [DIGESTS["A"]]


def test_false_block_is_recomputed_from_proposal_not_guard_reason(tmp_path):
    row = make_row(tmp_path, branch="prevention")
    ledger = row["ledger"]
    ledger["target_entries"] = []
    ledger["sdk_completed_loads"] = 0
    ledger["blocked"] = [
        {
            "raw_hex": ARTIFACTS["A"].hex(),
            "scope": ledger["scope"],
            "prior_entries": 0,
            "reason": "authored-wrong-buffer-label",
        }
    ]
    truth = replay.reference(row, DIGESTS)
    assert truth["verdict"] is None and truth["false_blocks"] == 1


def test_all_methods_receive_the_same_full_observation_at_each_cutoff(tmp_path, monkeypatch):
    row = make_row(tmp_path, "retry-reselect-legitimate")
    seen = []
    original_s, original_t = replay.receipt_checker, replay.completion_monitor

    def checker(original):
        def collect(obs):
            seen.append(obs)
            return original(obs)

        return collect

    def p(obs, path):
        seen.append(obs)
        return ProvenanceResult(original_s(obs), 0, None)

    monkeypatch.setattr(replay, "receipt_checker", checker(original_s))
    monkeypatch.setattr(replay, "completion_monitor", checker(original_t))
    monkeypatch.setattr(replay, "provenance_checker", p)
    replay.decisions(row, tmp_path / "decisions")
    assert len(seen) == 6
    assert seen[0] is seen[1] is seen[2]
    assert seen[3] is seen[4] is seen[5]
    assert len(seen[3].records) == len(seen[0].records) + 1


def test_fixed_coverage_denominator_includes_unexecuted_slots_and_replay_rejects_rehashed_gap(
    tmp_path, fake_p
):
    protocol = design.load_protocol(ROOT)
    slots = design.census(protocol)
    schedules = {s["id"]: s for s in protocol["schedules"]}
    versions = {b["id"]: b["version"] for b in protocol["backends"]}
    rows = []
    for index, slot in enumerate(slots):
        raw = (
            {"slot": slot, "status": "unexecuted"}
            if index == 0
            else run_slot(
                tmp_path / str(index),
                slot,
                schedules[slot["schedule"]],
                ARTIFACTS,
                versions[slot["backend"]],
                FakeAdapter,
            )
        )
        rows.append(replay.annotate(raw, DIGESTS, tmp_path / "p" / str(index)))
    sealed = {backend: DIGESTS for backend in versions}
    summary = replay.verify_rows(rows, slots, sealed)
    assert summary["planned_slots"] == 48
    assert summary["comparisons"]["after"]["S"]["planned_denominator"] == 22
    assert summary["comparisons"]["after"]["S"]["reference_unavailable"] == 1
    tampered = deepcopy(rows)
    tampered[2]["observations"]["after"]["records"] = tampered[2]["observations"]["after"][
        "records"
    ][:-1]
    tampered[2]["row_sha256"] = document_digest(
        {k: v for k, v in tampered[2].items() if k != "row_sha256"}
    )
    with pytest.raises(ValueError, match="actual frozen delivery"):
        replay.verify_rows(tampered, slots, sealed)
    tampered = deepcopy(rows)
    tampered[2]["reference"]["assessable"] = 1  # JSON boolean/int equality must remain strict.
    tampered[2]["row_sha256"] = document_digest(
        {k: v for k, v in tampered[2].items() if k != "row_sha256"}
    )
    with pytest.raises(ValueError, match="reference changed"):
        replay.verify_rows(tampered, slots, sealed)


def test_canonical_frame_keeps_selectors_relationships_and_native_timestamps(tmp_path):
    row = make_row(tmp_path)
    row["path_frame"]["native_messages"]["stderr"] = "2026-10-04 native message"
    frame = replay.canonical_frame(row["path_frame"], row["ledger"]["scope"]["request"])
    assert frame["native_messages"]["stderr"] == "2026-10-04 native message"
    assert frame["manifest"] == row["path_frame"]["manifest"]
    assert frame["public_request_id"] == frame["public_root_id"] == "request-0"
    assert frame["requested_uri"] == "spool://request-0/model"


def test_scoped_grouping_only_renames_nonce_and_keeps_token_scope_and_all_facts(tmp_path):
    first = make_row(tmp_path / "first", "retry-reselect-legitimate")
    second = make_row(tmp_path / "second", "retry-reselect-legitimate")
    first_input = deepcopy(first["observations"]["after"])
    expected = replay.canonical_scoped(first_input)
    assert expected == replay.canonical_scoped(second["observations"]["after"])
    assert first_input == first["observations"]["after"]
    assert document_digest(expected["contract"]) == document_digest(first_input["contract"])
    assert len(expected["records"]) == len(first_input["records"])
    changed = deepcopy(second["observations"]["after"])
    changed["records"][-1]["load_count"] = 2
    assert expected != replay.canonical_scoped(changed)
    changed = deepcopy(second["observations"]["after"])
    changed["records"][0]["scope"]["attempt"] = 99
    assert expected != replay.canonical_scoped(changed)
    changed["records"][0]["scope"]["request"] = "foreign"
    with pytest.raises(ValueError, match="another request"):
        replay.canonical_scoped(changed)


def test_exact_path_groups_can_hide_truth_but_full_scoped_groups_keep_native_binding(tmp_path):
    stable = make_row(tmp_path / "stable", "handoff-pinned-stable")
    drift = make_row(tmp_path / "drift", "handoff-pinned-drift")
    for row in (stable, drift):
        row["reference"] = replay.reference(row, DIGESTS)
    assert replay.equality_counts([stable, drift], None) == {
        "exact_evidence_groups": 1,
        "opposite_status_groups": 1,
        "opposite_status_slots": 2,
    }
    for cutoff in replay.CUTOFFS:
        assert replay.equality_counts([stable, drift], cutoff) == {
            "exact_evidence_groups": 2,
            "opposite_status_groups": 0,
            "opposite_status_slots": 0,
        }


@pytest.mark.parametrize(
    "schedule_id, verdict",
    [
        ("native-single-entry", "compliant"),
        ("handoff-pinned-drift", "violation"),
        ("native-same-buffer-reentry", "violation"),
    ],
)
def test_real_signed_provenance_replays_synthetic_native_evidence(tmp_path, schedule_id, verdict):
    pytest.importorskip("in_toto.verifylib")
    row = make_row(tmp_path, schedule_id)
    result = replay.annotate(row, DIGESTS, tmp_path / "signed-original")
    assert result["reference"]["verdict"] == verdict
    for cutoff in replay.CUTOFFS:
        signed = result["decisions"][cutoff]["P"]
        assert signed["verdict"] == verdict
        assert signed["verifier_calls"] == 1
        assert signed["artifact_rules_passed"] is (verdict == "compliant")
    independently = replay.decisions(row, tmp_path / "signed-replay")
    assert document_digest(independently) == document_digest(result["decisions"])
    assert len(list((tmp_path / "signed-replay").rglob("*.link"))) == 4
