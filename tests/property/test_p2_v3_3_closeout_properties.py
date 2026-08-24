"""Property invariants for registered v3.3 terminal evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.confirmatory_v3_3_closeout import (
    V33ProtocolRegistrationReceipt,
    build_technical_failure,
    dataset_attempt,
    registration_from_github_release,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    V3_3_PROTOCOL_SHA256,
    load_v3_3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationAbstention,
    ModelCalibrationAbstention,
    V3RuntimeError,
)

_COMMIT = "9b05d56d008eac983a5ec30bbfef70ec1a06ad06"


def _registration() -> V33ProtocolRegistrationReceipt:
    return registration_from_github_release(
        protocol=load_v3_3_confirmatory_protocol(),
        tagged_protocol_commit=_COMMIT,
        payload={
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
        },
    )


@given(
    message=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=0xD7FF),
        min_size=1,
        max_size=300,
    )
)
def test_any_error_message_is_persisted_only_by_digest(message: str) -> None:
    registration = _registration()
    failure = build_technical_failure(
        registration=registration,
        execution_commit=_COMMIT,
        failure_stage="execute_primary",
        dataset_role="primary",
        error=V3RuntimeError(message),
        failed_at=registration.release_published_at + timedelta(seconds=1),
    )
    assert failure.exception_message_sha256 == hashlib.sha256(message.encode()).hexdigest()
    assert "exception_message" not in failure.model_fields_set


@given(replacement=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))
def test_any_cross_protocol_attempt_rebinding_is_rejected(replacement: str) -> None:
    if replacement == V3_3_PROTOCOL_SHA256:
        return
    evidence = ModelCalibrationAbstention(
        protocol_sha256=replacement,
        dataset_id="uci_default_of_credit_card_clients",
        dataset_role="primary",
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
    with pytest.raises(V3RuntimeError, match="another protocol"):
        dataset_attempt(evidence)


@given(offset_seconds=st.integers(min_value=1, max_value=86400))
def test_any_post_registration_failure_timestamp_is_accepted(offset_seconds: int) -> None:
    registration = _registration()
    timestamp = registration.release_published_at + timedelta(seconds=offset_seconds)
    failure = build_technical_failure(
        registration=registration,
        execution_commit=_COMMIT,
        failure_stage="build_closeout",
        dataset_role=None,
        error=V3RuntimeError("failure"),
        failed_at=timestamp,
    )
    assert failure.failed_at == timestamp
    assert failure.failed_at.tzinfo == UTC


def test_naive_failure_time_is_never_accepted() -> None:
    with pytest.raises(V3RuntimeError, match="timezone"):
        build_technical_failure(
            registration=_registration(),
            execution_commit=_COMMIT,
            failure_stage="load_primary",
            dataset_role="primary",
            error=V3RuntimeError("failure"),
            failed_at=datetime(2026, 8, 24),
        )
