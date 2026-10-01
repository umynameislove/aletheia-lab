"""Synthetic execution/replay only; no pinned source outcome is opened."""

from __future__ import annotations

import json
import math
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import joblib
import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler

from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_cells as cells
from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_plan as plans
from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_protocol as inventory
from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_study as study
from aletheia_lab.benchmark.p2 import model_artifact_binding_new_source_verify as replay
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    apply_logit_calibration,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import CASES
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ProspectiveBindingError,
    json_bytes,
    publish_json,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

REPO = Path(__file__).resolve().parents[2]


def _unexpected(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("this operation is forbidden")


def _archive(directory: Path, spec: Any, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(600, 2))
    labels = (rng.random(600) < 1 / (1 + np.exp(-2 * features[:, 0]))).astype(int)
    private_ids = spec["format"] == "private-id-target-numeric-features"
    tokens = ("B", "M") if private_ids else ("0", "1")
    rows = []
    for i, ((x, y), label) in enumerate(zip(features, labels, strict=True)):
        rows.append(
            f"{100000 + i},{tokens[label]},{x:.8f},{y:.8f}"
            if private_ids
            else f"{x:.8f},{y:.8f},{tokens[label]}"
        )
    # An opposite-target duplicate feature group cannot cross partitions.
    if private_ids:
        rows.append(f"999999,{tokens[1 - labels[0]]},{features[0][0]:.8f},{features[0][1]:.8f}")
    else:
        rows.append(f"{features[0][0]:.8f},{features[0][1]:.8f},{tokens[1 - labels[0]]}")
    payload = ("\n".join(rows) + "\n").encode()
    path = directory / spec["archive_filename"]
    with ZipFile(path, "w") as archive:
        archive.writestr(spec["member"], payload)
    return {
        **spec,
        "archive_bytes": path.stat().st_size,
        "archive_sha256": file_sha256(path),
        "member_bytes": len(payload),
        "member_sha256": content_sha256(payload),
        "row_count": len(rows),
        "feature_count": 2,
    }


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    root, directory = tmp_path / "checkout", tmp_path / "private"
    (directory / "sources").mkdir(parents=True)
    for folder in ("configs/benchmark", "configs/evaluation", "src/aletheia_lab", "scripts"):
        (root / folder).mkdir(parents=True)
    for name in ("pyproject.toml", plans.SCRIPT_PATH, "src/aletheia_lab/__init__.py"):
        (root / name).write_text("# synthetic fixture\n", encoding="utf-8")
    for name in plans.INPUT_CONTRACTS:
        (root / name).write_bytes((REPO / name).read_bytes())
    protocol = json.loads((REPO / inventory.PROTOCOL_PATH).read_bytes())
    protocol["sources"] = [
        _archive(directory / "sources", spec, seed)
        for spec, seed in zip(protocol["sources"], (301, 991), strict=True)
    ]
    (root / inventory.PROTOCOL_PATH).write_bytes(json_bytes(protocol))
    monkeypatch.setattr(
        inventory, "FROZEN_PROTOCOL_SHA256", file_sha256(root / inventory.PROTOCOL_PATH)
    )
    monkeypatch.setattr(plans, "validate_loaded_code_root", lambda root: None)
    monkeypatch.setattr(
        plans, "_git_binding", lambda root: {"source_commit": "a" * 40, "working_tree_clean": True}
    )
    publish_json(
        directory / "inventory.json",
        inventory.audit_m4_new_sources(root=root, sources=directory / "sources"),
    )
    sealed = plans.prepare_study(root, directory)
    return root, directory, sealed["plan_sha256"]


def _execute(prepared: tuple[Path, Path, str]) -> dict[str, Any]:
    root, directory, digest = prepared
    return study.execute_study(
        root, directory, confirm_plan_sha256=digest, authorize_final_execution=True
    )


def test_prediction_blind_preparation_is_read_only_and_group_safe(
    prepared: Any, monkeypatch: Any
) -> None:
    root, directory, digest = prepared
    before = {
        p.relative_to(directory).as_posix(): file_sha256(p)
        for p in directory.rglob("*")
        if p.is_file()
    }
    for cls, method in (
        (StandardScaler, "fit"),
        (HistGradientBoostingClassifier, "fit"),
        (HistGradientBoostingClassifier, "predict_proba"),
    ):
        monkeypatch.setattr(cls, method, _unexpected)
    monkeypatch.setattr(cells, "fit_logit_calibration", _unexpected)
    monkeypatch.setattr(socket, "create_connection", _unexpected)
    result = plans.preflight_study(root, directory)
    assert result["prospective_plan_sha256"] == digest
    assert (
        result["model_fitted"]
        is result["final_predictions_computed"]
        is result["execution_authorized"]
        is False
    )
    assert result["synthetic_reader_preflight"]["sdk_capture_count"] == 32
    assert plans.prepare_study(root, directory)["plan_sha256"] == digest
    assert before == {
        p.relative_to(directory).as_posix(): file_sha256(p)
        for p in directory.rglob("*")
        if p.is_file()
    }
    _, sources = plans.bound_plan(root, directory)
    for source in sources:
        assert source.group_ids[0] == source.group_ids[-1]
        assert source.features.shape[1] == 2  # Private ID is not a feature.
        groups = [{source.group_ids[i] for i in indices} for indices in source.partitions.values()]
        assert not groups[0] & groups[1] and not groups[0] & groups[2] and not groups[1] & groups[2]


def test_synthetic_census_replays_without_runner_or_supplied_pickle(
    prepared: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(socket, "create_connection", _unexpected)
    receipt = _execute(prepared)
    assert receipt["cell_status_counts"] == {"verified": 2}
    assert receipt["scientific_summary"]["completed_sdk_capture_count"] == 64
    assert receipt["scientific_summary"]["source_cluster_count"] == 2
    assert receipt["scientific_summary"]["g1_denominator"] == 2
    root, directory, digest = prepared
    for name in (
        "_fit_models",
        "source_matrices",
        "load_event",
        "score_bundle",
        "cell_summary",
        "other_locus_controls",
    ):
        monkeypatch.setattr(cells, name, _unexpected)
    monkeypatch.setattr(study, "run_cell", _unexpected)
    monkeypatch.setattr(joblib, "load", _unexpected)
    verified = study.verify_study(root, directory)
    assert verified["status"] == "new_source_verification_pass"
    assert verified["scientific_summary"] == receipt["scientific_summary"]
    assert verified["mechanism_admitted"] is False and verified["provider_calls"] == 0
    assert file_sha256(directory / plans.PLAN_FILE) == digest
    for cell_id in receipt["cell_census"]:
        cell = directory / "cells" / cell_id.split("/")[0]
        scores = replay.read_object(cell / "scores.json")
        assert scores["B_matches_A_first_stage"] is True
        paths = scores["paths"]
        assert (
            paths["healthy"] == paths["sham"] == paths["corrected"] == paths["manifest_text_only"]
        )
        assert paths["faulty"] == paths["legitimate_B"]
        assert all(event["adapter"] == "binary-classes-0-1/identity" for event in scores["events"])
        audit = replay.read_object(cell / "input-audit.json")
        assert audit["checks"]["legitimate_B_missing_key_equal"]
        assert all(
            not audit["pairwise"][name]["missing_key"]["equal"]
            for name in ("adapter_column_reversal", "two_row_target_swap")
        )
    with pytest.raises(ProspectiveBindingError, match="already began"):
        _execute(prepared)
    with pytest.raises(ProspectiveBindingError, match="already began"):
        plans.prepare_study(root, directory)


@pytest.mark.parametrize(
    "mutation",
    [
        "code",
        "runtime",
        "inventory",
        "archive",
        "membership",
        "dirty",
        "commit",
        "authority",
        "confirmation",
        "symlink",
    ],
)
def test_drift_and_authority_fail_before_lease(
    prepared: Any, monkeypatch: Any, mutation: str
) -> None:
    root, directory, digest = prepared
    if mutation == "code":
        (root / plans.SCRIPT_PATH).write_text("# changed\n")
    elif mutation == "runtime":
        monkeypatch.setattr(plans, "runtime_binding", lambda: {"changed": True})
    elif mutation == "inventory":
        (directory / "inventory.json").write_bytes(b"{}")
    elif mutation == "archive":
        spec = inventory.load_m4_protocol(root)["sources"][0]
        (directory / "sources" / spec["archive_filename"]).write_bytes(b"changed")
    elif mutation == "membership":
        plan = replay.read_object(directory / plans.PLAN_FILE)
        plan["source_audits"][0]["membership_sha256"] = "0" * 64
        (directory / plans.PLAN_FILE).write_bytes(json_bytes(plan))
    elif mutation in ("dirty", "commit"):
        monkeypatch.setattr(
            plans,
            "_git_binding",
            lambda root: {
                "source_commit": ("a" if mutation == "dirty" else "b") * 40,
                "working_tree_clean": mutation != "dirty",
            },
        )
    elif mutation == "symlink":
        (directory / plans.LEASE_FILE).symlink_to(directory / "absent.json")
    with pytest.raises(ValueError):
        study.execute_study(
            root,
            directory,
            confirm_plan_sha256="0" * 64 if mutation == "confirmation" else digest,
            authorize_final_execution=mutation != "authority",
        )
    assert not (directory / "cells").exists()
    assert not (directory / plans.LEASE_FILE).exists()


def test_dirty_checkout_cannot_prepare_or_attest_loaded_code(
    prepared: Any, monkeypatch: Any
) -> None:
    root, directory, _ = prepared
    monkeypatch.setattr(
        plans, "_git_binding", lambda root: {"source_commit": "a" * 40, "working_tree_clean": False}
    )
    with pytest.raises(ProspectiveBindingError, match="committed clean"):
        plans.prepare_study(root, directory)
    monkeypatch.setattr(plans, "validate_loaded_code_root", _unexpected)
    with pytest.raises(AssertionError):
        plans.preflight_study(root, directory)


def test_train_only_scaler_and_A_only_separate_calibration(prepared: Any, monkeypatch: Any) -> None:
    root, directory, _ = prepared
    plan, sources = plans.bound_plan(root, directory)
    data = sources[0]
    data.features[list(data.partitions["final"])] += 100
    matrices, scaler = cells.source_matrices(data)
    assert scaler["mean"] == np.mean(data.features[list(data.partitions["train"])], axis=0).tolist()
    assert scaler["n_samples_seen"] == len(data.partitions["train"])
    assert np.mean(matrices["final"]) > 50
    fit_original, calibration_original = cells._fit_models, cells.fit_logit_calibration
    observed: list[Any] = []

    def capture_fit(matrix: Any, targets: Any, fixed: Any) -> Any:
        observed.append((np.asarray(matrix["train"]).copy(), list(targets), deepcopy(fixed)))
        return fit_original(matrix, targets, fixed)

    def capture_calibration(probabilities: Any, targets: Any, **options: Any) -> Any:
        observed.append((list(probabilities), list(targets), options))
        return calibration_original(probabilities, targets, **options)

    monkeypatch.setattr(cells, "_fit_models", capture_fit)
    monkeypatch.setattr(cells, "fit_logit_calibration", capture_calibration)
    cell_dir = directory / "standalone-synthetic"
    cell_dir.mkdir()
    cells.run_cell(root, sources[1], plan["protocol"], cell_dir, plan["inventory_sha256"])
    assert len(observed) == 2
    assert observed[0][2]["A"]["max_iter"] == 100 and observed[0][2]["B"]["max_iter"] == 1
    assert observed[0][1] == [sources[1].targets[i] for i in sources[1].partitions["train"]]
    assert observed[1][1] == [sources[1].targets[i] for i in sources[1].partitions["calibration"]]
    assert observed[1][2] == {"probability_clip": 1e-12, "max_iter": 200, "tolerance": 1e-9}


def test_calibration_application_clip_is_not_fit_clip() -> None:
    calibration = CalibrationResult(
        intercept=0.0,
        slope=0.5,
        iterations=0,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=2,
    )
    raw = [[1 - 1e-14, 1e-14], [1e-14, 1 - 1e-14]]
    result = cells.score_bundle(
        row_ids=["a", "b"], targets=[0, 1], raw=raw, calibration=calibration, clip=1e-15
    )
    assert result["calibrated_positive"] == list(
        apply_logit_calibration([p[1] for p in raw], calibration, clip=1e-15)
    )
    assert result["calibrated_positive"] != list(
        apply_logit_calibration([p[1] for p in raw], calibration, clip=1e-12)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_metric",
        "raw_score",
        "target",
        "calibration",
        "scaler",
        "event",
        "rival",
        "decision",
        "reader",
        "extra_file",
        "artifact",
        "census",
        "lease",
    ],
)
def test_resigned_result_tampering_fails_independent_replay(prepared: Any, mutation: str) -> None:
    root, directory, _ = prepared
    _execute(prepared)
    cell = directory / "cells" / inventory.SOURCE_IDS[0]
    scores = replay.read_object(cell / "scores.json")
    manifest = replay.read_object(cell / "manifest.json")
    result = replay.read_object(cell / "result.json")
    if mutation == "raw_metric":
        scores["paths"]["healthy"]["raw_metrics"]["reference_prior_log_loss"] += 0.1
    elif mutation == "raw_score":
        scores["paths"]["healthy"]["raw"][0].reverse()
    elif mutation == "target":
        scores["targets"][0] = 1 - scores["targets"][0]
    elif mutation == "calibration":
        manifest["calibration"]["slope"] += 0.1
    elif mutation == "scaler":
        manifest["preprocessor"]["mean"][0] += 0.1
    elif mutation == "event":
        scores["events"][1]["actual_loaded_sha256"] = scores["events"][0]["actual_loaded_sha256"]
    elif mutation == "rival":
        scores["rivals"]["two_row_target_swap"]["targets"] = scores["targets"]
    elif mutation == "decision":
        result["summary"]["g1_pass"] = not result["summary"]["g1_pass"]
    elif mutation == "reader":
        audit = replay.read_object(cell / "input-audit.json")
        audit["pairwise"]["two_row_target_swap"]["missing_key"]["equal"] = True
        (cell / "input-audit.json").write_bytes(json_bytes(audit))
    elif mutation == "extra_file":
        (cell / "unexpected.json").write_bytes(b"{}")
    elif mutation == "artifact":
        (cell / "artifact_A.joblib").write_bytes(b"not a model")
    elif mutation == "lease":
        lease = replay.read_object(directory / plans.LEASE_FILE)
        lease["explicit_u4_owner_authorization"] = False
        (directory / plans.LEASE_FILE).write_bytes(json_bytes(lease))
    for name, value in (
        ("scores.json", scores),
        ("manifest.json", manifest),
        ("result.json", result),
    ):
        (cell / name).write_bytes(json_bytes(value))
    receipt = replay.read_object(directory / plans.RECEIPT_FILE)
    if mutation == "census":
        receipt["census_dispositions"].reverse()
    receipt["files_sha256"] = study._inventory(directory)
    receipt["lease_sha256"] = file_sha256(directory / plans.LEASE_FILE)
    (directory / plans.RECEIPT_FILE).write_bytes(json_bytes(receipt))
    with pytest.raises(ValueError):
        study.verify_study(root, directory)


@pytest.mark.parametrize("failure", ["runtime", "interrupt"])
def test_failure_and_interruption_keep_census_and_prevent_repeat(
    prepared: Any, monkeypatch: Any, failure: str
) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        raise (
            KeyboardInterrupt
            if failure == "interrupt"
            else RuntimeError("private error is not logged")
        )

    monkeypatch.setattr(study, "run_cell", failed)
    receipt = _execute(prepared)
    assert len(receipt["census_dispositions"]) == 2
    assert receipt["scientific_summary"]["g1_denominator"] == 2
    assert receipt["scientific_summary"]["g1_pass_count"] == 0
    assert receipt["scientific_summary"]["disposition"] == "incomplete"
    root, directory, _ = prepared
    verified = study.verify_study(root, directory)
    assert verified["status"] == "failure_receipt_verified"
    assert "private error" not in json.dumps(receipt)
    with pytest.raises(ProspectiveBindingError, match="already began"):
        _execute(prepared)


@pytest.mark.parametrize(
    "loss_a,delta,empirical,changed,expected",
    [
        (0.2, 0.01, 0.01, True, True),
        (0.2, 0.009999, 0.01, True, False),
        (math.log(2), 0.1, 0.1, True, False),
        (0.2, 0.1, 0.0, True, False),
        (0.2, 0.1, -0.1, True, False),
        (0.2, 0.1, 0.1, False, False),
    ],
)
def test_raw_gate_boundaries_are_not_rescued_by_calibration(
    loss_a: float, delta: float, empirical: float, changed: bool, expected: bool
) -> None:
    a = {
        "raw": [[0.8, 0.2], [0.2, 0.8]],
        "raw_metrics": {"reference_prior_log_loss": loss_a, "empirical_log_loss": 0.2},
        "calibrated_metrics": {"reference_prior_log_loss": 0.1},
    }
    b = {
        "raw": [[0.6, 0.4], [0.4, 0.6]] if changed else a["raw"],
        "raw_metrics": {
            "reference_prior_log_loss": loss_a + delta,
            "empirical_log_loss": 0.2 + empirical,
        },
        "calibrated_metrics": {"reference_prior_log_loss": 10.0},
    }
    paths = {case: a if loaded == "A" else b for case, _, loaded, _ in CASES}
    result = cells.cell_summary(paths, {"minimum_raw_loss_delta": 0.01})
    assert result["g1_pass"] is expected


def test_reader_only_negative_effect_remains_measured_and_not_admitted() -> None:
    result = study.scientific_summary(
        [
            {
                "cell_id": "source-a/model",
                "status": "verified",
                "summary": {"g1_pass": False, "raw_reference_prior_loss_delta": -0.1},
                "g2_pass": False,
            },
            {"cell_id": "source-b/model", "status": "runtime_failure"},
        ]
    )
    assert result["g1_denominator"] == result["g2_denominator"] == 2
    assert result["structurally_verified_cells"] == 1
    assert result["cell_measurements"][0]["raw_reference_prior_loss_delta"] == -0.1
    assert result["mechanism_admitted"] is False


def test_lease_race_fails_without_fitting(prepared: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(study, "publish_immutable_file", lambda *args: "already_exists")
    monkeypatch.setattr(study, "run_cell", _unexpected)
    with pytest.raises(ProspectiveBindingError, match="another process"):
        _execute(prepared)
    assert not (prepared[1] / "cells").exists()


@pytest.mark.parametrize("where", ["cells", "retained_file"])
def test_result_inventory_rejects_symlinks(prepared: Any, where: str) -> None:
    root, directory, _ = prepared
    if where == "cells":
        (directory / "cells").symlink_to(root, target_is_directory=True)
    else:
        (directory / "cells").mkdir()
        (directory / "cells/foreign.json").symlink_to(directory / "inventory.json")
    with pytest.raises(ProspectiveBindingError, match="symlinks"):
        study._inventory(directory)


@pytest.mark.parametrize("payload", [b"[]", b"not JSON"])
def test_retained_objects_require_strict_JSON(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        replay.read_object(path)
    with pytest.raises(ProspectiveBindingError, match="regular"):
        replay.read_object(tmp_path / "absent.json")


def test_fit_recipe_drift_fails_closed(prepared: Any, monkeypatch: Any) -> None:
    plan, data = plans.bound_plan(prepared[0], prepared[1])
    matrices, _ = cells.source_matrices(data[0])
    original = HistGradientBoostingClassifier.fit

    def drifted(model: Any, *args: Any, **kwargs: Any) -> Any:
        original(model, *args, **kwargs)
        model.classes_ = np.asarray([1, 0])
        return model

    monkeypatch.setattr(HistGradientBoostingClassifier, "fit", drifted)
    with pytest.raises(ProspectiveBindingError, match="frozen recipe"):
        cells._fit_models(
            matrices,
            [data[0].targets[i] for i in data[0].partitions["train"]],
            cells.recipes(plan["protocol"]),
        )
    with pytest.raises(ProspectiveBindingError, match="independent fit"):
        replay._refit(data[0], plan["protocol"])


def test_controls_cannot_silently_drift(prepared: Any, monkeypatch: Any) -> None:
    root, directory, _ = prepared
    plan, sources = plans.bound_plan(root, directory)
    original = cells.score_bundle
    calls = []

    def drifted(**options: Any) -> Any:
        result = original(**options)
        calls.append(result)
        if len(calls) == 3:
            result["raw_metrics"]["reference_prior_log_loss"] += 0.1
        return result

    monkeypatch.setattr(cells, "score_bundle", drifted)
    cell_dir = directory / "standalone-synthetic"
    cell_dir.mkdir()
    with pytest.raises(ValueError):
        cells.run_cell(root, sources[0], plan["protocol"], cell_dir, plan["inventory_sha256"])


def test_failure_receipt_cannot_claim_verified_cell(prepared: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(study, "run_cell", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    _execute(prepared)
    root, directory, _ = prepared
    path = directory / "cells" / inventory.SOURCE_IDS[0] / "result.json"
    result = replay.read_object(path)
    result["g1_pass"] = True
    path.write_bytes(json_bytes(result))
    receipt = replay.read_object(directory / plans.RECEIPT_FILE)
    receipt["files_sha256"] = study._inventory(directory)
    (directory / plans.RECEIPT_FILE).write_bytes(json_bytes(receipt))
    with pytest.raises(ProspectiveBindingError):
        study.verify_study(root, directory)


def test_missing_key_input_is_not_forced_equal_or_counted_as_LLM_accuracy() -> None:
    audit = cells.synthetic_reader_preflight(REPO)
    assert audit["provider_calls"] == 0 and audit["lookup_is_llm_accuracy"] is False
    assert audit["pairwise"]["legitimate_B"]["missing_key"]["equal"] is True
    for name in ("adapter_column_reversal", "two_row_target_swap"):
        assert audit["pairwise"][name]["missing_key"]["equal"] is False
    assert audit["scientific_admission"] is False


def test_cli_reports_sanitized_error_without_live_execution(tmp_path: Path) -> None:
    import os

    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / plans.SCRIPT_PATH),
            "execute",
            "--study-dir",
            str(tmp_path),
            "--confirm-plan-sha256",
            "0" * 64,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    parsed = json.loads(result.stdout)
    assert parsed["error_type"] == "ProspectiveBindingError"
    assert "explicit U4" in parsed["reason"]
    assert not (tmp_path / plans.LEASE_FILE).exists()
