"""Tamper tests: the validator (or schema) must catch a doctored case set."""

from __future__ import annotations

import json

from aletheia_lab.benchmark.case_validation import validate_p1_cases
from aletheia_lab.benchmark.case_writer import dumps_deterministic, sha256_file
from aletheia_lab.benchmark.generator import generate_p1


def _generate(config, out):
    generate_p1(config, out)
    assert validate_p1_cases(out).passed  # clean set passes
    return out


def _retamper(case_dir, filename, mutate):
    """Mutate a JSON payload and re-sync its checksum so integrity alone passes."""

    path = case_dir / filename
    data = json.loads(path.read_text("utf-8"))
    mutate(data)
    path.write_text(dumps_deterministic(data), encoding="utf-8")
    checks_path = case_dir / "checksums.json"
    checks = json.loads(checks_path.read_text("utf-8"))
    if filename in checks:
        checks[filename] = sha256_file(path)
        checks_path.write_text(dumps_deterministic(checks), encoding="utf-8")


def test_tamper_injection_fault_type_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "injection.json",
        lambda d: d.__setitem__("fault_type", "label_noise"),
    )
    assert not validate_p1_cases(out).passed


def test_tamper_psi_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "injection.json",
        lambda d: d.__setitem__("psi", d["psi"] + 1.0),
    )
    report = validate_p1_cases(out)
    assert not report.passed


def test_tamper_achieved_distribution_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "injection.json",
        lambda d: d.__setitem__("achieved_distribution", {"Month-to-month": 1.0}),
    )
    assert not validate_p1_cases(out).passed


def test_tamper_metric_delta_breaks_schema_or_validator(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    case_dir = out / "p1-data-drift-01-full"

    def _bad_delta(data):
        data["observable_signals"]["baseline_metric_reference"]["delta"] += 0.05

    _retamper(case_dir, "manifest.json", _bad_delta)
    # MetricComparison requires delta == observed - reference, so this fails to load.
    assert not validate_p1_cases(out).passed


def test_swap_injection_between_settings_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    other = json.loads((out / "p1-data-drift-02-full" / "injection.json").read_text("utf-8"))
    _retamper(out / "p1-data-drift-01-full", "injection.json", lambda d: d.update(other))
    assert not validate_p1_cases(out).passed


def test_dataset_hash_inconsistency_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("dataset_sha256", "f" * 64),
    )
    assert not validate_p1_cases(out).passed


def test_wrong_severity_rank_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full", "manifest.json", lambda d: d.__setitem__("severity_rank", 9)
    )
    assert not validate_p1_cases(out).passed


def test_three_conditions_share_injection_and_ground_truth(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    for index in range(1, 6):
        inj = {
            (out / f"p1-data-drift-{index:02d}-{slug}" / "injection.json").read_bytes()
            for slug in ("full", "missing-key", "noisy")
        }
        gt = {
            (out / f"p1-data-drift-{index:02d}-{slug}" / "ground_truth.json").read_bytes()
            for slug in ("full", "missing-key", "noisy")
        }
        assert len(inj) == 1 and len(gt) == 1
