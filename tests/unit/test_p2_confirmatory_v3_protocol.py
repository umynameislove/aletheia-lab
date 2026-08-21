"""Adversarial contracts for the outcome-free v3 protocol candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import V3DatasetBinding
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    V3_PROTOCOL_SCHEMA_VERSION,
    DirectionEvidence,
    V3ConfirmatoryProtocol,
    V3ProtocolError,
    compile_dataset_split_receipt,
    evaluate_cross_dataset_decision,
    holm_adjusted_p_values,
    load_v3_confirmatory_protocol,
    reciprocal_pair_count,
    target_environment_class_counts,
    verify_compiled_split_receipts,
    verify_v3_protocol_artifacts,
)

_PROTOCOL_PATH = Path("configs/benchmark/p2_label_noise_shift_v3_protocol.json")
_PROTOCOL_SHA256 = "9b9db59c9555ecb41ef0ead5af3bafecf30189d69511e0b39711ebf1ff1220bf"
_DATASETS = (
    "uci_default_of_credit_card_clients",
    "uci_online_shoppers_purchasing_intention",
)


def _payload() -> dict[str, object]:
    value = json.loads(_PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _synthetic_binding_and_frame() -> tuple[V3DatasetBinding, pd.DataFrame]:
    frame = pd.DataFrame(
        {
            "ID": range(1, 2001),
            "feature": [index // 2 if index < 4 else index for index in range(2000)],
            "target": [0] * 1000 + [1] * 1000,
        }
    )
    binding = V3DatasetBinding.model_validate(
        {
            "dataset_id": "synthetic_protocol_dataset",
            "role": "primary",
            "uci_id": 999,
            "doi": "10.24432/TEST1",
            "source_page_uri": "https://archive.ics.uci.edu/dataset/999/test",
            "license": "CC_BY_4_0",
            "archive": {
                "source_uri": "https://archive.ics.uci.edu/static/public/999/test.zip",
                "file_name": "test.zip",
                "sha256": "a" * 64,
                "byte_count": 1,
                "member_path": "test.csv",
                "member_sha256": "b" * 64,
                "member_byte_count": 1,
                "member_count": 1,
            },
            "parser": {
                "format": "csv",
                "engine": "pandas_c",
                "sheet_name": None,
                "header_row": 0,
                "delimiter": ",",
                "keep_default_na": False,
            },
            "source_columns": ("ID", "feature", "target"),
            "target": {
                "column": "target",
                "storage": "integer_binary",
                "positive_token": "1",
                "negative_token": "0",
                "positive_semantics": "event",
            },
            "identifier_columns": ("ID",),
            "excluded_feature_columns": (),
            "post_outcome_exclusions": (),
            "categorical_features": (),
            "numeric_features": ("feature",),
            "expected_row_count": 2000,
            "expected_class_counts": {"0": 1000, "1": 1000},
            "minimum_records_per_class": 1000,
            "missing_value_policy": "fail_if_any_missing_or_blank",
            "undocumented_category_policy": "retain_as_explicit_other_category",
            "duplicate_split_policy": "keep_exact_analysis_feature_groups_within_one_partition",
            "split_seed": 7,
            "split_fractions": (0.6, 0.2, 0.2),
        }
    )
    return binding, frame


def _evidence(
    direction: str,
    *,
    effects: tuple[float, float] = (0.08, 0.07),
    lower: tuple[float, float] = (0.02, 0.01),
    p_values: tuple[float, float] = (0.01, 0.015),
    controls: bool = True,
    admissions: int = 0,
    assumptions: bool = True,
) -> DirectionEvidence:
    return DirectionEvidence.model_validate(
        {
            "direction": direction,
            "net_effects": dict(zip(_DATASETS, effects, strict=True)),
            "bootstrap_lower_bounds": dict(zip(_DATASETS, lower, strict=True)),
            "dataset_p_values": dict(zip(_DATASETS, p_values, strict=True)),
            "technical_controls_pass": controls,
            "prior_only_label_noise_admissions": admissions,
            "assumptions_pass": assumptions,
        }
    )


def test_protocol_loads_with_stable_hash_and_no_execution_authority() -> None:
    protocol = load_v3_confirmatory_protocol()

    assert protocol.schema_version == V3_PROTOCOL_SCHEMA_VERSION
    assert protocol.canonical_sha256() == _PROTOCOL_SHA256
    assert protocol.status == "frozen_protocol_candidate_not_registered"
    assert not protocol.model_fitted
    assert not protocol.predictive_metrics_generated
    assert not protocol.sealed_outcomes_generated
    assert not protocol.governance.registration_authorized_by_this_file
    assert not protocol.governance.execution_authorized_by_this_file


def test_protocol_transitively_verifies_design_datasets_and_v2_evidence() -> None:
    protocol = load_v3_confirmatory_protocol()
    design, manifest, receipt = verify_v3_protocol_artifacts(protocol)

    assert design.canonical_sha256() == protocol.artifacts.design_sha256
    assert manifest.canonical_sha256() == protocol.artifacts.dataset_manifest_sha256
    assert receipt.canonical_sha256() == protocol.artifacts.dataset_receipt_sha256
    assert tuple(item.dataset_id for item in protocol.dataset_splits) == _DATASETS


def test_split_receipts_are_exact_stratified_and_group_isolated() -> None:
    protocol = load_v3_confirmatory_protocol()
    primary, replication = protocol.dataset_splits

    assert primary.partition_counts == {"train": 18000, "development": 6000, "sealed_test": 6000}
    assert primary.partition_class_counts["train"] == {"0": 14018, "1": 3982}
    assert replication.partition_counts == {
        "train": 7399,
        "development": 2466,
        "sealed_test": 2465,
    }
    assert replication.partition_class_counts["sealed_test"] == {
        "false": 2084,
        "true": 381,
    }
    assert all(item.duplicate_group_cross_partition_count == 0 for item in protocol.dataset_splits)


def test_split_compiler_is_source_order_invariant_when_source_ids_are_stable() -> None:
    binding, frame = _synthetic_binding_and_frame()
    first = compile_dataset_split_receipt(
        dataset=binding,
        frame=frame,
        target_binding_sha256="c" * 64,
        record_identity_sha256="d" * 64,
    )
    shuffled = frame.sample(frac=1.0, random_state=17).reset_index(drop=True)
    second = compile_dataset_split_receipt(
        dataset=binding,
        frame=shuffled,
        target_binding_sha256="c" * 64,
        record_identity_sha256="d" * 64,
    )

    assert first.membership_sha256 == second.membership_sha256
    assert first.group_assignment_sha256 == second.group_assignment_sha256
    assert first.partition_counts == {"train": 1200, "development": 400, "sealed_test": 400}
    assert first.group_count == 1998


def test_recompiled_split_tampering_fails_closed() -> None:
    protocol = load_v3_confirmatory_protocol()
    changed = protocol.dataset_splits[0].model_copy(update={"membership_sha256": "0" * 64})

    with pytest.raises(V3ProtocolError, match="differ"):
        verify_compiled_split_receipts(protocol, (changed, protocol.dataset_splits[1]))


@pytest.mark.parametrize(
    ("source_count", "opposite_count", "rate", "expected"),
    [(100, 50, 0.3, 30), (100, 20, 0.3, 20), (20, 100, 0.3, 6)],
)
def test_reciprocal_control_is_one_to_one_and_reports_feasibility_cap(
    source_count: int, opposite_count: int, rate: float, expected: int
) -> None:
    assert reciprocal_pair_count(
        source_class_count=source_count,
        opposite_class_count=opposite_count,
        rate=rate,
    ) == expected


def test_prior_environment_counts_are_deterministic_and_preserve_total() -> None:
    low = target_environment_class_counts(
        total=1000, source_positive_prior=0.2, odds_multiplier=0.25
    )
    neutral = target_environment_class_counts(
        total=1000, source_positive_prior=0.2, odds_multiplier=1.0
    )
    high = target_environment_class_counts(
        total=1000, source_positive_prior=0.2, odds_multiplier=4.0
    )

    assert low == (941, 59)
    assert neutral == (800, 200)
    assert high == (500, 500)
    assert all(sum(counts) == 1000 for counts in (low, neutral, high))


def test_holm_and_intersection_union_decision_require_both_datasets() -> None:
    adjusted = holm_adjusted_p_values({"yes_to_no": 0.01, "no_to_yes": 0.04})
    assert adjusted == {"yes_to_no": 0.02, "no_to_yes": 0.04}

    decision = evaluate_cross_dataset_decision(
        (_evidence("yes_to_no"), _evidence("no_to_yes", p_values=(0.04, 0.06)))
    )
    assert decision.claim_allowed
    assert decision.disposition == "cross_dataset_admission"
    assert decision.direction_decisions[0].disposition == "pass"
    assert decision.direction_decisions[1].iut_p_value == 0.06
    assert decision.direction_decisions[1].disposition == "fail"


@pytest.mark.parametrize(
    "failed_evidence",
    [
        _evidence("yes_to_no", effects=(0.049, 0.08)),
        _evidence("yes_to_no", lower=(0.0, 0.01)),
        _evidence("yes_to_no", controls=False),
        _evidence("yes_to_no", admissions=1),
    ],
)
def test_any_failed_conjunct_blocks_direction(failed_evidence: DirectionEvidence) -> None:
    decision = evaluate_cross_dataset_decision(
        (failed_evidence, _evidence("no_to_yes", p_values=(0.2, 0.2)))
    )
    assert not decision.claim_allowed
    assert decision.direction_decisions[0].disposition == "fail"


def test_assumption_failure_abstains_instead_of_passing_or_becoming_evidence_against() -> None:
    decision = evaluate_cross_dataset_decision(
        (
            _evidence("yes_to_no", assumptions=False),
            _evidence("no_to_yes", p_values=(0.2, 0.2)),
        )
    )
    assert not decision.claim_allowed
    assert decision.direction_decisions[0].disposition == "abstain"
    assert decision.disposition == "abstain"


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("split_algorithm", "partition_order", ["development", "train", "sealed_test"]),
        ("split_algorithm", "fractions", [0.5, 0.25, 0.25]),
        ("preprocessing", "fit_partition", "all"),
        ("preprocessing", "deterministic_category_normalizations", []),
        ("models", "primary_parameters", ["C=1.0"]),
        ("models", "sensitivity_parameters", ["learning_rate=0.1"]),
        ("models", "calibration_tolerance", 1e-4),
        ("models", "calibration_probability_clip", 1e-6),
        ("models", "hyperparameter_search_forbidden", False),
        ("intervention", "directions", ["no_to_yes", "yes_to_no"]),
        ("intervention", "conditional_rates", [0.1, 0.2, 0.3, 0.4]),
        ("intervention", "co_primary_rate", 0.2),
        ("prior_shift", "odds_multipliers", [4.0, 1.0, 0.25]),
        ("prior_shift", "estimator_may_read_target", True),
        ("shift_estimators", "ordered_estimators", ["unadjusted_v2"]),
        ("shift_estimators", "bbse_condition_number_max", 100.0),
        ("shift_estimators", "silent_clipping_forbidden", False),
        ("inference", "minimum_practical_effect", 0.01),
        ("decision", "requirements", ["zero_prior_only_label_noise_admissions"]),
        ("decision", "any_dataset_failure_blocks_that_direction", False),
        ("governance", "execution_authorized_by_this_file", True),
    ],
)
def test_protocol_safeguards_cannot_be_relaxed(
    section: str, field: str, replacement: object
) -> None:
    payload = _payload()
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = replacement

    with pytest.raises(ValidationError):
        V3ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


def test_outcome_fields_and_partial_dataset_census_are_rejected() -> None:
    payload = _payload()
    payload["observed_effect"] = 1.0
    splits = payload["dataset_splits"]
    assert isinstance(splits, list)
    splits.pop()

    with pytest.raises(ValidationError):
        V3ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field",
    ["design_sha256", "dataset_manifest_sha256", "dataset_receipt_sha256", "v2_result_store_sha256"],
)
def test_transitive_artifact_hash_drift_fails_closed(field: str) -> None:
    protocol = load_v3_confirmatory_protocol()
    artifacts = protocol.artifacts.model_copy(update={field: "0" * 64})
    changed = protocol.model_copy(update={"artifacts": artifacts})

    with pytest.raises(V3ProtocolError, match="another"):
        verify_v3_protocol_artifacts(changed)


def test_split_receipt_cannot_be_rebound_to_other_target_records() -> None:
    protocol = load_v3_confirmatory_protocol()
    split = protocol.dataset_splits[0].model_copy(update={"target_binding_sha256": "0" * 64})
    changed = protocol.model_copy(update={"dataset_splits": (split, protocol.dataset_splits[1])})

    with pytest.raises(V3ProtocolError, match="not bound"):
        verify_v3_protocol_artifacts(changed)


def test_loaders_and_math_contracts_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(V3ProtocolError, match="unavailable"):
        load_v3_confirmatory_protocol(tmp_path / "missing.json")
    with pytest.raises(V3ProtocolError, match="two nonempty"):
        reciprocal_pair_count(source_class_count=0, opposite_class_count=1, rate=0.3)
    with pytest.raises(V3ProtocolError, match="binary pool"):
        target_environment_class_counts(total=1, source_positive_prior=0.2, odds_multiplier=1.0)
    with pytest.raises(V3ProtocolError, match="p-values"):
        holm_adjusted_p_values({"invalid": 2.0})
    with pytest.raises(V3ProtocolError, match="both directions"):
        evaluate_cross_dataset_decision((_evidence("yes_to_no"),))


def test_split_compiler_rejects_changed_rows_targets_and_identifiers() -> None:
    binding, frame = _synthetic_binding_and_frame()
    with pytest.raises(V3ProtocolError, match="row count"):
        compile_dataset_split_receipt(
            dataset=binding,
            frame=frame.iloc[:-1],
            target_binding_sha256="c" * 64,
            record_identity_sha256="d" * 64,
        )
    frame.loc[1999, "target"] = 2
    with pytest.raises(V3ProtocolError, match="target encoding"):
        compile_dataset_split_receipt(
            dataset=binding,
            frame=frame,
            target_binding_sha256="c" * 64,
            record_identity_sha256="d" * 64,
        )
    frame.loc[1999, "target"] = 1
    frame.loc[1999, "ID"] = 1
    with pytest.raises(V3ProtocolError, match="unique record"):
        compile_dataset_split_receipt(
            dataset=binding,
            frame=frame,
            target_binding_sha256="c" * 64,
            record_identity_sha256="d" * 64,
        )


def test_protocol_file_is_content_addressable() -> None:
    protocol = load_v3_confirmatory_protocol()
    raw_sha256 = hashlib.sha256(_PROTOCOL_PATH.read_bytes()).hexdigest()

    assert len(raw_sha256) == 64
    assert protocol.canonical_sha256() == _PROTOCOL_SHA256
