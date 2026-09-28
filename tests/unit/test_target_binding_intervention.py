"""Target-only mutation, independent replay, correction, and null controls."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.score_mapping_intervention import capture_evaluator_score_source
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    SourceArtifactPaths,
    capture_independent_score_witness,
)
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    TargetBindingError,
    TargetBindingSource,
    apply_paired_target_binding_fault,
    apply_target_binding_fault,
    restore_target_bindings,
)
from aletheia_lab.benchmark.p2.target_binding_verification import (
    verify_paired_target_binding,
    verify_target_binding,
)
from aletheia_lab.content_hashing import file_sha256


@dataclass
class FixedModel:
    classes_: NDArray[np.int64]
    scores: NDArray[np.float64]

    def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.scores.copy()


@pytest.fixture
def case(tmp_path: Path) -> dict:
    rows = tuple((f"r{i}", i % 2) for i in range(200))
    ids = tuple(row_id for row_id, _ in rows)
    matrix = np.arange(200, dtype=np.float64).reshape(-1, 1)
    model = FixedModel(
        np.array([0, 1]), np.array([(0.9, 0.1) if y == 0 else (0.1, 0.9) for _, y in rows])
    )
    calibration = CalibrationResult(
        intercept=0.0,
        slope=1.0,
        iterations=0,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=200,
    )
    artifacts = SourceArtifactPaths(
        *(tmp_path / name for name in ("data", "split", "preprocessor", "model"))
    )
    for path in (
        artifacts.dataset,
        artifacts.split,
        artifacts.preprocessor,
        artifacts.fitted_model,
    ):
        path.write_bytes(path.name.encode())
    witness = capture_independent_score_witness(
        dataset_id="synthetic",
        record_ids=ids,
        target_rows=rows,
        evaluation_matrix=matrix,
        model=model,
        calibration=calibration,
        artifacts=artifacts,
    )
    scores = capture_evaluator_score_source(
        dataset_id="synthetic",
        record_ids=ids,
        evaluation_matrix=matrix,
        model=model,
        calibration=calibration,
    )
    return {
        "target_source": TargetBindingSource("synthetic", rows),
        "witness": witness,
        "score_source": scores,
        "reference_model": model,
        "evaluation_matrix": matrix,
        "reference_calibration": calibration,
        "artifacts": artifacts,
    }


@pytest.mark.parametrize("dose", [0, 1, 2, 4])
def test_target_only_fault_replays_and_source_join_restores(case: dict, dose: int) -> None:
    source = case["target_source"]
    fault = apply_target_binding_fault(source, selected_shard_count=dose)
    assert fault == apply_target_binding_fault(source, selected_shard_count=dose)
    corrected = restore_target_bindings(source, fault)
    result = verify_target_binding(**case, intervention=fault, corrected_target_rows=corrected)
    assert result.corrected_log_loss == result.healthy_log_loss
    assert result.class_counts_preserved
    assert result.source_target_sha256 == result.corrected_target_sha256
    assert result.score_source_sha256 == case["witness"].calibrated_scores_sha256
    assert sorted(fault.donor_record_ids) == sorted(source.record_ids)
    assert result.changed_donor_count >= result.changed_target_count
    assert source.target_rows == case["witness"].target_rows
    if dose == 0:
        assert result.faulty_log_loss == result.healthy_log_loss
        assert result.changed_donor_count == result.changed_target_count == 0
    else:
        assert result.changed_target_count > 0
        assert result.changed_donor_count > result.changed_target_count
        assert result.faulty_log_loss > result.healthy_log_loss


def test_selector_does_not_depend_on_targets(case: dict) -> None:
    source = case["target_source"]
    flipped = TargetBindingSource(
        source.dataset_id, tuple((row_id, 1 - y) for row_id, y in source.target_rows)
    )
    a = apply_target_binding_fault(source, selected_shard_count=4)
    b = apply_target_binding_fault(flipped, selected_shard_count=4)
    assert a.row_shards == b.row_shards
    assert a.donor_record_ids == b.donor_record_ids
    assert a.selected_record_ids == b.selected_record_ids


@pytest.mark.parametrize(
    "change", ["donors", "rows", "changed", "shards", "correction", "score_source"]
)
def test_independent_checker_rejects_tampering(case: dict, change: str) -> None:
    source = case["target_source"]
    fault = apply_target_binding_fault(source, selected_shard_count=4)
    corrected = source.target_rows
    kwargs = dict(case)
    if change == "donors":
        fault = replace(fault, donor_record_ids=source.record_ids)
    elif change == "rows":
        fault = replace(fault, observed_target_rows=source.target_rows)
    elif change == "changed":
        fault = replace(fault, changed_record_ids=())
    elif change == "shards":
        fault = replace(fault, row_shards=tuple(0 for _ in source.record_ids))
    elif change == "correction":
        corrected = fault.observed_target_rows
    else:
        original = kwargs["score_source"]
        kwargs["score_source"] = replace(original, dataset_id="another-source")
    with pytest.raises(TargetBindingError):
        verify_target_binding(**kwargs, intervention=fault, corrected_target_rows=corrected)


def test_changed_upstream_artifact_is_rejected(case: dict) -> None:
    fault = apply_target_binding_fault(case["target_source"], selected_shard_count=4)
    case["artifacts"].dataset.write_bytes(b"changed source")
    with pytest.raises(TargetBindingError):
        verify_target_binding(
            **case, intervention=fault, corrected_target_rows=case["target_source"].target_rows
        )


@pytest.mark.parametrize("dose", [True, -1, 3, 20, 1.0])
def test_unknown_or_coerced_dose_is_rejected(case: dict, dose: object) -> None:
    with pytest.raises(TargetBindingError):
        apply_target_binding_fault(case["target_source"], selected_shard_count=dose)


def test_same_label_donors_are_not_filtered_to_manufacture_an_effect() -> None:
    # One selected shard contains only zeros; another nonselected row supplies class one.
    from aletheia_lab.benchmark.p2.target_binding_intervention import TARGET_SELECTOR_SEED
    from aletheia_lab.evidence.schema import sha256_text

    ids = tuple(f"r{i}" for i in range(200))
    selected = {
        i
        for i in ids
        if int(sha256_text(f"{TARGET_SELECTOR_SEED}\x00synthetic\x00{i}"), 16) % 20 < 1
    }
    rows = tuple((i, 0 if i in selected else 1) for i in ids)
    fault = apply_target_binding_fault(
        TargetBindingSource("synthetic", rows), selected_shard_count=1
    )
    assert fault.selected_record_ids
    assert fault.changed_record_ids == ()
    assert fault.observed_target_rows == rows
    assert fault.donor_record_ids != ids


def test_target_fault_can_improve_loss_so_direction_is_not_an_admission_rule(case: dict) -> None:
    source = case["target_source"]
    model = case["reference_model"]
    model.scores = model.scores[:, ::-1].copy()
    witness = capture_independent_score_witness(
        dataset_id=source.dataset_id,
        record_ids=source.record_ids,
        target_rows=source.target_rows,
        evaluation_matrix=case["evaluation_matrix"],
        model=model,
        calibration=case["reference_calibration"],
        artifacts=case["artifacts"],
    )
    scores = capture_evaluator_score_source(
        dataset_id=source.dataset_id,
        record_ids=source.record_ids,
        evaluation_matrix=case["evaluation_matrix"],
        model=model,
        calibration=case["reference_calibration"],
    )
    fault = apply_target_binding_fault(source, selected_shard_count=4)
    result = verify_target_binding(
        **{**case, "witness": witness, "score_source": scores},
        intervention=fault,
        corrected_target_rows=restore_target_bindings(source, fault),
    )
    assert result.faulty_log_loss < result.healthy_log_loss
    assert result.corrected_log_loss == result.healthy_log_loss


def test_adversarial_pair_has_independent_donor_lineage(case: dict) -> None:
    source = case["target_source"]
    fault = apply_paired_target_binding_fault(source, swapped_pairs=(("r0", "r1"),))
    assert fault.donor_record_ids[:2] == ("r1", "r0")
    assert fault.observed_target_rows[:2] == (("r0", 1), ("r1", 0))
    assert source.target_rows[:2] == (("r0", 0), ("r1", 1))
    checked = verify_paired_target_binding(
        **case, intervention=fault, corrected_target_rows=restore_target_bindings(source, fault)
    )
    assert checked.changed_target_count == checked.changed_donor_count == 2
    assert checked.class_counts_preserved
    assert checked.corrected_log_loss == checked.healthy_log_loss
    assert checked.faulty_log_loss > checked.healthy_log_loss


def test_empty_pair_ledger_is_a_sham(case: dict) -> None:
    source = case["target_source"]
    fault = apply_paired_target_binding_fault(source, swapped_pairs=())
    checked = verify_paired_target_binding(
        **case, intervention=fault, corrected_target_rows=restore_target_bindings(source, fault)
    )
    assert checked.changed_donor_count == checked.changed_target_count == 0
    assert checked.healthy_log_loss == checked.faulty_log_loss == checked.corrected_log_loss


def test_pair_verifier_does_not_call_mutator_or_correction(
    case: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aletheia_lab.benchmark.p2 import target_binding_intervention as injector

    source = case["target_source"]
    fault = apply_paired_target_binding_fault(source, swapped_pairs=(("r0", "r1"),))
    corrected = restore_target_bindings(source, fault)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("independent verifier called a mutation/correction helper")

    monkeypatch.setattr(injector, "apply_paired_target_binding_fault", forbidden)
    monkeypatch.setattr(injector, "restore_target_bindings", forbidden)
    checked = verify_paired_target_binding(
        **case, intervention=fault, corrected_target_rows=corrected
    )
    assert checked.corrected_log_loss == checked.healthy_log_loss


@pytest.mark.parametrize("change", ["donor", "target", "pairs", "correction", "score"])
def test_adversarial_pair_checker_fails_closed(case: dict, change: str) -> None:
    source = case["target_source"]
    fault = apply_paired_target_binding_fault(source, swapped_pairs=(("r0", "r1"),))
    corrected = source.target_rows
    kwargs = dict(case)
    if change == "donor":
        fault = replace(fault, donor_record_ids=source.record_ids)
    elif change == "target":
        fault = replace(fault, observed_target_rows=source.target_rows)
    elif change == "pairs":
        fault = replace(fault, swapped_pairs=(("r0", "r3"),))
    elif change == "correction":
        corrected = fault.observed_target_rows
    else:
        kwargs["score_source"] = replace(kwargs["score_source"], dataset_id="tampered")
    with pytest.raises(TargetBindingError):
        verify_paired_target_binding(**kwargs, intervention=fault, corrected_target_rows=corrected)


@pytest.mark.parametrize("pairs", [(("r0", "r2"),), (("r0", "r1"), ("r0", "r3")), (("x", "r1"),)])
def test_adversarial_pair_rejects_invalid_source_binding(case: dict, pairs: object) -> None:
    with pytest.raises(TargetBindingError):
        apply_paired_target_binding_fault(case["target_source"], swapped_pairs=pairs)


@pytest.mark.parametrize(
    "change", ["byte_hash", "duplicate_dose", "missing_dose", "duplicate_cell"]
)
def test_matched_development_census_is_hash_bound(tmp_path: Path, change: str) -> None:
    from aletheia_lab.benchmark.p2.target_binding_development import _matched_cells

    root = tmp_path / "repo"
    root.mkdir()
    prior = tmp_path / "private-prior"
    prior.mkdir()
    cell = {
        "dataset_id": "synthetic",
        "model_kind": "logistic_regression",
        "measurements": [{"selected_shards": dose} for dose in (0, 1, 2, 4)],
    }
    summary = {"schema_version": "score-mapping-development-symptom-study/v1", "cells": [cell]}
    path = prior / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    cells, digest = _matched_cells(
        root=root, output=tmp_path / "output", prior=prior, expected_sha256=file_sha256(path)
    )
    assert set(cells) == {("synthetic", "logistic_regression")}
    if change == "byte_hash":
        path.write_text(json.dumps(summary) + " ", encoding="utf-8")
    else:
        if change == "duplicate_dose":
            cell["measurements"].append({"selected_shards": 1})
        elif change == "missing_dose":
            cell["measurements"].pop()
        else:
            summary["cells"].append(cell)
        path.write_text(json.dumps(summary), encoding="utf-8")
        digest = file_sha256(path)
    with pytest.raises(ValueError):
        _matched_cells(root=root, output=tmp_path / "output", prior=prior, expected_sha256=digest)


@pytest.mark.parametrize("paired", [False, True])
def test_offline_runner_retains_sham_correction_and_recomputed_views(
    case: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paired: bool
) -> None:
    from aletheia_lab.benchmark.p2 import target_binding_development as development
    from aletheia_lab.benchmark.p2.canonical import canonical_sha256
    from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
        reference_prior_standardized_log_loss,
    )
    from aletheia_lab.benchmark.p2.score_mapping_development_symptoms import DevelopmentCellReplay
    from aletheia_lab.benchmark.p2.score_mapping_intervention import apply_evaluator_mapping_fault

    source = case["target_source"]
    probabilities = tuple(row[1] for row in case["witness"].calibrated_score_rows)
    replay = DevelopmentCellReplay(
        case["witness"],
        case["score_source"],
        case["reference_model"],
        case["reference_calibration"],
        case["artifacts"],
        case["evaluation_matrix"] + 5.0,
        case["evaluation_matrix"],
        source.record_ids,
        tuple(y for _, y in source.target_rows),
        source.target_rows,
        probabilities,
    )
    root, prior, matched = (tmp_path / name for name in ("root", "prior", "matched"))
    for path in (root, prior, matched):
        path.mkdir()
    protocol = SimpleNamespace(dataset_splits=(object(),), canonical_sha256=lambda: "protocol")
    dataset = SimpleNamespace(dataset_id="synthetic", archive=SimpleNamespace(file_name="local"))
    monkeypatch.setattr(development, "MODEL_KINDS", ("logistic_regression",))
    monkeypatch.setattr(development, "load_v3_confirmatory_protocol", lambda path: protocol)
    monkeypatch.setattr(
        development,
        "verify_v3_protocol_artifacts",
        lambda *a, **kw: (None, SimpleNamespace(datasets=(dataset,)), None),
    )
    monkeypatch.setattr(
        development, "load_v3_dataset_snapshot_for_registration", lambda **kw: (None, object())
    )
    monkeypatch.setattr(development, "reconstruct_runtime_split", lambda **kw: object())
    monkeypatch.setattr(development, "load_development_cell", lambda **kw: replay)
    monkeypatch.setattr(
        development,
        "_checked_prior_summary",
        lambda path: {
            "protocol_sha256": "protocol",
            "cells": [{"dataset_id": "synthetic", "model_kind": "logistic_regression"}],
        },
    )
    digest = None
    if paired:
        fault = apply_paired_target_binding_fault(source, swapped_pairs=(("r0", "r1"),))
        targets = tuple(y for _, y in fault.observed_target_rows)
        measurements = []
        for dose in (0, 1, 2, 4):
            mapping = apply_evaluator_mapping_fault(case["score_source"], selected_shard_count=dose)
            measurements.append(
                {
                    "selected_shards": dose,
                    "status": "resolution_matched_pair",
                    "swapped_pairs": fault.swapped_pairs,
                    "rival_log_loss": reference_prior_standardized_log_loss(
                        true_labels=targets, probabilities=probabilities
                    ),
                    "rival_target_binding_sha256": canonical_sha256(
                        {"target_rows": fault.observed_target_rows}
                    ),
                    "mapping_log_loss": reference_prior_standardized_log_loss(
                        true_labels=replay.dev_targets, probabilities=mapping.positive_probabilities
                    ),
                }
            )
        path = matched / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "score-mapping-development-symptom-study/v1",
                    "cells": [
                        {
                            "dataset_id": "synthetic",
                            "model_kind": "logistic_regression",
                            "measurements": measurements,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        digest = file_sha256(path)
    output = tmp_path / "output"
    report = development.run_target_binding_development(
        root=root,
        prior=prior,
        output=output,
        matched_prior=matched if paired else None,
        expected_matched_sha256=digest,
    )
    assert report["provider_calls"] == 0
    assert report["registered_attempt"] is False
    assert report["independently_admitted"] is False
    measurements = report["cells"][0]["measurements"]
    assert len(measurements) == 4
    assert measurements[0]["effect_direction"] == "flat"
    assert all(row["healthy_log_loss"] == row["corrected_log_loss"] for row in measurements)
    if paired:
        # Predecessor status cannot manufacture byte equality: the current views
        # are recomputed, and these deliberately unmatched synthetic pairs fail.
        assert all(
            not row["adversarial_paired_binding"]["payload_equality"]["6"]["missing_key"]
            for row in measurements[1:]
        )
        assert all(
            row["adversarial_paired_binding"]["correction_exact"] for row in measurements[1:]
        )
    assert json.loads((output / "summary.json").read_text()) == report
    with pytest.raises(ValueError):
        development.run_target_binding_development(root=root, prior=prior, output=output)
