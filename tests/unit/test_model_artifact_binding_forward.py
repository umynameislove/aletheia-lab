"""Synthetic end-to-end, endpoint and tamper tests for the forward M4 cell."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aletheia_lab.benchmark.p2 import model_artifact_binding_forward as cell
from aletheia_lab.benchmark.p2 import model_artifact_binding_forward_verify as verifier
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import PreprocessorState
from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    DevelopmentSource,
    ModelArtifactBindingError,
)
from aletheia_lab.content_hashing import file_sha256

ROOT = Path(__file__).resolve().parents[2]


def synthetic_source() -> DevelopmentSource:
    rng = np.random.default_rng(1307)
    matrix = rng.normal(size=(480, 3))
    labels = (matrix[:, 0] + 0.3 * matrix[:, 1] + rng.normal(scale=0.8, size=480) > 0).astype(int)
    columns = ("a", "b", "c")
    preprocessor = PreprocessorState(
        dataset_id="synthetic",
        categorical_columns=(),
        numeric_columns=columns,
        category_vocabulary={},
        numeric_means=(0.0, 0.0, 0.0),
        numeric_scales=(1.0, 1.0, 1.0),
        output_columns=columns,
        output_columns_sha256=canonical_sha256(
            {"schema_version": "p2-v3-preprocessor/1", "columns": columns}
        ),
    )
    return DevelopmentSource(
        archive_sha256="a" * 64,
        split_sha256="b" * 64,
        membership_sha256="c" * 64,
        preprocessor=preprocessor,
        train=matrix[:320],
        development=matrix[320:],
        train_ids=tuple(f"train-{i}" for i in range(320)),
        train_targets=tuple(labels[:320].tolist()),
        development_ids=tuple(f"dev-{i}" for i in range(160)),
        development_targets=tuple(labels[320:].tolist()),
        calibration_parameters=(1e-12, 200, 1e-9),
    )


@pytest.fixture
def options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    source = synthetic_source()
    monkeypatch.setattr(cell, "load_development_source", lambda **_: source)
    predecessor = tmp_path / "first-cell"
    predecessor.mkdir()
    (predecessor / "receipt.json").write_text(
        json.dumps({"status": "development_effect_insufficient"})
    )
    (predecessor / "artifact_A.joblib").write_bytes(b"historical; never deserialize")
    return {
        "root": ROOT,
        "archive": tmp_path / "synthetic.zip",
        "output": tmp_path / "new-cell",
        "predecessor": predecessor,
    }


def executed(options: dict[str, Path]) -> dict[str, Any]:
    prepared = cell.prepare_forward_cell(**options)
    return cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])


def rewrite(options: dict[str, Path], name: str, value: dict[str, Any]) -> None:
    path = options["output"] / name
    path.write_text(json.dumps(value))
    if name != "receipt.json":
        receipt_path = options["output"] / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["retained_sha256"][name] = file_sha256(path)
        receipt_path.write_text(json.dumps(receipt))


def test_prepare_is_prediction_blind_and_partition_is_disjoint(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cell, "fit_forward_models", lambda *_: pytest.fail("preparation fitted models")
    )
    prepared = cell.prepare_forward_cell(**options)
    assert set(p.name for p in options["output"].iterdir()) == {"plan.json"}
    plan = cell.json_file(options["output"] / "plan.json")
    assert plan["recipes"]["A"]["max_iter"] == 100
    assert plan["recipes"]["B"]["max_iter"] == 1
    assert plan["primary_endpoint"] == cell.SCORER_ID
    assert (
        prepared["partitions"]["calibration"]["count"]
        + prepared["partitions"]["measurement"]["count"]
        == 160
    )
    source = synthetic_source()
    calibration, measurement = cell.partition_indices(source)
    assert not set(calibration).intersection(measurement)
    assert set(calibration).union(measurement) == set(range(160))
    # Hash membership is stable under row reordering; measurement retains input order.
    permutation = tuple(reversed(range(160)))
    reordered = replace(
        source,
        development=source.development[list(permutation)],
        development_ids=tuple(source.development_ids[i] for i in permutation),
        development_targets=tuple(source.development_targets[i] for i in permutation),
    )
    reverse_cal, _ = cell.partition_indices(reordered)
    assert {source.development_ids[i] for i in calibration} == {
        reordered.development_ids[i] for i in reverse_cal
    }


def test_end_to_end_independent_replay_never_uses_runner_scoring_or_loads_pickle(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = cell.predecessor_bindings(ROOT, options["predecessor"])
    receipt = executed(options)
    source = synthetic_source()
    calibration, measurement = cell.partition_indices(source)
    assert receipt["train_count"] == 320
    assert receipt["calibration_count"] == len(calibration)
    assert receipt["measurement_count"] == len(measurement)
    assert receipt["scientific_admission"] is False and receipt["U4_authorized"] is False
    assert receipt["protected_predictions_or_metrics_computed"] is False
    assert receipt["provider_calls"] == 0
    assert all(receipt["checks"].values())
    scores = cell.json_file(options["output"] / "scores.json")
    paths = scores["paths"]
    assert paths["healthy"] == paths["sham"] == paths["corrected"] == paths["manifest_text_only"]
    assert paths["faulty"] == paths["legitimate_B"]
    assert paths["healthy"]["raw"] != paths["faulty"]["raw"]
    trace = {
        item["case"]: item
        for item in cell.json_file(options["output"] / "load-trace.json")["events"]
    }
    assert trace["faulty"]["declared_artifact_sha256"] != trace["faulty"]["actual_loaded_sha256"]
    assert (
        trace["legitimate_B"]["declared_artifact_sha256"]
        == trace["legitimate_B"]["actual_loaded_sha256"]
    )
    assert (
        trace["manifest_text_only"]["declared_artifact_sha256"]
        == trace["manifest_text_only"]["actual_loaded_sha256"]
    )
    assert (
        trace["manifest_text_only"]["reported_manifest_sha256"]
        != trace["manifest_text_only"]["actual_loaded_sha256"]
    )
    assert trace["faulty"]["actual_iterations"] == 1
    monkeypatch.setattr(cell, "fit_forward_models", lambda *_: pytest.fail("runner refit reused"))
    monkeypatch.setattr(cell, "score_metrics", lambda *_: pytest.fail("runner metric reused"))
    monkeypatch.setattr(
        cell, "development_decision", lambda **_: pytest.fail("runner decision reused")
    )
    monkeypatch.setattr(
        cell, "_trusted_load", lambda *_args, **_kwargs: pytest.fail("saved pickle loaded")
    )
    monkeypatch.setattr(cell.joblib, "load", lambda *_: pytest.fail("supplied pickle deserialized"))
    checked = verifier.verify_forward_cell(**options)
    assert checked["status"] == receipt["status"]
    assert checked["verification"] == "independent_refit_and_metric_replay"
    assert cell.predecessor_bindings(ROOT, options["predecessor"]) == before


@pytest.mark.parametrize(
    "mutation", ["recipe", "endpoint", "runtime", "partition", "code", "predecessor"]
)
def test_drift_fails_before_fit_or_lease(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    prepared = cell.prepare_forward_cell(**options)
    plan_path = options["output"] / "plan.json"
    plan = cell.json_file(plan_path)
    if mutation == "recipe":
        plan["recipes"]["B"]["max_iter"] = 25
    elif mutation == "endpoint":
        plan["primary_endpoint"] = "choose-best-sign"
    elif mutation == "runtime":
        plan["runtime"]["thread_limit"] = 2
    elif mutation == "partition":
        plan["partitions"]["seed"] += 1
    elif mutation == "code":
        plan["code_sha256"][cell.CODE_PATHS[0]] = "0" * 64
    else:
        (options["predecessor"] / "artifact_A.joblib").write_bytes(b"changed")
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr(cell, "fit_forward_models", lambda *_: pytest.fail("drifting plan fitted"))
    with pytest.raises(ModelArtifactBindingError, match="drift"):
        cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])
    assert not (options["output"] / "lease.json").exists()


def test_wrong_confirmation_and_repeat_cannot_overwrite(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = cell.prepare_forward_cell(**options)
    with pytest.raises(ModelArtifactBindingError, match="confirmation"):
        cell.execute_forward_cell(**options, confirm_plan_sha256="0" * 64)
    assert not (options["output"] / "lease.json").exists()
    with pytest.raises(ModelArtifactBindingError, match="creation state"):
        cell.prepare_forward_cell(**options)
    receipt = cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])
    before = file_sha256(options["output"] / "receipt.json")
    monkeypatch.setattr(
        cell, "fit_forward_models", lambda *_: pytest.fail("existing run fitted again")
    )
    with pytest.raises(ModelArtifactBindingError, match="already begun"):
        cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])
    assert file_sha256(options["output"] / "receipt.json") == before
    assert receipt["registered_attempt"] is False


@pytest.mark.parametrize(
    "filename,mutation",
    [
        ("receipt.json", "status"),
        ("receipt.json", "admission"),
        ("receipt.json", "delta"),
        ("scores.json", "raw"),
        ("scores.json", "metric"),
        ("scores.json", "rows"),
        ("load-trace.json", "loaded"),
        ("load-trace.json", "features"),
        ("manifest.json", "recipe"),
        ("manifest.json", "artifact"),
        ("rivals.json", "raw"),
        ("rivals.json", "shortcut"),
        ("lease.json", "plan"),
    ],
)
def test_rehashed_tampering_still_fails_independent_replay(
    options: dict[str, Path], filename: str, mutation: str
) -> None:
    executed(options)
    value = cell.json_file(options["output"] / filename)
    if filename == "receipt.json":
        if mutation == "status":
            value["status"] = "scientifically_admitted"
        elif mutation == "admission":
            value["scientific_admission"] = True
        else:
            value["raw_reference_prior_loss_delta"] += 0.02
    elif filename == "scores.json":
        if mutation == "raw":
            value["paths"]["sham"]["raw"][0] = [0.5, 0.5]
        elif mutation == "metric":
            value["paths"]["faulty"]["raw_metrics"]["reference_prior_log_loss"] += 0.02
        else:
            value["row_ids"].reverse()
    elif filename == "load-trace.json":
        if mutation == "loaded":
            value["events"][1]["actual_loaded_sha256"] = value["events"][1][
                "declared_artifact_sha256"
            ]
        else:
            value["events"][1]["features_sha256"] = "0" * 64
    elif filename == "manifest.json":
        if mutation == "recipe":
            value["recipes"]["B"]["max_iter"] = 2
        else:
            value["artifacts"]["B"] = "0" * 64
    elif filename == "rivals.json":
        if mutation == "raw":
            value["two_row_target_swap"]["pre_adapter_scores_sha256"] = "0" * 64
        else:
            value["matched_model_visible_inputs_proven"] = True
    else:
        value["plan_sha256"] = "0" * 64
    rewrite(options, filename, value)
    with pytest.raises(ModelArtifactBindingError):
        verifier.verify_forward_cell(**options)


def test_changed_artifact_cannot_be_deserialized_during_verification(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    executed(options)
    model_path = options["output"] / "artifact_B.joblib"
    model_path.write_bytes(b"untrusted pickle replaced the actual artifact")
    monkeypatch.setattr(cell.joblib, "load", lambda *_: pytest.fail("untrusted pickle loaded"))
    with pytest.raises(ModelArtifactBindingError, match="artifact changed"):
        verifier.verify_forward_cell(**options)


def test_fit_failure_is_retained_without_success_or_replay(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = cell.prepare_forward_cell(**options)

    def fail(_: Any) -> None:
        raise ModelArtifactBindingError("synthetic fit failure")

    monkeypatch.setattr(cell, "fit_forward_models", fail)
    with pytest.raises(ModelArtifactBindingError, match="fit failure"):
        cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])
    assert cell.json_file(options["output"] / "failure.json")["scientific_admission"] is False
    assert not (options["output"] / "receipt.json").exists()
    with pytest.raises(ModelArtifactBindingError, match="already begun"):
        cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])


@pytest.mark.parametrize(
    "mutation", ["overlap", "duplicate", "single-class", "small-class", "matrix", "nonfinite"]
)
def test_invalid_partitions_fail_before_fitting(mutation: str) -> None:
    source = synthetic_source()
    if mutation == "overlap":
        source = replace(source, development_ids=(source.train_ids[0], *source.development_ids[1:]))
    elif mutation == "duplicate":
        source = replace(
            source, development_ids=(source.development_ids[1], *source.development_ids[1:])
        )
    elif mutation == "single-class":
        source = replace(source, development_targets=(1,) * 160)
    elif mutation == "small-class":
        source = replace(source, development_targets=(0,) * 159 + (1,))
    elif mutation == "matrix":
        source = replace(source, development=source.development[:, :2])
    else:
        matrix = source.development.copy()
        matrix[0, 0] = np.nan
        source = replace(source, development=matrix)
    with pytest.raises(ModelArtifactBindingError):
        cell.partition_indices(source)


def test_output_repository_symlink_and_historical_content_are_guarded(
    options: dict[str, Path], tmp_path: Path
) -> None:
    inside = {**options, "output": ROOT / "must-not-publish-private"}
    with pytest.raises(ModelArtifactBindingError, match="outside"):
        cell.prepare_forward_cell(**inside)
    alias = tmp_path / "alias"
    alias.symlink_to(options["predecessor"], target_is_directory=True)
    with pytest.raises(ModelArtifactBindingError, match="symlink"):
        cell.predecessor_bindings(ROOT, alias)
    (options["predecessor"] / "receipt.json").write_text(json.dumps({"status": "pass"}))
    with pytest.raises(ModelArtifactBindingError, match="historical"):
        cell.prepare_forward_cell(**options)


def test_primary_decision_is_not_selected_from_calibrated_or_empirical_sign() -> None:
    a = {
        "raw": [[0.8, 0.2]],
        "calibrated_positive": [0.2],
        "raw_metrics": {"reference_prior_log_loss": 0.2, "empirical_log_loss": 0.2},
        "calibrated_metrics": {"reference_prior_log_loss": 0.2},
    }
    b = {
        "raw": [[0.7, 0.3]],
        "calibrated_positive": [0.3],
        "raw_metrics": {"reference_prior_log_loss": 0.205, "empirical_log_loss": 0.6},
        "calibrated_metrics": {"reference_prior_log_loss": 0.8},
    }
    assert (
        cell.development_decision(score_a=a, score_b=b, prior_loss=0.69)["status"]
        == "development_effect_insufficient"
    )
    b["raw_metrics"]["reference_prior_log_loss"] = 0.25
    b["calibrated_metrics"]["reference_prior_log_loss"] = 0.1
    assert (
        cell.development_decision(score_a=a, score_b=b, prior_loss=0.69)["status"]
        == "development_positive_control_observed"
    )
    assert (
        cell.development_decision(score_a=a, score_b=b, prior_loss=0.1)["status"]
        == "development_effect_insufficient"
    )
    b = copy.deepcopy(a)
    assert (
        cell.development_decision(score_a=a, score_b=b, prior_loss=0.69)["status"]
        == "development_effect_insufficient"
    )


@pytest.mark.parametrize("probabilities", [[0.0, 0.2], [1.0, 0.2], [float("nan"), 0.2]])
def test_independent_raw_scorer_has_no_hidden_clipping(probabilities: list[float]) -> None:
    with pytest.raises(ModelArtifactBindingError):
        verifier.independent_losses([0, 1], probabilities)


def test_balanced_loss_is_not_empirical_loss_on_imbalanced_labels() -> None:
    metrics = verifier.independent_losses([0, 0, 0, 1], [0.1, 0.2, 0.3, 0.3])
    assert metrics["reference_prior_log_loss"] != metrics["empirical_log_loss"]
    runtime = cell.score_metrics([0, 0, 0, 1], [0.1, 0.2, 0.3, 0.3])
    assert runtime == pytest.approx(metrics, abs=1e-12)


def test_cli_prepare_execute_verify_and_invalid_confirmation_are_local_only(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location(
        "artifact_binding_cli", ROOT / "scripts/model_artifact_binding_development.py"
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    arguments = [
        "--root",
        str(ROOT),
        "--archive",
        str(options["archive"]),
        "--output",
        str(options["output"]),
        "--predecessor-output",
        str(options["predecessor"]),
    ]
    monkeypatch.setattr(sys, "argv", ["artifact-control", "prepare-forward", *arguments])
    assert cli.main() == 0
    prepared = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        sys,
        "argv",
        ["artifact-control", "execute-forward", *arguments, "--confirm-plan-sha256", "0" * 64],
    )
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed_closed"
    assert not (options["output"] / "lease.json").exists()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "artifact-control",
            "execute-forward",
            *arguments,
            "--confirm-plan-sha256",
            prepared["plan_sha256"],
        ],
    )
    assert cli.main() == 0
    executed_result = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "argv", ["artifact-control", "verify-forward", *arguments])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == executed_result
    assert executed_result["provider_calls"] == 0


@pytest.mark.parametrize("action", ["prepare-forward", "execute-forward"])
def test_cli_missing_forward_arguments_never_create_output(
    options: dict[str, Path], monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    spec = importlib.util.spec_from_file_location(
        "artifact_binding_missing_cli", ROOT / "scripts/model_artifact_binding_development.py"
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "artifact-control",
            action,
            "--archive",
            str(options["archive"]),
            "--output",
            str(options["output"]),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert not options["output"].exists()
