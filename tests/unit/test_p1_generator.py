"""Generator tests on a synthetic dataset (no network, no real data)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.generator import GeneratorConfigError, generate_p1
from aletheia_lab.cli import app

runner = CliRunner()


def test_generates_exactly_15_cases(p1_generator_config, tmp_path):
    summary = generate_p1(p1_generator_config, tmp_path / "cases")
    assert summary["case_count"] == 15
    assert len(set(summary["case_ids"])) == 15


def test_matrix_5x3_and_condition_counts(p1_generator_config, tmp_path):
    summary = generate_p1(p1_generator_config, tmp_path / "cases")
    assert summary["condition_counts"] == {"full": 5, "missing_key": 5, "noisy": 5}
    assert len({s["injection_id"] for s in summary["settings"]}) == 5


def test_case_ids_are_deterministic(p1_generator_config, tmp_path):
    summary = generate_p1(p1_generator_config, tmp_path / "cases")
    assert "p1-data-drift-01-full" in summary["case_ids"]
    assert "p1-data-drift-05-noisy" in summary["case_ids"]


def test_two_runs_identical_manifests_and_checksums(p1_generator_config, tmp_path):
    generate_p1(p1_generator_config, tmp_path / "a")
    generate_p1(p1_generator_config, tmp_path / "b")
    for case in sorted(p.name for p in (tmp_path / "a").iterdir()):
        for name in (
            "manifest.json",
            "diagnosis_input.json",
            "ground_truth.json",
            "injection.json",
            "checksums.json",
        ):
            assert (tmp_path / "a" / case / name).read_bytes() == (
                tmp_path / "b" / case / name
            ).read_bytes()


def test_five_settings_have_different_signals(p1_generator_config, tmp_path):
    summary = generate_p1(p1_generator_config, tmp_path / "cases")
    psis = [s["psi"] for s in summary["settings"]]
    assert len(set(round(p, 6) for p in psis)) == 5  # meaningfully different drift


def test_generator_validation_passes_and_zero_leakage(p1_generator_config, tmp_path):
    out = tmp_path / "cases"
    generate_p1(p1_generator_config, out)
    report = validate_p1_cases(out)
    assert report.passed, report.as_dict()
    assert report.leakage_total == 0


def test_invalid_config_fails_closed(tmp_path):
    # project.yaml with no benchmark/fault_types.yaml alongside it.
    cfg = tmp_path / "project.yaml"
    (tmp_path / "data" / "processed").mkdir(parents=True)
    cfg.write_text(
        "dataset:\n  id: telco_customer_churn\n"
        f"paths:\n  processed_data: {(tmp_path / 'data' / 'processed').as_posix()}\n",
        encoding="utf-8",
    )
    with pytest.raises(GeneratorConfigError):
        generate_p1(cfg, tmp_path / "cases")


def test_cli_generate_and_validate(p1_generator_config, tmp_path):
    out = tmp_path / "cases"
    gen = runner.invoke(
        app,
        [
            "benchmark",
            "generate-p1",
            "--config",
            str(p1_generator_config),
            "--output-dir",
            str(out),
        ],
    )
    assert gen.exit_code == 0, gen.output
    summary = json.loads(gen.output[gen.output.index("{") : gen.output.rindex("}") + 1])
    assert summary["case_count"] == 15 and summary["leakage_total"] == 0
    val = runner.invoke(app, ["benchmark", "validate-p1", "--cases-dir", str(out)])
    assert val.exit_code == 0, val.output
