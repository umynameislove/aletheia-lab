"""Strict evidence contracts for the P2R v1.1 recovery runtime."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_closeout import ExecutionEnvironmentReceipt
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    DatasetBindingAudit,
    V3DatasetBinding,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
)
from aletheia_lab.benchmark.p2.instrument_validity import load_instrument_validity_protocol
from aletheia_lab.benchmark.p2.lightweight_protocol import LightweightConfirmatoryProtocol
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RProtocolRegistration,
    build_joint_closeout,
    build_technical_failure,
)
from aletheia_lab.benchmark.p2.p2r_recovery import (
    P2RArchiveReadinessReceipt,
    build_p2r_archive_readiness,
    load_p2r_v1_failure_audit,
)
from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    P2RRecoveryExecutionError,
    P2RRecoveryRegistration,
    P2RRecoverySealedMarker,
    RecoveryStoreEntry,
    build_recovery_sealed_marker,
    load_and_verify_recovery_terminal_store,
    load_recovery_marker,
    recovery_registration_from_release,
    verify_recovery_registration_pair,
    write_recovery_marker_exclusive,
    write_recovery_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    P2RRecoveryProtocol,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DATASET_MEASUREMENT_SCHEMA_VERSION,
    DatasetSeedMeasurement,
    build_joint_candidate_plan,
    paired_observations,
)


def _recoveries() -> tuple[P2RRecoveryProtocol, P2RRecoveryProtocol]:
    return (
        load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH),
        load_p2r_recovery_protocol(DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH),
    )


def _predecessors() -> tuple[LightweightConfirmatoryProtocol, LightweightConfirmatoryProtocol]:
    drift, preprocessing = _recoveries()
    return (
        verify_p2r_recovery_protocol(drift)[1],
        verify_p2r_recovery_protocol(preprocessing)[1],
    )


def _release(recovery: P2RRecoveryProtocol, marker: int) -> P2RRecoveryRegistration:
    created = datetime(2026, 8, 28, marker, tzinfo=UTC)
    return recovery_registration_from_release(
        recovery=recovery,
        tagged_protocol_commit=f"{marker:040x}",
        payload={
            "tag_name": recovery.governance.required_git_tag,
            "id": marker,
            "html_url": (
                "https://github.com/umynameislove/aletheia-lab/releases/tag/"
                + recovery.governance.required_git_tag
            ),
            "created_at": created.isoformat(),
            "published_at": (created + timedelta(minutes=1)).isoformat(),
            "immutable": True,
            "draft": False,
            "prerelease": False,
        },
    )


def _scientific_registration(protocol, marker: int) -> P2RProtocolRegistration:  # type: ignore[no-untyped-def]
    created = datetime(2026, 8, 27, marker, tzinfo=UTC)
    return P2RProtocolRegistration(
        mechanism=protocol.mechanism,
        protocol_sha256=protocol.canonical_sha256(),
        tagged_protocol_commit=f"{marker:040x}",
        tag_name=protocol.required_git_tag,
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            + protocol.required_git_tag
        ),
        release_id=marker,
        release_created_at=created,
        release_published_at=created + timedelta(minutes=1),
        immutable=True,
        draft=False,
        prerelease=False,
    )


def _environment(commit: str) -> ExecutionEnvironmentReceipt:
    return ExecutionEnvironmentReceipt(
        execution_commit=commit,
        python_version="3.12.0",
        python_implementation="CPython",
        operating_system="test-platform",
        machine="test-machine",
        package_versions={
            "numpy": "2.0.0",
            "pandas": "2.0.0",
            "pydantic": "2.0.0",
            "scikit-learn": "1.0.0",
            "scipy": "1.0.0",
        },
    )


def _measurement(
    protocol: LightweightConfirmatoryProtocol,
    *,
    dataset_index: int,
    seed: int,
) -> DatasetSeedMeasurement:
    dataset = protocol.datasets[dataset_index]
    payload: dict[str, object] = {
        "schema_version": DATASET_MEASUREMENT_SCHEMA_VERSION,
        "protocol_sha256": protocol.canonical_sha256(),
        "mechanism": protocol.mechanism,
        "dataset_id": dataset.dataset_id,
        "dataset_role": dataset.role,
        "split_membership_sha256": dataset.split_membership_sha256,
        "sealed_membership_sha256": dataset.sealed_membership_sha256,
        "target_feature": dataset.target_feature,
        "seed": seed,
        "declared_manipulation_magnitude": 0.20,
        "achieved_manipulation_magnitude": 0.20,
        "clean_accuracy": 0.80,
        "manipulated_accuracy": 0.78,
        "nuisance_accuracy": 0.795,
        "target_metric_delta": -0.02,
        "nuisance_effect_magnitude": 0.005,
        "model_sha256": f"{seed + dataset_index:064x}",
        "source_binding_sha256": canonical_sha256(
            {
                "protocol_sha256": protocol.canonical_sha256(),
                "dataset_id": dataset.dataset_id,
                "split_membership_sha256": dataset.split_membership_sha256,
                "sealed_membership_sha256": dataset.sealed_membership_sha256,
            }
        ),
        "intervention_sha256": canonical_sha256(
            {
                "kind": "intervention",
                "mechanism": protocol.mechanism,
                "dataset": dataset.dataset_id,
                "seed": seed,
            }
        ),
        "nuisance_comparator_sha256": canonical_sha256(
            {
                "kind": "nuisance",
                "mechanism": protocol.mechanism,
                "dataset": dataset.dataset_id,
                "seed": seed,
            }
        ),
    }
    return DatasetSeedMeasurement.model_validate(
        {**payload, "measurement_sha256": canonical_sha256(payload)}
    )


def _all_measurements(
    predecessors: tuple[LightweightConfirmatoryProtocol, LightweightConfirmatoryProtocol],
) -> tuple[DatasetSeedMeasurement, ...]:
    return tuple(
        _measurement(protocol, dataset_index=dataset_index, seed=seed)
        for protocol in predecessors
        for dataset_index in range(2)
        for seed in protocol.execution.seeds
    )


def _readiness() -> P2RArchiveReadinessReceipt:
    manifest = load_v3_dataset_binding_manifest()
    receipt = load_v3_dataset_binding_receipt()
    audits = {item.dataset_id: item for item in receipt.datasets}

    def inspect(**kwargs: object) -> DatasetBindingAudit:
        dataset = kwargs["dataset"]
        assert isinstance(dataset, V3DatasetBinding)
        return audits[dataset.dataset_id]

    with patch(
        "aletheia_lab.benchmark.p2.p2r_recovery.inspect_v3_dataset_archive",
        inspect,
    ):
        return build_p2r_archive_readiness(
            manifest=manifest,
            pinned_receipt=receipt,
            archive_directory="outcome-blind-test-double",
        )


def _marker(
    *,
    commit: str,
    recoveries: tuple[P2RRecoveryProtocol, P2RRecoveryProtocol],
    recovery_registrations: tuple[P2RRecoveryRegistration, P2RRecoveryRegistration],
    scientific_registrations: tuple[P2RProtocolRegistration, P2RProtocolRegistration],
    readiness: P2RArchiveReadinessReceipt,
) -> P2RRecoverySealedMarker:
    return build_recovery_sealed_marker(
        execution_commit=commit,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        scientific_registrations=scientific_registrations,
        readiness=readiness,
        failure_audit=load_p2r_v1_failure_audit(),
        opened_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


def test_recovery_release_registration_binds_both_protocol_layers() -> None:
    recoveries = _recoveries()
    registrations = tuple(_release(item, index) for index, item in enumerate(recoveries, 1))
    checked = verify_recovery_registration_pair(recoveries, registrations)

    assert tuple(item.recovery_protocol_sha256 for item in checked) == tuple(
        item.canonical_sha256() for item in recoveries
    )
    assert tuple(item.predecessor_protocol_sha256 for item in checked) == tuple(
        item.artifacts.predecessor_protocol_sha256 for item in recoveries
    )
    assert len({item.canonical_sha256() for item in checked}) == 2


def test_recovery_release_rejects_mutable_or_lookalike_identity() -> None:
    recovery = _recoveries()[0]
    registration = _release(recovery, 1)
    with pytest.raises(P2RRecoveryExecutionError, match="immutable"):
        recovery_registration_from_release(
            recovery=recovery,
            tagged_protocol_commit="1" * 40,
            payload={
                "tag_name": recovery.governance.required_git_tag,
                "id": 1,
                "html_url": registration.release_url,
                "created_at": "2026-08-28T00:00:00Z",
                "published_at": "2026-08-28T00:01:00Z",
                "immutable": False,
                "draft": False,
                "prerelease": False,
            },
        )
    with pytest.raises(ValidationError, match="release identity"):
        P2RRecoveryRegistration.model_validate(
            registration.model_copy(update={"release_url": registration.release_url + "-fake"}).model_dump()
        )


def test_cross_mechanism_registration_reuse_is_rejected() -> None:
    drift, prep = _recoveries()
    drift_registration = _release(drift, 1)
    with pytest.raises(P2RRecoveryExecutionError, match="mechanism census"):
        verify_recovery_registration_pair(
            (drift, prep),
            (drift_registration, drift_registration),
        )


@pytest.mark.parametrize("path", ("../outside.json", "/absolute.json", "a\\b.json"))
def test_recovery_store_entry_rejects_nonlocal_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="canonical and relative"):
        RecoveryStoreEntry(relative_path=path, sha256="0" * 64, byte_count=1)


def test_marker_is_derived_exclusive_and_tamper_evident(tmp_path: Path) -> None:
    recoveries = _recoveries()
    predecessors = _predecessors()
    readiness = _readiness()
    failure = load_p2r_v1_failure_audit()
    recovery_registrations = tuple(
        _release(item, index) for index, item in enumerate(recoveries, 1)
    )
    scientific_registrations = tuple(
        _scientific_registration(item, index) for index, item in enumerate(predecessors, 1)
    )
    marker = build_recovery_sealed_marker(
        execution_commit="a" * 40,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        scientific_registrations=scientific_registrations,
        readiness=readiness,
        failure_audit=failure,
        opened_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    path = tmp_path / "sealed.json"
    write_recovery_marker_exclusive(path, marker)
    assert load_recovery_marker(path) == marker
    with pytest.raises(P2RRecoveryExecutionError, match="rerun is forbidden"):
        write_recovery_marker_exclusive(path, marker)

    payload = marker.model_dump()
    payload["archive_readiness_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="marker hash"):
        P2RRecoverySealedMarker.model_validate(payload)


def test_failure_store_preserves_both_registration_layers_atomically(tmp_path: Path) -> None:
    recoveries = _recoveries()
    predecessors = _predecessors()
    recovery_registrations = tuple(
        _release(item, index) for index, item in enumerate(recoveries, 1)
    )
    scientific_registrations = tuple(
        _scientific_registration(item, index) for index, item in enumerate(predecessors, 1)
    )
    commit = "b" * 40
    readiness = _readiness()
    failure = build_technical_failure(
        protocols=predecessors,
        registrations=scientific_registrations,
        execution_commit=commit,
        failure_stage="execute_primary",
        error=RuntimeError("private recovery failure"),
    )
    output = tmp_path / "terminal"
    manifest = write_recovery_terminal_store(
        output_dir=output,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        predecessors=predecessors,
        scientific_registrations=scientific_registrations,
        terminal=failure,
        environment=_environment(commit),
        readiness=readiness,
        failure_audit=load_p2r_v1_failure_audit(),
        sealed_marker=_marker(
            commit=commit,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            scientific_registrations=scientific_registrations,
            readiness=readiness,
        ),
    )
    assert manifest.terminal_status == "technical_failure"
    assert load_and_verify_recovery_terminal_store(output) == manifest
    assert not (output / "scientific-store/measurements.json").exists()
    assert not (output / "scientific-store/instrument-audit.json").exists()

    with pytest.raises(P2RRecoveryExecutionError, match="already exists"):
        write_recovery_terminal_store(
            output_dir=output,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            predecessors=predecessors,
            scientific_registrations=scientific_registrations,
            terminal=failure,
            environment=_environment(commit),
            readiness=readiness,
            failure_audit=load_p2r_v1_failure_audit(),
            sealed_marker=_marker(
                commit=commit,
                recoveries=recoveries,
                recovery_registrations=recovery_registrations,
                scientific_registrations=scientific_registrations,
                readiness=readiness,
            ),
        )

    target = output / "recovery-registrations.json"
    target.write_text(target.read_text() + " ")
    with pytest.raises(P2RRecoveryExecutionError, match="hash or size"):
        load_and_verify_recovery_terminal_store(output)


def test_outer_store_is_not_published_when_nested_store_write_fails(
    tmp_path: Path,
) -> None:
    recoveries = _recoveries()
    predecessors = _predecessors()
    recovery_registrations = tuple(
        _release(item, index) for index, item in enumerate(recoveries, 1)
    )
    scientific_registrations = tuple(
        _scientific_registration(item, index) for index, item in enumerate(predecessors, 1)
    )
    commit = "e" * 40
    readiness = _readiness()
    failure = build_technical_failure(
        protocols=predecessors,
        registrations=scientific_registrations,
        execution_commit=commit,
        failure_stage="build_closeout",
        error=RuntimeError("nested write failed"),
    )
    output = tmp_path / "terminal"

    with (
        patch(
            "aletheia_lab.benchmark.p2.p2r_recovery_execution.write_terminal_store",
            side_effect=OSError("simulated durable-write failure"),
        ),
        pytest.raises(OSError, match="durable-write failure"),
    ):
        write_recovery_terminal_store(
            output_dir=output,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            predecessors=predecessors,
            scientific_registrations=scientific_registrations,
            terminal=failure,
            environment=_environment(commit),
            readiness=readiness,
            failure_audit=load_p2r_v1_failure_audit(),
            sealed_marker=_marker(
                commit=commit,
                recoveries=recoveries,
                recovery_registrations=recovery_registrations,
                scientific_registrations=scientific_registrations,
                readiness=readiness,
            ),
        )

    assert not output.exists()
    assert not tuple(tmp_path.glob(".terminal-*"))


def test_complete_store_preserves_full_scientific_census_and_recovery_chain(
    tmp_path: Path,
) -> None:
    recoveries = _recoveries()
    predecessors = _predecessors()
    recovery_registrations = tuple(
        _release(item, index) for index, item in enumerate(recoveries, 1)
    )
    scientific_registrations = tuple(
        _scientific_registration(item, index) for index, item in enumerate(predecessors, 1)
    )
    instrument = load_instrument_validity_protocol()
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(),
        protocols=predecessors,
    )
    measurements = _all_measurements(predecessors)
    observations = paired_observations(plan=plan, measurements=measurements)
    commit = "d" * 40
    environment = _environment(commit)
    readiness = _readiness()
    audit, closeout = build_joint_closeout(
        execution_commit=commit,
        plan=plan,
        observations=observations,
        measurements=measurements,
        environment=environment,
        protocols={item.mechanism: item for item in predecessors},
        registrations={item.mechanism: item for item in scientific_registrations},
        instrument_protocol=instrument,
        executed_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    output = tmp_path / "complete"
    manifest = write_recovery_terminal_store(
        output_dir=output,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        predecessors=predecessors,
        scientific_registrations=scientific_registrations,
        terminal=closeout,
        environment=environment,
        readiness=readiness,
        failure_audit=load_p2r_v1_failure_audit(),
        sealed_marker=_marker(
            commit=commit,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            scientific_registrations=scientific_registrations,
            readiness=readiness,
        ),
        measurements=measurements,
        observations=observations,
        audit=audit,
    )

    assert manifest.terminal_status == "complete"
    assert load_and_verify_recovery_terminal_store(output) == manifest
    assert (output / "scientific-store/measurements.json").is_file()
    assert (output / "scientific-store/paired-observations.json").is_file()
    assert (output / "scientific-store/instrument-audit.json").is_file()
    assert closeout.n_admitted == 2


def test_failure_message_is_hashed_and_never_published_in_outer_store(tmp_path: Path) -> None:
    recoveries = _recoveries()
    predecessors = _predecessors()
    recovery_registrations = tuple(
        _release(item, index) for index, item in enumerate(recoveries, 1)
    )
    scientific_registrations = tuple(
        _scientific_registration(item, index) for index, item in enumerate(predecessors, 1)
    )
    secret = "private filesystem detail"
    readiness = _readiness()
    failure = build_technical_failure(
        protocols=predecessors,
        registrations=scientific_registrations,
        execution_commit="c" * 40,
        failure_stage="load_replication",
        error=RuntimeError(secret),
    )
    output = tmp_path / "terminal"
    write_recovery_terminal_store(
        output_dir=output,
        recoveries=recoveries,
        recovery_registrations=recovery_registrations,
        predecessors=predecessors,
        scientific_registrations=scientific_registrations,
        terminal=failure,
        environment=_environment("c" * 40),
        readiness=readiness,
        failure_audit=load_p2r_v1_failure_audit(),
        sealed_marker=_marker(
            commit="c" * 40,
            recoveries=recoveries,
            recovery_registrations=recovery_registrations,
            scientific_registrations=scientific_registrations,
            readiness=readiness,
        ),
    )
    assert failure.exception_message_sha256 == hashlib.sha256(secret.encode()).hexdigest()
    assert secret.encode() not in b"".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    )
