"""Unit contracts for the P2R paired runtime and terminal closeout."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_closeout import ExecutionEnvironmentReceipt
from aletheia_lab.benchmark.p2.instrument_validity import (
    load_instrument_validity_protocol,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    LightweightConfirmatoryProtocol,
    load_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_closeout import (
    P2RCloseoutError,
    P2RDatasetDecision,
    P2RProtocolRegistration,
    build_joint_closeout,
    build_technical_failure,
    decide_dataset,
    load_and_verify_terminal_store,
    registration_from_release,
    write_terminal_store,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DATASET_MEASUREMENT_SCHEMA_VERSION,
    DatasetSeedMeasurement,
    P2RRuntimeError,
    build_joint_candidate_plan,
    measurement_census,
    paired_observations,
)


def _protocols() -> tuple[
    LightweightConfirmatoryProtocol,
    LightweightConfirmatoryProtocol,
]:
    return (
        load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH),
        load_lightweight_confirmatory_protocol(DEFAULT_PREPROCESSING_PROTOCOL_PATH),
    )


def _registration(
    protocol: LightweightConfirmatoryProtocol, marker: int
) -> P2RProtocolRegistration:
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
    delta: float = -0.02,
    nuisance: float = 0.005,
    achieved: float = 0.20,
) -> DatasetSeedMeasurement:
    dataset = protocol.datasets[dataset_index]
    clean = 0.80
    nuisance_accuracy = clean - nuisance
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
        "achieved_manipulation_magnitude": achieved,
        "clean_accuracy": clean,
        "manipulated_accuracy": clean + delta,
        "nuisance_accuracy": nuisance_accuracy,
        "target_metric_delta": delta,
        "nuisance_effect_magnitude": nuisance,
        "model_sha256": f"{seed + 1 + dataset_index:064x}",
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
                "protocol_sha256": protocol.canonical_sha256(),
                "dataset_id": dataset.dataset_id,
                "seed": seed,
            }
        ),
        "nuisance_comparator_sha256": canonical_sha256(
            {
                "kind": "nuisance",
                "protocol_sha256": protocol.canonical_sha256(),
                "dataset_id": dataset.dataset_id,
                "seed": seed,
            }
        ),
    }
    return DatasetSeedMeasurement.model_validate(
        {**payload, "measurement_sha256": canonical_sha256(payload)}
    )


def _all_measurements(
    *,
    drift_replication_delta: float = -0.02,
    preprocessing_replication_delta: float = -0.02,
) -> tuple[DatasetSeedMeasurement, ...]:
    drift, preprocessing = _protocols()
    output = []
    for protocol in (drift, preprocessing):
        for dataset_index in (0, 1):
            for seed in protocol.execution.seeds:
                delta = -0.02
                if protocol.mechanism == "data_drift" and dataset_index == 1:
                    delta = drift_replication_delta
                if protocol.mechanism == "preprocessing_bug" and dataset_index == 1:
                    delta = preprocessing_replication_delta
                output.append(
                    _measurement(
                        protocol,
                        dataset_index=dataset_index,
                        seed=seed,
                        delta=delta,
                    )
                )
    return tuple(output)


def test_joint_plan_has_ten_paired_candidates_not_twenty_fake_replicates() -> None:
    instrument = load_instrument_validity_protocol()
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(), protocols=_protocols()
    )

    assert len(plan.entries) == 10
    assert {item.fault_type for item in plan.entries} == {"data_drift", "preprocessing_bug"}
    assert all(
        tuple(sorted(item.seed for item in plan.entries if item.fault_type == mechanism))
        == (8201, 8202, 8203, 8204, 8205)
        for mechanism in ("data_drift", "preprocessing_bug")
    )


def test_paired_reduction_uses_the_weaker_dataset_and_larger_nuisance() -> None:
    instrument = load_instrument_validity_protocol()
    protocols = _protocols()
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(), protocols=protocols
    )
    measurements = list(_all_measurements())
    target = next(
        item
        for item in measurements
        if item.mechanism == "data_drift"
        and item.dataset_role == "external_replication"
        and item.seed == 8201
    )
    replacement = _measurement(
        protocols[0], dataset_index=1, seed=8201, delta=-0.011, nuisance=0.009
    )
    measurements[measurements.index(target)] = replacement

    observations = paired_observations(plan=plan, measurements=measurements)
    paired = next(
        item for item in observations if item.fault_type == "data_drift" and item.seed == 8201
    )

    assert paired.target_metric_delta == -0.011
    assert paired.nuisance_effect_magnitude == 0.009


def test_measurement_census_rejects_missing_duplicate_and_cross_protocol_evidence() -> None:
    drift, preprocessing = _protocols()
    mapping = {"data_drift": drift, "preprocessing_bug": preprocessing}
    measurements = list(_all_measurements())

    with pytest.raises(P2RRuntimeError, match="incomplete"):
        measurement_census(measurements[:-1], mapping)
    with pytest.raises(P2RRuntimeError, match="incomplete"):
        measurement_census((*measurements, measurements[0]), mapping)

    forged_payload = measurements[0].model_dump(exclude={"measurement_sha256"})
    forged_payload["protocol_sha256"] = preprocessing.canonical_sha256()
    forged = DatasetSeedMeasurement.model_validate(
        {
            **forged_payload,
            "measurement_sha256": canonical_sha256(forged_payload),
        }
    )
    measurements[0] = forged
    with pytest.raises(P2RRuntimeError, match="another protocol"):
        measurement_census(measurements, mapping)

    measurements = list(_all_measurements())
    measurements[0] = measurements[0].model_copy(update={"source_binding_sha256": "0" * 64})
    with pytest.raises(P2RRuntimeError, match="content or hash"):
        measurement_census(measurements, mapping)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"achieved_manipulation_magnitude": float("nan")}, "finite number"),
        ({"target_metric_delta": -0.5}, "not derived from accuracies"),
        ({"nuisance_effect_magnitude": 0.5}, "not derived from accuracies"),
    ],
)
def test_dataset_measurement_rejects_nonfinite_or_underived_values(
    update: dict[str, float], message: str
) -> None:
    drift = _protocols()[0]
    valid = _measurement(drift, dataset_index=0, seed=drift.execution.seeds[0])
    payload = valid.model_dump(exclude={"measurement_sha256"})
    payload.update(update)
    digest = (
        "0" * 64
        if any(not math.isfinite(value) for value in update.values())
        else canonical_sha256(payload)
    )
    with pytest.raises(ValidationError, match=message):
        DatasetSeedMeasurement.model_validate({**payload, "measurement_sha256": digest})


def test_dataset_decision_requires_all_five_seeds_and_derives_every_gate() -> None:
    drift = _protocols()[0]
    complete = tuple(
        _measurement(drift, dataset_index=0, seed=seed) for seed in drift.execution.seeds
    )

    decision = decide_dataset(protocol=drift, measurements=complete)
    assert decision.passed
    assert decision.median_target_effect == pytest.approx(0.02)
    assert decision.expected_direction_fraction == 1.0

    with pytest.raises(P2RCloseoutError, match="complete registered seed"):
        decide_dataset(protocol=drift, measurements=complete[:-1])
    with pytest.raises(P2RCloseoutError, match="measurement is invalid"):
        decide_dataset(
            protocol=drift,
            measurements=(
                complete[0].model_copy(update={"measurement_sha256": "0" * 64}),
                *complete[1:],
            ),
        )

    failed = tuple(
        _measurement(
            drift,
            dataset_index=0,
            seed=seed,
            delta=-0.005 if seed < 8205 else -0.02,
        )
        for seed in drift.execution.seeds
    )
    decision = decide_dataset(protocol=drift, measurements=failed)
    assert not decision.target_effect_pass
    assert not decision.direction_pass
    assert not decision.passed


def test_dataset_decision_hash_and_boolean_cannot_be_forged() -> None:
    drift = _protocols()[0]
    measurements = tuple(
        _measurement(drift, dataset_index=0, seed=seed) for seed in drift.execution.seeds
    )
    decision = decide_dataset(protocol=drift, measurements=measurements)

    with pytest.raises(ValidationError, match="conjunctive"):
        P2RDatasetDecision.model_validate(
            decision.model_copy(update={"passed": False}).model_dump()
        )
    with pytest.raises(ValidationError, match="does not bind"):
        P2RDatasetDecision.model_validate(
            decision.model_copy(update={"decision_sha256": "0" * 64}).model_dump()
        )


def test_release_registration_requires_immutable_exact_mechanism_identity() -> None:
    drift = _protocols()[0]
    created = "2026-08-27T00:00:00Z"
    payload = {
        "tag_name": drift.required_git_tag,
        "id": 101,
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/" + drift.required_git_tag
        ),
        "created_at": created,
        "published_at": created,
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }
    registration = registration_from_release(
        protocol=drift, tagged_protocol_commit="1" * 40, payload=payload
    )
    assert registration.immutable

    with pytest.raises(P2RCloseoutError, match="immutable"):
        registration_from_release(
            protocol=drift,
            tagged_protocol_commit="1" * 40,
            payload={**payload, "immutable": False},
        )
    with pytest.raises(P2RCloseoutError, match="incomplete"):
        registration_from_release(
            protocol=drift,
            tagged_protocol_commit="1" * 40,
            payload={"tag_name": drift.required_git_tag},
        )
    with pytest.raises(ValidationError, match="release identity"):
        P2RProtocolRegistration.model_validate(
            registration.model_copy(
                update={"release_url": registration.release_url + "-lookalike"}
            ).model_dump()
        )
    with pytest.raises(P2RCloseoutError, match="must be an object"):
        registration_from_release(protocol=drift, tagged_protocol_commit="1" * 40, payload=[])
    with pytest.raises(P2RCloseoutError, match="must be an integer"):
        registration_from_release(
            protocol=drift,
            tagged_protocol_commit="1" * 40,
            payload={**payload, "id": True},
        )
    with pytest.raises(ValidationError, match="precedes creation"):
        P2RProtocolRegistration.model_validate(
            registration.model_copy(
                update={
                    "release_published_at": registration.release_created_at - timedelta(seconds=1)
                }
            ).model_dump()
        )


@pytest.mark.parametrize(
    ("drift_replication", "preprocessing_replication", "expected"),
    [
        (-0.02, -0.02, ("admitted", "admitted", 2)),
        (-0.005, -0.02, ("assumption_limited", "admitted", 1)),
        (-0.005, -0.005, ("assumption_limited", "assumption_limited", 0)),
    ],
)
def test_joint_closeout_derives_mechanism_tracks_without_inflating_denominator(
    drift_replication: float,
    preprocessing_replication: float,
    expected: tuple[str, str, int],
) -> None:
    instrument = load_instrument_validity_protocol()
    drift, preprocessing = _protocols()
    protocols = (drift, preprocessing)
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(), protocols=protocols
    )
    measurements = _all_measurements(
        drift_replication_delta=drift_replication,
        preprocessing_replication_delta=preprocessing_replication,
    )
    observations = paired_observations(plan=plan, measurements=measurements)

    with pytest.raises(P2RCloseoutError, match="another commit"):
        build_joint_closeout(
            execution_commit="a" * 40,
            plan=plan,
            observations=observations,
            measurements=measurements,
            environment=_environment("f" * 40),
            protocols={"data_drift": drift, "preprocessing_bug": preprocessing},
            registrations={
                "data_drift": _registration(drift, 1),
                "preprocessing_bug": _registration(preprocessing, 2),
            },
            instrument_protocol=instrument,
            executed_at=datetime(2026, 8, 28, tzinfo=UTC),
        )

    with pytest.raises(P2RCloseoutError, match="do not derive"):
        build_joint_closeout(
            execution_commit="a" * 40,
            plan=plan,
            observations=tuple(reversed(observations)),
            measurements=measurements,
            environment=_environment("a" * 40),
            protocols={"data_drift": drift, "preprocessing_bug": preprocessing},
            registrations={
                "data_drift": _registration(drift, 1),
                "preprocessing_bug": _registration(preprocessing, 2),
            },
            instrument_protocol=instrument,
            executed_at=datetime(2026, 8, 28, tzinfo=UTC),
        )

    _, closeout = build_joint_closeout(
        execution_commit="a" * 40,
        plan=plan,
        observations=observations,
        measurements=measurements,
        environment=_environment("a" * 40),
        protocols={"data_drift": drift, "preprocessing_bug": preprocessing},
        registrations={
            "data_drift": _registration(drift, 1),
            "preprocessing_bug": _registration(preprocessing, 2),
        },
        instrument_protocol=instrument,
        executed_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    assert tuple(item.disposition for item in closeout.mechanism_closeouts) == expected[:2]
    assert closeout.n_admitted == expected[2]
    assert closeout.n_mechanisms == 2


def test_atomic_store_verifies_and_rejects_tampering_or_overwrite(tmp_path: Path) -> None:
    instrument = load_instrument_validity_protocol()
    drift, preprocessing = _protocols()
    protocols = (drift, preprocessing)
    registrations = (_registration(drift, 1), _registration(preprocessing, 2))
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(), protocols=protocols
    )
    measurements = _all_measurements()
    observations = paired_observations(plan=plan, measurements=measurements)
    audit, closeout = build_joint_closeout(
        execution_commit="b" * 40,
        plan=plan,
        observations=observations,
        measurements=measurements,
        environment=_environment("b" * 40),
        protocols={"data_drift": drift, "preprocessing_bug": preprocessing},
        registrations={item.mechanism: item for item in registrations},
        instrument_protocol=instrument,
        executed_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    output = tmp_path / "store"
    with pytest.raises(P2RCloseoutError, match="another execution commit"):
        write_terminal_store(
            output_dir=tmp_path / "wrong-environment",
            protocols=protocols,
            registrations=registrations,
            terminal=closeout,
            environment=_environment("f" * 40),
            measurements=measurements,
            observations=observations,
            audit=audit,
        )
    manifest = write_terminal_store(
        output_dir=output,
        protocols=protocols,
        registrations=registrations,
        terminal=closeout,
        environment=_environment("b" * 40),
        measurements=measurements,
        observations=observations,
        audit=audit,
    )
    assert load_and_verify_terminal_store(output) == manifest
    with pytest.raises(P2RCloseoutError, match="already exists"):
        write_terminal_store(
            output_dir=output,
            protocols=protocols,
            registrations=registrations,
            terminal=closeout,
            environment=_environment("b" * 40),
            measurements=measurements,
            observations=observations,
            audit=audit,
        )

    unexpected = output / "unmanifested.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    with pytest.raises(P2RCloseoutError, match="unmanifested"):
        load_and_verify_terminal_store(output)
    unexpected.unlink()

    target = output / "measurements.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(P2RCloseoutError, match="hash or size"):
        load_and_verify_terminal_store(output)


def test_failure_store_cannot_publish_partial_measurements(tmp_path: Path) -> None:
    drift, preprocessing = _protocols()
    protocols = (drift, preprocessing)
    registrations = (_registration(drift, 1), _registration(preprocessing, 2))
    failure = build_technical_failure(
        protocols=protocols,
        registrations=registrations,
        execution_commit="c" * 40,
        failure_stage="execute_replication",
        error=RuntimeError("private diagnostic detail"),
    )
    assert (
        failure.exception_message_sha256 == hashlib.sha256(b"private diagnostic detail").hexdigest()
    )
    with pytest.raises(P2RCloseoutError, match="partial"):
        write_terminal_store(
            output_dir=tmp_path / "bad",
            protocols=protocols,
            registrations=registrations,
            terminal=failure,
            environment=_environment("c" * 40),
            measurements=_all_measurements()[:1],
        )

    output = tmp_path / "failure"
    store = write_terminal_store(
        output_dir=output,
        protocols=protocols,
        registrations=registrations,
        terminal=failure,
        environment=_environment("c" * 40),
    )
    assert store.terminal_status == "technical_failure"
    assert load_and_verify_terminal_store(output) == store
    assert not (output / "measurements.json").exists()
    assert not (output / "instrument-audit.json").exists()
