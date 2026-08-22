"""Adversarial contracts for v3 registration, closeout, and result storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3ConfirmatoryCloseout,
    V3ExecutionEnvironmentReceipt,
    V3ProtocolRegistrationReceipt,
    build_assumption_families,
    load_and_verify_result_store,
    registration_from_github_release,
    write_result_store,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import (
    PriorOnlyControlSummary,
    PriorShiftEstimatorEvidence,
    SensitivityDoseSummary,
    V3DatasetOutcome,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_inference import (
    SeedNetEffect,
    analyze_dataset,
    decide_study,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    MmdClassResult,
    MmdDiagnostic,
    holm_adjust_all,
)

_PRIMARY = "uci_default_of_credit_card_clients"
_REPLICATION = "uci_online_shoppers_purchasing_intention"
_COMMIT = "0e05e3e0f360a42dd302fe4b1c84afc871d871f3"


def _release_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 987654,
        "tag_name": "p2-label-noise-shift-factorial-v3.1",
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-shift-factorial-v3.1"
        ),
        "created_at": "2026-08-21T01:00:00Z",
        "published_at": "2026-08-21T01:05:00Z",
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }
    payload.update(changes)
    return payload


def _seed_effects(dataset_id: str, role: str) -> tuple[SeedNetEffect, ...]:
    output: list[SeedNetEffect] = []
    for direction_index, direction in enumerate(("yes_to_no", "no_to_yes")):
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
    return MmdDiagnostic(
        dataset_id=dataset_id,
        representation="registered_preprocessed_model_inputs",
        statistic="balanced_linear_time_unbiased_rbf_mmd_squared",
        bandwidth="median_nonzero_deterministic_paired_squared_distance",
        permutation_p_value="plus_one_greater_or_equal",
        classes=(
            MmdClassResult(
                class_label=0,
                source_count=50,
                target_count=50,
                balanced_count=50,
                bandwidth_squared=1.0,
                statistic=0.01,
                permutation_p_value=p_value,
                resamples=2000,
                seed=314160,
            ),
            MmdClassResult(
                class_label=1,
                source_count=50,
                target_count=50,
                balanced_count=50,
                bandwidth_squared=1.0,
                statistic=0.01,
                permutation_p_value=p_value,
                resamples=2000,
                seed=314161,
            ),
        ),
    )


def _outcome(dataset_id: str, role: str, *, mmd_p: float = 0.5) -> V3DatasetOutcome:
    estimators = ("unadjusted_v2", "oracle_prior_ratio", "bbse", "mlls_em", "rlls")
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
    return V3DatasetOutcome(
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
        mmd_holm_adjusted_p_values=holm_adjust_all(raw_mmd),
        assumptions_pass=all(value >= 0.05 for value in holm_adjust_all(raw_mmd).values()),
        sensitivity_summaries=sensitivity,
    )


def _registration() -> V3ProtocolRegistrationReceipt:
    return registration_from_github_release(
        protocol=load_v3_confirmatory_protocol(),
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


def _closeout(
    primary: V3DatasetOutcome, replication: V3DatasetOutcome
) -> V3ConfirmatoryCloseout:
    protocol = load_v3_confirmatory_protocol()
    families = build_assumption_families(primary, replication)
    assumptions = all(item.assumptions_pass for item in families)
    primary_inference = analyze_dataset(
        protocol=protocol,
        dataset_id=primary.dataset_id,
        dataset_role=primary.dataset_role,
        split_membership_sha256=primary.split_membership_sha256,
        seed_effects=primary.seed_effects,
        prior_only_admissions={"yes_to_no": 0, "no_to_yes": 0},
        assumptions_pass={"yes_to_no": assumptions, "no_to_yes": assumptions},
        bootstrap_resamples=200,
        sign_flip_resamples=1000,
    )
    replication_inference = analyze_dataset(
        protocol=protocol,
        dataset_id=replication.dataset_id,
        dataset_role=replication.dataset_role,
        split_membership_sha256=replication.split_membership_sha256,
        seed_effects=replication.seed_effects,
        prior_only_admissions={"yes_to_no": 0, "no_to_yes": 0},
        assumptions_pass={"yes_to_no": assumptions, "no_to_yes": assumptions},
        bootstrap_resamples=200,
        sign_flip_resamples=1000,
    )
    decision = decide_study(
        protocol=protocol,
        primary=primary_inference,
        replication=replication_inference,
    )
    registration = _registration()
    environment = _environment()
    return V3ConfirmatoryCloseout(
        registration_sha256=registration.canonical_sha256(),
        execution_commit=_COMMIT,
        executed_at=registration.release_published_at + timedelta(hours=1),
        environment_sha256=environment.canonical_sha256(),
        primary_outcome_sha256=primary.canonical_sha256(),
        replication_outcome_sha256=replication.canonical_sha256(),
        primary_inference=primary_inference,
        replication_inference=replication_inference,
        assumption_families=families,
        decision=decision,
    )


def test_registration_requires_exact_immutable_public_release() -> None:
    receipt = _registration()
    assert receipt.immutable
    assert receipt.release_published_at < datetime.now(UTC)
    with pytest.raises(V3RuntimeError, match="immutable"):
        registration_from_github_release(
            protocol=load_v3_confirmatory_protocol(),
            tagged_protocol_commit=_COMMIT,
            payload=_release_payload(immutable=False),
        )
    with pytest.raises(V3RuntimeError, match="immutable"):
        registration_from_github_release(
            protocol=load_v3_confirmatory_protocol(),
            tagged_protocol_commit=_COMMIT,
            payload=_release_payload(html_url="https://github.com/other/repo/releases/tag/v3"),
        )


def test_cross_dataset_mmd_holm_is_global_and_fail_closed() -> None:
    primary = _outcome(_PRIMARY, "primary", mmd_p=0.5)
    replication = _outcome(_REPLICATION, "external_replication", mmd_p=0.01)
    families = build_assumption_families(primary, replication)
    assert len(families) == 3
    assert all(len(item.raw_p_values) == 4 for item in families)
    assert all(not item.assumptions_pass for item in families)
    assert all(
        max(item.holm_adjusted_p_values.values()) >= max(item.raw_p_values.values())
        for item in families
    )


def test_registered_outcome_rejects_replayed_shift_census() -> None:
    outcome = _outcome(_PRIMARY, "primary")
    payload = outcome.model_dump()
    evidence = list(payload["prior_shift_evidence"])
    evidence[-1] = evidence[0]
    payload["prior_shift_evidence"] = tuple(evidence)
    with pytest.raises(ValueError, match="shift-estimator census"):
        V3DatasetOutcome.model_validate(payload)


def test_dataset_outcome_rejects_fabricated_local_mmd_report() -> None:
    outcome = _outcome(_PRIMARY, "primary")
    payload = outcome.model_dump()
    payload["mmd_holm_adjusted_p_values"] = {
        key: 0.5 for key in outcome.mmd_holm_adjusted_p_values
    }
    with pytest.raises(ValueError, match="MMD Holm report"):
        V3DatasetOutcome.model_validate(payload)


def test_atomic_store_roundtrip_and_tamper_detection(tmp_path) -> None:
    primary = _outcome(_PRIMARY, "primary")
    replication = _outcome(_REPLICATION, "external_replication")
    closeout = _closeout(primary, replication)
    destination = tmp_path / "result-store"
    manifest = write_result_store(
        output_dir=destination,
        registration=_registration(),
        environment=_environment(),
        primary=primary,
        replication=replication,
        closeout=closeout,
    )
    assert load_and_verify_result_store(destination) == manifest
    with (destination / "primary-outcome.json").open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(V3RuntimeError, match="checksum"):
        load_and_verify_result_store(destination)


def test_result_store_never_overwrites_existing_destination(tmp_path) -> None:
    primary = _outcome(_PRIMARY, "primary")
    replication = _outcome(_REPLICATION, "external_replication")
    closeout = _closeout(primary, replication)
    destination = tmp_path / "result-store"
    destination.mkdir()
    with pytest.raises(V3RuntimeError, match="already exists"):
        write_result_store(
            output_dir=destination,
            registration=_registration(),
            environment=_environment(),
            primary=primary,
            replication=replication,
            closeout=closeout,
        )
