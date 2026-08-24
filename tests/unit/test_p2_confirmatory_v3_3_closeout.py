"""Adversarial contracts for v3.3 registration and atomic closeout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_3_closeout import (
    V33ConfirmatoryCloseout,
    build_closeout,
    build_technical_failure,
    dataset_attempt,
    load_and_verify_terminal_store,
    registration_from_github_release,
    write_failure_store,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    V3_3_PROTOCOL_SHA256,
    load_v3_3_confirmatory_protocol,
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
    CalibrationAbstention,
    Direction,
    ModelCalibrationAbstention,
    V3RuntimeError,
    validate_registered_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    EstimatorName,
    MmdClassResult,
    MmdDiagnostic,
    holm_adjust_all,
)

_COMMIT = "9b05d56d008eac983a5ec30bbfef70ec1a06ad06"
_PRIMARY = "uci_default_of_credit_card_clients"
_REPLICATION = "uci_online_shoppers_purchasing_intention"
DatasetRole = Literal["primary", "external_replication"]


def _release_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 3_300_001,
        "tag_name": "p2-label-noise-shift-factorial-v3.3",
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-shift-factorial-v3.3"
        ),
        "created_at": "2026-08-20T08:00:00Z",
        "published_at": "2026-08-20T08:05:00Z",
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }
    payload.update(changes)
    return payload


def _registration():  # type: ignore[no-untyped-def]
    return registration_from_github_release(
        protocol=load_v3_3_confirmatory_protocol(),
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


def _abstention(dataset_id: str, role: DatasetRole) -> ModelCalibrationAbstention:
    return ModelCalibrationAbstention(
        protocol_sha256=V3_3_PROTOCOL_SHA256,
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


def _seed_effects(
    dataset_id: str,
    role: DatasetRole,
    *,
    signal: float = 0.10,
) -> tuple[SeedNetEffect, ...]:
    output = []
    directions: tuple[Direction, ...] = ("yes_to_no", "no_to_yes")
    for direction_index, direction in enumerate(directions):
        for rate in (0.1, 0.2, 0.3):
            for seed in range(6101, 6151):
                effect = signal * rate / 0.3 + (seed - 6125.5) * 1e-5
                control = tuple(0.7 + index * 0.002 for index in range(12))
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
                        corrupted_losses=tuple(value * (1.0 + effect) for value in control),
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
    classes = tuple(
        MmdClassResult(
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
        for label in (0, 1)
    )
    return MmdDiagnostic(
        dataset_id=dataset_id,
        representation="registered_preprocessed_model_inputs",
        statistic="balanced_linear_time_unbiased_rbf_mmd_squared",
        bandwidth="median_nonzero_deterministic_paired_squared_distance",
        permutation_p_value="plus_one_greater_or_equal",
        classes=classes,
    )


def _outcome(
    dataset_id: str,
    role: DatasetRole,
    *,
    mmd_p: float = 0.5,
    signal: float = 0.10,
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
        protocol_sha256=V3_3_PROTOCOL_SHA256,
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
        seed_effects=_seed_effects(dataset_id, role, signal=signal),
        prior_shift_evidence=shift,
        prior_only_controls=controls,
        mmd_diagnostics=tuple(_mmd(dataset_id, mmd_p) for _ in range(3)),
        mmd_holm_adjusted_p_values=adjusted_mmd,
        assumptions_pass=all(value >= 0.05 for value in adjusted_mmd.values()),
        sensitivity_summaries=sensitivity,
    )


def _closeout(
    *, mmd_p: float = 0.5, signal: float = 0.10
) -> tuple[object, object, object, object]:
    registration = _registration()
    primary = dataset_attempt(_outcome(_PRIMARY, "primary", mmd_p=mmd_p, signal=signal))
    replication = dataset_attempt(
        _outcome(
            _REPLICATION,
            "external_replication",
            mmd_p=mmd_p,
            signal=signal,
        )
    )
    closeout = build_closeout(
        protocol=load_v3_3_confirmatory_protocol(),
        registration=registration,
        environment=_environment(),
        execution_commit=_COMMIT,
        primary=primary,
        replication=replication,
        executed_at=registration.release_published_at + timedelta(seconds=1),
    )
    return registration, primary, replication, closeout


def test_registered_runtime_accepts_exact_v3_3_identity() -> None:
    protocol = load_v3_3_confirmatory_protocol()
    assert validate_registered_protocol(protocol) == protocol


def test_registration_is_immutable_release_bound_and_outcome_free() -> None:
    registration = _registration()
    assert registration.protocol_sha256 == V3_3_PROTOCOL_SHA256
    assert registration.tag_name == "p2-label-noise-shift-factorial-v3.3"
    assert registration.immutable and not registration.draft and not registration.prerelease


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tag_name", "p2-label-noise-shift-factorial-v3.2"),
        ("immutable", False),
        ("draft", True),
        ("prerelease", True),
        ("html_url", "https://example.com/release"),
    ],
)
def test_registration_rejects_mutable_or_rebound_release(
    field: str, replacement: object
) -> None:
    with pytest.raises(V3RuntimeError, match="immutable"):
        registration_from_github_release(
            protocol=load_v3_3_confirmatory_protocol(),
            tagged_protocol_commit=_COMMIT,
            payload=_release_payload(**{field: replacement}),
        )


def test_complete_attempts_can_produce_cross_dataset_admission() -> None:
    _, _, _, closeout = _closeout()
    assert closeout.disposition == "cross_dataset_admission"
    assert closeout.cross_dataset_claim_allowed
    assert len(closeout.assumption_families) == 3
    assert closeout.primary_inference is not None
    assert closeout.replication_inference is not None
    assert closeout.decision is not None


def test_scientific_abstention_preserves_complete_inference() -> None:
    _, _, _, closeout = _closeout(mmd_p=0.001)
    assert closeout.disposition == "abstain"
    assert not closeout.cross_dataset_claim_allowed
    assert closeout.primary_inference is not None
    assert closeout.replication_inference is not None
    assert closeout.decision is not None
    assert len(closeout.assumption_families) == 3


def test_complete_but_nonconfirming_evidence_fails_closed() -> None:
    _, _, _, closeout = _closeout(signal=0.0)
    assert closeout.disposition == "fail_closed"
    assert not closeout.cross_dataset_claim_allowed
    assert closeout.primary_inference is not None
    assert closeout.replication_inference is not None
    assert closeout.decision is not None


def test_calibration_abstention_exposes_no_partial_scientific_evidence() -> None:
    registration = _registration()
    primary = dataset_attempt(_abstention(_PRIMARY, "primary"))
    replication = dataset_attempt(_abstention(_REPLICATION, "external_replication"))
    closeout = build_closeout(
        protocol=load_v3_3_confirmatory_protocol(),
        registration=registration,
        environment=_environment(),
        execution_commit=_COMMIT,
        primary=primary,
        replication=replication,
        executed_at=registration.release_published_at + timedelta(seconds=1),
    )
    assert closeout.disposition == "abstain"
    assert closeout.primary_inference is None
    assert closeout.replication_inference is None
    assert closeout.decision is None
    assert closeout.assumption_families == ()


def test_mixed_scientific_evidence_fails_closed() -> None:
    _, _, _, closeout = _closeout(mmd_p=0.001)
    payload = closeout.model_dump()
    payload["replication_inference"] = None
    with pytest.raises(ValidationError, match="both complete"):
        V33ConfirmatoryCloseout.model_validate(payload)


def test_result_store_is_atomic_non_overwriting_and_tamper_evident(tmp_path: Path) -> None:
    registration, primary, replication, closeout = _closeout(mmd_p=0.001)
    destination = tmp_path / "terminal-store"
    manifest = write_result_store(
        output_dir=destination,
        registration=registration,
        environment=_environment(),
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
            environment=_environment(),
            primary=primary,
            replication=replication,
            closeout=closeout,
        )
    (destination / "primary-attempt.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(V3RuntimeError, match="cannot load|checksum"):
        load_and_verify_terminal_store(destination)


def test_technical_failure_hashes_message_and_publishes_no_partial_outcome(
    tmp_path: Path,
) -> None:
    registration = _registration()
    failure = build_technical_failure(
        registration=registration,
        execution_commit=_COMMIT,
        failure_stage="execute_replication",
        dataset_role="external_replication",
        error=V3RuntimeError("private diagnostic detail"),
        failed_at=registration.release_published_at + timedelta(seconds=1),
    )
    destination = tmp_path / "failure-store"
    manifest = write_failure_store(
        output_dir=destination,
        registration=registration,
        environment=_environment(),
        failure=failure,
    )
    assert manifest.terminal_status == "technical_failure"
    assert "private diagnostic detail" not in (destination / "technical-failure.json").read_text()
    assert not (destination / "primary-attempt.json").exists()
    assert load_and_verify_terminal_store(destination) == manifest


def test_closeout_and_failure_cannot_predate_release() -> None:
    registration = _registration()
    before = registration.release_published_at - timedelta(seconds=1)
    with pytest.raises(V3RuntimeError, match="predate"):
        build_technical_failure(
            registration=registration,
            execution_commit=_COMMIT,
            failure_stage="build_closeout",
            dataset_role=None,
            error=V3RuntimeError("failure"),
            failed_at=before,
        )
    with pytest.raises(V3RuntimeError, match="after"):
        build_closeout(
            protocol=load_v3_3_confirmatory_protocol(),
            registration=registration,
            environment=_environment(),
            execution_commit=_COMMIT,
            primary=dataset_attempt(_abstention(_PRIMARY, "primary")),
            replication=dataset_attempt(_abstention(_REPLICATION, "external_replication")),
            executed_at=before,
        )


def test_registration_and_failure_timestamps_require_timezone() -> None:
    registration = _registration()
    assert registration.release_created_at.tzinfo == UTC
    with pytest.raises(V3RuntimeError, match="registration"):
        registration_from_github_release(
            protocol=load_v3_3_confirmatory_protocol(),
            tagged_protocol_commit=_COMMIT,
            payload=_release_payload(published_at="not-a-date"),
        )
    with pytest.raises(V3RuntimeError, match="timezone"):
        build_technical_failure(
            registration=registration,
            execution_commit=_COMMIT,
            failure_stage="build_closeout",
            dataset_role=None,
            error=V3RuntimeError("failure"),
            failed_at=datetime(2026, 8, 24),
        )
