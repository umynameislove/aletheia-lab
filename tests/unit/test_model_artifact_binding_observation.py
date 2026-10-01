"""Retained-cell projection with no new fit, injection or pickle loading."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_model_artifact_binding_forward import synthetic_source

from aletheia_lab.benchmark.p2 import model_artifact_binding_forward as cell
from aletheia_lab.benchmark.p2.model_artifact_binding_development import ModelArtifactBindingError
from aletheia_lab.benchmark.p2.model_artifact_binding_observation import (
    retained_artifact_observations,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import audit_artifact_binding_observations

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def retained(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("artifact-input")
    predecessor = directory / "predecessor"
    predecessor.mkdir()
    (predecessor / "receipt.json").write_text(
        json.dumps({"status": "development_effect_insufficient"})
    )
    options = {
        "root": ROOT,
        "archive": directory / "synthetic.zip",
        "output": directory / "cell",
        "predecessor": predecessor,
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cell, "load_development_source", lambda **_: synthetic_source())
        prepared = cell.prepare_forward_cell(**options)
        cell.execute_forward_cell(**options, confirm_plan_sha256=prepared["plan_sha256"])
    return options


def load(retained: dict[str, Path]) -> dict[str, Any]:
    return retained_artifact_observations(
        root=ROOT,
        cell_dir=retained["output"],
        expected_receipt_sha256=file_sha256(retained["output"] / "receipt.json"),
    )


def test_retained_projection_never_fits_predicts_or_deserializes(
    retained: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier

    def forbidden(*_: Any, **__: Any) -> None:
        pytest.fail("retained input audit fitted, predicted or deserialized")

    monkeypatch.setattr(joblib, "load", forbidden)
    monkeypatch.setattr(HistGradientBoostingClassifier, "fit", forbidden)
    monkeypatch.setattr(HistGradientBoostingClassifier, "predict_proba", forbidden)
    monkeypatch.setattr(cell, "execute_forward_cell", forbidden)
    before = {p.name: file_sha256(p) for p in retained["output"].iterdir()}
    observations = load(retained)
    result = audit_artifact_binding_observations(ROOT, observations)
    assert all(result["checks"].values())
    after = {p.name: file_sha256(p) for p in retained["output"].iterdir()}
    assert before == after


def test_every_probe_comes_from_its_retained_path_at_the_same_indices(
    retained: dict[str, Path],
) -> None:
    observations = load(retained)
    scores = cell.json_file(retained["output"] / "scores.json")
    indices = (scores["targets"].index(0), scores["targets"].index(1))
    for case, *_ in cell.CASES:
        assert observations[case].raw_probe_scores == tuple(
            tuple(scores["paths"][case]["raw"][i]) for i in indices
        )
        assert observations[case].source_targets == observations[case].scoring_targets == (0, 1)
    assert observations["two_row_target_swap"].scoring_targets == (1, 0)
    assert observations["adapter_column_reversal"].scored_probe_positive == tuple(
        scores["paths"]["healthy"]["raw"][i][0] for i in indices
    )
    assert observations["faulty"] == replace(
        observations["legitimate_B"], intended_artifact="artifact-0", reported_artifact="artifact-0"
    )
    assert (
        observations["manifest_text_only"].intended_artifact
        == observations["manifest_text_only"].loaded_artifact
        == "artifact-0"
    )
    assert observations["manifest_text_only"].reported_artifact == "artifact-1"


def test_receipt_confirmation_and_retained_drift_fail_before_capture(
    retained: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ModelArtifactBindingError, match="receipt identity"):
        retained_artifact_observations(
            root=ROOT, cell_dir=retained["output"], expected_receipt_sha256="0" * 64
        )
    import aletheia_lab.benchmark.p2.model_artifact_binding_observation as module

    monkeypatch.setattr(
        module, "file_sha256", lambda p: "0" * 64 if p.name == "scores.json" else file_sha256(p)
    )
    with pytest.raises(ModelArtifactBindingError, match="artifact changed"):
        load(retained)


def test_cli_emits_only_aggregate_input_audit(
    retained: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    spec = importlib.util.spec_from_file_location(
        "artifact_input_cli", ROOT / "scripts/audit_model_artifact_binding_input.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--root",
            str(ROOT),
            "--cell-dir",
            str(retained["output"]),
            "--confirm-receipt-sha256",
            file_sha256(retained["output"] / "receipt.json"),
        ],
    )
    assert module.main() == 0
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result["retained_cell_unchanged"] is True
    assert (
        "wire_payloads" not in result
        and "row_ids" not in text
        and str(retained["output"]) not in text
    )
    assert result["provider_calls"] == 0
    assert len(result["audit_code_sha256"]) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "receipt_scope",
        "retained_keys",
        "lease",
        "manifest",
        "events",
        "scores_census",
        "raw_shape",
        "raw_sum",
        "raw_hash",
        "raw_metric",
        "targets",
        "control",
        "rival",
    ],
)
def test_retained_semantic_checks_reject_drift_even_with_unchanged_byte_check(
    retained: dict[str, Path], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import aletheia_lab.benchmark.p2.model_artifact_binding_observation as module

    data = {p.name: cell.json_file(p) for p in retained["output"].glob("*.json")}
    scores = data["scores.json"]
    if mutation == "receipt_scope":
        data["receipt.json"]["scientific_admission"] = True
    elif mutation == "retained_keys":
        data["receipt.json"]["retained_sha256"].pop("rivals.json")
    elif mutation == "lease":
        data["lease.json"]["plan_sha256"] = "0" * 64
    elif mutation == "manifest":
        data["manifest.json"]["recipes"]["A"]["max_iter"] = 99
    elif mutation == "events":
        data["load-trace.json"]["events"].pop()
    elif mutation == "scores_census":
        scores["paths"].pop("sham")
    elif mutation == "raw_shape":
        scores["paths"]["healthy"]["raw"].pop()
    elif mutation == "raw_sum":
        scores["paths"]["healthy"]["raw"][0] = [0.8, 0.8]
    elif mutation == "raw_hash":
        scores["paths"]["healthy"]["raw_scores_sha256"] = "0" * 64
    elif mutation == "raw_metric":
        scores["paths"]["healthy"]["raw_metrics"]["reference_prior_log_loss"] += 0.1
    elif mutation == "targets":
        scores["targets"][0] = 1 - scores["targets"][0]
    elif mutation == "control":
        scores["paths"]["sham"]["calibrated_scores_sha256"] = "0" * 64
    else:
        data["rivals.json"]["two_row_target_swap"]["targets"][0] = 4
    # Isolate semantic validation independently of the already tested byte pin.
    monkeypatch.setattr(module, "json_file", lambda p: data[p.name])
    with pytest.raises(ModelArtifactBindingError):
        load(retained)


def test_extra_retained_file_blocks_projection(
    retained: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.iterdir

    def extra(path: Path) -> Any:
        files = list(original(path))
        return (
            iter(files + [path / "unclassified.txt"]) if path == retained["output"] else iter(files)
        )

    monkeypatch.setattr(Path, "iterdir", extra)
    with pytest.raises(ModelArtifactBindingError, match="file census"):
        load(retained)
