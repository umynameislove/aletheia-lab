"""Train/evaluate artifacts, two-run reproducibility, and CLI success/failure."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aletheia_lab.baseline.run import resolve_settings, train, verify
from aletheia_lab.baseline.schema import MetricsReport
from aletheia_lab.cli import app

runner = CliRunner()

_ARTIFACTS = (
    "config.resolved.json",
    "split_manifest.json",
    "metrics.json",
    "predictions.json",
    "model.joblib",
    "provenance.json",
    "checksums.json",
)


def test_train_writes_all_artifacts_and_valid_metrics(project_config, tmp_path):
    settings = resolve_settings(project_config)
    out = tmp_path / "run1"
    result = train(settings, out)
    for name in _ARTIFACTS:
        assert (out / name).exists(), f"missing artifact {name}"
    # metrics.json conforms to the schema
    MetricsReport(**json.loads((out / "metrics.json").read_text()))
    assert result["metrics"]["splits"]["test"]["n"] > 0


def test_metrics_json_is_deterministic_across_runs(project_config, tmp_path):
    settings = resolve_settings(project_config)
    a = train(settings, tmp_path / "a")
    b = train(settings, tmp_path / "b")
    for name in ("split_manifest.json", "metrics.json", "config.resolved.json"):
        assert (Path(a["run_dir"]) / name).read_bytes() == (Path(b["run_dir"]) / name).read_bytes()


def test_two_runs_reproducible_within_tolerance(project_config, tmp_path):
    settings = resolve_settings(project_config)
    report = verify(settings, tmp_path / "verify")
    assert report.passed, report.as_dict()
    assert all(report.exact_fields.values())
    for value in report.tolerance_fields.values():
        assert value <= settings.tolerance


def test_cli_train_success(project_config, tmp_path):
    result = runner.invoke(
        app,
        [
            "baseline",
            "train",
            "--config",
            str(project_config),
            "--output-dir",
            str(tmp_path / "cli"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cli" / "metrics.json").exists()


def test_cli_verify_success(project_config, tmp_path):
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--config",
            str(project_config),
            "--output-dir",
            str(tmp_path / "v"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_train_failure_on_missing_dataset(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        "dataset:\n  id: telco_customer_churn\n"
        f"paths:\n  processed_data: {empty.as_posix()}\n"
        "baseline:\n  seed: 42\n  output_dir: "
        f"{(tmp_path / 'o').as_posix()}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["baseline", "train", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
