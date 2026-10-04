"""Synthetic evidence controls, independent of private validation artifacts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from itertools import combinations
from pathlib import Path

import pytest

from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_evidence_analysis import (
    ablate,
    analyze_rows,
    closeout,
    record_group,
    sufficient_records,
)
from aletheia_lab.evaluation.model_load_retention_development import make_episodes

A, B = "a" * 64, "b" * 64


def view(root_digest=A, *, duplicate_parent=False):
    root_scope, scope = Scope("request", 0), Scope("request", 1)
    root = Record(
        "root", root_scope, "selection", root_digest, "root-token", 1, "pin_at_acceptance"
    )
    child = Record(
        "child",
        scope,
        "selection",
        A,
        "child-token",
        1,
        "inherit",
        parent_scope=root_scope,
        parent_selection="root-token",
    )
    records = (
        root,
        child,
        Record("load", scope, "load", A, "child-token"),
        Record("close", scope, "closure", load_count=1),
        Record("unused-parent-load", root_scope, "load", A, "root-token"),
        Record("unused-parent-close", root_scope, "closure", load_count=1),
    )
    if duplicate_parent:
        records += (replace(root, digest=B),)
    return Observation(LoadContract("pin_at_acceptance", (A, B)), scope, records)


def test_parent_necessity_is_an_indistinguishable_pair_not_an_observed_ablation_gain():
    healthy, wrong = view(A), view(B)
    assert receipt_checker(healthy).verdict == completion_monitor(healthy).verdict == "compliant"
    assert receipt_checker(wrong).verdict == completion_monitor(wrong).verdict == "violation"
    healthy_missing = ablate(healthy, ("parent", "other"))
    wrong_missing = ablate(wrong, ("parent", "other"))
    assert healthy_missing == wrong_missing
    assert receipt_checker(healthy_missing).verdict == "unknown"


def test_static_sufficiency_keeps_all_conflicting_parent_variants_and_target_records():
    obs = view(duplicate_parent=True)
    projected = replace(obs, records=sufficient_records(obs))
    assert len(projected.records) == len(obs.records) - 2
    assert receipt_checker(projected).reason == "conflicting_parent_receipts"
    assert completion_monitor(projected).verdict == "conflict"
    target_conflict = replace(obs, records=obs.records + (replace(obs.records[1], digest=B),))
    assert (
        receipt_checker(
            replace(target_conflict, records=sufficient_records(target_conflict))
        ).verdict
        == "conflict"
    )


@pytest.mark.parametrize("root_digest", [A, B])
@pytest.mark.parametrize("retry", ["inherit", "reselect"])
def test_static_projection_preserves_truthful_subsets_order_and_duplicate_delivery(
    root_digest, retry
):
    original = view(root_digest)
    records = original.records
    if retry == "reselect":
        records = tuple(
            replace(record, phase="reselect", parent_scope=None, parent_selection=None)
            if record.scope == original.scope and record.kind == "selection"
            else record
            for record in records
        )
    original = replace(original, contract=replace(original.contract, retry=retry), records=records)
    for size in range(len(records) + 1):
        for subset in combinations(records, size):
            for delivered in (subset, tuple(reversed(subset)), subset + subset):
                obs = replace(original, records=delivered)
                selected = replace(obs, records=sufficient_records(obs))
                for checker in (receipt_checker, completion_monitor):
                    first, second = checker(obs), checker(selected)
                    assert (first.verdict, first.reason, first.eligibility) == (
                        second.verdict,
                        second.reason,
                        second.eligibility,
                    )


def test_exposed_analysis_never_restores_lost_records_or_mutates_rows():
    rows = []
    for episode in make_episodes():
        records = tuple(
            d.record for d in episode.deliveries if d.record.scope.request == episode.target.request
        )
        before = tuple(
            d.record
            for d in episode.deliveries
            if not d.delayed and d.record.scope.request == episode.target.request
        )
        obs = Observation(episode.contract, episode.target, records)
        rows.append(
            {
                "status": "completed",
                "slot": {
                    "branch": "observation",
                    "planned_target_entries": len(episode.produced_digests),
                },
                "reference": episode.truth(),
                "observations": {
                    "before": asdict(replace(obs, records=before)),
                    "after": asdict(obs),
                },
            }
        )
    unchanged = deepcopy(rows)
    report = analyze_rows(rows)
    assert rows == unchanged
    assert report["cutoffs"]["after"]["unmodified"]["S"]["planned_denominator"] == 9
    assert report["cutoffs"]["after"]["unmodified"]["S"]["correct_identified"] == 8
    assert report["cutoffs"]["after"]["remove_each_group"]["parent"]["S"]["correct_identified"] < 8
    assert report["cutoffs"]["before"]["sufficiency"]["subset_count_per_view"] == 32
    assert report["remaining_gaps"] == {"missing_buffer_witness:reference_violation": 1}


def test_invalid_empty_or_group_input_fails_and_unexecuted_slots_remain_in_denominator():
    with pytest.raises(ValueError, match="empty"):
        analyze_rows([])
    with pytest.raises(ValueError, match="group"):
        ablate(view(), ("invented",))
    row = {
        "status": "unexecuted",
        "slot": {"branch": "observation", "planned_target_entries": 1},
        "reference": {"assessable": False, "verdict": "unknown"},
    }
    report = analyze_rows([row])
    count = report["cutoffs"]["after"]["unmodified"]["S"]
    assert count["planned_denominator"] == 1 and count["reference_assessable"] == 0
    assert report["remaining_gaps"] == {"unavailable_execution": 1}


def test_no_policy_and_unrelated_records_do_not_warrant_compliance():
    obs = view()
    assert receipt_checker(ablate(obs, (), remove_policy=True)).verdict == "unknown"
    assert record_group(Record("foreign", Scope("other", 1), "registry"), obs) == "other"


def test_closeout_verifies_authority_and_keeps_original_bytes(tmp_path, monkeypatch):
    from aletheia_lab.evaluation import model_load_validation_run as frozen

    root, study = tmp_path / "repo", tmp_path / "frozen"
    root.mkdir()
    study.mkdir()
    original = study / "results.json"
    original.write_bytes(b"immutable-original")
    observed = []
    monkeypatch.setattr(
        frozen,
        "verify",
        lambda *args: observed.append(args) or {"results_sha256": A, "seal_sha256": B},
    )
    rows = [
        {
            "status": "unexecuted",
            "slot": {"branch": "observation", "planned_target_entries": 1},
            "reference": {"assessable": False, "verdict": "unknown"},
        }
    ]
    monkeypatch.setattr(frozen, "read_signed", lambda *args: {"rows": rows})
    report = closeout(root, Path("plan"), study, tmp_path / "closeout.json")
    assert observed and report["validation_results_sha256"] == A
    assert report["native_loads_replayed"] == 0
    assert original.read_bytes() == b"immutable-original"
    assert (tmp_path / "closeout.json").exists()
    for output in (root / "private.json", study / "private.json"):
        with pytest.raises(ValueError, match="outside"):
            closeout(root, Path("plan"), study, output)


def test_closeout_detects_source_change_before_publishing(tmp_path, monkeypatch):
    from aletheia_lab.evaluation import model_load_validation_run as frozen

    root, study = tmp_path / "repo", tmp_path / "frozen"
    root.mkdir()
    study.mkdir()
    original = study / "results.json"
    original.write_bytes(b"initial")

    def invalid_verify(*args):
        original.write_bytes(b"changed")
        return {"results_sha256": A, "seal_sha256": B}

    monkeypatch.setattr(frozen, "verify", invalid_verify)
    rows = [
        {
            "status": "unexecuted",
            "slot": {"branch": "observation", "planned_target_entries": 1},
            "reference": {"assessable": False, "verdict": "unknown"},
        }
    ]
    monkeypatch.setattr(frozen, "read_signed", lambda *args: {"rows": rows})
    with pytest.raises(ValueError, match="changed"):
        closeout(root, Path("plan"), study, tmp_path / "closeout.json")
    assert not (tmp_path / "closeout.json").exists()
