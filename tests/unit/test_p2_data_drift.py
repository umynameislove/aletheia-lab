"""Regression tests for Phase 2 native categorical drift.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed;
* ``DataDriftError`` when objects disagree with one another.

Expected counts, distributions and PSI values are rebuilt from the contract
statement or from the shared signal helpers rather than copied out of the
production module, so the assertions are independent evidence.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    CleanTestSet,
    PredictionVector,
)
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.data_drift import (
    APPORTIONMENT_RULE,
    DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE,
    DRIFT_INTERVENTION_TYPE,
    DRIFT_MEASUREMENT_SCHEMA_VERSION,
    DRIFT_PROTOCOL_VERSION,
    DRIFT_SOURCE_SCHEMA_VERSION,
    PSI_METHOD,
    RESAMPLING_CONTROL_INTERVENTION_TYPE,
    ROW_SELECTION_POLICY,
    CategoricalDriftResult,
    CategoricalDriftSpec,
    DataDriftError,
    DriftEvaluationSource,
    DriftMeasurement,
    DriftObservedEvaluationSet,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    apportion,
    build_drift_observed_evaluation_set,
    distribution_total_variation,
    measure_drift_candidate,
    normalize_distribution,
    select_category_rows,
    validate_categorical_drift,
    validate_drift_measurement,
    validate_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.identity import DataDriftParameters, FamilyIdentity
from aletheia_lab.benchmark.signals import (
    categorical_distribution,
    population_stability_index,
)

_H = {letter: letter * 64 for letter in "abcdef"}
_HEX_0 = "0" * 64

_CATEGORIES = ("Month-to-month", "One year", "Two year")
_SOURCE_SIZE = 120
_OUTPUT_SIZE = 300

#: The frozen M1 grid, restated as the contract's own numbers so a silent change
#: shows up here as a failure rather than as a different experiment.
_M1_GRID: dict[str, tuple[dict[str, float] | None, int, str, str]] = {
    "M1-F1": (
        {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
        1,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-F2": (
        {"Month-to-month": 0.90, "One year": 0.06, "Two year": 0.04},
        3,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-S1": (
        {"Month-to-month": 0.60, "One year": 0.20, "Two year": 0.20},
        5,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-I1": (
        {"Month-to-month": 0.40, "One year": 0.30, "Two year": 0.30},
        4,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-R1": (
        {"Month-to-month": 0.70, "One year": 0.18, "Two year": 0.12},
        2,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-B1": (None, 105, "designed_benign_control", RESAMPLING_CONTROL_INTERVENTION_TYPE),
}
_RESERVE_ORDER = {"M1-R1": 1, "M1-R2": 2, "M1-R3": 3}


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _ids(count: int = _SOURCE_SIZE) -> tuple[str, ...]:
    return tuple(f"{index:05d}-EVAL" for index in range(count))


def _values(count: int = _SOURCE_SIZE) -> tuple[str, ...]:
    return tuple(_CATEGORIES[index % len(_CATEGORIES)] for index in range(count))


def _source(**overrides: object) -> DriftEvaluationSource:
    payload: dict[str, object] = {
        "schema_version": DRIFT_SOURCE_SCHEMA_VERSION,
        "split": "test",
        "dataset_snapshot_id": "telco_customer_churn@2026-07",
        "dataset_sha256": _H["a"],
        "model_data_split_manifest_sha256": _H["b"],
        "feature": "Contract",
        "record_ids": _ids(),
        "feature_values": _values(),
        "attested_raw_feature_matrix_sha256": _H["e"],
        "attested_raw_target_sha256": _H["f"],
        "attested_model_sha256": _H["c"],
        "attested_preprocessing_specification_sha256": _H["d"],
    }
    payload.update(overrides)
    return DriftEvaluationSource(**payload)  # type: ignore[arg-type]


def _parameters(
    target: dict[str, float], *, feature: str = "Contract", output_size: int = _OUTPUT_SIZE
) -> DataDriftParameters:
    return DataDriftParameters(feature=feature, target_distribution=target, output_size=output_size)


def _target_for(slot_id: str) -> dict[str, float]:
    declared, _, _, _ = _M1_GRID[slot_id]
    if declared is not None:
        return declared
    return _source().observed_distribution()


def _spec(slot_id: str = "M1-F1", **overrides: object) -> CategoricalDriftSpec:
    _, seed, _, _ = _M1_GRID[slot_id]
    output_size = _SOURCE_SIZE if slot_id == "M1-B1" else _OUTPUT_SIZE
    payload: dict[str, object] = {
        "injection_id": slot_id,
        "parameters": _parameters(_target_for(slot_id), output_size=output_size),
        "seed": seed,
    }
    payload.update(overrides)
    return CategoricalDriftSpec(**payload)  # type: ignore[arg-type]


def _identity(
    parameters: DataDriftParameters, seed: int, intervention_type: str, **overrides: object
) -> FamilyIdentity:
    payload: dict[str, object] = {
        "dataset_snapshot_id": "telco_customer_churn@2026-07",
        "dataset_sha256": _H["a"],
        "model_data_split_manifest_sha256": _H["b"],
        "fault_type": "data_drift",
        "intervention_type": intervention_type,
        "canonical_intervention_parameters": parameters,
        "seed": seed,
        "reference_construction_id": "clean-test-reference/v1",
        "injector_contract_version": "drift/v1",
        "model_specification_sha256": _H["c"],
        "preprocessing_specification_sha256": _H["d"],
        "identity_schema_version": "p2-family-identity/v1",
    }
    payload.update(overrides)
    return FamilyIdentity(**payload)  # type: ignore[arg-type]


def _slot(slot_id: str = "M1-F1", **overrides: object) -> CandidateSlot:
    _, seed, role, intervention_type = _M1_GRID[slot_id]
    output_size = _SOURCE_SIZE if slot_id == "M1-B1" else _OUTPUT_SIZE
    reserve_order = _RESERVE_ORDER.get(slot_id)
    payload: dict[str, object] = {
        "slot_id": slot_id,
        "fault_type": "data_drift",
        "slot_kind": "reserve" if reserve_order else "primary",
        "role": role,
        "reserve_order": reserve_order,
        "identity": _identity(
            _parameters(_target_for(slot_id), output_size=output_size), seed, intervention_type
        ),
    }
    payload.update(overrides)
    return CandidateSlot(**payload)  # type: ignore[arg-type]


def _labels(count: int = _SOURCE_SIZE) -> tuple[int, ...]:
    """Eighty negatives then forty positives, so label 1 is the minority."""

    return tuple(1 if index >= (count * 2) // 3 else 0 for index in range(count))


def _test_set(**overrides: object) -> CleanTestSet:
    payload: dict[str, object] = {
        "schema_version": CLEAN_TEST_SET_SCHEMA_VERSION,
        "split": "test",
        "record_ids": _ids(),
        "attested_true_labels": _labels(),
        "attested_test_feature_matrix_sha256": _H["e"],
        "attested_target_sha256": _H["f"],
        "attested_split_manifest_sha256": _H["b"],
        "attested_model_sha256": _H["c"],
    }
    payload.update(overrides)
    return CleanTestSet(**payload)  # type: ignore[arg-type]


def _vector(predictions: tuple[int, ...], *, role: str) -> PredictionVector:
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,  # type: ignore[arg-type]
        predictions=predictions,
    )


def _wrong_by(count: int) -> tuple[int, ...]:
    return tuple(1 - label if index < count else label for index, label in enumerate(_labels()))


def _selected_labels(result: CategoricalDriftResult) -> tuple[int, ...]:
    labels_by_id = dict(zip(_ids(), _labels(), strict=True))
    return tuple(labels_by_id[record_id] for record_id in result.selected_record_ids)


def _observed_wrong_by(result: CategoricalDriftResult, count: int) -> tuple[int, ...]:
    labels = _selected_labels(result)
    return tuple(1 - label if index < count else label for index, label in enumerate(labels))


def _observed_set(
    result: CategoricalDriftResult,
    *,
    source: DriftEvaluationSource | None = None,
    test_set: CleanTestSet | None = None,
) -> DriftObservedEvaluationSet:
    return build_drift_observed_evaluation_set(
        result=result,
        source=source or _source(),
        test_set=test_set or _test_set(),
        attested_drifted_feature_matrix_sha256=_HEX_0,
    )


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


@pytest.fixture
def source() -> DriftEvaluationSource:
    return _source()


@pytest.fixture
def drift(source: DriftEvaluationSource) -> CategoricalDriftResult:
    return apply_categorical_drift(source=source, spec=_spec(), slot=_slot())


@pytest.fixture
def control(source: DriftEvaluationSource) -> CategoricalDriftResult:
    return apply_empirical_resampling_control(
        source=source, spec=_spec("M1-B1"), slot=_slot("M1-B1")
    )


# --------------------------------------------------------------------------- #
# Pure algorithm, checked against the contract statement
# --------------------------------------------------------------------------- #


def test_apportionment_sums_to_the_requested_batch_size() -> None:
    counts = apportion(
        target_distribution={"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
        output_size=1409,
    )
    assert sum(counts.values()) == 1409


def test_apportionment_uses_largest_remainder_with_a_name_tiebreak() -> None:
    """Three equal thirds of ten: floors are 3/3/3 and one unit remains.

    Every remainder is identical, so the name tiebreak decides. The rule sorts
    descending, so the extra unit goes to the alphabetically *last* category —
    the same answer Phase 1 gives, restated here rather than copied from the
    implementation.
    """

    counts = apportion(target_distribution={"a": 1.0, "b": 1.0, "c": 1.0}, output_size=10)
    assert counts == {"a": 3, "b": 3, "c": 4}
    assert sum(counts.values()) == 10


def test_apportionment_normalises_unnormalised_weights() -> None:
    assert apportion(target_distribution={"a": 2.0, "b": 2.0}, output_size=8) == {"a": 4, "b": 4}


def test_apportionment_rejects_an_empty_or_non_positive_request() -> None:
    with pytest.raises(DataDriftError, match="must be positive"):
        apportion(target_distribution={"a": 1.0}, output_size=0)
    with pytest.raises(DataDriftError, match="at least one category"):
        apportion(target_distribution={}, output_size=10)


def test_normalisation_rejects_a_non_positive_total() -> None:
    with pytest.raises(DataDriftError, match="positive finite total"):
        normalize_distribution({"a": 0.0, "b": 0.0})


@pytest.mark.parametrize("bad_weight", [-0.5, float("-inf"), float("nan"), -0.0])
def test_exported_distribution_helpers_reject_invalid_weights(bad_weight: float) -> None:
    """Regression: a positive total must not hide one negative category weight."""

    with pytest.raises(DataDriftError):
        normalize_distribution({"a": 2.0, "b": bad_weight})
    with pytest.raises(DataDriftError):
        apportion(target_distribution={"a": 2.0, "b": bad_weight}, output_size=10)


def test_row_selection_ignores_the_order_the_pool_was_listed_in() -> None:
    pool = tuple(f"{index:05d}-EVAL" for index in range(40))
    forward = select_category_rows(
        pool_record_ids=pool, count=12, seed=1, injection_id="M1-F1", category="One year"
    )
    backward = select_category_rows(
        pool_record_ids=tuple(reversed(pool)),
        count=12,
        seed=1,
        injection_id="M1-F1",
        category="One year",
    )
    assert forward == backward


def test_row_selection_changes_with_the_seed_and_the_category() -> None:
    pool = tuple(f"{index:05d}-EVAL" for index in range(40))
    base = select_category_rows(
        pool_record_ids=pool, count=12, seed=1, injection_id="M1-F1", category="One year"
    )
    assert base != select_category_rows(
        pool_record_ids=pool, count=12, seed=2, injection_id="M1-F1", category="One year"
    )
    assert base != select_category_rows(
        pool_record_ids=pool, count=12, seed=1, injection_id="M1-F1", category="Two year"
    )


def test_row_selection_cycles_deterministically_when_it_needs_replacement() -> None:
    pool = ("00000-EVAL", "00001-EVAL", "00002-EVAL")
    drawn = select_category_rows(
        pool_record_ids=pool, count=7, seed=1, injection_id="M1-F1", category="One year"
    )
    assert len(drawn) == 7
    assert set(drawn) == set(pool)
    assert drawn[:3] == drawn[3:6]


def test_row_selection_refuses_an_absent_category() -> None:
    with pytest.raises(DataDriftError, match="absent from the source"):
        select_category_rows(
            pool_record_ids=(), count=3, seed=1, injection_id="M1-F1", category="Weekly"
        )


# --------------------------------------------------------------------------- #
# Positive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slot_id", ["M1-F1", "M1-F2", "M1-S1", "M1-I1", "M1-R1"])
def test_every_frozen_fault_slot_produces_a_valid_batch(slot_id: str) -> None:
    result = apply_categorical_drift(source=_source(), spec=_spec(slot_id), slot=_slot(slot_id))
    validate_categorical_drift(result, source=_source(), spec=_spec(slot_id), slot=_slot(slot_id))
    assert result.provenance.injection_id == slot_id
    assert result.provenance.intervention_type == DRIFT_INTERVENTION_TYPE


def test_the_achieved_distribution_matches_an_independent_count(
    drift: CategoricalDriftResult,
) -> None:
    counted = Counter(drift.selected_feature_values)
    total = sum(counted.values())
    expected = {category: count / total for category, count in counted.items()}
    assert dict(drift.achieved_distribution) == pytest.approx(expected)


def test_the_reference_distribution_matches_the_shared_helper(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    expected = categorical_distribution(list(source.feature_values))
    assert dict(drift.reference_distribution) == pytest.approx(expected)


def test_the_reported_psi_matches_the_shared_helper(drift: CategoricalDriftResult) -> None:
    expected = population_stability_index(
        dict(drift.reference_distribution), dict(drift.achieved_distribution)
    )
    assert drift.provenance.population_stability_index == pytest.approx(expected)
    assert drift.provenance.psi_method == PSI_METHOD


def test_the_batch_size_matches_the_declared_output_size(drift: CategoricalDriftResult) -> None:
    assert len(drift.selected_record_ids) == _OUTPUT_SIZE
    assert drift.provenance.output_size == _OUTPUT_SIZE
    assert sum(count for _, count in drift.category_counts) == _OUTPUT_SIZE


def test_every_drawn_row_comes_from_its_own_category(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    """Within-category resampling: a row never changes the value it is drawn for."""

    lookup = dict(zip(source.record_ids, source.feature_values, strict=True))
    for record_id, value in zip(
        drift.selected_record_ids, drift.selected_feature_values, strict=True
    ):
        assert lookup[record_id] == value


def test_the_same_input_produces_a_byte_identical_batch(drift: CategoricalDriftResult) -> None:
    again = apply_categorical_drift(source=_source(), spec=_spec(), slot=_slot())
    assert again.artifact_sha256() == drift.artifact_sha256()
    assert again.model_dump(mode="json") == drift.model_dump(mode="json")


def test_the_caller_source_object_is_not_mutated() -> None:
    original = _source()
    before = original.model_dump(mode="json")
    apply_categorical_drift(source=original, spec=_spec(), slot=_slot())
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_snapshot_id", "another_snapshot@2026-07"),
        ("dataset_sha256", _HEX_0),
        ("model_data_split_manifest_sha256", _HEX_0),
        ("attested_model_sha256", _HEX_0),
        ("attested_preprocessing_specification_sha256", _HEX_0),
    ],
)
def test_a_source_from_another_frozen_family_is_refused(field: str, value: str) -> None:
    """Regression: a valid-looking source cannot be replayed under another slot."""

    with pytest.raises(DataDriftError, match="differs from the frozen family identity"):
        apply_categorical_drift(
            source=_source(**{field: value}),
            spec=_spec(),
            slot=_slot(),
        )


def test_the_result_pins_its_protocol_and_policies(drift: CategoricalDriftResult) -> None:
    provenance = drift.provenance
    assert provenance.drift_protocol_version == DRIFT_PROTOCOL_VERSION
    assert provenance.apportionment_rule == APPORTIONMENT_RULE
    assert provenance.row_selection_policy == ROW_SELECTION_POLICY


def test_the_result_carries_no_metric_outcome_or_family_class(
    drift: CategoricalDriftResult,
) -> None:
    names = set(type(drift).model_fields) | set(type(drift.provenance).model_fields)
    forbidden = ("outcome", "eligib", "family_class", "admission", "cause", "context")
    assert not any(token in name for name in names for token in forbidden)


def test_the_benign_control_preserves_the_clean_marginal(
    control: CategoricalDriftResult,
) -> None:
    assert control.achieved_distribution == control.reference_distribution
    assert control.provenance.population_stability_index == pytest.approx(0.0, abs=1e-12)
    assert control.provenance.intervention_type == RESAMPLING_CONTROL_INTERVENTION_TYPE


def test_a_one_row_empirical_control_cannot_claim_distribution_equivalence() -> None:
    """Regression: synchronized spec/slot edits cannot collapse M1-B1 to one row."""

    source = _source()
    parameters = _parameters(source.observed_distribution(), output_size=1)
    spec = CategoricalDriftSpec(injection_id="M1-B1", parameters=parameters, seed=105)
    slot = CandidateSlot(
        slot_id="M1-B1",
        fault_type="data_drift",
        slot_kind="primary",
        role="designed_benign_control",
        identity=_identity(parameters, 105, RESAMPLING_CONTROL_INTERVENTION_TYPE),
    )
    with pytest.raises(DataDriftError):
        apply_empirical_resampling_control(source=source, spec=spec, slot=slot)


def test_validation_accepts_the_batch_it_produced(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    returned = validate_categorical_drift(drift, source=source, spec=_spec(), slot=_slot())
    assert returned.artifact_sha256() == drift.artifact_sha256()


def test_the_public_api_exports_the_mechanism() -> None:
    import aletheia_lab.benchmark.p2 as package

    for name in (
        "CategoricalDriftResult",
        "CategoricalDriftSpec",
        "DataDriftError",
        "DriftEvaluationSource",
        "DriftMetricComparison",
        "DriftObservedEvaluationSet",
        "apply_categorical_drift",
        "apply_empirical_resampling_control",
        "build_drift_observed_evaluation_set",
        "compare_drift_metrics",
        "distribution_total_variation",
        "validate_categorical_drift",
        "validate_drift_observed_evaluation_set",
    ):
        assert name in package.__all__
        assert hasattr(package, name)


# --------------------------------------------------------------------------- #
# Phase 1 / Phase 2 namespace separation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "identifier",
    ["p1-family-" + "a" * 64, "p1-context-" + "b" * 64, "p1-cases/5", "P1-CASE-0001"],
)
def test_a_phase_one_identifier_cannot_enter_a_phase_two_source(identifier: str) -> None:
    with pytest.raises(ValidationError, match="Phase 1"):
        _source(record_ids=(identifier, *_ids()[1:]))


def test_a_phase_one_dataset_snapshot_cannot_seed_a_phase_two_identity() -> None:
    """The kernel already closes this; the drift module repeats it as depth."""

    with pytest.raises(ValidationError, match="Phase 1 identifiers"):
        _identity(
            _parameters(_target_for("M1-F1")),
            1,
            DRIFT_INTERVENTION_TYPE,
            dataset_snapshot_id="p1-telco@2026-01",
        )


def test_a_phase_one_case_cannot_be_replayed_as_a_drift_batch(
    drift: CategoricalDriftResult,
) -> None:
    """There is no field a p1-family artifact could occupy, and none it can create."""

    forged = _forge(drift, selected_record_ids=("p1-case-0001", *drift.selected_record_ids[1:]))
    with pytest.raises(ValidationError, match="Phase 1"):
        type(forged).model_validate(forged.model_dump())


def test_no_phase_one_module_is_reachable_from_the_drift_mechanism() -> None:
    """The only Phase 1 import is the pure signal helper, which owns no artifact."""

    import aletheia_lab.benchmark.p2.data_drift as module

    source_text = module.__doc__ or ""
    assert "p1-cases" in source_text or "Phase 1 artifact" in source_text
    imported = {name for name in dir(module) if name.startswith("BenchmarkCase")}
    assert imported == set()


# --------------------------------------------------------------------------- #
# Exploits: slot, family, source
# --------------------------------------------------------------------------- #


def test_a_slot_from_another_mechanism_is_refused(source: DriftEvaluationSource) -> None:
    with pytest.raises((DataDriftError, ValidationError)):
        apply_categorical_drift(
            source=source, spec=_spec(), slot=_slot("M1-F1", fault_type="label_noise")
        )


def test_a_benign_slot_cannot_run_the_fault_directed_entry_point(
    source: DriftEvaluationSource,
) -> None:
    with pytest.raises(DataDriftError):
        apply_categorical_drift(source=source, spec=_spec("M1-B1"), slot=_slot("M1-B1"))


def test_a_fault_slot_cannot_run_the_benign_entry_point(source: DriftEvaluationSource) -> None:
    with pytest.raises(DataDriftError):
        apply_empirical_resampling_control(source=source, spec=_spec(), slot=_slot())


def test_a_spec_whose_injection_id_differs_from_the_slot_is_refused(
    source: DriftEvaluationSource,
) -> None:
    with pytest.raises(DataDriftError, match="injection_id"):
        apply_categorical_drift(source=source, spec=_spec("M1-F2"), slot=_slot("M1-F1"))


def test_a_tampered_seed_is_refused(source: DriftEvaluationSource) -> None:
    with pytest.raises(DataDriftError, match="seed differs"):
        apply_categorical_drift(source=source, spec=_spec(seed=999), slot=_slot())


def test_a_tampered_target_distribution_is_refused(source: DriftEvaluationSource) -> None:
    other = _spec(parameters=_parameters({"Month-to-month": 0.5, "One year": 0.5}))
    with pytest.raises((DataDriftError, ValidationError)):
        apply_categorical_drift(source=source, spec=other, slot=_slot())


def test_a_spec_for_another_feature_is_refused(source: DriftEvaluationSource) -> None:
    other_parameters = _parameters(_target_for("M1-F1"), feature="PaymentMethod")
    other = _spec(parameters=other_parameters)
    with pytest.raises(DataDriftError):
        apply_categorical_drift(
            source=source,
            spec=other,
            slot=_slot("M1-F1", identity=_identity(other_parameters, 1, DRIFT_INTERVENTION_TYPE)),
        )


def test_a_batch_cannot_be_replayed_against_another_source(
    drift: CategoricalDriftResult,
) -> None:
    other = _source(attested_model_sha256=_HEX_0)
    with pytest.raises(DataDriftError):
        validate_categorical_drift(drift, source=other, spec=_spec(), slot=_slot())


def test_a_batch_cannot_be_replayed_across_families(drift: CategoricalDriftResult) -> None:
    """A different family identity is a different experiment, not a relabelling."""

    other_slot = _slot(
        "M1-F1",
        identity=_identity(
            _parameters(_target_for("M1-F1")),
            1,
            DRIFT_INTERVENTION_TYPE,
            dataset_sha256=_HEX_0,
        ),
    )
    with pytest.raises(DataDriftError):
        validate_categorical_drift(drift, source=_source(), spec=_spec(), slot=other_slot)


def test_a_target_category_absent_from_the_source_is_refused() -> None:
    """The frozen M1-F1 target names three categories; this source has two."""

    two_category_values = tuple(
        ("Month-to-month", "One year")[index % 2] for index in range(_SOURCE_SIZE)
    )
    with pytest.raises(DataDriftError, match="absent from the source"):
        apply_categorical_drift(
            source=_source(feature_values=two_category_values), spec=_spec(), slot=_slot()
        )


# --------------------------------------------------------------------------- #
# Exploits: artifact tamper
# --------------------------------------------------------------------------- #


def test_a_tampered_selected_row_fingerprint_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    forged = _forge(drift, provenance=_forge(drift.provenance, selected_record_ids_sha256=_HEX_0))
    with pytest.raises(DataDriftError):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_reordered_batch_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    """The batch is an ordered sample; permuting it is a different artifact."""

    forged = _forge(
        drift,
        selected_record_ids=tuple(reversed(drift.selected_record_ids)),
        selected_feature_values=tuple(reversed(drift.selected_feature_values)),
    )
    with pytest.raises(DataDriftError, match="deterministic selection"):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_dropped_row_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    forged = _forge(
        drift,
        selected_record_ids=drift.selected_record_ids[:-1],
        selected_feature_values=drift.selected_feature_values[:-1],
    )
    with pytest.raises(ValidationError, match="declared output size"):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_forged_achieved_distribution_is_refused(drift: CategoricalDriftResult) -> None:
    forged = _forge(drift, achieved_distribution=drift.reference_distribution)
    with pytest.raises(ValidationError, match="proportions of the drawn feature"):
        type(forged).model_validate(forged.model_dump())


def test_a_forged_category_count_is_refused(drift: CategoricalDriftResult) -> None:
    first, rest = drift.category_counts[0], drift.category_counts[1:]
    forged = _forge(drift, category_counts=((first[0], first[1] + 1), *rest))
    with pytest.raises(ValidationError, match="sum to the batch size"):
        type(forged).model_validate(forged.model_dump())


def test_a_distribution_that_does_not_sum_to_one_is_refused(
    drift: CategoricalDriftResult,
) -> None:
    broken = tuple((category, value / 2) for category, value in drift.achieved_distribution)
    forged = _forge(drift, achieved_distribution=broken)
    with pytest.raises(ValidationError, match="must sum to one"):
        type(forged).model_validate(forged.model_dump())


def test_an_unsorted_distribution_is_refused(drift: CategoricalDriftResult) -> None:
    forged = _forge(drift, achieved_distribution=tuple(reversed(drift.achieved_distribution)))
    with pytest.raises(ValidationError, match="canonical sorted category order"):
        type(forged).model_validate(forged.model_dump())


def test_a_forged_psi_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    forged = _forge(drift, provenance=_forge(drift.provenance, population_stability_index=0.0))
    with pytest.raises(DataDriftError):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_non_finite_psi_is_refused(drift: CategoricalDriftResult) -> None:
    forged = _forge(
        drift, provenance=_forge(drift.provenance, population_stability_index=float("nan"))
    )
    with pytest.raises(ValidationError):
        type(forged).model_validate(forged.model_dump())


@pytest.mark.parametrize(
    "field",
    [
        "source_record_ids_sha256",
        "source_membership_sha256",
        "source_feature_values_sha256",
        "source_artifact_sha256",
        "selected_feature_values_sha256",
        "category_counts_sha256",
        "spec_sha256",
        "drift_slot_sha256",
        "attested_model_sha256",
        "attested_raw_target_sha256",
    ],
)
def test_a_tampered_provenance_digest_is_refused(
    field: str, source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    forged = _forge(drift, provenance=_forge(drift.provenance, **{field: _HEX_0}))
    with pytest.raises((DataDriftError, ValidationError)):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_batch_forged_with_model_copy_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    swapped = (
        drift.selected_record_ids[1],
        drift.selected_record_ids[0],
        *drift.selected_record_ids[2:],
    )
    forged = _forge(drift, selected_record_ids=swapped)
    with pytest.raises((DataDriftError, ValidationError)):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_a_batch_forged_with_model_construct_is_refused(
    source: DriftEvaluationSource, drift: CategoricalDriftResult
) -> None:
    forged = CategoricalDriftResult.model_construct(
        schema_version=drift.schema_version,
        selected_record_ids=drift.selected_record_ids[:-1],
        selected_feature_values=drift.selected_feature_values[:-1],
        category_counts=drift.category_counts,
        reference_distribution=drift.reference_distribution,
        achieved_distribution=drift.achieved_distribution,
        provenance=drift.provenance,
    )
    with pytest.raises(ValidationError):
        validate_categorical_drift(forged, source=source, spec=_spec(), slot=_slot())


def test_an_unknown_field_is_refused(drift: CategoricalDriftResult) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CategoricalDriftResult(
            **{**drift.model_dump(), "family_class": "eligible_failure"}  # type: ignore[arg-type]
        )


def test_a_wrong_schema_version_is_refused(drift: CategoricalDriftResult) -> None:
    with pytest.raises(ValidationError):
        CategoricalDriftResult(
            **{**drift.model_dump(), "schema_version": "p2-categorical-drift-result/v2"}  # type: ignore[arg-type]
        )


def test_the_result_cannot_be_mutated(drift: CategoricalDriftResult) -> None:
    with pytest.raises(ValidationError):
        drift.selected_record_ids = ()
    with pytest.raises(ValidationError):
        drift.provenance.seed = 0
    with pytest.raises(TypeError):
        drift.category_counts[0] = ("x", 1)  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Measurement and the benign control boundary
# --------------------------------------------------------------------------- #


def _measure(
    result: CategoricalDriftResult, slot_id: str, *, observed_wrong: int
) -> DriftMeasurement:
    source = _source()
    test_set = _test_set()
    return measure_drift_candidate(
        result=result,
        source=source,
        spec=_spec(slot_id),
        slot=_slot(slot_id),
        test_set=test_set,
        observed_set=_observed_set(result, source=source, test_set=test_set),
        clean_reference_predictions=_vector(_labels(), role="reference"),
        observed_predictions=_vector(_observed_wrong_by(result, observed_wrong), role="observed"),
    )


@pytest.mark.parametrize("observed_wrong", [0, 12, 60])
def test_a_fault_directed_measurement_stops_at_validity_review(
    observed_wrong: int, drift: CategoricalDriftResult
) -> None:
    """The guardrail policy is not frozen, so no eligibility is decided here."""

    measurement = _measure(drift, "M1-F1", observed_wrong=observed_wrong)
    assert measurement.status == "validity_review_required"
    assert measurement.comparison.macro_f1_delta is not None
    assert measurement.comparison.minority_recall_delta is not None


def test_a_benign_control_with_identical_predictions_is_verified_pending_admission(
    control: CategoricalDriftResult,
) -> None:
    measurement = _measure(control, "M1-B1", observed_wrong=0)
    assert measurement.status == "equivalence_verified_pending_admission"
    assert measurement.distribution_total_variation == pytest.approx(0.0, abs=1e-12)
    assert (
        measurement.distribution_equivalence_tolerance == DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE
    )


def test_fault_measurement_scores_the_actual_resampled_occurrences(
    drift: CategoricalDriftResult,
) -> None:
    """Regression: 300 drift rows must never be scored against 120 clean labels."""

    measurement = _measure(drift, "M1-F1", observed_wrong=0)
    assert measurement.comparison.reference.prediction_count == _SOURCE_SIZE
    assert measurement.comparison.observed.prediction_count == _OUTPUT_SIZE
    assert measurement.comparison.observed.accuracy == 1.0
    assert measurement.comparison.observed_evaluation_source_sha256 != (
        measurement.comparison.reference_evaluation_source_sha256
    )


def test_a_clean_length_vector_cannot_masquerade_as_drift_batch_predictions(
    drift: CategoricalDriftResult,
) -> None:
    source = _source()
    test_set = _test_set()
    with pytest.raises(DataDriftError, match="one entry per resampled occurrence"):
        measure_drift_candidate(
            result=drift,
            source=source,
            spec=_spec(),
            slot=_slot(),
            test_set=test_set,
            observed_set=_observed_set(drift, source=source, test_set=test_set),
            clean_reference_predictions=_vector(_labels(), role="reference"),
            observed_predictions=_vector(_labels(), role="observed"),
        )


def test_observed_targets_are_derived_from_selected_source_rows(
    drift: CategoricalDriftResult,
) -> None:
    observed = _observed_set(drift)
    assert observed.selected_record_ids == drift.selected_record_ids
    assert observed.true_labels == _selected_labels(drift)
    assert len(set(observed.occurrence_ids)) == _OUTPUT_SIZE


@pytest.mark.parametrize("field", ["true_labels", "occurrence_ids", "selected_record_ids"])
def test_tampering_with_observed_row_binding_is_refused(
    field: str, drift: CategoricalDriftResult
) -> None:
    source = _source()
    test_set = _test_set()
    observed = _observed_set(drift, source=source, test_set=test_set)
    values = getattr(observed, field)
    if field == "true_labels":
        changed = (1 - values[0], *values[1:])
    else:
        changed = (values[1], values[0], *values[2:])
    forged = _forge(observed, **{field: changed})
    with pytest.raises((DataDriftError, ValidationError)):
        validate_drift_observed_evaluation_set(
            forged,
            result=drift,
            source=source,
            test_set=test_set,
        )


def test_observed_set_cannot_bind_a_different_clean_test_source(
    drift: CategoricalDriftResult,
) -> None:
    observed = _observed_set(drift)
    with pytest.raises(DataDriftError, match="target source"):
        validate_drift_observed_evaluation_set(
            observed,
            result=drift,
            source=_source(),
            test_set=_test_set(attested_target_sha256=_HEX_0),
        )


def test_an_existing_measurement_cannot_be_replayed_with_another_feature_matrix(
    drift: CategoricalDriftResult,
) -> None:
    source = _source()
    test_set = _test_set()
    observed = _observed_set(drift, source=source, test_set=test_set)
    measurement = _measure(drift, "M1-F1", observed_wrong=0)
    another_matrix = type(observed).model_validate(
        {
            **observed.model_dump(),
            "attested_drifted_feature_matrix_sha256": _H["a"],
        }
    )
    with pytest.raises(DataDriftError, match="recomputed bound measurement"):
        validate_drift_measurement(
            measurement,
            result=drift,
            source=source,
            spec=_spec(),
            slot=_slot(),
            test_set=test_set,
            observed_set=another_matrix,
            clean_reference_predictions=_vector(_labels(), role="reference"),
            observed_predictions=_vector(_selected_labels(drift), role="observed"),
        )


def test_benign_status_cannot_ignore_distribution_equivalence(
    control: CategoricalDriftResult,
) -> None:
    measurement = _measure(control, "M1-B1", observed_wrong=0)
    forged = _forge(
        measurement,
        distribution_total_variation=0.5,
        status="equivalence_verified_pending_admission",
    )
    with pytest.raises(ValidationError, match="control status must be derived"):
        type(forged).model_validate(forged.model_dump())


def test_distribution_total_variation_detects_a_collapsed_control() -> None:
    assert distribution_total_variation(
        {"Month-to-month": 1 / 3, "One year": 1 / 3, "Two year": 1 / 3},
        {"Two year": 1.0},
    ) == pytest.approx(2 / 3)


def test_a_benign_control_that_moved_a_metric_is_an_equivalence_failure(
    control: CategoricalDriftResult,
) -> None:
    measurement = _measure(control, "M1-B1", observed_wrong=6)
    assert measurement.status == "benign_equivalence_failure"


def test_a_benign_equivalence_failure_cannot_be_relabelled(
    control: CategoricalDriftResult,
) -> None:
    """Not stable, not an eligible failure: the contract's own rejection reason."""

    measurement = _measure(control, "M1-B1", observed_wrong=6)
    forged = _forge(measurement, status="equivalence_verified_pending_admission")
    with pytest.raises(ValidationError, match="derived from the recomputed metrics"):
        type(forged).model_validate(forged.model_dump())
    assert "eligible_failure" not in set(type(measurement).model_fields)


def test_a_benign_control_cannot_borrow_the_fault_directed_status(
    control: CategoricalDriftResult,
) -> None:
    measurement = _measure(control, "M1-B1", observed_wrong=0)
    forged = _forge(measurement, status="validity_review_required")
    with pytest.raises(ValidationError, match="derived from the recomputed metrics"):
        type(forged).model_validate(forged.model_dump())


def test_a_fault_directed_measurement_cannot_claim_equivalence(
    drift: CategoricalDriftResult,
) -> None:
    measurement = _measure(drift, "M1-F1", observed_wrong=0)
    forged = _forge(measurement, status="equivalence_verified_pending_admission")
    with pytest.raises(ValidationError, match="stops at validity review"):
        type(forged).model_validate(forged.model_dump())


def test_a_forged_measurement_comparison_is_refused(drift: CategoricalDriftResult) -> None:
    measurement = _measure(drift, "M1-F1", observed_wrong=12)
    other = _measure(drift, "M1-F1", observed_wrong=60)
    forged = _forge(measurement, comparison=other.comparison)
    with pytest.raises(DataDriftError, match="recomputed bound measurement"):
        validate_drift_measurement(
            forged,
            result=drift,
            source=_source(),
            spec=_spec(),
            slot=_slot(),
            test_set=_test_set(),
            observed_set=_observed_set(drift),
            clean_reference_predictions=_vector(_labels(), role="reference"),
            observed_predictions=_vector(_observed_wrong_by(drift, 12), role="observed"),
        )


def test_a_measurement_bound_to_another_test_set_is_refused(
    drift: CategoricalDriftResult,
) -> None:
    measurement = _measure(drift, "M1-F1", observed_wrong=12)
    with pytest.raises((DataDriftError, ValidationError)):
        validate_drift_measurement(
            measurement,
            result=drift,
            source=_source(),
            spec=_spec(),
            slot=_slot(),
            test_set=_test_set(attested_model_sha256=_HEX_0),
            observed_set=_observed_set(drift),
            clean_reference_predictions=_vector(_labels(), role="reference"),
            observed_predictions=_vector(_observed_wrong_by(drift, 12), role="observed"),
        )


def test_a_measurement_carries_no_eligibility_or_family_field(
    drift: CategoricalDriftResult,
) -> None:
    measurement = _measure(drift, "M1-F1", observed_wrong=12)
    names = set(type(measurement).model_fields)
    forbidden = ("eligib", "family", "admission", "context", "cause")
    assert not any(token in name for name in names for token in forbidden)
    assert measurement.schema_version == DRIFT_MEASUREMENT_SCHEMA_VERSION


def test_a_non_finite_metric_cannot_enter_a_measurement(drift: CategoricalDriftResult) -> None:
    measurement = _measure(drift, "M1-F1", observed_wrong=12)
    forged = _forge(
        measurement,
        comparison=_forge(measurement.comparison, accuracy_delta=float("inf")),
    )
    with pytest.raises(ValidationError):
        type(forged).model_validate(forged.model_dump())


def test_a_single_category_source_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least two categories"):
        _source(feature_values=tuple("Month-to-month" for _ in range(_SOURCE_SIZE)))


def test_duplicate_source_record_identifiers_are_refused() -> None:
    ids = _ids()
    with pytest.raises(ValidationError, match="must be unique"):
        _source(record_ids=(ids[0], *ids[1:-1], ids[0]))


def test_a_length_mismatch_between_identifiers_and_values_is_refused() -> None:
    with pytest.raises(ValidationError, match="must align"):
        _source(feature_values=_values()[:-1])


def test_the_psi_helper_is_symmetric_in_its_own_epsilon_floor() -> None:
    """A distribution compared with itself has no stability index to report."""

    distribution = {"a": 0.5, "b": 0.5}
    assert population_stability_index(distribution, distribution) == pytest.approx(0.0)
    assert math.isfinite(population_stability_index({"a": 1.0}, {"b": 1.0}))
