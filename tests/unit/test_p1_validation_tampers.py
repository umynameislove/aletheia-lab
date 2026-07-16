"""Tamper tests: the validator (or schema) must catch a doctored case set."""

from __future__ import annotations

import json
import shutil

import pytest

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


def _assert_rejected_for(out, expected):
    report = validate_p1_cases(out)
    assert not report.passed
    assert any(expected in error for error in report.errors), report.errors


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
    # ObservedOutcome requires delta == observed - reference, so this fails to load.
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


# --- Commit 2: severity, condition-completeness and distractor tampering ---


def test_tamper_one_condition_severity_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("severity_rank", d["severity_rank"] + 100),
    )
    assert not validate_p1_cases(out).passed


def test_swap_ranks_between_settings_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    # Find two settings with different ranks and swap all three conditions' ranks,
    # so the rank set stays {1..5} but no longer follows PSI order.
    from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only

    ranks = {}
    for i in range(1, 6):
        ranks[i] = load_case_dir_schema_only(
            out / f"p1-data-drift-{i:02d}-full"
        ).manifest.severity_rank
    a, b = 1, 2
    ra, rb = ranks[a], ranks[b]
    for slug in ("full", "missing-key", "noisy"):
        _retamper(
            out / f"p1-data-drift-{a:02d}-{slug}",
            "manifest.json",
            lambda d, r=rb: d.__setitem__("severity_rank", r),
        )
        _retamper(
            out / f"p1-data-drift-{b:02d}-{slug}",
            "manifest.json",
            lambda d, r=ra: d.__setitem__("severity_rank", r),
        )
    report = validate_p1_cases(out)
    assert not report.passed
    assert report.checks.get("severity_ranks_match_psi_order") is False


def test_missing_a_condition_is_caught(p1_generator_config, tmp_path):
    import shutil

    out = _generate(p1_generator_config, tmp_path / "c")
    shutil.rmtree(out / "p1-data-drift-01-missing-key")
    assert not validate_p1_cases(out).passed


def test_tamper_distractor_psi_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    case_dir = out / "p1-data-drift-01-noisy"

    def _bump(data):
        data["observable_signals"]["distractor_comparisons"][0]["psi"] = 0.009

    # Tamper the distractor PSI in both the manifest and the diagnosis input.
    _retamper(case_dir, "manifest.json", _bump)
    _retamper(case_dir, "diagnosis_input.json", _bump)
    # DistractorComparison recomputes PSI from its distributions, so this fails.
    assert not validate_p1_cases(out).passed


def test_nonfinite_ground_truth_delta_on_all_conditions_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    for slug in ("full", "missing-key", "noisy"):
        _retamper(
            out / f"p1-data-drift-01-{slug}",
            "ground_truth.json",
            lambda d: d["observed_outcome"].__setitem__("delta", float("inf")),
        )
    assert not validate_p1_cases(out).passed


# --- Commit 2 v2: extra-field leak, synced-fake-PSI, schema_version ---


def test_extra_field_in_diagnosis_input_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "diagnosis_input.json",
        lambda d: d.__setitem__("cause_label", "data_drift"),
    )
    assert not validate_p1_cases(out).passed


def test_extra_field_nested_in_observable_signals_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "diagnosis_input.json",
        lambda d: d["observable_signals"].__setitem__("smuggled", "data_drift"),
    )
    assert not validate_p1_cases(out).passed


def test_synced_fake_primary_psi_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    for slug in ("full", "missing-key", "noisy"):
        case_dir = out / f"p1-data-drift-01-{slug}"
        _retamper(case_dir, "injection.json", lambda d: d.__setitem__("psi", 0.999))
        manifest = json.loads((case_dir / "manifest.json").read_text())
        if manifest["observable_signals"].get("psi") is not None:
            _retamper(
                case_dir,
                "manifest.json",
                lambda d: d["observable_signals"].__setitem__("psi", 0.999),
            )
            _retamper(
                case_dir,
                "diagnosis_input.json",
                lambda d: d["observable_signals"].__setitem__("psi", 0.999),
            )
    # InjectionProvenance recomputes PSI from the distributions, so the fake fails.
    assert not validate_p1_cases(out).passed


def test_wrong_schema_version_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("schema_version", "p1-cases/999"),
    )
    assert not validate_p1_cases(out).passed


# --- Commit 2 v3: candidate_feature, sample_size, target-all-zero, expected_behavior ---


@pytest.mark.parametrize("slug", ["full", "missing-key", "noisy"])
def test_candidate_feature_mismatch_is_caught(p1_generator_config, tmp_path, slug):
    out = _generate(p1_generator_config, tmp_path / "c")
    case_dir = out / f"p1-data-drift-01-{slug}"
    for name in ("manifest.json", "diagnosis_input.json"):
        _retamper(
            case_dir,
            name,
            lambda d: d["observable_signals"].__setitem__("candidate_feature", "PaymentMethod"),
        )
    _assert_rejected_for(out, "candidate_feature != injection.feature")


def test_negative_sample_size_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    case_dir = out / "p1-data-drift-01-full"
    for name in ("manifest.json", "diagnosis_input.json"):
        _retamper(case_dir, name, lambda d: d["observable_signals"].__setitem__("sample_size", -99))
    _assert_rejected_for(out, "sample_size must be positive")


def test_positive_sample_size_mismatch_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    case_dir = out / "p1-data-drift-01-full"
    for name in ("manifest.json", "diagnosis_input.json"):
        _retamper(case_dir, name, lambda d: d["observable_signals"].__setitem__("sample_size", 999))
    _assert_rejected_for(out, "sample_size != injection.output_size")


def test_all_zero_target_distribution_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    zeros = {"Month-to-month": 0.0, "One year": 0.0, "Two year": 0.0}
    for slug in ("full", "missing-key", "noisy"):
        case_dir = out / f"p1-data-drift-01-{slug}"
        _retamper(
            case_dir, "injection.json", lambda d: d.__setitem__("target_distribution", dict(zeros))
        )
        _retamper(
            case_dir,
            "manifest.json",
            lambda d: d["injection_parameters"].__setitem__("target_distribution", dict(zeros)),
        )
        _retamper(
            case_dir,
            "ground_truth.json",
            lambda d: d["injection_parameters"].__setitem__("target_distribution", dict(zeros)),
        )
    _assert_rejected_for(out, "target_distribution total must be positive")


def test_tampered_expected_diagnosis_behavior_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("expected_diagnosis_behavior", "confidently guess"),
    )
    _assert_rejected_for(out, "expected_diagnosis_behavior does not match")


def test_behavior_from_another_condition_is_caught(p1_generator_config, tmp_path):
    from aletheia_lab.benchmark.case_schema import EXPECTED_BEHAVIOR

    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("expected_diagnosis_behavior", EXPECTED_BEHAVIOR["noisy"]),
    )
    _assert_rejected_for(out, "expected_diagnosis_behavior does not match")


def test_missing_expected_diagnosis_behavior_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.pop("expected_diagnosis_behavior"),
    )
    _assert_rejected_for(out, "expected_diagnosis_behavior")


# --- Commit 3: family identity and derived failure semantics ---


def _case_for_eligibility(out, classification):
    from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only

    for index in range(1, 6):
        case_dir = out / f"p1-data-drift-{index:02d}-full"
        gt = load_case_dir_schema_only(case_dir).ground_truth
        if gt.failure_eligibility.classification == classification:
            return index
    raise AssertionError(f"no case with eligibility {classification}")


def test_one_sibling_family_id_change_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "manifest.json",
        lambda d: d.__setitem__("case_family_id", "p1-family-" + "f" * 64),
    )
    _assert_rejected_for(out, "case_family_id does not match canonical injection identity")


def test_two_families_forced_to_same_family_id_are_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    family_one = json.loads((out / "p1-data-drift-01-full" / "manifest.json").read_text("utf-8"))[
        "case_family_id"
    ]
    for slug in ("full", "missing-key", "noisy"):
        _retamper(
            out / f"p1-data-drift-02-{slug}",
            "manifest.json",
            lambda d: d.__setitem__("case_family_id", family_one),
        )
    _assert_rejected_for(out, "case_family_id does not match canonical injection identity")


def test_sibling_injection_provenance_difference_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "injection.json",
        lambda d: d.__setitem__("injector", "tampered.Injector"),
    )
    report = validate_p1_cases(out)
    assert not report.passed
    assert report.checks["conditions_share_injection_and_ground_truth"] is False


def test_outcome_class_changed_without_values_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "ground_truth.json",
        lambda d: d["observed_outcome"].__setitem__("classification", "stable"),
    )
    _assert_rejected_for(out, "classification must be derived from delta")


def test_injected_change_cannot_be_relabelled_as_failure_claim(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "ground_truth.json",
        lambda d: d["injected_change"].__setitem__("feature", "PaymentMethod"),
    )
    _assert_rejected_for(out, "injected_change does not match injection provenance")


def test_synced_fake_eligibility_is_recomputed_and_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    eligible_index = _case_for_eligibility(out, "eligible_failure")

    def _fake_control(data):
        data["failure_eligibility"]["classification"] = "stable_control"
        data["hidden_failure_cause"] = None

    for slug in ("full", "missing-key", "noisy"):
        _retamper(
            out / f"p1-data-drift-{eligible_index:02d}-{slug}",
            "ground_truth.json",
            _fake_control,
        )
    _assert_rejected_for(out, "failure eligibility must be recomputed")


@pytest.mark.parametrize("classification", ["stable_control", "improvement_control"])
def test_control_with_hidden_failure_cause_is_caught(p1_generator_config, tmp_path, classification):
    out = _generate(p1_generator_config, tmp_path / "c")
    index = _case_for_eligibility(out, classification)

    def _add_cause(data):
        data["hidden_failure_cause"] = {
            "cause_label": "data_drift",
            "causal_mechanism": "categorical_distribution_shift",
            "affected_components": ["Contract"],
            "expected_symptoms": ["metric_regression", "distribution_shift:Contract"],
        }

    _retamper(out / f"p1-data-drift-{index:02d}-full", "ground_truth.json", _add_cause)
    _assert_rejected_for(out, "control outcome must not assert hidden_failure_cause")


def test_eligible_regression_without_hidden_cause_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    index = _case_for_eligibility(out, "eligible_failure")
    _retamper(
        out / f"p1-data-drift-{index:02d}-full",
        "ground_truth.json",
        lambda d: d.__setitem__("hidden_failure_cause", None),
    )
    _assert_rejected_for(out, "eligible failure requires hidden_failure_cause")


def test_hidden_failure_cause_must_match_measured_intervention(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    index = _case_for_eligibility(out, "eligible_failure")
    _retamper(
        out / f"p1-data-drift-{index:02d}-full",
        "ground_truth.json",
        lambda d: d["hidden_failure_cause"].__setitem__("causal_mechanism", "label_corruption"),
    )
    _assert_rejected_for(out, "hidden failure cause does not match measured intervention")


def test_eligibility_policy_version_mismatch_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    _retamper(
        out / "p1-data-drift-01-full",
        "ground_truth.json",
        lambda d: d["failure_eligibility"].__setitem__(
            "policy_version", "accuracy-regression/v999"
        ),
    )
    _assert_rejected_for(out, "unexpected eligibility policy")


def test_duplicate_context_directory_is_caught(p1_generator_config, tmp_path):
    out = _generate(p1_generator_config, tmp_path / "c")
    shutil.copytree(out / "p1-data-drift-01-full", out / "duplicate-full-context")
    report = validate_p1_cases(out)
    assert not report.passed
    assert report.checks["exactly_15_cases"] is False
    assert report.checks["unique_case_ids"] is False
