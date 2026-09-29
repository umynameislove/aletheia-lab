"""Synthetic checks of the family-paired pilot, blind packets and inference."""

from __future__ import annotations

import io
import json
import math
import zipfile

import pytest

from aletheia_lab.evaluation.diagnosis_main_paired_pilot import (
    PairedPilotError,
    _blind_files,
    _family_delta,
    _zip,
    analyze_pilot,
    blind_adjudication_packet,
    select_families,
    sha256,
)


def _fixture() -> tuple[dict, bytes, dict, dict]:
    families = [f"f{index:02d}" for index in range(8)]
    rows = []
    outputs = []
    for family in families:
        for condition in ("full", "missing_key", "noisy"):
            for arm in ("B1", "A3"):
                claim_id = f"{family}:{condition}:{arm}"
                rows.append(
                    {
                        "claim_id": claim_id,
                        "claim_text": "Evidence supports this test claim.",
                        "visible_evidence": [
                            {"evidence_id": "e1", "kind": "log", "title": "Trace", "content": "ok"}
                        ],
                    }
                )
                outputs.append(
                    {
                        "family_id": family,
                        "condition": condition,
                        "arm": arm,
                        "category": "claim_scored",
                        "claim_ids": [claim_id],
                        "automatic_primary_loss": 0.0,
                        "automatic_partial_half_loss": 0.0,
                    }
                )
    files_a, map_a = _blind_files(slot="rater_1", rows=rows, key=b"k" * 32, guide="guide")
    files_b, map_b = _blind_files(slot="rater_2", rows=rows, key=b"k" * 32, guide="guide")
    coordinator = {
        "schema_version": "p5-impact-family-paired-pilot/v1",
        "packet_ids": {
            "rater_1": json.loads(files_a["items.json"])["packet_id"],
            "rater_2": json.loads(files_b["items.json"])["packet_id"],
        },
        "mapping": map_a + map_b,
        "selected_family_ids": families,
        "outputs": outputs,
        "frozen_primary_B1_minus_A3": -0.2,
        "frozen_partial_half_B1_minus_A3": -0.1,
        "blind_zip_sha256": {"rater_1.zip": sha256(_zip(files_a))},
    }
    first = json.loads(files_a["submission-template.json"])
    second = json.loads(files_b["submission-template.json"])
    for submission in (first, second):
        submission["attestation"] = dict.fromkeys(submission["attestation"], True)
        for decision in submission["decisions"]:
            decision.update(
                support_label="fully_supported",
                evidence_ids_used=["e1"],
                rationale="The visible evidence supports the claim.",
            )
    return coordinator, _zip(files_a), first, second


def _decision_for(coordinator: dict, submission: dict, claim_id: str) -> dict:
    blind_id = next(
        row["blind_claim_id"]
        for row in coordinator["mapping"]
        if row["source_claim_id"] == claim_id and row["slot"] == submission["rater_slot"]
    )
    return next(row for row in submission["decisions"] if row["blind_claim_id"] == blind_id)


def test_srs_family_selection_is_stable_complete_and_not_claim_weighted() -> None:
    frame = tuple(f"f{index:02d}" for index in range(32))
    first = select_families(frame, b"s" * 32)
    assert first == select_families(tuple(reversed(frame)), b"s" * 32)
    assert len(first) == len(set(first)) == 8
    assert all(family in frame for family in first)
    with pytest.raises(PairedPilotError):
        select_families(frame[:-1], b"s" * 32)


def test_blind_packet_contains_claim_evidence_but_no_arm_or_machine_answer() -> None:
    coordinator, first_zip, first, second = _fixture()
    with zipfile.ZipFile(io.BytesIO(first_zip)) as archive:
        packet = json.loads(archive.read("items.json"))
    assert len(packet["items"]) == 48
    assert set(packet["items"][0]) == {
        "number",
        "blind_claim_id",
        "claim_text",
        "visible_evidence",
    }
    assert b"automatic_label" not in first_zip
    assert b"family_id" not in first_zip
    assert first["packet_id"] != second["packet_id"]
    assert coordinator["blind_zip_sha256"]["rater_1.zip"] == sha256(first_zip)


def test_complete_family_pair_preserves_frozen_baseline_and_estimates_delta() -> None:
    coordinator, _, first, second = _fixture()
    for submission in (first, second):
        _decision_for(coordinator, submission, "f00:full:B1")["support_label"] = "unsupported"
    result = analyze_pilot(
        coordinator,
        first,
        second,
        {"schema_version": "p5-impact-blind-adjudication/v1", "decisions": [], "attestation": None},
    )
    assert result["disagreement_count"] == 0
    assert result["scored_claim_count"] == 48
    assert math.isclose(result["primary"]["estimated_family_mean_relabel_correction"], 1 / 24)
    assert math.isclose(result["primary"]["pilot_estimated_human_relabel_effect"], -0.2 + 1 / 24)
    assert result["primary"]["pilot_design_standard_error"] > 0
    assert result["partial_half_sensitivity"]["frozen_automatic_effect"] == -0.1
    assert result["frozen_outcomes_mutated"] is False


def test_disagreement_packet_is_blind_and_requires_third_human_resolution() -> None:
    coordinator, first_zip, first, second = _fixture()
    claim_id = "f00:full:B1"
    _decision_for(coordinator, second, claim_id)["support_label"] = "unsupported"
    files = blind_adjudication_packet(coordinator, first_zip, first, second)
    packet = json.loads(files["items.json"])
    assert len(packet["items"]) == 1
    assert set(packet["items"][0]) == {
        "number",
        "blind_claim_id",
        "claim_text",
        "visible_evidence",
        "independent_labels",
    }
    assert claim_id.encode() not in files["items.json"]
    with pytest.raises(PairedPilotError, match="blind adjudication"):
        analyze_pilot(
            coordinator,
            first,
            second,
            {
                "schema_version": "p5-impact-blind-adjudication/v1",
                "decisions": [],
                "attestation": None,
            },
        )
    decision = json.loads(files["adjudication-template.json"])["decisions"][0]
    decision.update(support_label="unsupported", rationale="Evidence does not establish the claim.")
    result = analyze_pilot(
        coordinator,
        first,
        second,
        {
            "schema_version": "p5-impact-blind-adjudication/v1",
            "decisions": [decision],
            "attestation": {
                "same_human_completed_all_disagreements": True,
                "automatic_labels_not_viewed": True,
                "arm_and_mapping_not_viewed": True,
            },
        },
    )
    assert result["disagreement_count"] == 1


def test_no_disagreements_need_no_third_human_attestation() -> None:
    coordinator, first_zip, first, second = _fixture()
    files = blind_adjudication_packet(coordinator, first_zip, first, second)
    template = json.loads(files["adjudication-template.json"])
    assert template["decisions"] == []
    assert template["attestation"] is None
    assert analyze_pilot(coordinator, first, second, template)["disagreement_count"] == 0


def test_missing_rating_or_false_attestation_blocks_analysis() -> None:
    coordinator, _, first, second = _fixture()
    first["decisions"].pop()
    with pytest.raises(PairedPilotError, match="does not cover"):
        analyze_pilot(
            coordinator,
            first,
            second,
            {
                "schema_version": "p5-impact-blind-adjudication/v1",
                "decisions": [],
                "attestation": None,
            },
        )
    coordinator, _, first, second = _fixture()
    first["attestation"]["completed_without_ai_assistance"] = False
    with pytest.raises(PairedPilotError, match="submission"):
        analyze_pilot(
            coordinator,
            first,
            second,
            {
                "schema_version": "p5-impact-blind-adjudication/v1",
                "decisions": [],
                "attestation": None,
            },
        )


def test_unhashable_evidence_identifier_is_rejected_cleanly() -> None:
    coordinator, _, first, second = _fixture()
    first["decisions"][0]["evidence_ids_used"] = [{"bad": "identifier"}]
    with pytest.raises(PairedPilotError, match="human decisions"):
        analyze_pilot(
            coordinator,
            first,
            second,
            {
                "schema_version": "p5-impact-blind-adjudication/v1",
                "decisions": [],
                "attestation": None,
            },
        )


def test_unscored_slots_stay_fixed_and_claims_average_within_output() -> None:
    coordinator, _, _, _ = _fixture()
    final = {
        claim_id: "fully_supported"
        for output in coordinator["outputs"]
        for claim_id in output["claim_ids"]
    }
    full_b1 = next(
        output
        for output in coordinator["outputs"]
        if (output["family_id"], output["condition"], output["arm"]) == ("f00", "full", "B1")
    )
    full_b1["claim_ids"].append("another-claim")
    final["another-claim"] = "fully_supported"
    final["f00:full:B1"] = "unsupported"
    assert math.isclose(_family_delta(coordinator, final, 0.0)["f00"], 1 / 6)
    full_b1.update(category="technical_failure", claim_ids=[], automatic_primary_loss=1.0)
    assert _family_delta(coordinator, final, 0.0)["f00"] == 0.0
