"""Registration, compilation, and closeout contracts for P2R v1.2."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_closeout import ExecutionEnvironmentReceipt
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
)
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RCloseoutError,
    build_technical_failure,
    load_and_verify_terminal_store,
    write_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    P2RArchiveReadinessReceipt,
    build_p2r_archive_readiness,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_execution import (
    P2R_V1_2_TAGGED_COMMIT,
    P2RV12ExecutionError,
    P2RV12Registration,
    build_sealed_marker,
    compile_execution_protocol,
    registration_from_release,
    verify_registration_pair,
    write_marker_exclusive,
)
from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    P2RV12MethodologicalAmendmentProtocol,
    load_p2r_v1_2_protocol,
)


def _amendments() -> tuple[
    P2RV12MethodologicalAmendmentProtocol,
    P2RV12MethodologicalAmendmentProtocol,
]:
    return (
        load_p2r_v1_2_protocol(DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH),
        load_p2r_v1_2_protocol(DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH),
    )


@pytest.fixture(scope="module")
def readiness() -> P2RArchiveReadinessReceipt:
    return build_p2r_archive_readiness(
        manifest=load_v3_dataset_binding_manifest(),
        pinned_receipt=load_v3_dataset_binding_receipt(),
        archive_directory="data/raw/p2-v3",
    )


def _release(amendment: P2RV12MethodologicalAmendmentProtocol, marker: int) -> dict[str, object]:
    timestamp = f"2026-08-29T0{marker}:00:00Z"
    return {
        "tag_name": amendment.governance.required_git_tag,
        "id": marker,
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            + amendment.governance.required_git_tag
        ),
        "created_at": timestamp,
        "published_at": timestamp,
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }


def _registrations(
    readiness: P2RArchiveReadinessReceipt,
) -> tuple[P2RV12Registration, P2RV12Registration]:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)
    registrations = tuple(
        registration_from_release(
            amendment=amendment,
            execution_protocol=protocol,
            archive_readiness=readiness,
            tagged_protocol_commit=P2R_V1_2_TAGGED_COMMIT,
            payload=_release(amendment, index),
        )
        for index, (amendment, protocol) in enumerate(
            zip(amendments, protocols, strict=True), start=1
        )
    )
    return registrations  # type: ignore[return-value]


def test_compilation_changes_only_registered_target_and_identity() -> None:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)

    assert tuple(item.protocol_version for item in protocols) == (
        "p2r-data_drift-confirmatory/1.2",
        "p2r-preprocessing_bug-confirmatory/1.2",
    )
    assert tuple(item.datasets[1].target_feature for item in protocols) == (
        "OperatingSystems",
        "OperatingSystems",
    )
    for amendment, protocol in zip(amendments, protocols, strict=True):
        assert amendment.scientific_invariants.model_or_preprocessing_changed is False
        assert protocol.model.parameters == (
            "C=1.0",
            "solver=lbfgs",
            "max_iter=1000",
            "random_state=42",
        )
        assert protocol.execution.seeds == (8201, 8202, 8203, 8204, 8205)
        assert protocol.endpoint.minimum_practical_effect == 0.01
        assert protocol.execution.maximum_registered_execution_attempts == 1


def test_registration_binds_amendment_execution_and_archive_readiness(
    readiness: P2RArchiveReadinessReceipt,
) -> None:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)
    registrations = _registrations(readiness)

    checked = verify_registration_pair(
        amendments, protocols, registrations, readiness
    )
    assert tuple(item.amendment_protocol_sha256 for item in checked) == tuple(
        item.canonical_sha256() for item in amendments
    )
    assert tuple(item.protocol_sha256 for item in checked) == tuple(
        item.canonical_sha256() for item in protocols
    )
    assert {item.archive_readiness_sha256 for item in checked} == {
        readiness.canonical_sha256()
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amendment_protocol_sha256", "0" * 64),
        ("protocol_sha256", "1" * 64),
        ("archive_readiness_sha256", "2" * 64),
    ],
)
def test_registration_replay_or_rebinding_fails_closed(
    readiness: P2RArchiveReadinessReceipt,
    field: str,
    value: str,
) -> None:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)
    registrations = list(_registrations(readiness))
    payload = registrations[0].model_dump()
    payload[field] = value
    registrations[0] = P2RV12Registration.model_validate(payload)
    with pytest.raises(P2RV12ExecutionError, match="frozen amendment chain"):
        verify_registration_pair(amendments, protocols, registrations, readiness)


def test_release_contract_rejects_mutable_or_cross_mechanism_evidence(
    readiness: P2RArchiveReadinessReceipt,
) -> None:
    amendment = _amendments()[0]
    protocol = compile_execution_protocol(amendment)
    payload = _release(amendment, 1)
    payload["immutable"] = False
    with pytest.raises(P2RV12ExecutionError, match="immutable"):
        registration_from_release(
            amendment=amendment,
            execution_protocol=protocol,
            archive_readiness=readiness,
            tagged_protocol_commit=P2R_V1_2_TAGGED_COMMIT,
            payload=payload,
        )

    payload = _release(amendment, 1)
    payload["tag_name"] = "p2r-preprocessing-mismatch-confirmatory-v1.2"
    with pytest.raises(P2RV12ExecutionError, match="immutable"):
        registration_from_release(
            amendment=amendment,
            execution_protocol=protocol,
            archive_readiness=readiness,
            tagged_protocol_commit=P2R_V1_2_TAGGED_COMMIT,
            payload=payload,
        )


def test_marker_is_content_bound_and_single_use(
    tmp_path: Path,
    readiness: P2RArchiveReadinessReceipt,
) -> None:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)
    registrations = _registrations(readiness)
    marker = build_sealed_marker(
        execution_commit="a" * 40,
        amendments=amendments,
        protocols=protocols,
        registrations=registrations,
        archive_readiness=readiness,
        opened_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    path = tmp_path / "sealed-open.json"
    write_marker_exclusive(path, marker)
    with pytest.raises(P2RV12ExecutionError, match="rerun is forbidden"):
        write_marker_exclusive(path, marker)
    payload = marker.model_dump()
    payload["registration_sha256s"] = ("0" * 64, "1" * 64)
    with pytest.raises(ValidationError, match="marker hash"):
        type(marker).model_validate(payload)


def test_terminal_store_round_trips_v1_2_registration_schema(
    tmp_path: Path,
    readiness: P2RArchiveReadinessReceipt,
) -> None:
    amendments = _amendments()
    protocols = tuple(compile_execution_protocol(item) for item in amendments)
    registrations = _registrations(readiness)
    commit = "b" * 40
    environment = ExecutionEnvironmentReceipt(
        execution_commit=commit,
        python_version="3.12.0",
        python_implementation="CPython",
        operating_system="test",
        machine="test",
        package_versions={
            "numpy": "2",
            "pandas": "2",
            "pydantic": "2",
            "scikit-learn": "1",
            "scipy": "1",
        },
    )
    failure = build_technical_failure(
        protocols=protocols,
        registrations=registrations,
        execution_commit=commit,
        failure_stage="load_primary",
        error=RuntimeError("synthetic boundary failure"),
    )
    marker = build_sealed_marker(
        execution_commit=commit,
        amendments=amendments,
        protocols=protocols,
        registrations=registrations,
        archive_readiness=readiness,
        opened_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    with pytest.raises(P2RCloseoutError, match="sealed-open marker"):
        write_terminal_store(
            output_dir=tmp_path / "incomplete-store",
            protocols=protocols,
            registrations=registrations,
            terminal=failure,
            environment=environment,
        )
    store = write_terminal_store(
        output_dir=tmp_path / "store",
        protocols=protocols,
        registrations=registrations,
        terminal=failure,
        environment=environment,
        sealed_marker=marker,
    )
    assert (tmp_path / "store" / "sealed-open.json").is_file()
    assert load_and_verify_terminal_store(tmp_path / "store") == store
