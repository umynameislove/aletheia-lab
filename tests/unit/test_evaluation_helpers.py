"""Tests for leaf evaluation helper modules (Nhóm B).

Covers behavioral contracts of:
  - benchmark/fault_types.py   — FaultType enum values, StrEnum contract
  - benchmark/validators.py    — unique_case_ids, has_required_fault_coverage
  - diagnosis/variants.py      — DiagnosisVariant compatibility alias
  - evaluation/abstention.py   — detects_abstention marker matching
  - evaluation/agreement.py    — cohens_kappa (completes missing branches)
  - evaluation/correctness.py  — normalized_label_match
  - evaluation/faithfulness.py — claim_support_ratio
  - evaluation/judge.py        — JudgeResult schema validation
  - evaluation/metrics.py      — mean success path, all divergence_label branches
  - evaluation/stats.py        — bootstrap_mean_ci invariants and determinism

All tests are offline, deterministic, and require no external I/O.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.benchmark.fault_types import FaultType
from aletheia_lab.benchmark.manifest import BenchmarkCase
from aletheia_lab.benchmark.validators import has_required_fault_coverage, unique_case_ids
from aletheia_lab.diagnosis.schema import PilotVariant
from aletheia_lab.diagnosis.variants import DiagnosisVariant
from aletheia_lab.evaluation.abstention import ABSTENTION_MARKERS, detects_abstention
from aletheia_lab.evaluation.agreement import cohens_kappa
from aletheia_lab.evaluation.correctness import normalized_label_match
from aletheia_lab.evaluation.faithfulness import claim_support_ratio
from aletheia_lab.evaluation.judge import JudgeResult
from aletheia_lab.evaluation.metrics import divergence_label, mean
from aletheia_lab.evaluation.stats import bootstrap_mean_ci

# ---------------------------------------------------------------------------
# Fixture helper — BenchmarkCase factory via the minimal fixture template
# ---------------------------------------------------------------------------

_MINIMAL_CASE_PATH = Path("tests") / "fixtures" / "minimal_case.json"
_MINIMAL_CASE_DATA: dict[str, object] = json.loads(
    _MINIMAL_CASE_PATH.read_text(encoding="utf-8")
)


def _make_case(case_id: str = "case-01", fault_type: str = "data_drift") -> BenchmarkCase:
    """Return a valid BenchmarkCase with overridden case_id and fault_type."""
    data = dict(_MINIMAL_CASE_DATA)
    data["case_id"] = case_id
    data["fault_type"] = fault_type
    return BenchmarkCase.model_validate(data)


# ===========================================================================
# benchmark/fault_types.py — FaultType StrEnum
# ===========================================================================


def test_fault_type_string_values_are_exact() -> None:
    """Each FaultType member must serialize to its declared lowercase string."""
    assert FaultType.DATA_DRIFT == "data_drift"
    assert FaultType.LABEL_NOISE == "label_noise"
    assert FaultType.PREPROCESSING_BUG == "preprocessing_bug"
    assert FaultType.TRAIN_EVAL_MISMATCH == "train_eval_mismatch"
    assert FaultType.PROMPT_REGRESSION == "prompt_regression"


def test_fault_type_is_str_enum() -> None:
    """FaultType values must compare equal to plain strings (StrEnum contract)."""
    assert str(FaultType.DATA_DRIFT) == FaultType.DATA_DRIFT
    assert isinstance(FaultType.DATA_DRIFT, str)


def test_fault_type_rejects_unknown_value() -> None:
    """Constructing FaultType from an unknown string must raise ValueError."""
    with pytest.raises(ValueError):
        FaultType("unknown_fault_type")


def test_fault_type_membership_count() -> None:
    """Exactly 5 fault types are defined; guard against silent addition."""
    assert len(list(FaultType)) == 5


# ===========================================================================
# benchmark/validators.py — unique_case_ids
# ===========================================================================


def test_unique_case_ids_empty_iterable_returns_true() -> None:
    """An empty iterable has no duplicates."""
    assert unique_case_ids([]) is True


def test_unique_case_ids_single_case_is_unique() -> None:
    assert unique_case_ids([_make_case("case-01")]) is True


def test_unique_case_ids_distinct_cases_are_unique() -> None:
    assert unique_case_ids([_make_case("case-01"), _make_case("case-02")]) is True


def test_unique_case_ids_detects_duplicate() -> None:
    """Two cases sharing the same case_id must be flagged as non-unique."""
    assert unique_case_ids([_make_case("dup"), _make_case("dup")]) is False


def test_unique_case_ids_stops_at_first_duplicate() -> None:
    """Duplicate detection must work even with a mix of unique and duplicate IDs."""
    cases = [_make_case("a"), _make_case("b"), _make_case("a"), _make_case("c")]
    assert unique_case_ids(cases) is False


# ===========================================================================
# benchmark/validators.py — has_required_fault_coverage
# ===========================================================================


def test_has_required_fault_coverage_empty_returns_false() -> None:
    """No cases → counts dict is empty → bool({}) is False."""
    assert has_required_fault_coverage([], minimum_per_fault=1) is False


def test_has_required_fault_coverage_single_fault_meets_minimum() -> None:
    cases = [_make_case("c1", "data_drift"), _make_case("c2", "data_drift")]
    assert has_required_fault_coverage(cases, minimum_per_fault=2) is True


def test_has_required_fault_coverage_single_fault_below_minimum() -> None:
    cases = [_make_case("c1", "data_drift")]
    assert has_required_fault_coverage(cases, minimum_per_fault=2) is False


def test_has_required_fault_coverage_mixed_faults_all_meet_minimum() -> None:
    cases = [
        _make_case("c1", "data_drift"),
        _make_case("c2", "data_drift"),
        _make_case("c3", "label_noise"),
        _make_case("c4", "label_noise"),
    ]
    assert has_required_fault_coverage(cases, minimum_per_fault=2) is True


def test_has_required_fault_coverage_one_fault_below_minimum_fails() -> None:
    """If any fault type falls below minimum, the whole check fails."""
    cases = [
        _make_case("c1", "data_drift"),
        _make_case("c2", "data_drift"),
        _make_case("c3", "label_noise"),  # only 1 for label_noise
    ]
    assert has_required_fault_coverage(cases, minimum_per_fault=2) is False


def test_has_required_fault_coverage_minimum_one_accepts_singletons() -> None:
    cases = [_make_case("c1", "data_drift"), _make_case("c2", "label_noise")]
    assert has_required_fault_coverage(cases, minimum_per_fault=1) is True


# ===========================================================================
# diagnosis/variants.py — DiagnosisVariant alias
# ===========================================================================


def test_diagnosis_variant_is_exact_alias_for_pilot_variant() -> None:
    """DiagnosisVariant must be the same class object as PilotVariant."""
    assert DiagnosisVariant is PilotVariant


def test_diagnosis_variant_b1_plain_string_value() -> None:
    assert DiagnosisVariant.B1_PLAIN == "b1_plain"


def test_diagnosis_variant_a3_evidence_contract_string_value() -> None:
    assert DiagnosisVariant.A3_EVIDENCE_CONTRACT == "a3_evidence_contract"


def test_diagnosis_variant_members_match_pilot_variant_members() -> None:
    """Alias must expose exactly the same set of members as PilotVariant."""
    assert set(DiagnosisVariant) == set(PilotVariant)


# ===========================================================================
# evaluation/abstention.py — detects_abstention
# ===========================================================================


def test_detects_abstention_insufficient_evidence_marker() -> None:
    assert detects_abstention("insufficient evidence for a conclusion") is True


def test_detects_abstention_not_enough_evidence_marker() -> None:
    assert detects_abstention("not enough evidence to diagnose") is True


def test_detects_abstention_cannot_determine_marker() -> None:
    assert detects_abstention("I cannot determine the root cause") is True


def test_detects_abstention_vietnamese_marker_1() -> None:
    assert detects_abstention("không đủ bằng chứng để kết luận") is True


def test_detects_abstention_vietnamese_marker_2() -> None:
    assert detects_abstention("chưa đủ bằng chứng") is True


def test_detects_abstention_case_insensitive() -> None:
    """detects_abstention must normalize via casefold before matching."""
    assert detects_abstention("INSUFFICIENT EVIDENCE observed") is True


def test_detects_abstention_confident_response_returns_false() -> None:
    assert detects_abstention("The root cause is a distribution shift in Contract.") is False


def test_detects_abstention_empty_string_returns_false() -> None:
    assert detects_abstention("") is False


def test_abstention_markers_tuple_is_non_empty() -> None:
    """Guard against accidentally emptying the markers tuple."""
    assert len(ABSTENTION_MARKERS) > 0
    assert all(isinstance(m, str) for m in ABSTENTION_MARKERS)


# ===========================================================================
# evaluation/agreement.py — cohens_kappa (completes missing branches)
# ===========================================================================


def test_cohens_kappa_length_mismatch_raises_value_error() -> None:
    """Unequal-length rater sequences must raise ValueError."""
    with pytest.raises(ValueError, match="same length"):
        cohens_kappa(["a", "b"], ["a"])


def test_cohens_kappa_empty_sequences_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not be empty"):
        cohens_kappa([], [])


def test_cohens_kappa_single_label_both_raters_returns_one() -> None:
    """When both raters use exactly one unique label, expected agreement is 1.0,
    and the function must return 1.0 via the shortcut branch (not a 0/0 division)."""
    result = cohens_kappa(["a", "a", "a"], ["a", "a", "a"])
    assert result == pytest.approx(1.0)


def test_cohens_kappa_complete_disagreement_two_categories() -> None:
    """Perfectly anti-correlated binary raters → kappa = -1.0."""
    # observed = 0.0, expected = 0.5 → kappa = (0 - 0.5) / (1 - 0.5) = -1.0
    result = cohens_kappa(["a", "b", "a", "b"], ["b", "a", "b", "a"])
    assert result == pytest.approx(-1.0)


def test_cohens_kappa_chance_agreement_yields_zero() -> None:
    """When observed == expected, kappa is 0.0 (no better than chance)."""
    # rater_a = ["a","a","b"], rater_b = ["b","b","b"]
    # observed = 1/3, expected = 0*(2/3) + 1*(1/3) = 1/3 → kappa = 0.0
    result = cohens_kappa(["a", "a", "b"], ["b", "b", "b"])
    assert result == pytest.approx(0.0)


# ===========================================================================
# evaluation/correctness.py — normalized_label_match
# ===========================================================================


def test_normalized_label_match_identical_labels_returns_one() -> None:
    assert normalized_label_match("data_drift", "data_drift") == pytest.approx(1.0)


def test_normalized_label_match_different_labels_returns_zero() -> None:
    assert normalized_label_match("data_drift", "label_noise") == pytest.approx(0.0)


def test_normalized_label_match_case_insensitive() -> None:
    assert normalized_label_match("DATA_DRIFT", "data_drift") == pytest.approx(1.0)


def test_normalized_label_match_whitespace_stripped() -> None:
    assert normalized_label_match("  data_drift  ", "data_drift") == pytest.approx(1.0)


def test_normalized_label_match_mixed_case_and_whitespace() -> None:
    assert normalized_label_match("  Data_Drift  ", "DATA_DRIFT") == pytest.approx(1.0)


# ===========================================================================
# evaluation/faithfulness.py — claim_support_ratio
# ===========================================================================


def test_claim_support_ratio_empty_returns_zero() -> None:
    """Empty claim map must return 0.0 (no support)."""
    assert claim_support_ratio({}) == pytest.approx(0.0)


def test_claim_support_ratio_all_supported_returns_one() -> None:
    assert claim_support_ratio({"c1": True, "c2": True, "c3": True}) == pytest.approx(1.0)


def test_claim_support_ratio_none_supported_returns_zero() -> None:
    assert claim_support_ratio({"c1": False, "c2": False}) == pytest.approx(0.0)


def test_claim_support_ratio_half_supported() -> None:
    assert claim_support_ratio({"c1": True, "c2": False}) == pytest.approx(0.5)


def test_claim_support_ratio_two_thirds_supported() -> None:
    result = claim_support_ratio({"c1": True, "c2": True, "c3": False})
    assert result == pytest.approx(2.0 / 3.0)


def test_claim_support_ratio_single_supported_claim() -> None:
    assert claim_support_ratio({"only": True}) == pytest.approx(1.0)


def test_claim_support_ratio_single_unsupported_claim() -> None:
    assert claim_support_ratio({"only": False}) == pytest.approx(0.0)


# ===========================================================================
# evaluation/judge.py — JudgeResult schema validation
# ===========================================================================


def _make_judge_result(**overrides: object) -> JudgeResult:
    base: dict[str, object] = {
        "case_id": "case-01",
        "variant": "b1_plain",
        "correctness": 0.8,
        "faithfulness": 0.7,
        "abstention": 0.0,
        "judge_id": "judge-rule-v1",
    }
    base.update(overrides)
    return JudgeResult.model_validate(base)


def test_judge_result_valid_construction() -> None:
    result = _make_judge_result()
    assert result.case_id == "case-01"
    assert result.variant == "b1_plain"
    assert result.correctness == pytest.approx(0.8)
    assert result.faithfulness == pytest.approx(0.7)
    assert result.abstention == pytest.approx(0.0)
    assert result.judge_id == "judge-rule-v1"
    assert result.notes is None


def test_judge_result_optional_notes_accepted() -> None:
    result = _make_judge_result(notes="borderline case")
    assert result.notes == "borderline case"


def test_judge_result_boundary_scores_accepted() -> None:
    """Boundary values 0.0 and 1.0 must be accepted for all score fields."""
    result = _make_judge_result(correctness=0.0, faithfulness=1.0, abstention=0.0)
    assert result.correctness == pytest.approx(0.0)
    assert result.faithfulness == pytest.approx(1.0)


def test_judge_result_correctness_above_one_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_judge_result(correctness=1.01)


def test_judge_result_faithfulness_below_zero_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_judge_result(faithfulness=-0.01)


def test_judge_result_abstention_above_one_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_judge_result(abstention=1.001)


def test_judge_result_missing_required_field_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeResult.model_validate(
            {"case_id": "c1", "variant": "b1_plain", "correctness": 0.5}
        )


# ===========================================================================
# evaluation/metrics.py — complete remaining branches
# ===========================================================================


def test_mean_with_multiple_values() -> None:
    """mean must return the arithmetic average for a non-empty iterable."""
    assert mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_mean_with_single_value() -> None:
    assert mean([5.0]) == pytest.approx(5.0)


def test_mean_generator_input() -> None:
    """mean must work with any iterable, not just lists."""
    assert mean(x * 1.0 for x in range(1, 4)) == pytest.approx(2.0)


def test_divergence_label_faithful_and_correct() -> None:
    assert divergence_label(True, True) == "faithful_and_correct"


def test_divergence_label_unfaithful_and_wrong() -> None:
    assert divergence_label(False, False) == "unfaithful_and_wrong"


# ===========================================================================
# evaluation/stats.py — bootstrap_mean_ci
# ===========================================================================


def test_bootstrap_mean_ci_empty_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        bootstrap_mean_ci([])


def test_bootstrap_mean_ci_single_value_returns_exact_mean() -> None:
    """With a single value, every bootstrap sample is identical → CI = [v, v]."""
    low, high = bootstrap_mean_ci([3.14], seed=0)
    assert low == pytest.approx(3.14)
    assert high == pytest.approx(3.14)


def test_bootstrap_mean_ci_all_equal_values_returns_exact_mean() -> None:
    """All-equal values yield zero variance → CI collapses to the mean."""
    low, high = bootstrap_mean_ci([2.0, 2.0, 2.0, 2.0], seed=0)
    assert low == pytest.approx(2.0)
    assert high == pytest.approx(2.0)


def test_bootstrap_mean_ci_bounds_ordered() -> None:
    """The lower bound must never exceed the upper bound."""
    low, high = bootstrap_mean_ci([1.0, 2.0, 3.0, 4.0, 5.0], seed=42)
    assert low <= high


def test_bootstrap_mean_ci_bounds_within_data_range() -> None:
    """CI bounds must lie within [min(values), max(values)]."""
    values = [0.1, 0.5, 0.9, 0.7, 0.3]
    low, high = bootstrap_mean_ci(values, seed=0)
    assert min(values) <= low
    assert high <= max(values)


def test_bootstrap_mean_ci_deterministic_with_fixed_seed() -> None:
    """Identical seed must produce identical results (numpy RNG contract)."""
    values = [0.2, 0.4, 0.6, 0.8]
    first = bootstrap_mean_ci(values, seed=7)
    second = bootstrap_mean_ci(values, seed=7)
    assert first == second


def test_bootstrap_mean_ci_different_seeds_may_differ() -> None:
    """Different seeds should generally produce different results for varied data."""
    values = [0.1, 0.3, 0.5, 0.7, 0.9]
    result_0 = bootstrap_mean_ci(values, seed=0)
    result_1 = bootstrap_mean_ci(values, seed=1)
    # Results with different seeds are not guaranteed to differ, but for
    # sufficiently varied data this is the expected behavior.
    # We only assert the structural invariant holds for both.
    assert result_0[0] <= result_0[1]
    assert result_1[0] <= result_1[1]
