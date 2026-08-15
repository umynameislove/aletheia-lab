"""Tests for the benchmark CLI subcommands.

Tests are grouped by behavioral contract:
  - Help / subcommand registration
  - Exception handlers (invalid/missing paths → exit 1 + FAIL message)
  - Failure paths (validation returns passed=False → exit 1 + FAIL message)
  - FileExistsError / immutable-output boundaries (monkeypatched)

All tests are offline and deterministic. Representative success paths build
synthetic P1 cases and an evidence store under ``tmp_path``; no frozen project
artifacts are read or written and no external providers are contacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia_lab.benchmark.cli import benchmark_app

_runner = CliRunner()

# ---------------------------------------------------------------------------
# Helper — nonexistent path factory
# ---------------------------------------------------------------------------


def _np(tmp_path: Path, name: str) -> Path:
    """Return a path that does NOT exist inside tmp_path."""
    return tmp_path / name


@pytest.fixture
def generated_p1_assets(p1_generator_config: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Generate and validate a real synthetic case set and evidence store via CLI."""

    cases_dir = tmp_path / "cases"
    store_dir = tmp_path / "evidence-store"
    generated = _runner.invoke(
        benchmark_app,
        [
            "generate-p1",
            "--config",
            str(p1_generator_config),
            "--output-dir",
            str(cases_dir),
        ],
    )
    assert generated.exit_code == 0, generated.output
    assert "Generated 15 cases, validation PASS, leakage 0" in generated.output

    evidence = _runner.invoke(
        benchmark_app,
        [
            "generate-p1-evidence",
            "--cases-dir",
            str(cases_dir),
            "--output-dir",
            str(store_dir),
        ],
    )
    assert evidence.exit_code == 0, evidence.output
    assert "Generated and verified 15 bundles" in evidence.output
    assert "machine leakage PASS" in evidence.output
    return cases_dir, store_dir


# ---------------------------------------------------------------------------
# Top-level benchmark help and subcommand registration
# ---------------------------------------------------------------------------


def test_benchmark_help_exits_zero() -> None:
    result = _runner.invoke(benchmark_app, ["--help"])
    assert result.exit_code == 0


def test_benchmark_help_registers_all_pipeline_commands() -> None:
    result = _runner.invoke(benchmark_app, ["--help"])
    for name in (
        "generate-p1",
        "validate-p1",
        "generate-p1-evidence",
        "validate-p1-evidence",
        "run-p1-pilot-mock",
        "validate-p1-pilot",
        "preflight-p1-openai",
        "run-p1-openai-smoke",
        "validate-p1-openai-smoke",
        "run-p1-openai-full",
        "validate-p1-openai-full",
        "evaluate-p1-pilot",
        "freeze-p1-result",
        "validate-p1-result-lock",
        "generate-p1-closeout",
        "validate-p1-closeout",
        "validate-p1-final",
    ):
        assert name in result.output, f"subcommand '{name}' not found in help output"


# ---------------------------------------------------------------------------
# generate-p1 — exception boundaries
# ---------------------------------------------------------------------------


def test_generate_p1_generator_config_error_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GeneratorConfigError from generate_p1 must map to exit 1 + FAIL message."""
    from aletheia_lab.benchmark.generator import GeneratorConfigError

    def _raise(*_a: object, **_kw: object) -> None:
        raise GeneratorConfigError("fault_types.yaml is missing")

    monkeypatch.setattr("aletheia_lab.benchmark.cli.generate_p1", _raise)
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1",
            "--config",
            str(_np(tmp_path, "cfg.yaml")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_generate_p1_dataset_schema_error_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DatasetSchemaError from generate_p1 must map to exit 1 + FAIL message."""
    from aletheia_lab.baseline.loader import DatasetSchemaError

    def _raise(*_a: object, **_kw: object) -> None:
        raise DatasetSchemaError("processed CSV has wrong schema")

    monkeypatch.setattr("aletheia_lab.benchmark.cli.generate_p1", _raise)
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1",
            "--config",
            str(_np(tmp_path, "cfg.yaml")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_generate_p1_validation_failure_after_generation_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When generate_p1 succeeds but validate_p1_cases returns passed=False, exit 1."""
    from aletheia_lab.benchmark.case_validation import ValidationReport

    monkeypatch.setattr(
        "aletheia_lab.benchmark.cli.generate_p1",
        lambda *_a, **_kw: {"leakage_total": 0},
    )
    monkeypatch.setattr(
        "aletheia_lab.benchmark.cli.validate_p1_cases",
        lambda *_a: ValidationReport(passed=False, errors=["expected 15 cases, got 0"]),
    )
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1",
            "--config",
            str(_np(tmp_path, "cfg.yaml")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1 — validation failure boundary
# ---------------------------------------------------------------------------


def test_generated_p1_validation_commands_pass(
    generated_p1_assets: tuple[Path, Path],
) -> None:
    """Exercise both validators against one shared synthetic pipeline lifecycle."""

    cases_dir, store_dir = generated_p1_assets
    cases_result = _runner.invoke(
        benchmark_app,
        ["validate-p1", "--cases-dir", str(cases_dir)],
    )
    assert cases_result.exit_code == 0, cases_result.output
    assert "Validation PASS" in cases_result.output
    assert '"passed": true' in cases_result.output

    evidence_result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-evidence",
            "--store-dir",
            str(store_dir),
            "--cases-dir",
            str(cases_dir),
        ],
    )
    assert evidence_result.exit_code == 0, evidence_result.output
    assert "Evidence technical validation PASS" in evidence_result.output
    assert '"passed": true' in evidence_result.output


def test_validate_p1_empty_cases_dir_exits_nonzero(tmp_path: Path) -> None:
    """An empty cases directory has 0 of 15 required cases → validation fails."""
    cases_dir = tmp_path / "empty_cases"
    cases_dir.mkdir()
    result = _runner.invoke(
        benchmark_app,
        ["validate-p1", "--cases-dir", str(cases_dir)],
    )
    assert result.exit_code == 1
    assert "Validation FAILED" in result.output


def test_validate_p1_missing_cases_dir_exits_nonzero(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        ["validate-p1", "--cases-dir", str(_np(tmp_path, "no_dir"))],
    )
    assert result.exit_code == 1
    assert "Validation FAILED" in result.output


# ---------------------------------------------------------------------------
# generate-p1-evidence — exception boundary (FileNotFoundError via OSError)
# ---------------------------------------------------------------------------


def test_generate_p1_evidence_missing_cases_dir_exits_one(tmp_path: Path) -> None:
    """generate_p1_evidence_store raises when cases_dir does not exist → exit 1."""
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1-evidence",
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_generate_p1_evidence_file_exists_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generate_p1_evidence_store raises FileExistsError for immutable output → exit 1."""

    def _raise(*_a: object, **_kw: object) -> None:
        raise FileExistsError("output already exists and is immutable")

    monkeypatch.setattr("aletheia_lab.benchmark.cli.generate_p1_evidence_store", _raise)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1-evidence",
            "--cases-dir",
            str(cases_dir),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-evidence — validation failure boundary
# ---------------------------------------------------------------------------


def test_validate_p1_evidence_missing_store_exits_nonzero(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-evidence",
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
        ],
    )
    assert result.exit_code == 1
    assert "Evidence validation FAILED" in result.output


# ---------------------------------------------------------------------------
# run-p1-pilot-mock — exception boundary (FileNotFoundError is subclass of OSError)
# ---------------------------------------------------------------------------


def test_run_p1_pilot_mock_missing_store_exits_one(tmp_path: Path) -> None:
    """run_p1_matched_pilot raises FileNotFoundError (caught as OSError) → exit 1."""
    result = _runner.invoke(
        benchmark_app,
        [
            "run-p1-pilot-mock",
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_run_p1_pilot_mock_file_exists_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FileExistsError from run_p1_matched_pilot (immutable output) → exit 1."""

    def _raise(*_a: object, **_kw: object) -> None:
        raise FileExistsError("pilot output directory already exists")

    monkeypatch.setattr("aletheia_lab.benchmark.cli.run_p1_matched_pilot", _raise)
    result = _runner.invoke(
        benchmark_app,
        [
            "run-p1-pilot-mock",
            "--store-dir",
            str(_np(tmp_path, "store")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-pilot — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_pilot_missing_dirs_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-pilot",
            "--output-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# preflight-p1-openai — exception boundary
# ---------------------------------------------------------------------------


def test_preflight_p1_openai_missing_store_exits_one(tmp_path: Path) -> None:
    """Missing store or config triggers FileNotFoundError inside the try block → exit 1."""
    result = _runner.invoke(
        benchmark_app,
        [
            "preflight-p1-openai",
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--output",
            str(_np(tmp_path, "out.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# run-p1-openai-smoke — exception boundary (no API key, no network)
# ---------------------------------------------------------------------------


def test_run_p1_openai_smoke_missing_config_exits_one(tmp_path: Path) -> None:
    """A nonexistent config triggers FileNotFoundError in the authorization phase."""
    result = _runner.invoke(
        benchmark_app,
        [
            "run-p1-openai-smoke",
            "--store-dir",
            str(_np(tmp_path, "store")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "preflight.json")),
            "--output-dir",
            str(_np(tmp_path, "out")),
            "--confirm-preflight-sha256",
            "a" * 64,
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-openai-smoke — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_openai_smoke_missing_dirs_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-openai-smoke",
            "--output-dir",
            str(_np(tmp_path, "no_dir")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# run-p1-openai-full — exception boundary (no API key, no network)
# ---------------------------------------------------------------------------


def test_run_p1_openai_full_missing_config_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "run-p1-openai-full",
            "--store-dir",
            str(_np(tmp_path, "store")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "preflight.json")),
            "--output-dir",
            str(_np(tmp_path, "out")),
            "--confirm-preflight-sha256",
            "a" * 64,
            "--confirm-estimated-full-retry-ceiling-usd",
            "1.0",
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-openai-full — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_openai_full_missing_dirs_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-openai-full",
            "--output-dir",
            str(_np(tmp_path, "no_dir")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# evaluate-p1-pilot — exception boundary
# ---------------------------------------------------------------------------


def test_evaluate_p1_pilot_missing_dirs_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "evaluate-p1-pilot",
            "--pilot-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--output",
            str(_np(tmp_path, "report.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# freeze-p1-result — exception boundary
# ---------------------------------------------------------------------------


def test_freeze_p1_result_missing_paths_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "freeze-p1-result",
            "--pilot-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
            "--evaluation",
            str(_np(tmp_path, "no_eval.json")),
            "--output",
            str(_np(tmp_path, "lock.json")),
            "--execution-commit-sha",
            "a" * 40,
            "--evaluation-commit-sha",
            "b" * 40,
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-result-lock — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_result_lock_missing_paths_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-result-lock",
            "--lock",
            str(_np(tmp_path, "no_lock.json")),
            "--pilot-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
            "--evaluation",
            str(_np(tmp_path, "no_eval.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# generate-p1-closeout — exception boundary
# ---------------------------------------------------------------------------


def test_generate_p1_closeout_missing_paths_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "generate-p1-closeout",
            "--lock",
            str(_np(tmp_path, "no_lock.json")),
            "--pilot-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
            "--evaluation",
            str(_np(tmp_path, "no_eval.json")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-closeout — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_closeout_missing_paths_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-closeout",
            "--lock",
            str(_np(tmp_path, "no_lock.json")),
            "--pilot-dir",
            str(_np(tmp_path, "no_pilot")),
            "--store-dir",
            str(_np(tmp_path, "no_store")),
            "--cases-dir",
            str(_np(tmp_path, "no_cases")),
            "--config",
            str(_np(tmp_path, "no_config.yaml")),
            "--preflight",
            str(_np(tmp_path, "no_preflight.json")),
            "--evaluation",
            str(_np(tmp_path, "no_eval.json")),
            "--output-dir",
            str(_np(tmp_path, "out")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# validate-p1-final — exception boundary
# ---------------------------------------------------------------------------


def test_validate_p1_final_missing_paths_exits_one(tmp_path: Path) -> None:
    result = _runner.invoke(
        benchmark_app,
        [
            "validate-p1-final",
            "--record",
            str(_np(tmp_path, "no_record.json")),
            "--machine-result",
            str(_np(tmp_path, "no_machine.json")),
            "--result-lock",
            str(_np(tmp_path, "no_lock.json")),
            "--evidence-review",
            str(_np(tmp_path, "no_ev_review.json")),
            "--evidence-blind-packet",
            str(_np(tmp_path, "no_ev_blind.json")),
            "--evidence-mapping-packet",
            str(_np(tmp_path, "no_ev_map.json")),
            "--diagnosis-review",
            str(_np(tmp_path, "no_diag_review.json")),
            "--diagnosis-blind-packet",
            str(_np(tmp_path, "no_diag_blind.json")),
            "--diagnosis-mapping-packet",
            str(_np(tmp_path, "no_diag_map.json")),
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output
