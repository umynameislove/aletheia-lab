"""Tests for reporting helper modules.

Covers behavioral contracts of:
  - reporting/plots.py  — figure_filename normalization
  - reporting/tables.py — metric_rows schema and ordering

All tests are offline, deterministic, and free of external I/O.
"""

from __future__ import annotations

from collections.abc import Iterator

from aletheia_lab.evaluation.judge import JudgeResult
from aletheia_lab.reporting.plots import figure_filename
from aletheia_lab.reporting.tables import metric_rows

# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

_EXPECTED_ROW_KEYS = frozenset(
    {"case_id", "variant", "correctness", "faithfulness", "abstention", "judge_id"}
)


def _make_result(
    case_id: str = "case-01",
    *,
    variant: str = "b1_plain",
    correctness: float = 0.8,
    faithfulness: float = 0.7,
    abstention: float = 0.0,
    judge_id: str = "judge-rule-v1",
    notes: str | None = None,
) -> JudgeResult:
    return JudgeResult(
        case_id=case_id,
        variant=variant,
        correctness=correctness,
        faithfulness=faithfulness,
        abstention=abstention,
        judge_id=judge_id,
        notes=notes,
    )


# ===========================================================================
# reporting/plots.py — figure_filename
# ===========================================================================


def test_figure_filename_simple_lowercase_unchanged() -> None:
    assert figure_filename("accuracy") == "accuracy.png"


def test_figure_filename_default_extension_is_png() -> None:
    assert figure_filename("recall").endswith(".png")


def test_figure_filename_spaces_replaced_by_underscores() -> None:
    assert figure_filename("ROC AUC") == "roc_auc.png"


def test_figure_filename_slashes_replaced_by_underscores() -> None:
    assert figure_filename("precision/recall") == "precision_recall.png"


def test_figure_filename_casefold_applied() -> None:
    assert figure_filename("ACCURACY") == "accuracy.png"


def test_figure_filename_leading_trailing_whitespace_stripped() -> None:
    assert figure_filename("  f1 score  ") == "f1_score.png"


def test_figure_filename_combined_transformations() -> None:
    """Mixed case, spaces and slashes must all be normalized together."""
    assert figure_filename("  Precision / Recall  ") == "precision___recall.png"


def test_figure_filename_custom_extension() -> None:
    assert figure_filename("accuracy", "svg") == "accuracy.svg"


def test_figure_filename_no_local_path_in_output() -> None:
    """Output must never contain path separators (guards against path leakage)."""
    result = figure_filename("some metric", "pdf")
    assert "\\" not in result
    assert result.count("/") == 0


# ===========================================================================
# reporting/tables.py — metric_rows
# ===========================================================================


def test_metric_rows_empty_input_returns_empty_list() -> None:
    assert metric_rows([]) == []


def test_metric_rows_single_result_returns_one_row() -> None:
    rows = metric_rows([_make_result("case-01")])
    assert len(rows) == 1


def test_metric_rows_row_has_correct_schema() -> None:
    """Every row must contain exactly the six expected keys."""
    rows = metric_rows([_make_result()])
    assert set(rows[0].keys()) == _EXPECTED_ROW_KEYS


def test_metric_rows_values_match_judge_result_fields() -> None:
    result = _make_result(
        case_id="c42",
        variant="a3_evidence_contract",
        correctness=0.6,
        faithfulness=0.5,
        abstention=0.1,
        judge_id="judge-llm-v2",
    )
    row = metric_rows([result])[0]
    assert row["case_id"] == "c42"
    assert row["variant"] == "a3_evidence_contract"
    assert row["correctness"] == 0.6
    assert row["faithfulness"] == 0.5
    assert row["abstention"] == 0.1
    assert row["judge_id"] == "judge-llm-v2"


def test_metric_rows_notes_field_excluded_from_output() -> None:
    """notes is a private annotation; it must not appear in the table row."""
    result = _make_result(notes="borderline: confidence in the diagnosis is low")
    row = metric_rows([result])[0]
    assert "notes" not in row


def test_metric_rows_multiple_results_preserves_order() -> None:
    """Row ordering must match the input iterable order."""
    results = [_make_result("c01"), _make_result("c02"), _make_result("c03")]
    rows = metric_rows(results)
    assert len(rows) == 3
    assert rows[0]["case_id"] == "c01"
    assert rows[1]["case_id"] == "c02"
    assert rows[2]["case_id"] == "c03"


def test_metric_rows_generator_input_accepted() -> None:
    """metric_rows must work with any Iterable, not just lists."""

    def _gen() -> Iterator[JudgeResult]:
        yield _make_result("gen-01")
        yield _make_result("gen-02")

    rows = metric_rows(_gen())
    assert len(rows) == 2
    assert rows[0]["case_id"] == "gen-01"


def test_metric_rows_output_contains_no_local_paths() -> None:
    """Table values must not embed filesystem paths from the running machine."""
    result = _make_result("case-path-check")
    row = metric_rows([result])[0]
    for value in row.values():
        assert "\\" not in str(value)
        assert "Users" not in str(value)


def test_metric_rows_all_rows_have_identical_key_set() -> None:
    """When multiple results are returned, every row must have the same schema."""
    results = [_make_result(f"c{i:02d}") for i in range(5)]
    rows = metric_rows(results)
    key_sets = [frozenset(row.keys()) for row in rows]
    assert len(set(key_sets)) == 1
    assert key_sets[0] == _EXPECTED_ROW_KEYS
