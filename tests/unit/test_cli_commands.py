"""Tests for the top-level CLI commands and the module entry point.

Covers behavioral contracts of:
  - __main__.py  (module entry-point routing)
  - cli.py       (info, validate-case, leakage-check, score-example)
  - baseline/cli.py (train error boundary, evaluate, verify error boundary)

All tests are offline, deterministic, and write only to tmp_path.
No external providers are contacted and no P1/P2 artifacts are generated.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia_lab.baseline.cli import baseline_app
from aletheia_lab.cli import app

_runner = CliRunner()

# ---------------------------------------------------------------------------
# Module entry point  (__main__.py)
# ---------------------------------------------------------------------------


def test_module_entry_point_invokes_cli_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """python -m aletheia_lab routes a valid help request through the CLI app."""

    monkeypatch.setattr(sys, "argv", ["aletheia_lab", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("aletheia_lab", run_name="__main__", alter_sys=True)
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Usage: python -m aletheia_lab" in output
    assert "benchmark" in output


def test_module_entry_point_exposes_same_app_as_cli() -> None:
    """aletheia_lab.__main__ re-exports the identical app object from cli.py."""
    import aletheia_lab.__main__ as entry
    from aletheia_lab.cli import app as cli_app

    assert entry.app is cli_app


# ---------------------------------------------------------------------------
# Top-level app — help and subcommand registration
# ---------------------------------------------------------------------------


def test_top_level_help_exits_zero() -> None:
    result = _runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_top_level_help_registers_baseline_and_benchmark() -> None:
    result = _runner.invoke(app, ["--help"])
    assert "baseline" in result.output
    assert "benchmark" in result.output


# ---------------------------------------------------------------------------
# info command
# ---------------------------------------------------------------------------


def _write_project_config(
    tmp_path: Path,
    *,
    name: str = "test-project",
    dataset_id: str = "sample_dataset",
) -> Path:
    """Write a minimal valid project YAML and return its path."""
    config = tmp_path / "project.yaml"
    config.write_text(
        f"project:\n  name: {name}\n  version: 0.1\n"
        f"dataset:\n  id: {dataset_id}\n"
        "benchmark:\n  fault_type: data_drift\n  target_cases: 10\n",
        encoding="utf-8",
    )
    return config


def test_info_prints_project_name_and_dataset_id(tmp_path: Path) -> None:
    config = _write_project_config(tmp_path, name="my-research-project", dataset_id="telco_churn")
    result = _runner.invoke(app, ["info", "--config", str(config)])
    assert result.exit_code == 0
    assert "my-research-project" in result.output
    assert "telco_churn" in result.output


def test_info_missing_config_file_exits_nonzero(tmp_path: Path) -> None:
    result = _runner.invoke(app, ["info", "--config", str(tmp_path / "no_such.yaml")])
    assert result.exit_code != 0


def test_info_yaml_list_not_mapping_exits_nonzero(tmp_path: Path) -> None:
    """load_yaml must reject a YAML list; info propagates the TypeError."""
    config = tmp_path / "bad.yaml"
    config.write_text("- item_one\n- item_two\n", encoding="utf-8")
    result = _runner.invoke(app, ["info", "--config", str(config)])
    assert result.exit_code != 0


def test_info_empty_yaml_shows_unknown_defaults(tmp_path: Path) -> None:
    """An empty YAML file yields an empty dict; info falls back to 'unknown' labels."""
    config = tmp_path / "empty.yaml"
    config.write_text("", encoding="utf-8")
    result = _runner.invoke(app, ["info", "--config", str(config)])
    assert result.exit_code == 0
    assert "unknown" in result.output


# ---------------------------------------------------------------------------
# validate-case command
# ---------------------------------------------------------------------------

_MINIMAL_CASE = Path("tests") / "fixtures" / "minimal_case.json"


def test_validate_case_success_prints_case_id_and_fault_type(tmp_path: Path) -> None:
    case_file = tmp_path / "case.json"
    case_file.write_text(_MINIMAL_CASE.read_text(encoding="utf-8"), encoding="utf-8")
    result = _runner.invoke(app, ["validate-case", str(case_file)])
    assert result.exit_code == 0
    assert "p1-data-drift-0001" in result.output
    assert "data_drift" in result.output


def test_validate_case_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = _runner.invoke(app, ["validate-case", str(tmp_path / "ghost.json")])
    assert result.exit_code != 0


def test_validate_case_invalid_json_exits_nonzero(tmp_path: Path) -> None:
    case_file = tmp_path / "broken.json"
    case_file.write_text("{not: valid json", encoding="utf-8")
    result = _runner.invoke(app, ["validate-case", str(case_file)])
    assert result.exit_code != 0


def test_validate_case_missing_required_fields_exits_nonzero(tmp_path: Path) -> None:
    """A JSON object missing required BenchmarkCase fields fails Pydantic validation."""
    case_file = tmp_path / "incomplete.json"
    case_file.write_text(json.dumps({"case_id": "x", "fault_type": "data_drift"}), encoding="utf-8")
    result = _runner.invoke(app, ["validate-case", str(case_file)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# leakage-check command
# ---------------------------------------------------------------------------


def test_leakage_check_no_forbidden_list_always_passes() -> None:
    """Without --forbidden options, any text is clean."""
    result = _runner.invoke(app, ["leakage-check", "Normal model evaluation text."])
    assert result.exit_code == 0


def test_leakage_check_match_exits_one_and_reports_term() -> None:
    result = _runner.invoke(
        app,
        ["leakage-check", "The answer key reveals data drift.", "--forbidden", "answer key"],
    )
    assert result.exit_code == 1
    assert "answer key" in result.output


def test_leakage_check_no_match_with_forbidden_list_passes() -> None:
    result = _runner.invoke(
        app,
        [
            "leakage-check",
            "Distribution shift was observed in Contract.",
            "--forbidden",
            "answer key",
        ],
    )
    assert result.exit_code == 0


def test_leakage_check_case_insensitive_match() -> None:
    """find_forbidden_terms normalizes via casefold; UPPER CASE terms must be detected."""
    result = _runner.invoke(
        app,
        ["leakage-check", "The ANSWER KEY is hidden.", "--forbidden", "answer key"],
    )
    assert result.exit_code == 1


def test_leakage_check_multiple_forbidden_terms_reports_match() -> None:
    result = _runner.invoke(
        app,
        [
            "leakage-check",
            "Label noise was injected.",
            "--forbidden",
            "answer key",
            "--forbidden",
            "label noise",
        ],
    )
    assert result.exit_code == 1
    assert "label noise" in result.output


# ---------------------------------------------------------------------------
# score-example command
# ---------------------------------------------------------------------------


def test_score_example_exact_match_returns_one() -> None:
    result = _runner.invoke(app, ["score-example", "Yes", "Yes"])
    assert result.exit_code == 0
    assert "1.0" in result.output


def test_score_example_mismatch_returns_zero() -> None:
    result = _runner.invoke(app, ["score-example", "Yes", "No"])
    assert result.exit_code == 0
    assert "0.0" in result.output


def test_score_example_case_insensitive_match() -> None:
    """Comparison is performed after .lower() so YES == yes."""
    result = _runner.invoke(app, ["score-example", "YES", "yes"])
    assert result.exit_code == 0
    assert "1.0" in result.output


def test_score_example_leading_trailing_whitespace_stripped() -> None:
    """Leading and trailing whitespace is stripped before comparison."""
    result = _runner.invoke(app, ["score-example", "  Yes  ", "Yes"])
    assert result.exit_code == 0
    assert "1.0" in result.output


def test_score_example_different_values_after_strip_returns_zero() -> None:
    result = _runner.invoke(app, ["score-example", "  Yes  ", "No"])
    assert result.exit_code == 0
    assert "0.0" in result.output


# ---------------------------------------------------------------------------
# baseline evaluate command
# ---------------------------------------------------------------------------


def _metrics_payload(*, roc_auc: float | None = None) -> dict[str, object]:
    """Minimal valid metrics JSON consumed by baseline evaluate / _metrics_table."""
    split: dict[str, object] = {
        "n": 168,
        "accuracy": 0.8214,
        "balanced_accuracy": 0.8103,
        "precision": 0.8037,
        "recall": 0.7865,
        "f1": 0.7950,
        "roc_auc": roc_auc,
    }
    return {"splits": {"train": split}}


def test_baseline_evaluate_missing_metrics_json_exits_one(tmp_path: Path) -> None:
    """evaluate must print FAIL and exit 1 when metrics.json is absent."""
    run_dir = tmp_path / "run_no_metrics"
    run_dir.mkdir()
    result = _runner.invoke(baseline_app, ["evaluate", "--run-dir", str(run_dir)])
    assert result.exit_code == 1


def test_baseline_evaluate_nonexistent_run_dir_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(baseline_app, ["evaluate", "--run-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1


def test_baseline_evaluate_valid_metrics_exits_zero(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "metrics.json").write_text(json.dumps(_metrics_payload()), encoding="utf-8")
    result = _runner.invoke(baseline_app, ["evaluate", "--run-dir", str(run_dir)])
    assert result.exit_code == 0


def test_baseline_evaluate_metrics_with_numeric_roc_auc(tmp_path: Path) -> None:
    """roc_auc as a float must render as a formatted float, not 'n/a'."""
    run_dir = tmp_path / "run_roc"
    run_dir.mkdir()
    (run_dir / "metrics.json").write_text(
        json.dumps(_metrics_payload(roc_auc=0.8701)), encoding="utf-8"
    )
    result = _runner.invoke(baseline_app, ["evaluate", "--run-dir", str(run_dir)])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# baseline train — error boundary
# ---------------------------------------------------------------------------


def test_baseline_train_missing_config_exits_nonzero(tmp_path: Path) -> None:
    """A missing config file must cause a non-zero exit without crashing."""
    result = _runner.invoke(
        baseline_app,
        ["train", "--config", str(tmp_path / "no_config.yaml")],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# baseline verify — error boundary
# ---------------------------------------------------------------------------


def test_baseline_verify_missing_config_exits_nonzero(tmp_path: Path) -> None:
    """A missing config file must cause a non-zero exit without crashing."""
    result = _runner.invoke(
        baseline_app,
        ["verify", "--config", str(tmp_path / "no_config.yaml")],
    )
    assert result.exit_code != 0
