"""Adversarial tests for v3.2 registration and atomic terminal evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_2_closeout import (
    V32ConfirmatoryCloseout,
    V32DatasetAttempt,
    V32ProtocolRegistrationReceipt,
    build_closeout,
    build_technical_failure,
    dataset_attempt,
    load_and_verify_terminal_store,
    registration_from_github_release,
    write_failure_store,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    load_v3_2_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3ExecutionEnvironmentReceipt,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import (
    PriorOnlyControlSummary,
    PriorShiftEstimatorEvidence,
    SensitivityDoseSummary,
    V3DatasetOutcome,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_inference import SeedNetEffect
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    V3_2_PROTOCOL_SHA256,
    CalibrationAbstention,
    Direction,
    ModelCalibrationAbstention,
    V3RuntimeError,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    EstimatorName,
    MmdClassResult,
    MmdDiagnostic,
    holm_adjust_all,
)

_COMMIT = "d63e4262961930d7d8126875d38c2c9625893f14"


_PRIMARY = "uci_default_of_credit_card_clients"
_REPLICATION = "uci_online_shoppers_purchasing_intention"
DatasetRole = Literal["primary", "external_replication"]


def _seed_effects(dataset_id: str, role: DatasetRole) -> tuple[SeedNetEffect, ...]:
    output: list[SeedNetEffect] = []
    directions: tuple[Direction, ...] = (
        "yes_to_no",
        "no_to_yes",
    )
    for direction_index, direction in enumerate(directions):
        for rate in (0.1, 0.2, 0.3):
            for seed in range(6101, 6151):
                effect = 0.10 * rate / 0.3 + (seed - 6125.5) * 1e-5
                control = tuple(0.7 + index * 0.002 for index in range(12))
                corrupted = tuple(value * (1.0 + effect) for value in control)
                output.append(
                    SeedNetEffect(
                        dataset_id=dataset_id,
                        dataset_role=role,
                        direction=direction,
                        conditional_rate=rate,
                        corruption_seed=seed,
                        mutation_sha256=f"{direction_index + 1:x}" * 64,
                        corrupted_model_sha256="a" * 64,
                        prior_matched_control_model_sha256="b" * 64,
                        reciprocal_control_model_sha256="c" * 64,
                        corrupted_losses=corrupted,
                        prior_matched_control_losses=control,
                        relative_net_effect=effect,
                        mutation_reconciled=True,
                        prior_match_reconciled=True,
                        reciprocal_prevalence_reconciled=True,
                        serialization_reconciled=True,
                    )
                )
    return tuple(output)


def _mmd(dataset_id: str, p_value: float) -> MmdDiagnostic:
    def class_result(label: Literal[0, 1]) -> MmdClassResult:
        return MmdClassResult(
            class_label=label,
            source_count=50,
            target_count=50,
            balanced_count=50,
            bandwidth_squared=1.0,
            statistic=0.01,
            permutation_p_value=p_value,
            resamples=2000,
            seed=314160 + label,
        )

    return MmdDiagnostic(
        dataset_id=dataset_id,
        representation="registered_preprocessed_model_inputs",
        statistic="balanced_linear_time_unbiased_rbf_mmd_squared",
        bandwidth="median_nonzero_deterministic_paired_squared_distance",
        permutation_p_value="plus_one_greater_or_equal",
        classes=(class_result(0), class_result(1)),
    )


def _synthetic_complete_outcome(
    dataset_id: str,
    role: DatasetRole,
    *,
    mmd_p: float = 0.5,
) -> V3DatasetOutcome:
    estimators: tuple[EstimatorName, ...] = (
        "unadjusted_v2",
        "oracle_prior_ratio",
        "bbse",
        "mlls_em",
        "rlls",
    )
    shift = tuple(
        PriorShiftEstimatorEvidence(
            dataset_id=dataset_id,
            odds_multiplier=multiplier,
            environment_seed=seed,
            environment_sha256="d" * 64,
            estimator=estimator,
            estimate_sha256="e" * 64,
            status="ok",
            estimated_positive_prior=0.5,
            oracle_positive_prior=0.5,
            reference_prior_log_loss=0.7,
            reason=None,
        )
        for multiplier in (0.25, 1.0, 4.0)
        for seed in range(7101, 7151)
        for estimator in estimators
    )
    controls = tuple(
        PriorOnlyControlSummary(
            odds_multiplier=multiplier,
            replicate_count=50,
            mean_relative_score_change=0.0,
            raw_one_sided_p_value=0.5,
            bonferroni_adjusted_p_value=1.0,
            label_noise_admission=False,
        )
        for multiplier in (0.25, 4.0)
    )
    sensitivity = tuple(
        SensitivityDoseSummary(
            direction=direction,
            conditional_rate=rate,
            replicate_count=50,
            mean_relative_net_effect=0.1,
        )
        for direction in ("yes_to_no", "no_to_yes")
        for rate in (0.1, 0.2, 0.3)
    )
    raw_mmd = {
        f"odds={multiplier:g}/class={label}": mmd_p
        for multiplier in (0.25, 1.0, 4.0)
        for label in (0, 1)
    }
    adjusted_mmd = holm_adjust_all(raw_mmd)
    return V3DatasetOutcome(
        protocol_sha256=V3_2_PROTOCOL_SHA256,
        dataset_id=dataset_id,
        dataset_role=role,
        execution_mode="registered_execution",
        corruption_replicate_count=50,
        environment_replicate_count=50,
        split_membership_sha256="1" * 64,
        runtime_split_sha256="2" * 64,
        preprocessor_sha256="3" * 64,
        output_columns_sha256="4" * 64,
        clean_primary_model_sha256="5" * 64,
        clean_roundtrip_model_sha256="5" * 64,
        clean_roundtrip_equal=True,
        seed_effects=_seed_effects(dataset_id, role),
        prior_shift_evidence=shift,
        prior_only_controls=controls,
        mmd_diagnostics=tuple(_mmd(dataset_id, mmd_p) for _ in range(3)),
        mmd_holm_adjusted_p_values=adjusted_mmd,
        assumptions_pass=all(value >= 0.05 for value in adjusted_mmd.values()),
        sensitivity_summaries=sensitivity,
    )


def _release_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 1234567,
        "tag_name": "p2-label-noise-shift-factorial-v3.2",
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-shift-factorial-v3.2"
        ),
        "created_at": "2026-08-24T04:00:00Z",
        "published_at": "2026-08-24T04:05:00Z",
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }
    payload.update(changes)
    return payload


def _registration() -> V32ProtocolRegistrationReceipt:
    return registration_from_github_release(
        protocol=load_v3_2_confirmatory_protocol(),
        tagged_protocol_commit=_COMMIT,
        payload=_release_payload(),
    )


def _environment() -> V3ExecutionEnvironmentReceipt:
    return V3ExecutionEnvironmentReceipt(
        execution_commit=_COMMIT,
        python_version="3.12.0",
        python_implementation="CPython",
        operating_system="test-os",
        machine="test-machine",
        package_versions={
            "numpy": "2.0",
            "pandas": "2.0",
            "pydantic": "2.0",
            "scikit-learn": "1.5",
            "scipy": "1.14",
        },
    )


def _abstention(
    dataset_id: str,
    role: Literal["primary", "external_replication"],
) -> ModelCalibrationAbstention:
    return ModelCalibrationAbstention(
        protocol_sha256=V3_2_PROTOCOL_SHA256,
        dataset_id=dataset_id,
        dataset_role=role,
        model_kind="logistic_regression",
        training_role="clean-reference",
        training_targets_sha256="a" * 64,
        sample_weights_sha256=None,
        calibration_abstention=CalibrationAbstention(
            reason_code="singular_hessian",
            iterations=0,
            gradient_infinity_norm=None,
            objective_mean=None,
            development_record_count=100,
        ),
    )


def _attempt(
    dataset_id: str,
    role: Literal["primary", "external_replication"],
) -> V32DatasetAttempt:
    return dataset_attempt(_abstention(dataset_id, role))


def _abstention_closeout() -> V32ConfirmatoryCloseout:
    registration = _registration()
    return build_closeout(
        protocol=load_v3_2_confirmatory_protocol(),
        registration=registration,
        environment=_environment(),
        execution_commit=_COMMIT,
        primary=_attempt(_PRIMARY, "primary"),
        replication=_attempt(_REPLICATION, "external_replication"),
        executed_at=registration.release_published_at + timedelta(hours=1),
    )


def test_complete_attempts_produce_one_protocol_bound_scientific_closeout() -> None:
    registration = _registration()
    primary = _synthetic_complete_outcome(
        "uci_default_of_credit_card_clients",
        "primary",
    )
    replication = _synthetic_complete_outcome(
        "uci_online_shoppers_purchasing_intention",
        "external_replication",
    )

    closeout = build_closeout(
        protocol=load_v3_2_confirmatory_protocol(),
        registration=registration,
        environment=_environment(),
        execution_commit=_COMMIT,
        primary=dataset_attempt(primary),
        replication=dataset_attempt(replication),
        executed_at=registration.release_published_at + timedelta(seconds=1),
    )

    assert closeout.disposition == "cross_dataset_admission"
    assert closeout.cross_dataset_claim_allowed
    assert len(closeout.assumption_families) == 3
    assert closeout.primary_inference is not None
    assert closeout.replication_inference is not None
    assert closeout.decision is not None
    assert closeout.primary_inference.protocol_sha256 == V3_2_PROTOCOL_SHA256
    assert closeout.replication_inference.protocol_sha256 == V3_2_PROTOCOL_SHA256
    assert closeout.decision.protocol_sha256 == V3_2_PROTOCOL_SHA256


def test_v3_2_registration_is_release_bound_and_outcome_free() -> None:
    registration = _registration()
    assert registration.protocol_sha256 == V3_2_PROTOCOL_SHA256
    assert registration.tag_name == "p2-label-noise-shift-factorial-v3.2"
    assert registration.immutable
    assert not registration.draft
    assert not registration.prerelease


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tag_name", "p2-label-noise-shift-factorial-v3.1"),
        ("immutable", False),
        ("draft", True),
        ("prerelease", True),
        ("html_url", "https://example.com/release"),
    ],
)
def test_registration_rejects_mutable_or_rebound_release(field: str, replacement: object) -> None:
    with pytest.raises(V3RuntimeError, match="immutable"):
        registration_from_github_release(
            protocol=load_v3_2_confirmatory_protocol(),
            tagged_protocol_commit=_COMMIT,
            payload=_release_payload(**{field: replacement}),
        )


def test_calibration_abstention_contains_no_partial_fit_or_metrics() -> None:
    attempt = _attempt(_PRIMARY, "primary")
    assert attempt.status == "abstain"
    assert attempt.outcome is None
    assert attempt.calibration_abstention is not None
    assert not attempt.predictive_metrics_generated
    assert not attempt.partial_model_reusable


def test_calibration_abstention_cannot_claim_or_expose_partial_inference() -> None:
    closeout = _abstention_closeout()
    assert closeout.disposition == "abstain"
    assert not closeout.cross_dataset_claim_allowed
    assert closeout.primary_inference is None
    assert closeout.replication_inference is None
    assert closeout.decision is None
    assert closeout.assumption_families == ()

    payload = closeout.model_dump()
    payload["cross_dataset_claim_allowed"] = True
    with pytest.raises(ValidationError, match="cannot allow"):
        V32ConfirmatoryCloseout.model_validate(payload)


def test_assumption_abstention_preserves_complete_scientific_inference() -> None:
    registration = _registration()
    primary = _synthetic_complete_outcome(_PRIMARY, "primary", mmd_p=0.001)
    replication = _synthetic_complete_outcome(
        _REPLICATION,
        "external_replication",
        mmd_p=0.001,
    )

    closeout = build_closeout(
        protocol=load_v3_2_confirmatory_protocol(),
        registration=registration,
        environment=_environment(),
        execution_commit=_COMMIT,
        primary=dataset_attempt(primary),
        replication=dataset_attempt(replication),
        executed_at=registration.release_published_at + timedelta(seconds=1),
    )

    assert closeout.disposition == "abstain"
    assert not closeout.cross_dataset_claim_allowed
    assert closeout.primary_inference is not None
    assert closeout.replication_inference is not None
    assert closeout.decision is not None
    assert closeout.decision.disposition == "abstain"
    assert len(closeout.assumption_families) == 3
    assert not all(item.assumptions_pass for item in closeout.assumption_families)


def test_abstention_rejects_mixed_or_partial_scientific_evidence() -> None:
    calibration = _abstention_closeout()
    payload = calibration.model_dump()
    payload["assumption_families"] = (
        {
            "odds_multiplier": 1.0,
            "raw_p_values": {"candidate": 0.5},
            "holm_adjusted_p_values": {"candidate": 0.5},
            "assumptions_pass": True,
        },
    )
    with pytest.raises(ValidationError, match="cannot expose assumption families"):
        V32ConfirmatoryCloseout.model_validate(payload)


def test_scientific_abstention_is_persisted_atomically(tmp_path: Path) -> None:
    registration = _registration()
    environment = _environment()
    primary = dataset_attempt(_synthetic_complete_outcome(_PRIMARY, "primary", mmd_p=0.001))
    replication = dataset_attempt(
        _synthetic_complete_outcome(
            _REPLICATION,
            "external_replication",
            mmd_p=0.001,
        )
    )
    closeout = build_closeout(
        protocol=load_v3_2_confirmatory_protocol(),
        registration=registration,
        environment=environment,
        execution_commit=_COMMIT,
        primary=primary,
        replication=replication,
        executed_at=registration.release_published_at + timedelta(seconds=1),
    )

    destination = tmp_path / "scientific-abstention"
    manifest = write_result_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )

    assert manifest.terminal_status == "abstain"
    assert load_and_verify_terminal_store(destination) == manifest


def test_result_store_is_atomic_content_addressed_and_non_overwriting(
    tmp_path: Path,
) -> None:
    registration = _registration()
    environment = _environment()
    primary = _attempt(_PRIMARY, "primary")
    replication = _attempt(_REPLICATION, "external_replication")
    closeout = build_closeout(
        protocol=load_v3_2_confirmatory_protocol(),
        registration=registration,
        environment=environment,
        execution_commit=_COMMIT,
        primary=primary,
        replication=replication,
        executed_at=registration.release_published_at + timedelta(hours=1),
    )
    destination = tmp_path / "terminal-store"
    manifest = write_result_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    assert manifest.terminal_status == "abstain"
    assert load_and_verify_terminal_store(destination) == manifest
    with pytest.raises(V3RuntimeError, match="already exists"):
        write_result_store(
            output_dir=destination,
            registration=registration,
            environment=environment,
            primary=primary,
            replication=replication,
            closeout=closeout,
        )


def test_store_tampering_is_detected(tmp_path: Path) -> None:
    registration = _registration()
    environment = _environment()
    primary = _attempt(_PRIMARY, "primary")
    replication = _attempt(_REPLICATION, "external_replication")
    closeout = _abstention_closeout()
    destination = tmp_path / "terminal-store"
    write_result_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    (destination / "primary-attempt.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="cannot load|checksum"):
        load_and_verify_terminal_store(destination)


def test_hard_failure_store_hashes_message_and_publishes_no_partial_outcome(
    tmp_path: Path,
) -> None:
    registration = _registration()
    environment = _environment()
    error = V3RuntimeError("private diagnostic detail")
    failure = build_technical_failure(
        registration=registration,
        execution_commit=_COMMIT,
        failure_stage="execute_replication",
        dataset_role="external_replication",
        error=error,
        failed_at=registration.release_published_at + timedelta(hours=1),
    )
    destination = tmp_path / "failure-store"
    manifest = write_failure_store(
        output_dir=destination,
        registration=registration,
        environment=environment,
        failure=failure,
    )
    assert manifest.terminal_status == "technical_failure"
    assert "private diagnostic detail" not in (destination / "technical-failure.json").read_text(
        encoding="utf-8"
    )
    assert not (destination / "primary-attempt.json").exists()
    assert not (destination / "replication-attempt.json").exists()
    assert load_and_verify_terminal_store(destination) == manifest


def test_failure_and_closeout_cannot_predate_registration() -> None:
    registration = _registration()
    before = registration.release_published_at - timedelta(seconds=1)
    with pytest.raises(V3RuntimeError, match="predate"):
        build_technical_failure(
            registration=registration,
            execution_commit=_COMMIT,
            failure_stage="load_primary",
            dataset_role="primary",
            error=V3RuntimeError("failure"),
            failed_at=before,
        )
    with pytest.raises(V3RuntimeError, match="after"):
        build_closeout(
            protocol=load_v3_2_confirmatory_protocol(),
            registration=registration,
            environment=_environment(),
            execution_commit=_COMMIT,
            primary=_attempt(_PRIMARY, "primary"),
            replication=_attempt(_REPLICATION, "external_replication"),
            executed_at=before,
        )


def test_failure_timestamp_must_be_timezone_aware() -> None:
    registration = _registration()
    with pytest.raises(V3RuntimeError, match="timezone"):
        build_technical_failure(
            registration=registration,
            execution_commit=_COMMIT,
            failure_stage="build_closeout",
            dataset_role=None,
            error=V3RuntimeError("failure"),
            failed_at=datetime(2026, 8, 24),
        )


def test_registration_release_timestamps_are_aware() -> None:
    registration = _registration()
    assert registration.release_created_at.tzinfo == UTC
    assert registration.release_published_at.tzinfo == UTC
