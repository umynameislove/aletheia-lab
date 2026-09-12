"""Contracts for the immutable V3.1 source-cohort failure closeout."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import aletheia_lab.evaluation.claim_support_v3_cohort_failure as failure
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_support_v3_cohort_failure import (
    FAILURE_CLOSEOUT_PATH,
    build_failure_closeout,
    classify_source_reading_mismatch,
    verify_failure_closeout,
)
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    facts,
    source_payload,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import seal
from aletheia_lab.project.identity import canonical_project_json, content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_tracked_closeout_matches_frozen_public_safe_audit() -> None:
    closeout = verify_failure_closeout(ROOT)

    assert closeout == build_failure_closeout()
    assert closeout["terminal_request_count"] == 360
    assert closeout["parsed_count"] == 360
    assert closeout["accepted_count"] == 355
    assert closeout["technical_failure_count"] == 0
    assert closeout["semantic_failure_count"] == 5
    assert closeout["failure_sequences"] == [26, 53, 103, 232, 307]
    assert closeout["mismatched_reading_count"] == 11
    assert closeout["mismatch_classification_counts"] == {
        "target_ordinal_token_copied_as_value": 8,
        "wrong_visible_numeric_leaf": 3,
    }


def test_closeout_keeps_failed_cohort_closed_and_does_not_leak_values() -> None:
    closeout = build_failure_closeout()

    assert closeout["rerun_forbidden"] is True
    assert closeout["prospective_successor_required"] is True
    assert closeout["retrospective_value_repair_permitted"] is False
    assert closeout["new_provider_execution_authorized"] is False
    assert closeout["relation_planning_unlocked"] is False
    assert closeout["relation_execution_authorized"] is False
    assert closeout["claims_materialized"] is False
    assert closeout["blind_packets_generated"] is False
    assert closeout["human_annotations_collected"] is False
    assert closeout["main_or_sealed_outcomes_opened"] is False
    for case in closeout["failure_cases"]:
        for mismatch in case["mismatches"]:
            assert "observed_value" not in mismatch
            assert "expected_value" not in mismatch


def test_closeout_hash_is_stable_across_process_hash_seeds() -> None:
    command = (
        sys.executable,
        "-c",
        "from aletheia_lab.evaluation.claim_support_v3_cohort_failure import "
        "build_failure_closeout; print(build_failure_closeout()['closeout_sha256'])",
    )
    observed = []
    for seed in ("1", "104729"):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        observed.append(completed.stdout.strip())
    assert observed == [build_failure_closeout()["closeout_sha256"]] * 2


@pytest.mark.parametrize(
    ("observed", "expected", "visible", "classification", "locations"),
    (
        ("1.0", "1.00", {}, "numeric_reformat_only", []),
        (
            "0.5",
            "0.4",
            {("ev-performance-summary", "/payload/observed/accuracy"): "0.5"},
            "wrong_visible_numeric_leaf",
            [
                {
                    "evidence_id": "ev-performance-summary",
                    "json_pointer": "/payload/observed/accuracy",
                }
            ],
        ),
        ("3", "0.4", {}, "target_ordinal_token_copied_as_value", []),
        ("not-a-number", "0.4", {}, "nonvisible_or_unclassified_value", []),
    ),
)
def test_mismatch_classifier_is_value_safe_and_distinguishes_root_causes(
    observed: str,
    expected: str,
    visible: dict[tuple[str, str], str],
    classification: str,
    locations: list[dict[str, str]],
) -> None:
    assert classify_source_reading_mismatch(
        observed_value=observed,
        expected_value=expected,
        target_count=4,
        visible_facts=visible,
    ) == (classification, locations)


def test_resealed_closeout_tamper_is_rejected(tmp_path: Path) -> None:
    tampered = copy.deepcopy(build_failure_closeout())
    tampered.pop("closeout_sha256")
    tampered["accepted_count"] = 356
    tampered = seal(tampered, "closeout_sha256")
    path = tmp_path / FAILURE_CLOSEOUT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(canonical_project_json(tampered) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="differs from frozen audit"):
        verify_failure_closeout(tmp_path)


def _failed_payload(slot: dict[str, object], sequence: int) -> dict[str, object]:
    payload = copy.deepcopy(source_payload(slot["expected"]))
    if sequence in {26, 307}:
        for reading in payload["readings"]:
            reading["value"] = str(reading["target"])
        payload["readings"][2]["value"] = payload["readings"][0]["value"]
        return payload
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(slot["context"]))
    observed = facts(context)[("ev-performance-summary", "/payload/observed/minority_recall")]
    target = 1 if sequence == 53 else 2
    payload["readings"][target - 1]["value"] = observed
    return payload


def test_full_terminal_replay_reconstructs_the_frozen_closeout(monkeypatch) -> None:
    design = build_design(ROOT)
    cases = {case["sequence"]: case for case in failure.EXPECTED_FAILURE_CASES}
    payloads: dict[str, dict[str, object]] = {}
    records: dict[str, tuple[SimpleNamespace, ...]] = {}
    outcomes = []
    rate_limited = 0
    usage_assigned = False
    for slot in design["slots"]:
        schedule = slot["source_schedule"]
        sequence = schedule["sequence"]
        case = cases.get(sequence)
        identity = case["gateway_request_identity_sha256"] if case else f"{sequence:064x}"
        payload = _failed_payload(slot, sequence) if case else source_payload(slot["expected"])
        payload_hash = content_sha256(canonical_project_json(payload).encode("utf-8"))
        if case:
            assert payload_hash == case["parsed_payload_sha256"]
        is_model = schedule["execution_route"] == "model_gateway"
        accepted = case is None
        attempt_rows: list[SimpleNamespace] = []
        if is_model and accepted and rate_limited < 70:
            attempt_rows.append(
                SimpleNamespace(provider_failure_category="rate_limited", usage=None)
            )
            rate_limited += 1
        if is_model:
            usage = SimpleNamespace(
                input_tokens=382_931 if not usage_assigned else 0,
                output_tokens=17_737 if not usage_assigned else 0,
                total_tokens=400_668 if not usage_assigned else 0,
            )
            usage_assigned = True
        else:
            usage = SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0)
        attempt_rows.append(SimpleNamespace(provider_failure_category=None, usage=usage))
        payloads[identity] = payload
        records[identity] = tuple(attempt_rows)
        outcomes.append(
            {
                "sequence": sequence,
                "source_request_sha256": (
                    case["source_request_sha256"] if case else f"{sequence + 400:064x}"
                ),
                "slot_sha256": slot["slot_sha256"],
                "gateway_request_identity_sha256": identity,
                "execution_route": schedule["execution_route"],
                "gateway_status": "parsed",
                "accepted": accepted,
                "semantic_issue_code": None if accepted else "source_value_mismatch",
                "attempt_count": len(attempt_rows),
                "failure_categories": ({"rate_limited": 1} if len(attempt_rows) == 2 else {}),
                "parsed_payload_sha256": payload_hash,
                "source_instance_sha256s": [],
                "outcome_sha256": case["outcome_sha256"] if case else f"{sequence + 800:064x}",
            }
        )

    class Reader:
        def __init__(self, identity: str) -> None:
            self.identity = identity

        def terminal_inventory(self, _identity: str) -> SimpleNamespace:
            return SimpleNamespace(gateway_status="parsed")

        def terminal_parsed_payload(self, _identity: str) -> dict[str, object]:
            return payloads[self.identity]

        def terminal_attempt_records(self, _identity: str) -> tuple[SimpleNamespace, ...]:
            return records[self.identity]

    monkeypatch.setattr(failure, "_reader", lambda _store, identity: Reader(identity))
    receipt = {
        "status": "v3_1_source_cohort_complete_with_failures",
        "source_commit_ref": failure.FAILED_SOURCE_COMMIT,
        "authorization_sha256": failure.FAILED_AUTHORIZATION_SHA256,
        "plan_sha256": failure.FAILED_PLAN_SHA256,
        "rehearsal_sha256": failure.FAILED_REHEARSAL_SHA256,
        "qualification_receipt_sha256": failure.FAILED_QUALIFICATION_RECEIPT_SHA256,
        "protocol_sha256": failure.FAILED_PROTOCOL_SHA256,
        "design_sha256": failure.FAILED_DESIGN_SHA256,
        "receipt_sha256": failure.FAILED_RECEIPT_SHA256,
        "terminal_store_sha256": failure.FAILED_STORE_SHA256,
        "terminal_request_count": 360,
        "parsed_count": 360,
        "accepted_count": 355,
        "technical_failure_count": 0,
        "semantic_failure_count": 5,
        "source_claim_instance_count": 710,
        "expected_source_claim_instance_count": 720,
        "provider_attempt_count": 385,
        "technical_attempt_count": 430,
        "provider_failure_category_counts": {"rate_limited": 70},
        "semantic_issue_counts": {"source_value_mismatch": 5},
        "provider_usage_complete": False,
        "rerun_forbidden": True,
        "failures_preserved_without_adaptive_replacement": True,
        "outcomes": outcomes,
        **failure.FALSE_FLAGS,
    }

    closeout = failure.audit_failed_source_cohort(ROOT, Path("/unused"), receipt)

    assert closeout == build_failure_closeout()
