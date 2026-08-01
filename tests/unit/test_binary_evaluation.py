"""Regression tests for the authoritative binary metric evaluator.

Expected metric values are built with ``sklearn`` rather than by restating the
production formulas, so the tests are independent evidence rather than a second
copy of the implementation.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed;
* ``BinaryEvaluationError`` when objects disagree with one another.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    CONFUSION_LABEL_ORDER,
    METRIC_COMPARISON_SCHEMA_VERSION,
    METRIC_PROTOCOL_VERSION,
    MINORITY_LABEL,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    PRIMARY_ACCURACY_THRESHOLD,
    BinaryEvaluationError,
    BinaryMetricSnapshot,
    CleanTestSet,
    ConfusionMatrix,
    MetricComparison,
    PredictionVector,
    compare_binary_metrics,
    confusion_for,
    derived_primary_outcome,
    metric_snapshot,
    validate_metric_comparison,
)
from aletheia_lab.benchmark.p2.contracts import ClassificationRecord

_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_0 = "0" * 64

#: Sixty negatives then forty positives: label 1 is the minority, as the alpha
#: slice requires, and both classes are present.
_TRUE_LABELS: tuple[int, ...] = tuple([0] * 60 + [1] * 40)


def _ids(count: int = len(_TRUE_LABELS)) -> tuple[str, ...]:
    return tuple(f"{index:05d}-TEST" for index in range(count))


def _test_set(**overrides: object) -> CleanTestSet:
    payload: dict[str, object] = {
        "schema_version": CLEAN_TEST_SET_SCHEMA_VERSION,
        "split": "test",
        "record_ids": _ids(),
        "attested_true_labels": _TRUE_LABELS,
        "attested_test_feature_matrix_sha256": _HEX_A,
        "attested_target_sha256": _HEX_B,
        "attested_split_manifest_sha256": _HEX_B,
        "attested_model_sha256": _HEX_C,
    }
    payload.update(overrides)
    return CleanTestSet(**payload)  # type: ignore[arg-type]


def _vector(predictions: tuple[int, ...], *, role: str = "reference") -> PredictionVector:
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,  # type: ignore[arg-type]
        predictions=predictions,
    )


def _predictions_with_accuracy(correct: int) -> tuple[int, ...]:
    """Return a vector that is right on the first ``correct`` records only."""

    return tuple(
        label if index < correct else 1 - label for index, label in enumerate(_TRUE_LABELS)
    )


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


@pytest.fixture
def test_set() -> CleanTestSet:
    return _test_set()


@pytest.fixture
def reference() -> PredictionVector:
    return _vector(_predictions_with_accuracy(90), role="reference")


@pytest.fixture
def observed() -> PredictionVector:
    return _vector(_predictions_with_accuracy(70), role="observed")


# --------------------------------------------------------------------------- #
# Metrics against sklearn
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("correct", [100, 90, 70, 50, 0])
def test_every_metric_matches_sklearn(correct: int, test_set: CleanTestSet) -> None:
    predictions = _predictions_with_accuracy(correct)
    snapshot = metric_snapshot(test_set=test_set, predictions=_vector(predictions))
    truth = list(_TRUE_LABELS)
    guess = list(predictions)

    assert snapshot.accuracy == pytest.approx(accuracy_score(truth, guess))
    assert snapshot.macro_f1 == pytest.approx(
        f1_score(truth, guess, average="macro", zero_division=0)
    )
    assert snapshot.minority_recall == pytest.approx(
        recall_score(truth, guess, pos_label=1, zero_division=0)
    )
    expected = confusion_matrix(truth, guess, labels=[0, 1])
    assert snapshot.confusion.true_negative == int(expected[0][0])
    assert snapshot.confusion.false_positive == int(expected[0][1])
    assert snapshot.confusion.false_negative == int(expected[1][0])
    assert snapshot.confusion.true_positive == int(expected[1][1])


def test_the_confusion_label_order_is_the_one_sklearn_is_given() -> None:
    assert CONFUSION_LABEL_ORDER == (0, 1)
    assert MINORITY_LABEL == 1


def test_zero_division_yields_zero_rather_than_raising(test_set: CleanTestSet) -> None:
    """Predicting all zeros gives the positive class no precision and no recall."""

    all_negative = tuple(0 for _ in _TRUE_LABELS)
    snapshot = metric_snapshot(test_set=test_set, predictions=_vector(all_negative))
    assert snapshot.minority_recall == 0.0
    assert snapshot.macro_f1 == pytest.approx(
        f1_score(list(_TRUE_LABELS), list(all_negative), average="macro", zero_division=0)
    )


def test_a_degenerate_confusion_matrix_does_not_divide_by_zero() -> None:
    empty_positive = ConfusionMatrix(
        true_negative=10, false_positive=0, false_negative=0, true_positive=0
    )
    assert empty_positive.minority_recall() == 0.0
    assert empty_positive.label_f1(1) == 0.0
    assert empty_positive.accuracy() == 1.0


def test_the_snapshot_rates_are_recomputed_from_the_counts(test_set: CleanTestSet) -> None:
    snapshot = metric_snapshot(
        test_set=test_set, predictions=_vector(_predictions_with_accuracy(80))
    )
    forged = _forge(snapshot, accuracy=0.99)
    with pytest.raises(ValidationError, match="derived from the confusion counts"):
        type(forged).model_validate(forged.model_dump())


def test_a_snapshot_whose_counts_do_not_sum_is_rejected(test_set: CleanTestSet) -> None:
    snapshot = metric_snapshot(
        test_set=test_set, predictions=_vector(_predictions_with_accuracy(80))
    )
    forged = _forge(snapshot, prediction_count=99)
    with pytest.raises(ValidationError, match="add up to the prediction count"):
        type(forged).model_validate(forged.model_dump())


# --------------------------------------------------------------------------- #
# Input rejection
# --------------------------------------------------------------------------- #


def test_a_prediction_count_mismatch_is_rejected(test_set: CleanTestSet) -> None:
    with pytest.raises(BinaryEvaluationError, match="one entry per clean-test record"):
        metric_snapshot(test_set=test_set, predictions=_vector(_TRUE_LABELS[:-1]))


def test_a_non_binary_true_label_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be binary 0 or 1"):
        _test_set(attested_true_labels=(2, *_TRUE_LABELS[1:]))


def test_a_non_binary_prediction_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be binary 0 or 1"):
        _vector((2, *_TRUE_LABELS[1:]))


def test_a_float_prediction_is_rejected() -> None:
    """Strict mode refuses a float, so NaN can never reach the counting loop."""

    with pytest.raises(ValidationError):
        _vector((float("nan"), *_TRUE_LABELS[1:]))  # type: ignore[arg-type]


def test_a_single_class_test_set_is_rejected() -> None:
    with pytest.raises(ValidationError, match="both classes present"):
        _test_set(attested_true_labels=tuple(0 for _ in _TRUE_LABELS))


def test_a_test_set_where_label_one_is_not_the_minority_is_rejected() -> None:
    """The pinned minority label must be checked, never silently re-derived."""

    majority_positive = tuple([1] * 60 + [0] * 40)
    with pytest.raises(ValidationError, match="pins label 1 as the minority class"):
        _test_set(attested_true_labels=majority_positive)


def test_a_balanced_test_set_is_rejected() -> None:
    balanced = tuple([0] * 50 + [1] * 50)
    with pytest.raises(ValidationError, match="pins label 1 as the minority class"):
        _test_set(attested_true_labels=balanced)


def test_duplicate_record_identifiers_are_rejected() -> None:
    ids = _ids()
    with pytest.raises(ValidationError, match="record IDs must be unique"):
        _test_set(record_ids=(ids[0], *ids[1:-1], ids[0]))


def test_a_length_mismatch_between_identifiers_and_labels_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must align"):
        _test_set(attested_true_labels=_TRUE_LABELS[:-1])


def test_confusion_for_rejects_misaligned_vectors() -> None:
    with pytest.raises(BinaryEvaluationError, match="one entry each per record"):
        confusion_for(true_labels=[0, 1, 0], predictions=[0, 1])


# --------------------------------------------------------------------------- #
# Outcome derivation
# --------------------------------------------------------------------------- #


def test_the_outcome_rule_is_the_contract_kernel_rule(test_set: CleanTestSet) -> None:
    """A comparison and a classification record must never disagree.

    The threshold rule is imported from the contract kernel rather than
    restated, and this test pins that by driving a real ``ClassificationRecord``
    with the same numbers.
    """

    reference = _vector(_predictions_with_accuracy(90), role="reference")
    observed = _vector(_predictions_with_accuracy(70), role="observed")
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    record = ClassificationRecord(
        schema_version="p2-classification-record/1",
        candidate_id="p2-candidate-" + "a" * 64,
        role="fault_directed",
        eligibility_policy_version="preprocessing-bug-impact/alpha-v1",
        primary_metric="accuracy",
        reference_value=comparison.reference.accuracy,
        observed_value=comparison.observed.accuracy,
        delta=comparison.accuracy_delta,
        threshold=PRIMARY_ACCURACY_THRESHOLD,
        measured_outcome=comparison.measured_primary_outcome,
        family_class="eligible_failure",
    )
    assert record.measured_outcome == comparison.measured_primary_outcome == "regression"


@pytest.mark.parametrize(
    ("reference_correct", "observed_correct", "expected"),
    [
        (50, 51, "improvement"),
        (51, 50, "regression"),
        (50, 50, "stable"),
        (50, 90, "improvement"),
        (90, 50, "regression"),
    ],
)
def test_the_outcome_is_derived_from_the_accuracy_delta(
    reference_correct: int, observed_correct: int, expected: str, test_set: CleanTestSet
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=_vector(
            _predictions_with_accuracy(reference_correct), role="reference"
        ),
        observed_predictions=_vector(_predictions_with_accuracy(observed_correct), role="observed"),
    )
    assert comparison.measured_primary_outcome == expected


def test_the_threshold_is_applied_to_the_float_delta_exactly_as_the_kernel_does() -> None:
    """A one-record difference is not always outside the stable band.

    ``0.51 - 0.50`` is slightly above ``0.01`` in binary floating point, while
    ``0.03 - 0.02`` is slightly below it. The comparison applies the frozen
    ``<=``/``>=`` rule to the float as the contract kernel does, rather than
    inventing a tolerance that would make the two disagree. This test pins the
    behaviour so a future tolerance change is a deliberate, visible decision.
    """

    assert derived_primary_outcome(0.51 - 0.50) == "improvement"
    assert derived_primary_outcome(0.50 - 0.51) == "regression"
    assert derived_primary_outcome(0.03 - 0.02) == "stable"
    assert PRIMARY_ACCURACY_THRESHOLD > (0.03 - 0.02)


def test_a_comparison_cannot_declare_its_own_outcome(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    forged = _forge(comparison, measured_primary_outcome="improvement")
    with pytest.raises(ValidationError, match="must be derived from the accuracy delta"):
        type(forged).model_validate(forged.model_dump())


def test_a_comparison_cannot_choose_its_own_threshold(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    forged = _forge(comparison, primary_threshold=0.5)
    with pytest.raises(ValidationError, match="may not choose its own"):
        type(forged).model_validate(forged.model_dump())


def test_no_outcome_named_benign_can_be_reached(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    """``benign`` is a role-level classification, not a reading of one delta."""

    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    with pytest.raises(ValidationError):
        MetricComparison(
            **{**comparison.model_dump(), "measured_primary_outcome": "benign"}  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("delta", [-0.4, -0.02, 0.0, 0.005, 0.3])
def test_the_guardrail_deltas_are_reported_without_a_verdict(
    delta: float, test_set: CleanTestSet
) -> None:
    """Macro-F1 and minority recall are measured, never judged here."""

    comparison = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=_vector(_predictions_with_accuracy(90), role="reference"),
        observed_predictions=_vector(_predictions_with_accuracy(70), role="observed"),
    )
    names = set(type(comparison).model_fields)
    assert not any(token in name for name in names for token in ("eligib", "family", "admission"))
    assert comparison.macro_f1_delta == pytest.approx(
        comparison.observed.macro_f1 - comparison.reference.macro_f1
    )
    assert comparison.minority_recall_delta == pytest.approx(
        comparison.observed.minority_recall - comparison.reference.minority_recall
    )


# --------------------------------------------------------------------------- #
# Validation boundary
# --------------------------------------------------------------------------- #


def test_validation_accepts_the_comparison_it_produced(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    returned = validate_metric_comparison(
        comparison,
        test_set=test_set,
        reference_predictions=reference,
        observed_predictions=observed,
    )
    assert returned.canonical_sha256() == comparison.canonical_sha256()


@pytest.mark.parametrize(
    "field",
    [
        "attested_true_labels",
        "attested_model_sha256",
        "attested_test_feature_matrix_sha256",
        "attested_target_sha256",
        "attested_split_manifest_sha256",
    ],
)
def test_a_changed_test_set_is_caught_at_the_validation_boundary(
    field: str, test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    replacement: object = tuple([0] * 61 + [1] * 39) if field == "attested_true_labels" else _HEX_0
    other = _test_set(**{field: replacement})
    with pytest.raises(BinaryEvaluationError):
        validate_metric_comparison(
            comparison,
            test_set=other,
            reference_predictions=reference,
            observed_predictions=observed,
        )


def test_a_swapped_prediction_vector_is_caught_at_the_validation_boundary(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    other = _vector(_predictions_with_accuracy(85), role="observed")
    with pytest.raises(BinaryEvaluationError, match="observed prediction vector"):
        validate_metric_comparison(
            comparison,
            test_set=test_set,
            reference_predictions=reference,
            observed_predictions=other,
        )


def test_a_comparison_forged_with_model_copy_is_rejected(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    forged = _forge(comparison, evaluation_source_sha256=_HEX_0)
    with pytest.raises(BinaryEvaluationError, match="not bound to this clean test set"):
        validate_metric_comparison(
            forged,
            test_set=test_set,
            reference_predictions=reference,
            observed_predictions=observed,
        )


def test_a_comparison_forged_with_model_construct_is_rejected(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    forged = MetricComparison.model_construct(
        schema_version=comparison.schema_version,
        metric_protocol_version=comparison.metric_protocol_version,
        primary_metric=comparison.primary_metric,
        primary_threshold=comparison.primary_threshold,
        reference=comparison.reference,
        observed=comparison.observed,
        accuracy_delta=0.5,
        macro_f1_delta=comparison.macro_f1_delta,
        minority_recall_delta=comparison.minority_recall_delta,
        measured_primary_outcome=comparison.measured_primary_outcome,
        evaluation_source_sha256=comparison.evaluation_source_sha256,
        reference_predictions_sha256=comparison.reference_predictions_sha256,
        observed_predictions_sha256=comparison.observed_predictions_sha256,
    )
    with pytest.raises(ValidationError, match="observed minus reference"):
        validate_metric_comparison(
            forged,
            test_set=test_set,
            reference_predictions=reference,
            observed_predictions=observed,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_primary_outcome_rejects_non_finite_deltas(value: float) -> None:
    with pytest.raises(BinaryEvaluationError, match="must be finite"):
        derived_primary_outcome(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accuracy_delta", float("nan")),
        ("accuracy_delta", float("inf")),
        ("macro_f1_delta", float("-inf")),
        ("minority_recall_delta", float("nan")),
    ],
)
def test_metric_comparison_rejects_non_finite_deltas(
    field: str,
    value: float,
    test_set: CleanTestSet,
    reference: PredictionVector,
    observed: PredictionVector,
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=reference,
        observed_predictions=observed,
    )
    payload = comparison.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError, match="metric deltas must be finite"):
        MetricComparison.model_validate(payload)


def test_the_vector_roles_must_be_the_declared_ones(test_set: CleanTestSet) -> None:
    swapped = _vector(_predictions_with_accuracy(90), role="observed")
    with pytest.raises(BinaryEvaluationError, match="reference role"):
        compare_binary_metrics(
            test_set=test_set,
            reference_predictions=swapped,
            observed_predictions=_vector(_predictions_with_accuracy(70), role="observed"),
        )


def test_extra_fields_are_refused(test_set: CleanTestSet) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CleanTestSet(
            **{**test_set.model_dump(), "eligibility": "eligible_failure"}  # type: ignore[arg-type]
        )


def test_the_protocol_version_is_pinned(test_set: CleanTestSet) -> None:
    snapshot = metric_snapshot(test_set=test_set, predictions=_vector(_TRUE_LABELS))
    assert snapshot.metric_protocol_version == METRIC_PROTOCOL_VERSION
    with pytest.raises(ValidationError):
        BinaryMetricSnapshot(
            **{**snapshot.model_dump(), "metric_protocol_version": "binary-alpha-metrics/v2"}  # type: ignore[arg-type]
        )


def test_the_comparison_schema_version_is_pinned(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    assert comparison.schema_version == METRIC_COMPARISON_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Immutability, proven by attempted mutation
# --------------------------------------------------------------------------- #


def test_the_test_set_cannot_be_mutated(test_set: CleanTestSet) -> None:
    with pytest.raises(ValidationError):
        test_set.attested_true_labels = ()


def test_a_prediction_vector_cannot_be_mutated(reference: PredictionVector) -> None:
    with pytest.raises(ValidationError):
        reference.predictions = (1,)
    with pytest.raises(TypeError):
        reference.predictions[0] = 1  # type: ignore[index]


def test_a_snapshot_and_its_confusion_cannot_be_mutated(test_set: CleanTestSet) -> None:
    snapshot = metric_snapshot(test_set=test_set, predictions=_vector(_TRUE_LABELS))
    with pytest.raises(ValidationError):
        snapshot.accuracy = 0.0
    with pytest.raises(ValidationError):
        snapshot.confusion.true_positive = 0


def test_a_comparison_and_its_nested_snapshots_cannot_be_mutated(
    test_set: CleanTestSet, reference: PredictionVector, observed: PredictionVector
) -> None:
    comparison = compare_binary_metrics(
        test_set=test_set, reference_predictions=reference, observed_predictions=observed
    )
    with pytest.raises(ValidationError):
        comparison.accuracy_delta = 0.0
    with pytest.raises(ValidationError):
        comparison.observed.macro_f1 = 1.0
    with pytest.raises(ValidationError):
        comparison.observed.confusion.false_negative = 0
