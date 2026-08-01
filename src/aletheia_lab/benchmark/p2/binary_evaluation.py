"""Authoritative binary metrics for the Phase 2 alpha slice.

Every number a Phase 2 candidate reports about its own performance is computed
here, from prediction vectors, and never accepted from a caller. A candidate that
could declare its own accuracy could declare its own outcome, and the whole
benchmark would rest on self-report.

Four decisions make that guarantee checkable.

**Confusion counts are named, not positional.** ``true_negative``,
``false_positive``, ``false_negative`` and ``true_positive`` are separate fields
with a pinned label order of ``(0, 1)``. A bare 2x2 array carries its label order
only in a comment, and comments do not survive refactors.

**Every rate is recomputable from the confusion counts.** Accuracy, macro-F1 and
minority recall are derived quantities, so the validator recomputes them from the
four counts rather than reading them. A snapshot whose numbers disagree with its
own counts is rejected.

**Minority class is verified, not assumed.** The alpha slice pins label 1 as the
minority class, so the evaluator refuses a test set where label 1 is not actually
rarer than label 0, rather than silently re-deriving which class is small. It
never looks at predictions to make that decision.

**Outcome is derived by the one rule the contract kernel already uses.**
:func:`aletheia_lab.benchmark.p2.contracts._derived_metric_outcome` is imported
rather than restated: a second copy of the threshold rule would be free to drift
away from the one that classification records are checked against.

What this module deliberately does not do: decide eligibility, decide a family
class, or apply a guardrail harm threshold. The macro-F1 and minority-recall harm
thresholds for this mechanism are still alpha-provisional, so this module reports
the deltas and stops.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import _derived_metric_outcome
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.validation import ContractViolation

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

CLEAN_TEST_SET_SCHEMA_VERSION: Final[Literal["p2-clean-test-set/v1"]] = "p2-clean-test-set/v1"
PREDICTION_VECTOR_SCHEMA_VERSION: Final[Literal["p2-prediction-vector/v1"]] = (
    "p2-prediction-vector/v1"
)
CONFUSION_MATRIX_SCHEMA_VERSION: Final[Literal["p2-confusion-matrix/v1"]] = "p2-confusion-matrix/v1"
METRIC_SNAPSHOT_SCHEMA_VERSION: Final[Literal["p2-binary-metric-snapshot/v1"]] = (
    "p2-binary-metric-snapshot/v1"
)
METRIC_COMPARISON_SCHEMA_VERSION: Final[Literal["p2-binary-metric-comparison/v1"]] = (
    "p2-binary-metric-comparison/v1"
)

#: Pinned metric protocol. A ``Literal`` rather than free text, so no caller can
#: invent a protocol name that looks official.
METRIC_PROTOCOL_VERSION: Final[Literal["binary-alpha-metrics/v1"]] = "binary-alpha-metrics/v1"

#: The confusion-matrix label order. Named fields make this explicit anyway; the
#: constant exists so a test can pin it against ``sklearn``'s ``labels=`` argument.
CONFUSION_LABEL_ORDER: Final[tuple[int, int]] = (0, 1)

#: The alpha slice pins the positive churn label as the minority class. The
#: evaluator verifies the claim against the true labels rather than trusting it.
MINORITY_LABEL: Final[Literal[1]] = 1

#: Zero-division policy, matching ``sklearn``'s ``zero_division=0``.
ZERO_DIVISION_POLICY: Final[Literal["zero/v1"]] = "zero/v1"
ZERO_DIVISION_VALUE: Final[float] = 0.0

#: The alpha primary metric and its accuracy-harm threshold. The threshold is the
#: one the contract kernel already enforces on classification records.
PRIMARY_METRIC: Final[Literal["accuracy"]] = "accuracy"
PRIMARY_ACCURACY_THRESHOLD: Final[float] = 0.01

#: The three outcomes a primary-metric comparison can produce.
#:
#: ``benign`` is deliberately absent. It is a role-level classification that
#: needs equivalence evidence, not a reading of one metric delta, so it cannot be
#: reached from here at all.
PrimaryOutcome = Literal["regression", "stable", "improvement"]

BINARY_LABELS: Final[frozenset[int]] = frozenset({0, 1})

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

_MAX_RECORDS: Final[int] = 5_000_000
_FLOAT_TOLERANCE: Final[float] = 1e-12

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class BinaryEvaluationError(ContractViolation):
    """Raised when evaluation artifacts disagree with one another.

    Malformed single objects raise ``pydantic.ValidationError`` instead: model
    validators report through Pydantic, which wraps every ``ValueError`` they
    raise.
    """


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise BinaryEvaluationError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even when a caller built the object unsafely.

    ``model_copy(update=...)`` and ``model_construct()`` skip validation in
    Pydantic v2, so any object arriving at a trust boundary is rebuilt from its
    own dump before it is trusted.
    """

    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _check_record_ids(record_ids: Sequence[str]) -> None:
    """Reject anything that cannot anchor a per-record measurement."""

    if not record_ids:
        raise ValueError("an evaluation set must contain at least one record")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("an evaluation set exceeds the supported record count")
    seen: set[str] = set()
    for position, record_id in enumerate(record_ids):
        if not record_id or record_id != record_id.strip():
            raise ValueError(f"record ID at position {position} must be non-blank and trimmed")
        if record_id != unicodedata.normalize("NFC", record_id):
            raise ValueError(f"record ID at position {position} must be Unicode NFC")
        if record_id in seen:
            raise ValueError("record IDs must be unique; duplicates would double-count a record")
        seen.add(record_id)


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator`` under the pinned zero-division policy."""

    if denominator == 0:
        return ZERO_DIVISION_VALUE
    return numerator / denominator


# --------------------------------------------------------------------------- #
# Confusion matrix
# --------------------------------------------------------------------------- #


class ConfusionMatrix(_StrictFrozenModel):
    """The four confusion counts for label order ``(0, 1)``, named individually.

    Naming the cells removes the commonest silent error in this area: a 2x2
    array whose row/column convention is documented only in prose, and which
    therefore transposes without anybody noticing.
    """

    schema_version: Literal["p2-confusion-matrix/v1"] = "p2-confusion-matrix/v1"
    true_negative: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_positive: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.true_negative + self.false_positive + self.false_negative + self.true_positive

    def accuracy(self) -> float:
        """Correct predictions over all predictions."""

        return _safe_ratio(self.true_negative + self.true_positive, self.total)

    def label_f1(self, label: Literal[0, 1]) -> float:
        """F1 for one label, treating that label as positive.

        The parameter is a ``Literal`` rather than an ``int`` so a third label
        is a type error at the call site instead of an unreachable branch that
        pretends to guard something.
        """

        if label == 1:
            true_positive, false_positive, false_negative = (
                self.true_positive,
                self.false_positive,
                self.false_negative,
            )
        else:
            true_positive, false_positive, false_negative = (
                self.true_negative,
                self.false_negative,
                self.false_positive,
            )
        precision = _safe_ratio(true_positive, true_positive + false_positive)
        recall = _safe_ratio(true_positive, true_positive + false_negative)
        if precision + recall == 0.0:
            return ZERO_DIVISION_VALUE
        return 2.0 * precision * recall / (precision + recall)

    def macro_f1(self) -> float:
        """Unweighted mean of the per-label F1 scores."""

        return (self.label_f1(0) + self.label_f1(1)) / 2.0

    def minority_recall(self) -> float:
        """Recall of the pinned minority label."""

        return _safe_ratio(self.true_positive, self.true_positive + self.false_negative)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


class CleanTestSet(_StrictFrozenModel):
    """The frozen clean evaluation rows both runs are scored against.

    ``attested_*`` names say what this layer can and cannot prove. The true
    labels and the four digests describe artifacts produced outside this
    module, so it carries them forward and binds them; it does not recompute
    them from raw data it never receives.
    """

    schema_version: Literal["p2-clean-test-set/v1"]
    split: Literal["test"]
    record_ids: tuple[str, ...]
    attested_true_labels: tuple[int, ...]
    attested_test_feature_matrix_sha256: Sha256
    attested_target_sha256: Sha256
    attested_split_manifest_sha256: Sha256
    attested_model_sha256: Sha256

    @model_validator(mode="after")
    def _test_set_is_usable_for_the_alpha_slice(self) -> CleanTestSet:
        _check_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.attested_true_labels):
            raise ValueError(
                "record_ids and true labels must align: "
                f"{len(self.record_ids)} ids against {len(self.attested_true_labels)} labels"
            )
        for position, label in enumerate(self.attested_true_labels):
            if label not in BINARY_LABELS:
                raise ValueError(f"true label at position {position} must be binary 0 or 1")
        positives = sum(1 for label in self.attested_true_labels if label == 1)
        negatives = len(self.attested_true_labels) - positives
        if positives == 0 or negatives == 0:
            raise ValueError(
                "the alpha metric protocol needs both classes present in the clean test set"
            )
        if positives >= negatives:
            raise ValueError(
                "the alpha slice pins label 1 as the minority class; this test set has "
                f"{positives} positives against {negatives} negatives, so the pinned "
                "minority label would be wrong"
            )
        return self

    @property
    def record_count(self) -> int:
        return len(self.record_ids)

    def record_ids_sha256(self) -> str:
        """Digest the identifier sequence, order included."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "record_ids": list(self.record_ids)}
        )

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including row order.

        Order-sensitive on purpose: two prediction vectors can only be compared
        row by row, so a permuted test set is a different evaluation.
        """

        return canonical_sha256(
            {"schema_version": self.schema_version, "clean_test_set": self.model_dump(mode="json")}
        )


class PredictionVector(_StrictFrozenModel):
    """One real prediction vector, aligned to a clean test set by position.

    This is measured data. There is no metric field, no verdict field and no
    pass flag: the vector is an input to a computation, never its conclusion.
    """

    schema_version: Literal["p2-prediction-vector/v1"]
    role: Literal["reference", "observed"]
    predictions: tuple[int, ...]

    @model_validator(mode="after")
    def _predictions_are_binary(self) -> PredictionVector:
        if not self.predictions:
            raise ValueError("a prediction vector must contain at least one prediction")
        for position, value in enumerate(self.predictions):
            if value not in BINARY_LABELS:
                raise ValueError(f"prediction at position {position} must be binary 0 or 1")
        return self

    def canonical_sha256(self) -> str:
        """Digest the vector with its order included."""

        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "role": self.role,
                "predictions": list(self.predictions),
            }
        )


# --------------------------------------------------------------------------- #
# Metric snapshot
# --------------------------------------------------------------------------- #


class BinaryMetricSnapshot(_StrictFrozenModel):
    """One run's metrics, every rate derived from the confusion counts.

    The validator recomputes accuracy, macro-F1 and minority recall from
    ``confusion`` rather than reading them, so a snapshot whose numbers disagree
    with its own counts cannot exist.
    """

    schema_version: Literal["p2-binary-metric-snapshot/v1"]
    metric_protocol_version: Literal["binary-alpha-metrics/v1"]
    zero_division_policy: Literal["zero/v1"]
    minority_label: Literal[1]
    prediction_count: int = Field(ge=1)
    accuracy: float = Field(ge=0.0, le=1.0)
    macro_f1: float = Field(ge=0.0, le=1.0)
    minority_recall: float = Field(ge=0.0, le=1.0)
    confusion: ConfusionMatrix

    @model_validator(mode="after")
    def _rates_are_derived_from_the_counts(self) -> BinaryMetricSnapshot:
        confusion = self.confusion
        if confusion.total != self.prediction_count:
            raise ValueError("the confusion counts must add up to the prediction count")
        for name, declared, expected in (
            ("accuracy", self.accuracy, confusion.accuracy()),
            ("macro_f1", self.macro_f1, confusion.macro_f1()),
            ("minority_recall", self.minority_recall, confusion.minority_recall()),
        ):
            if abs(declared - expected) > _FLOAT_TOLERANCE:
                raise ValueError(f"{name} must be derived from the confusion counts")
        return self

    def canonical_sha256(self) -> str:
        """Digest every serialized field of this snapshot."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "snapshot": self.model_dump(mode="json")}
        )


def confusion_for(*, true_labels: Sequence[int], predictions: Sequence[int]) -> ConfusionMatrix:
    """Count the four confusion cells for label order ``(0, 1)``."""

    if len(true_labels) != len(predictions):
        _fail(
            "true labels and predictions must have one entry each per record: "
            f"{len(true_labels)} against {len(predictions)}"
        )
    if not true_labels:
        _fail("a confusion matrix needs at least one record")
    counts = {"tn": 0, "fp": 0, "fn": 0, "tp": 0}
    for position, (truth, predicted) in enumerate(zip(true_labels, predictions, strict=True)):
        if truth not in BINARY_LABELS or predicted not in BINARY_LABELS:
            _fail(f"non-binary value at position {position}")
        if truth == 0:
            counts["tn" if predicted == 0 else "fp"] += 1
        else:
            counts["fn" if predicted == 0 else "tp"] += 1
    return ConfusionMatrix(
        true_negative=counts["tn"],
        false_positive=counts["fp"],
        false_negative=counts["fn"],
        true_positive=counts["tp"],
    )


def metric_snapshot(
    *, test_set: CleanTestSet, predictions: PredictionVector
) -> BinaryMetricSnapshot:
    """Score one prediction vector against the clean test set."""

    test_set = _revalidated(test_set)
    predictions = _revalidated(predictions)
    if len(predictions.predictions) != test_set.record_count:
        _fail(
            "the prediction vector must have one entry per clean-test record: "
            f"{len(predictions.predictions)} against {test_set.record_count}"
        )
    confusion = confusion_for(
        true_labels=test_set.attested_true_labels, predictions=predictions.predictions
    )
    return BinaryMetricSnapshot(
        schema_version=METRIC_SNAPSHOT_SCHEMA_VERSION,
        metric_protocol_version=METRIC_PROTOCOL_VERSION,
        zero_division_policy=ZERO_DIVISION_POLICY,
        minority_label=MINORITY_LABEL,
        prediction_count=len(predictions.predictions),
        accuracy=confusion.accuracy(),
        macro_f1=confusion.macro_f1(),
        minority_recall=confusion.minority_recall(),
        confusion=confusion,
    )


# --------------------------------------------------------------------------- #
# Comparison and measured outcome
# --------------------------------------------------------------------------- #


def derived_primary_outcome(delta: float) -> PrimaryOutcome:
    """Return the measured primary outcome for an accuracy delta.

    The rule is imported from the contract kernel rather than restated, so the
    outcome a comparison reports and the outcome a classification record is
    checked against can never drift apart.
    """

    if not math.isfinite(delta):
        _fail("the primary-metric delta must be finite")
    outcome = _derived_metric_outcome(delta, PRIMARY_ACCURACY_THRESHOLD)
    if outcome == "regression":
        return "regression"
    if outcome == "improvement":
        return "improvement"
    if outcome == "stable":
        return "stable"
    _fail(f"the contract kernel returned an unexpected outcome: {outcome!r}")


class MetricComparison(_StrictFrozenModel):
    """Two scored runs, their deltas and the measured primary outcome.

    ``measured_primary_outcome`` is derived by a validator from
    ``accuracy_delta``, so a caller who edits one without the other is rejected
    rather than believed. Guardrail deltas are reported and left unjudged: the
    macro-F1 and minority-recall harm thresholds for this mechanism are still
    alpha-provisional, and inventing one here would freeze it by accident.
    """

    schema_version: Literal["p2-binary-metric-comparison/v1"]
    metric_protocol_version: Literal["binary-alpha-metrics/v1"]
    primary_metric: Literal["accuracy"]
    primary_threshold: float = Field(gt=0.0)
    reference: BinaryMetricSnapshot
    observed: BinaryMetricSnapshot
    accuracy_delta: float
    macro_f1_delta: float
    minority_recall_delta: float
    measured_primary_outcome: PrimaryOutcome
    evaluation_source_sha256: Sha256
    reference_predictions_sha256: Sha256
    observed_predictions_sha256: Sha256

    @field_validator("accuracy_delta", "macro_f1_delta", "minority_recall_delta")
    @classmethod
    def _metric_deltas_are_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric deltas must be finite")
        return value

    @model_validator(mode="after")
    def _deltas_and_outcome_are_derived(self) -> MetricComparison:
        if abs(self.primary_threshold - PRIMARY_ACCURACY_THRESHOLD) > _FLOAT_TOLERANCE:
            raise ValueError(
                f"the alpha primary threshold is {PRIMARY_ACCURACY_THRESHOLD}; a comparison "
                "may not choose its own"
            )
        if self.reference.prediction_count != self.observed.prediction_count:
            raise ValueError("both runs must be scored on the same number of records")
        for name, declared, expected in (
            (
                "accuracy_delta",
                self.accuracy_delta,
                self.observed.accuracy - self.reference.accuracy,
            ),
            (
                "macro_f1_delta",
                self.macro_f1_delta,
                self.observed.macro_f1 - self.reference.macro_f1,
            ),
            (
                "minority_recall_delta",
                self.minority_recall_delta,
                self.observed.minority_recall - self.reference.minority_recall,
            ),
        ):
            if abs(declared - expected) > _FLOAT_TOLERANCE:
                raise ValueError(f"{name} must be observed minus reference")
        expected_outcome = derived_primary_outcome(self.accuracy_delta)
        if self.measured_primary_outcome != expected_outcome:
            raise ValueError(
                "measured_primary_outcome must be derived from the accuracy delta and the "
                f"frozen threshold; expected {expected_outcome!r}"
            )
        return self

    def canonical_sha256(self) -> str:
        """Digest every serialized field of this comparison."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "comparison": self.model_dump(mode="json")}
        )


def compare_binary_metrics(
    *,
    test_set: CleanTestSet,
    reference_predictions: PredictionVector,
    observed_predictions: PredictionVector,
) -> MetricComparison:
    """Score both runs against the clean test set and derive the outcome."""

    test_set = _revalidated(test_set)
    reference_predictions = _revalidated(reference_predictions)
    observed_predictions = _revalidated(observed_predictions)
    if reference_predictions.role != "reference":
        _fail("the reference vector must declare the reference role")
    if observed_predictions.role != "observed":
        _fail("the observed vector must declare the observed role")

    reference = metric_snapshot(test_set=test_set, predictions=reference_predictions)
    observed = metric_snapshot(test_set=test_set, predictions=observed_predictions)
    accuracy_delta = observed.accuracy - reference.accuracy
    return MetricComparison(
        schema_version=METRIC_COMPARISON_SCHEMA_VERSION,
        metric_protocol_version=METRIC_PROTOCOL_VERSION,
        primary_metric=PRIMARY_METRIC,
        primary_threshold=PRIMARY_ACCURACY_THRESHOLD,
        reference=reference,
        observed=observed,
        accuracy_delta=accuracy_delta,
        macro_f1_delta=observed.macro_f1 - reference.macro_f1,
        minority_recall_delta=observed.minority_recall - reference.minority_recall,
        measured_primary_outcome=derived_primary_outcome(accuracy_delta),
        evaluation_source_sha256=test_set.artifact_sha256(),
        reference_predictions_sha256=reference_predictions.canonical_sha256(),
        observed_predictions_sha256=observed_predictions.canonical_sha256(),
    )


def validate_metric_comparison(
    comparison: MetricComparison,
    *,
    test_set: CleanTestSet,
    reference_predictions: PredictionVector,
    observed_predictions: PredictionVector,
) -> MetricComparison:
    """Recompute the whole comparison from the vectors and reject any mismatch.

    This is the one authoritative entry point for a metric comparison. Every
    object is rebuilt from its own dump first, so anything assembled with
    ``model_copy`` or ``model_construct`` is re-validated rather than trusted.
    """

    comparison = _revalidated(comparison)
    expected = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=reference_predictions,
        observed_predictions=observed_predictions,
    )
    if comparison.evaluation_source_sha256 != expected.evaluation_source_sha256:
        _fail("the comparison is not bound to this clean test set")
    if comparison.reference_predictions_sha256 != expected.reference_predictions_sha256:
        _fail("the comparison is not bound to this reference prediction vector")
    if comparison.observed_predictions_sha256 != expected.observed_predictions_sha256:
        _fail("the comparison is not bound to this observed prediction vector")
    if comparison.canonical_sha256() != expected.canonical_sha256():
        _fail("the comparison does not match the metrics recomputed from the vectors")
    return comparison
