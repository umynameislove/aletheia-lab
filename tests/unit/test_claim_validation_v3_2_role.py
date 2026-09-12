"""Contracts for the prospective V3.2 source-measurement role decision."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest

import aletheia_lab.evaluation.claim_support_v3_2_role as role
from aletheia_lab.evaluation.claim_support_v3_2_role import (
    PREDECESSOR_CLOSEOUT_SHA256,
    SOURCE_ROLE,
    build_deterministic_source_census,
    build_protocol,
    verify_protocol,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_failure import build_failure_closeout
from aletheia_lab.evaluation.claim_validation_v3_design import build_design

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def protocol() -> dict[str, object]:
    return build_protocol(ROOT)


@pytest.fixture(scope="module")
def source_rows() -> list[dict[str, object]]:
    return build_deterministic_source_census(ROOT)


def test_tracked_v3_2_protocol_rebuilds_from_frozen_inputs(
    protocol: dict[str, object],
) -> None:
    assert verify_protocol(ROOT) == protocol

    assert protocol["status"] == "v3_2_source_measurement_role_frozen"
    assert protocol["predecessor_v3_1_closeout_sha256"] == PREDECESSOR_CLOSEOUT_SHA256
    assert protocol["predecessor_v3_1_rerun_forbidden"] is True
    assert protocol["historical_provider_outputs_reused"] is False
    assert protocol["historical_accepted_sources_admitted"] is False


def test_deterministic_source_census_is_complete_balanced_and_collision_free(
    source_rows: list[dict[str, object]],
) -> None:
    rows = source_rows

    assert len(rows) == 720
    assert len({row["source_instance_sha256"] for row in rows}) == 720
    assert set(Counter(row["source_slot_sha256"] for row in rows).values()) == {2}
    assert Counter(row["variant"] for row in rows) == {
        "A1": 90,
        "A2": 90,
        "A3": 90,
        "B0": 90,
        "B1": 90,
        "B2": 90,
        "CodeGraph": 90,
        "FULL": 90,
    }
    assert Counter(row["evidence_condition"] for row in rows) == {
        "full": 240,
        "missing_key": 240,
        "noisy": 240,
    }
    assert len({row["claim"]["claim_text"] for row in rows}) == 328


def test_role_change_removes_source_provider_without_changing_relation_instrument(
    protocol: dict[str, object],
) -> None:
    assert protocol["source_measurement_role"] == SOURCE_ROLE
    assert protocol["source_provider_request_count"] == 0
    assert protocol["source_provider_transcription_is_measured_construct"] is False
    assert protocol["source_values_derived_only_from_authenticated_visible_evidence"] is True
    assert protocol["source_target_allocation_reused_without_outcome_adaptation"] is True
    assert protocol["relation_assignment_reused_without_outcome_adaptation"] is True
    assert protocol["relation_provider_role_unchanged"] is True
    assert protocol["relation_qualification_provider_request_count"] == 12
    assert protocol["prospective_relation_request_count"] == 240


def test_protocol_grants_no_live_or_downstream_authority(
    protocol: dict[str, object],
) -> None:
    assert protocol["provider_calls_executed"] is False
    assert protocol["qualification_execution_authorized"] is False
    assert protocol["relation_planning_unlocked"] is False
    assert protocol["relation_execution_authorized"] is False
    assert protocol["automatic_labels_generated"] is False
    assert protocol["claims_materialized"] is False
    assert protocol["blind_packets_generated"] is False
    assert protocol["human_annotations_collected"] is False
    assert protocol["main_or_sealed_outcomes_opened"] is False
    assert protocol["next_authorized_action"] == (
        "implement_v3_2_relation_qualification_boundary"
    )


def test_variant_names_are_provenance_not_comparison_authority(
    protocol: dict[str, object],
) -> None:
    assert protocol["variant_schedule_retained_for_provenance_only"] is True
    assert protocol["variant_superiority_claims_permitted"] is False
    assert protocol["failure_rate_generalization_permitted"] is False
    assert protocol["family_is_statistical_dependence_unit"] is True


def test_tracked_protocol_tamper_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = copy.deepcopy(build_protocol(ROOT))
    changed["source_provider_request_count"] = 315
    monkeypatch.setattr(role, "read_document", lambda *_args: changed)

    with pytest.raises(ValueError, match="tracked V3.2"):
        verify_protocol(ROOT)


def test_predecessor_change_fails_before_a_new_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = copy.deepcopy(role.verify_failure_closeout(ROOT))
    changed["closeout_sha256"] = "0" * 64
    monkeypatch.setattr(role, "verify_failure_closeout", lambda _root: changed)

    with pytest.raises(ValueError, match="exact immutable V3.1"):
        build_protocol(ROOT)


def test_source_instance_identity_changes_with_bound_source_claim(
    source_rows: list[dict[str, object]],
) -> None:
    rows = source_rows
    first = rows[0]
    second = rows[1]

    assert first["source_slot_sha256"] == second["source_slot_sha256"]
    assert first["claim_ordinal"] != second["claim_ordinal"]
    assert first["source_instance_sha256"] != second["source_instance_sha256"]


def test_failed_v3_1_slots_are_not_post_hoc_excluded(
    source_rows: list[dict[str, object]],
) -> None:
    failed_slots = {case["slot_sha256"] for case in build_failure_closeout()["failure_cases"]}
    successor_slots = {row["predecessor_slot_sha256"] for row in source_rows}

    assert failed_slots <= successor_slots


def test_non_visible_value_cannot_enter_deterministic_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = copy.deepcopy(build_design(ROOT))
    claim = design["slots"][0]["expected"][0]
    claim["material_parts"][0]["text"] += "0"
    claim["claim_text"] = "; ".join(part["text"] for part in claim["material_parts"])
    monkeypatch.setattr(role, "build_design", lambda _root: design)

    with pytest.raises(ValueError, match="authenticated visible evidence"):
        build_deterministic_source_census(ROOT)
