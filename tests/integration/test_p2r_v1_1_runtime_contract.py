"""Outcome-blind integration boundary for the P2R v1.1 runtime."""

from __future__ import annotations

import inspect

from aletheia_lab.benchmark.p2.instrument_validity import (
    load_instrument_validity_protocol,
)
from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol,
    verify_p2r_recovery_protocol_pair,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    build_joint_candidate_plan,
    execute_p2r_dataset,
)


def test_v1_1_reuses_the_exact_v1_scientific_candidate_plan() -> None:
    recoveries = verify_p2r_recovery_protocol_pair(
        load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH),
        load_p2r_recovery_protocol(DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH),
    )
    predecessors = tuple(verify_p2r_recovery_protocol(item)[1] for item in recoveries)
    instrument = load_instrument_validity_protocol()
    plan = build_joint_candidate_plan(
        instrument_protocol_sha256=instrument.canonical_sha256(),
        protocols=predecessors,
    )

    assert tuple(item.mechanism for item in predecessors) == (
        "data_drift",
        "preprocessing_bug",
    )
    assert all(item.execution.seeds == (8201, 8202, 8203, 8204, 8205) for item in predecessors)
    assert all(item.endpoint.minimum_practical_effect == 0.01 for item in predecessors)
    assert all(item.endpoint.minimum_expected_direction_fraction == 0.8 for item in predecessors)
    assert len(plan.entries) == 10
    assert {item.fault_type for item in plan.entries} == {
        "data_drift",
        "preprocessing_bug",
    }


def test_recovery_runtime_does_not_introduce_an_alternate_scientific_executor() -> None:
    source = inspect.getsource(execute_p2r_dataset)
    assert "checked.execution.seeds" in source
    assert "protocol.endpoint" not in source or "minimum_practical_effect" not in source
    assert "recovery" not in source.lower()
