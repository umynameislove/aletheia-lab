"""Adversarial tests for v3.2 registration and atomic terminal evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Literal, cast

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
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import V3DatasetOutcome
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    V3_2_PROTOCOL_SHA256,
    CalibrationAbstention,
    ModelCalibrationAbstention,
    V3RuntimeError,
)

_COMMIT = "d63e4262961930d7d8126875d38c2c9625893f14"


def _synthetic_complete_outcome(dataset_id: str, role: str) -> V3DatasetOutcome:
    """Reuse the established registered-shape fixture without static test coupling."""

    fixture_module = import_module("tests.unit.test_p2_confirmatory_v3_closeout")
    factory = cast(
        Callable[[str, str], V3DatasetOutcome],
        fixture_module._outcome,
    )
    return factory(dataset_id, role)


_PRIMARY = "uci_default_of_credit_card_clients"
_REPLICATION = "uci_online_shoppers_purchasing_intention"


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
    ).model_copy(update={"protocol_sha256": V3_2_PROTOCOL_SHA256})
    replication = _synthetic_complete_outcome(
        "uci_online_shoppers_purchasing_intention",
        "external_replication",
    ).model_copy(update={"protocol_sha256": V3_2_PROTOCOL_SHA256})

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


def test_abstention_closeout_cannot_claim_or_expose_partial_inference() -> None:
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
