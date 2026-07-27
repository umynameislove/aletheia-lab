"""Regression tests for Phase 2 canonical serialization and family identity."""

from __future__ import annotations

import copy
import json
import unicodedata

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.case_schema import case_family_id_for
from aletheia_lab.benchmark.p2 import (
    IDENTITY_FIELD_NAMES,
    P2_CANDIDATE_PREFIX,
    P2_FAMILY_PREFIX,
    DataDriftParameters,
    FamilyIdentity,
    LabelNoiseParameters,
    PreprocessingBugParameters,
    candidate_id_for,
    canonical_json,
    canonical_sha256,
    family_id_for,
    normalize_number,
    normalize_text,
    proposed_family_sha256,
)

_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64


def _drift_parameters(**overrides: object) -> DataDriftParameters:
    payload: dict[str, object] = {
        "feature": "Contract",
        "target_distribution": {"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
        "output_size": 1409,
    }
    payload.update(overrides)
    return DataDriftParameters(**payload)  # type: ignore[arg-type]


def _identity(**overrides: object) -> FamilyIdentity:
    payload: dict[str, object] = {
        "dataset_snapshot_id": "telco_customer_churn@2026-07",
        "dataset_sha256": _HEX_A,
        "model_data_split_manifest_sha256": _HEX_B,
        "fault_type": "data_drift",
        "intervention_type": "categorical_distribution_shift",
        "canonical_intervention_parameters": _drift_parameters(),
        "seed": 1,
        "reference_construction_id": "clean-test-reference/v1",
        "injector_contract_version": "categorical-drift/v1",
        "model_specification_sha256": _HEX_C,
        "preprocessing_specification_sha256": _HEX_D,
        "identity_schema_version": "p2-family-identity/v1",
    }
    payload.update(overrides)
    return FamilyIdentity(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Canonical serialization
# --------------------------------------------------------------------------- #


def test_canonical_json_sorts_keys_and_uses_compact_separators() -> None:
    text = canonical_json({"b": 1, "a": 2})
    assert text == '{"a":"2","b":"1"}'
    assert " " not in text


def test_canonical_json_preserves_non_ascii_without_escaping() -> None:
    text = canonical_json({"feature": "Contrât"})
    assert "\\u" not in text
    assert "Contrât" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (0.80, "0.8"),
        (0.5, "0.5"),
        (100.0, "100"),
        (0.0, "0"),
        (3, "3"),
        (-2, "-2"),
        (0.010, "0.01"),
    ],
)
def test_normalize_number_strips_trailing_zeros(value: float, expected: str) -> None:
    assert normalize_number(value) == expected


def test_trailing_zero_variants_produce_one_canonical_form() -> None:
    assert canonical_json({"w": 0.80}) == canonical_json({"w": 0.8})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json({"metric": value})


def test_canonical_json_rejects_negative_zero() -> None:
    with pytest.raises(ValueError, match="negative zero"):
        canonical_json({"metric": -0.0})


def test_canonical_json_rejects_bool_as_number_but_keeps_bool_type() -> None:
    assert canonical_json({"flag": True}) == '{"flag":true}'


def test_canonical_json_rejects_non_string_keys() -> None:
    with pytest.raises(TypeError, match="keys must be str"):
        canonical_json({1: "x"})


def test_canonical_json_rejects_unserializable_types() -> None:
    with pytest.raises(TypeError, match="not canonically serializable"):
        canonical_json({"when": object()})


def test_unicode_nfc_and_nfd_hash_identically() -> None:
    composed = unicodedata.normalize("NFC", "Café")
    decomposed = unicodedata.normalize("NFD", "Café")
    assert composed != decomposed
    assert canonical_sha256({"k": composed}) == canonical_sha256({"k": decomposed})


def test_canonical_json_rejects_keys_that_collide_after_nfc() -> None:
    composed = unicodedata.normalize("NFC", "é")
    decomposed = unicodedata.normalize("NFD", "é")
    with pytest.raises(ValueError, match="duplicate key after NFC"):
        canonical_json({composed: 1, decomposed: 2})


def test_normalize_text_returns_nfc() -> None:
    assert normalize_text(unicodedata.normalize("NFD", "é")) == unicodedata.normalize("NFC", "é")


def test_canonical_json_preserves_sequence_order() -> None:
    assert canonical_json({"xs": [2, 1]}) != canonical_json({"xs": [1, 2]})


# --------------------------------------------------------------------------- #
# Identity: the twelve fields are each load-bearing
# --------------------------------------------------------------------------- #


def test_identity_payload_has_exactly_twelve_fields() -> None:
    payload = _identity().identity_payload()
    assert set(payload) == set(IDENTITY_FIELD_NAMES)
    assert len(IDENTITY_FIELD_NAMES) == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_snapshot_id", "telco_customer_churn@2026-08"),
        ("dataset_sha256", "e" * 64),
        ("model_data_split_manifest_sha256", "f" * 64),
        ("seed", 2),
        ("reference_construction_id", "corrupted-reference/v1"),
        ("injector_contract_version", "categorical-drift/v2"),
        ("model_specification_sha256", "0" * 64),
        ("preprocessing_specification_sha256", "1" * 64),
        ("intervention_type", "categorical_distribution_shift_v2"),
    ],
)
def test_each_scalar_identity_field_changes_the_family_id(field: str, value: object) -> None:
    baseline = family_id_for(_identity())
    changed = family_id_for(_identity(**{field: value}))
    assert changed != baseline


def test_changing_intervention_parameters_changes_the_family_id() -> None:
    baseline = family_id_for(_identity())
    changed = family_id_for(
        _identity(canonical_intervention_parameters=_drift_parameters(output_size=1408))
    )
    assert changed != baseline


def test_changing_fault_type_and_parameters_changes_the_family_id() -> None:
    baseline = family_id_for(_identity())
    label_noise = _identity(
        fault_type="label_noise",
        intervention_type="training_target_label_corruption",
        canonical_intervention_parameters=LabelNoiseParameters(
            flip_rate=0.05,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
    )
    assert family_id_for(label_noise) != baseline


def test_identity_schema_version_is_a_domain_separator() -> None:
    payload = _identity().identity_payload()
    tampered = copy.deepcopy(payload)
    tampered["identity_schema_version"] = "p2-family-identity/v2"
    assert canonical_sha256(tampered) != canonical_sha256(payload)


def test_fault_type_must_match_its_parameter_union() -> None:
    with pytest.raises(ValidationError, match="requires"):
        _identity(fault_type="label_noise")


# --------------------------------------------------------------------------- #
# Identity: forbidden inputs must not participate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "forbidden",
    [
        "measured_outcome",
        "eligibility_policy_version",
        "evidence_condition",
        "diagnosis_variant",
        "diagnosis_provider",
        "split_membership",
        "created_at",
        "artifact_path",
        "severity_rank",
        "reviewer_decision",
    ],
)
def test_identity_rejects_forbidden_fields(forbidden: str) -> None:
    with pytest.raises(ValidationError):
        _identity(**{forbidden: "anything"})


def test_family_id_is_invariant_across_sibling_conditions() -> None:
    identity = _identity()
    ids = {family_id_for(identity) for _ in ("full", "missing_key", "noisy")}
    assert len(ids) == 1


def test_identity_rejects_untrimmed_or_non_nfc_strings() -> None:
    with pytest.raises(ValidationError, match="whitespace"):
        _identity(reference_construction_id=" clean-test-reference/v1")
    with pytest.raises(ValidationError, match="NFC"):
        _identity(reference_construction_id=unicodedata.normalize("NFD", "référence"))


def test_identity_rejects_phase_one_dataset_namespace() -> None:
    with pytest.raises(ValidationError, match="Phase 1"):
        _identity(dataset_snapshot_id="p1-telco")


# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #


def test_family_and_candidate_ids_use_the_phase_two_namespace() -> None:
    identity = _identity()
    fingerprint = proposed_family_sha256(identity)
    family_id = family_id_for(identity)
    candidate = candidate_id_for(slot_id="M1-F1", family_fingerprint=fingerprint)

    assert family_id == f"{P2_FAMILY_PREFIX}{fingerprint}"
    assert candidate.startswith(P2_CANDIDATE_PREFIX)
    assert not family_id.startswith("p1-")
    assert not candidate.startswith("p1-")


def test_phase_one_identity_function_is_untouched_by_phase_two() -> None:
    """Phase 1 IDs must keep their frozen value and their own namespace."""

    p1_id = case_family_id_for(
        fault_type="data_drift",
        dataset_id="telco_customer_churn",
        dataset_sha256=_HEX_A,
        split_manifest_sha256=_HEX_B,
        injection_id="drift_contract_s1",
        injector="X",
        feature="Contract",
        seed=1,
        target_distribution={"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
        output_size=100,
    )
    assert p1_id.startswith("p1-family-")
    assert p1_id != family_id_for(_identity())


def test_candidate_id_depends_on_slot_and_fingerprint() -> None:
    fingerprint = proposed_family_sha256(_identity())
    first = candidate_id_for(slot_id="M1-F1", family_fingerprint=fingerprint)
    second = candidate_id_for(slot_id="M1-F2", family_fingerprint=fingerprint)
    assert first != second


def test_candidate_id_rejects_malformed_inputs() -> None:
    fingerprint = proposed_family_sha256(_identity())
    with pytest.raises(ValueError, match="slot pattern"):
        candidate_id_for(slot_id="bad slot", family_fingerprint=fingerprint)
    with pytest.raises(ValueError, match="SHA-256"):
        candidate_id_for(slot_id="M1-F1", family_fingerprint="not-a-digest")


def test_family_id_is_deterministic_for_equal_payloads() -> None:
    assert family_id_for(_identity()) == family_id_for(_identity())


def test_identity_payload_is_json_round_trippable() -> None:
    payload = _identity().identity_payload()
    assert json.loads(canonical_json(payload)) is not None


# --------------------------------------------------------------------------- #
# Parameter models
# --------------------------------------------------------------------------- #


def test_drift_parameters_reject_empty_or_negative_weights() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        _drift_parameters(target_distribution={})
    with pytest.raises(ValidationError, match="negative"):
        _drift_parameters(target_distribution={"a": -0.5})


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), float("-inf"), -0.0])
def test_drift_parameters_reject_noncanonical_weights(weight: float) -> None:
    with pytest.raises(ValidationError, match="finite|negative zero"):
        _drift_parameters(target_distribution={"a": weight})


def test_label_noise_parameters_bound_the_flip_rate() -> None:
    with pytest.raises(ValidationError):
        LabelNoiseParameters(
            flip_rate=0.6,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )


def test_preprocessing_parameters_require_distinct_ranks() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        PreprocessingBugParameters(
            target_feature="Contract",
            source_rank=2,
            mapped_rank=2,
            mode="inference_only",
            transform_name="one_hot_encoder",
        )


def test_parameter_models_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DataDriftParameters(
            feature="Contract",
            target_distribution={"a": 1.0},
            output_size=10,
            surprise="extra",  # type: ignore[call-arg]
        )
