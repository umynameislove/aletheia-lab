"""Synthetic end-to-end dose replay, input boundaries and failure preservation."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aletheia_lab.benchmark.p2 import model_artifact_binding_dose as dose
from aletheia_lab.benchmark.p2 import model_artifact_binding_dose_verify as verifier
from aletheia_lab.benchmark.p2 import model_artifact_binding_forward as forward
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


@pytest.fixture(scope="module")
def retained(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("artifact-dose")
    source = synthetic_source()
    first = directory / "cell1"
    first.mkdir()
    (first / "receipt.json").write_text(json.dumps({"status": "development_effect_insufficient"}))
    predecessor = directory / "cell2"
    options = {"root": ROOT, "archive": directory / "toy.zip", "predecessor": predecessor}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forward, "load_development_source", lambda **_: source)
        patch.setattr(dose, "load_development_source", lambda **_: source)
        patch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network access"))
        previous = {**options, "output": predecessor, "predecessor": first}
        prepared = forward.prepare_forward_cell(**previous)
        forward.execute_forward_cell(**previous, confirm_plan_sha256=prepared["plan_sha256"])
        options.update(expected_receipt_sha256=file_sha256(predecessor / "receipt.json"))
        output = directory / "sweep"
        prepared = dose.prepare_dose_study(**options, output=output)
        receipt = dose.execute_dose_study(
            **options, output=output, confirm_plan_sha256=prepared["plan_sha256"]
        )
    return {"source": source, "options": options, "output": output, "receipt": receipt}


@pytest.fixture
def options(retained: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(dose, "load_development_source", lambda **_: retained["source"])
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network access"))
    output = tmp_path / "sweep"
    shutil.copytree(retained["output"], output)
    return {**retained["options"], "output": output}


def test_end_to_end_source_refit_and_all_five_doses(
    retained: Any, options: Any, monkeypatch: Any
) -> None:
    before = {p.name: file_sha256(p) for p in options["predecessor"].iterdir()}
    for name in ("_paths", "load_event", "score_bundle", "dose_summary", "_fit_alternate"):
        monkeypatch.setattr(
            dose, name, lambda *_, **__: pytest.fail("runner used during independent replay")
        )
    monkeypatch.setattr(dose.joblib, "load", lambda *_: pytest.fail("supplied pickle loaded"))
    verified = verifier.verify_dose_study(**options)
    assert verified["verification"] == "independent_refit_and_metric_replay_no_pickle_load"
    assert verified["doses"].keys() == {"1", "5", "10", "25", "50"}
    assert verified["selected_dose"] is None
    assert verified["sdk_capture_count"] == 160 and verified["provider_calls"] == 0
    assert verified["U4_authorized"] is verified["scientific_admission"] is False
    assert verified["protected_predictions_or_metrics_computed"] is False
    assert file_sha256(options["output"] / "artifact_A.joblib") == file_sha256(
        options["predecessor"] / "artifact_A.joblib"
    )
    for result in verified["doses"].values():
        assert all(
            result[name]
            for name in (
                "sham_exact",
                "correction_exact",
                "manifest_only_exact",
                "legitimate_B_exact",
                "B_matches_A_stage",
            )
        )
    audits = forward.json_file(options["output"] / "input-audit.json")["doses"]
    for audit in audits.values():
        assert audit["pairwise"]["legitimate_B"]["missing_key"]["equal"] is True
        assert audit["pairwise"]["legitimate_B"]["full"]["equal"] is False
        assert audit["pairwise"]["adapter_column_reversal"]["missing_key"]["equal"] is False
        assert audit["pairwise"]["two_row_target_swap"]["missing_key"]["equal"] is False
    assert before == {p.name: file_sha256(p) for p in options["predecessor"].iterdir()}


def test_prepare_has_no_fit_load_or_prediction(
    options: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    for name in ("_fit_alternate", "load_event", "probability_rows"):
        monkeypatch.setattr(
            dose, name, lambda *_, **__: pytest.fail("prepare consumed fitted output")
        )
    options["output"] = tmp_path / "new"
    result = dose.prepare_dose_study(**options)
    plan = forward.json_file(options["output"] / "plan.json")
    assert result["status"] == "development_dose_plan_fixed"
    assert plan["doses"] == [1, 5, 10, 25, 50]
    assert plan["recipes"]["A"]["max_iter"] == 100
    assert plan["selection"].startswith("none-report-all-doses")
    assert {p.name for p in options["output"].iterdir()} == {"plan.json"}


def test_effect_does_not_rescue_inadequate_healthy_reference() -> None:
    a = {
        "raw": [[0.9, 0.1]],
        "raw_metrics": {"reference_prior_log_loss": 0.84, "empirical_log_loss": 0.35},
        "calibrated_metrics": {"reference_prior_log_loss": 0.8},
    }
    b = {
        "raw": [[0.8, 0.2]],
        "raw_metrics": {"reference_prior_log_loss": 0.98, "empirical_log_loss": 0.42},
        "calibrated_metrics": {"reference_prior_log_loss": 0.92},
    }
    paths = {
        "healthy": a,
        "sham": copy.deepcopy(a),
        "corrected": a,
        "manifest_text_only": a,
        "faulty": b,
        "legitimate_B": b,
    }
    result = dose.dose_summary(paths)
    assert result["effect_threshold_met"] is True
    assert result["healthy_better_than_uniform"] is False
    assert result["prospective_joint_gate_met"] is False
    paths["faulty"] = a
    assert dose.dose_summary(paths)["effect_threshold_met"] is False


def rewrite(options: Any, filename: str, payload: Any) -> None:
    path = options["output"] / filename
    path.write_text(json.dumps(payload))
    if filename != "receipt.json":
        receipt = forward.json_file(options["output"] / "receipt.json")
        receipt["retained_sha256"][filename] = file_sha256(path)
        (options["output"] / "receipt.json").write_text(json.dumps(receipt))


@pytest.mark.parametrize(
    "mutation",
    [
        "scores",
        "target",
        "load_trace",
        "stage",
        "effect",
        "uniform",
        "audit",
        "census",
        "admission",
        "lease",
    ],
)
def test_tampering_fails_even_with_resealed_outer_hashes(options: Any, mutation: str) -> None:
    if mutation in {"scores", "target", "load_trace", "stage", "census"}:
        data = forward.json_file(options["output"] / "scores.json")
        cell = data["doses"]["5"]
        if mutation == "scores":
            cell["paths"]["faulty"]["raw"][0] = [0.4, 0.6]
        elif mutation == "target":
            data["targets"][0] = 1 - data["targets"][0]
        elif mutation == "load_trace":
            cell["events"][1]["actual_loaded_sha256"] = cell["events"][0]["actual_loaded_sha256"]
        elif mutation == "stage":
            cell["B_matches_A_stage"] = False
        else:
            del data["doses"]["50"]
        rewrite(options, "scores.json", data)
    elif mutation == "audit":
        rewrite(options, "input-audit.json", {"doses": {}})
    elif mutation == "lease":
        rewrite(options, "lease.json", {"registered_attempt": True})
    else:
        data = forward.json_file(options["output"] / "receipt.json")
        if mutation == "effect":
            data["doses"]["5"]["raw_reference_prior_loss_delta"] += 0.1
        elif mutation == "uniform":
            data["uniform_reference_loss"] = 1.0
        else:
            data["scientific_admission"] = True
        rewrite(options, "receipt.json", data)
    with pytest.raises(ModelArtifactBindingError):
        verifier.verify_dose_study(**options)


@pytest.mark.parametrize(
    "mutation", ["hash", "extra", "missing", "plan", "nonobject", "calibration"]
)
def test_retained_file_or_plan_changes_rejected_before_refit(
    options: Any, mutation: str, monkeypatch: Any
) -> None:
    output = options["output"]
    if mutation == "hash":
        (output / "artifact_B5.joblib").write_bytes(b"untrusted bytes")
    elif mutation == "extra":
        (output / "extra.json").write_text("{}")
    elif mutation == "missing":
        (output / "scores.json").unlink()
    elif mutation == "nonobject":
        rewrite(options, "scores.json", [])
    else:
        data = forward.json_file(output / "plan.json")
        data["doses" if mutation == "plan" else "calibration"] = []
        (output / "plan.json").write_text(json.dumps(data))
    monkeypatch.setattr(verifier, "_refit", lambda *_: pytest.fail("bad retained state refit"))
    with pytest.raises(ModelArtifactBindingError):
        verifier.verify_dose_study(**options)


def test_execution_rejects_wrong_confirmation_and_repeated_start(options: Any) -> None:
    for digest in ("0" * 64, file_sha256(options["output"] / "plan.json")):
        with pytest.raises(ModelArtifactBindingError):
            dose.execute_dose_study(**options, confirm_plan_sha256=digest)


def test_failed_fit_retained_without_success_or_repeat(
    options: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    options["output"] = tmp_path / "failure"
    prepared = dose.prepare_dose_study(**options)
    monkeypatch.setattr(
        dose,
        "_fit_alternate",
        lambda *_: (_ for _ in ()).throw(ValueError("synthetic fit failure")),
    )
    with pytest.raises(ValueError, match="synthetic fit failure"):
        dose.execute_dose_study(**options, confirm_plan_sha256=prepared["plan_sha256"])
    assert (
        forward.json_file(options["output"] / "failure.json")["status"]
        == "development_failed_closed"
    )
    assert not (options["output"] / "receipt.json").exists()
    with pytest.raises(ValueError, match="already began"):
        dose.execute_dose_study(**options, confirm_plan_sha256=prepared["plan_sha256"])


def test_output_cannot_be_in_repo_or_symlink(options: Any, tmp_path: Path) -> None:
    for output in (ROOT / "private-dose-test", options["output"]):
        with pytest.raises(ValueError):
            dose.prepare_dose_study(**{**options, "output": output})
    symlink = tmp_path / "linked"
    try:
        symlink.symlink_to(options["output"], target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(ValueError):
        verifier.verify_dose_study(**{**options, "output": symlink})


def load_cli() -> Any:
    spec = importlib.util.spec_from_file_location(
        "artifact_dose_cli", ROOT / "scripts/model_artifact_binding_dose.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_sanitizes_failure(options: Any, monkeypatch: Any, capsys: Any) -> None:
    cli = load_cli()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dose",
            "verify",
            "--archive",
            str(options["archive"]),
            "--predecessor",
            str(options["predecessor"]),
            "--confirm-receipt-sha256",
            "0" * 64,
            "--output",
            str(options["output"]),
        ],
    )
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed_closed",
        "error_type": "ModelArtifactBindingError",
    }


@pytest.mark.parametrize(
    "args",
    [
        ["execute"],
        ["inventory"],
        [
            "execute",
            "--archive",
            "a",
            "--predecessor",
            "p",
            "--output",
            "o",
            "--confirm-receipt-sha256",
            "0" * 64,
        ],
    ],
)
def test_cli_requires_exact_action_arguments(args: list[str], monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["dose", *args])
    with pytest.raises(SystemExit):
        load_cli().main()
