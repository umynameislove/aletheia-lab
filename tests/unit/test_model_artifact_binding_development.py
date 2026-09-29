"""Synthetic-only controls for the M4 wrong-artifact development cell."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aletheia_lab.benchmark.p2 import model_artifact_binding_development as cell
from aletheia_lab.benchmark.p2 import model_artifact_binding_verify as verifier
from aletheia_lab.content_hashing import file_sha256


def _synthetic_source() -> SimpleNamespace:
    rng = np.random.default_rng(1207)
    matrix = rng.normal(size=(240, 3))
    probabilities = 1.0 / (1.0 + np.exp(-0.7 * matrix[:, 0] + 0.3 * matrix[:, 1]))
    labels = (rng.random(240) < probabilities).astype(int)
    return SimpleNamespace(
        train=matrix[:160],
        development=matrix[160:],
        train_targets=tuple(labels[:160].tolist()),
        development_targets=tuple(labels[160:].tolist()),
        train_ids=tuple(f"train-{index}" for index in range(160)),
        development_ids=tuple(f"dev-{index}" for index in range(80)),
        calibration_parameters=(1e-12, 200, 1e-9),
        bindings=lambda: {"source": "synthetic-no-protected-rows"},
    )


def test_wrong_load_and_correction_replay_from_saved_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    archive = tmp_path / "synthetic.zip"
    archive.write_bytes(b"unused synthetic stand-in")
    output = tmp_path / "private" / "m4"
    output.parent.mkdir()
    source = _synthetic_source()
    monkeypatch.setattr(cell, "load_development_source", lambda **_: source)
    monkeypatch.setattr(verifier, "load_development_source", lambda **_: source)

    receipt = cell.run_development_cell(root=root, archive=archive, output=output)
    checked = verifier.verify_development_cell(root=root, archive=archive, output=output)
    assert checked["status"] == receipt["status"]
    assert checked["provider_calls"] == 0
    assert checked["registered_attempt"] is False
    assert checked["scientific_admission"] is False
    paths = receipt["paths"]
    assert paths["healthy"] == paths["sham"] == paths["corrected"]
    assert paths["healthy"]["loaded_artifact_sha256"] != paths["faulty"]["loaded_artifact_sha256"]
    assert (
        paths["faulty"]["loaded_artifact_sha256"] == paths["promoted_B"]["loaded_artifact_sha256"]
    )
    assert paths["faulty"]["raw_scores_sha256"] != paths["healthy"]["raw_scores_sha256"]
    assert receipt["changed_raw_score_rows"] > 0

    # Changing the receipt or the model bytes independently must fail verification.
    receipt_path = output / "receipt.json"
    original_receipt = receipt_path.read_bytes()
    changed = json.loads(original_receipt)
    changed["paths"]["faulty"]["declared_artifact_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(changed))
    with pytest.raises(cell.ModelArtifactBindingError):
        verifier.verify_development_cell(root=root, archive=archive, output=output)
    receipt_path.write_bytes(original_receipt)
    model_path = output / "artifact_B.joblib"
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(cell.ModelArtifactBindingError, match="saved artifact"):
        verifier.verify_development_cell(root=root, archive=archive, output=output)


def test_trusted_loader_rejects_unbound_bytes_before_deserialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.joblib"
    candidate.write_bytes(b"not a model")
    monkeypatch.setattr(cell.joblib, "load", lambda *_: pytest.fail("unbound bytes were loaded"))
    with pytest.raises(cell.ModelArtifactBindingError, match="pre-load manifest"):
        cell._trusted_load(candidate, "0" * 64, expected_feature_count=3)
    assert file_sha256(candidate) != "0" * 64


def test_candidate_views_preserve_missing_lineage_boundary() -> None:
    receipt = {
        "paths": {
            "healthy": {"calibrated_log_loss": 0.2, "raw_scores_sha256": "a" * 64},
            "faulty": {
                "calibrated_log_loss": 0.3,
                "declared_artifact_sha256": "b" * 64,
                "loaded_artifact_sha256": "c" * 64,
                "raw_scores_sha256": "d" * 64,
            },
        },
        "manifest": {"artifact_B_sha256": "c" * 64},
        "runtime": {"python": "synthetic"},
    }
    views = cell.evidence_views(receipt)
    assert set(views) == {"full", "missing_key", "noisy", "misleading"}
    assert "loaded_artifact_sha256" not in views["missing_key"]
    assert "loaded_artifact_sha256" in views["full"]
