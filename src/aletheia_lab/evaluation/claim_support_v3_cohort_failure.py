"""Read-only closeout and root-cause audit for the failed V3.1 source cohort.

The audit binds public-safe metadata and mismatch classes, never the observed or
expected numeric values.  It cannot authorize a rerun, relations, materialization
or a blind packet.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    facts,
    source_payload_issue,
    target_parts,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    read_document,
    seal,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

FAILURE_CLOSEOUT_PATH = "configs/evaluation/claim_support_validation_v3_source_cohort_failure.json"
FAILED_SOURCE_COMMIT = "d69ce3af5ade3a08a3cae2c8a861cdd6e1be7fb4"
FAILED_AUTHORIZATION_SHA256 = "63676babbd3589513acd4367bb8746d2006d2e9416297ee0f747832b66868a0f"
FAILED_PLAN_SHA256 = "e1da46d3758fd97f778afc79cf0e66562ee762cb41acc822a4389cf6cce37982"
FAILED_REHEARSAL_SHA256 = "645f2cd9e219bd74d5a903db2b880aff284e48db664b677aa879ea5a5f6209bf"
FAILED_QUALIFICATION_RECEIPT_SHA256 = (
    "2d5e0d592a7020a23fc55b564cabe9731fc915ff05b4549bba00a9a386e684e5"
)
FAILED_PROTOCOL_SHA256 = "26ee09f82c92a6a822e296827bcb3946eb01b0889171ca080dcea5389ab2b141"
FAILED_DESIGN_SHA256 = "138d22359d891b8e08c8690f9f4efeb7d59475e104eeb16229b77d343ba2e152"
FAILED_RECEIPT_SHA256 = "877bd71911f087c08ee783853f22d57231cae83549f2bcb352d82d0fa0e8625d"
FAILED_STORE_SHA256 = "fec3f8b1c02c8260fc64634c1b41d0ded82123258f30e71e5aa9ac08b9ac960c"
FAILED_SEQUENCES = (26, 53, 103, 232, 307)
FALSE_FLAGS = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "admitted_to_corpus": False,
    "relation_execution_authorized": False,
    "relation_planning_unlocked": False,
}


def _case(
    *,
    sequence: int,
    schedule_round: int,
    variant: str,
    family_id: str,
    mechanism: str,
    evidence_condition: str,
    gateway: str,
    request: str,
    slot: str,
    payload: str,
    outcome: str,
    mismatches: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "schedule_round": schedule_round,
        "variant": variant,
        "family_id": family_id,
        "mechanism": mechanism,
        "evidence_condition": evidence_condition,
        "gateway_request_identity_sha256": gateway,
        "source_request_sha256": request,
        "slot_sha256": slot,
        "parsed_payload_sha256": payload,
        "outcome_sha256": outcome,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


EXPECTED_FAILURE_CASES = (
    _case(
        sequence=26,
        schedule_round=2,
        variant="A3",
        family_id="ccf-data-drift-categorical-contract-balance-40-4",
        mechanism="data_drift",
        evidence_condition="noisy",
        gateway="8cccd165a49c5e4947ba760b02efc69e19e8022cb73b2fd6e88a6d90deb987d4",
        request="02a2d1e967024d82d5885562d9c81dbfd22c35c9fd1ade802cb63578e5db7439",
        slot="cea6abf7d6c5660b2421ae6555edf2fdecb3639b23f114ad2d71da8a8e6e4ada",
        payload="f71ac7b89481ae243febaeae7a4374ec1cae1c46dd19774c07165802b8563486",
        outcome="22de11f8df62a037c5fcc186f132af4217ea9476c3eb5131e408bfc8d2880347",
        mismatches=[
            {
                "target": target,
                "expected_evidence_id": evidence_id,
                "expected_json_pointer": pointer,
                "classification": "target_ordinal_token_copied_as_value",
                "matched_visible_locations": [],
            }
            for target, evidence_id, pointer in (
                (1, "ev-key-measurement", "/payload/observed_shares/Month-to-month"),
                (2, "ev-performance-summary", "/payload/delta/minority_recall"),
                (3, "ev-key-measurement", "/payload/observed_shares/Month-to-month"),
                (4, "ev-performance-summary", "/payload/observed/accuracy"),
            )
        ],
    ),
    _case(
        sequence=53,
        schedule_round=3,
        variant="A2",
        family_id="ccf-data-drift-categorical-contract-balance-40-4",
        mechanism="data_drift",
        evidence_condition="missing_key",
        gateway="60fa7d5fc458af88bc9a037a8c3971144226ffff21d81af7f66ac3e9a2349c2f",
        request="16dae5b88b7dc318ea04d3bb211dd28d434702ccdc9ea063f821f31a4d87d118",
        slot="2fa2d12f6b692f3831adebc8272335b00de91127dc36177606ebb9ad825cf619",
        payload="088f860acc8bb71ccbe36830e7c470beee0487272591ef50eea23b010198455f",
        outcome="008ab126185b5ca8de52e0f1e5962756b16d3827d37a5a6c94a478728516ba59",
        mismatches=[
            {
                "target": 1,
                "expected_evidence_id": "ev-performance-summary",
                "expected_json_pointer": "/payload/delta/minority_recall",
                "classification": "wrong_visible_numeric_leaf",
                "matched_visible_locations": [
                    {
                        "evidence_id": "ev-performance-summary",
                        "json_pointer": "/payload/observed/minority_recall",
                    }
                ],
            }
        ],
    ),
    _case(
        sequence=103,
        schedule_round=5,
        variant="A2",
        family_id="ccf-label-noise-symmetric-flip-10-2",
        mechanism="label_noise",
        evidence_condition="noisy",
        gateway="b3497c9319a7d527bbd41f6ece98d5614a5c5ab6951df978af6eb7df18bd566b",
        request="b451d625fa36d76417aeda30e24723d8e47bc4592f70d7b400be659c32a12f13",
        slot="8100ca9b5f40ff18d5e51c490c60fd6bfcb0d7d78d3e8e6b5eae84d907ec9d28",
        payload="ac06aca07c7013540427d14a8cb657d1579e16ce5915897217e60f8d5b3f9c03",
        outcome="3410b43a2bd88726dd8c02b0c6a89630050bc316ee28b1451b0cd33c802779c0",
        mismatches=[
            {
                "target": 2,
                "expected_evidence_id": "ev-performance-summary",
                "expected_json_pointer": "/payload/delta/minority_recall",
                "classification": "wrong_visible_numeric_leaf",
                "matched_visible_locations": [
                    {
                        "evidence_id": "ev-performance-summary",
                        "json_pointer": "/payload/observed/minority_recall",
                    }
                ],
            }
        ],
    ),
    _case(
        sequence=232,
        schedule_round=10,
        variant="A2",
        family_id="ccf-label-noise-symmetric-flip-10-2",
        mechanism="label_noise",
        evidence_condition="full",
        gateway="355ddb76d19b766dc22d3667d1f97fae9f7385ee0c6d46b76f2cce04ec3e2917",
        request="ed18838698916725bab32b0a903a01a1c301d745903751d20420efde370ce716",
        slot="0adeb9429467c66c11f8ee3d7838a97865d0807232bfaede48281a44f8b5d58f",
        payload="ac06aca07c7013540427d14a8cb657d1579e16ce5915897217e60f8d5b3f9c03",
        outcome="61d0ca305fe3cd1e3b6538d9133c6bdff7cf88d56758d204063b475747a6462d",
        mismatches=[
            {
                "target": 2,
                "expected_evidence_id": "ev-performance-summary",
                "expected_json_pointer": "/payload/delta/minority_recall",
                "classification": "wrong_visible_numeric_leaf",
                "matched_visible_locations": [
                    {
                        "evidence_id": "ev-performance-summary",
                        "json_pointer": "/payload/observed/minority_recall",
                    }
                ],
            }
        ],
    ),
    _case(
        sequence=307,
        schedule_round=13,
        variant="FULL",
        family_id="ccf-data-drift-categorical-contract-shift-60-5",
        mechanism="data_drift",
        evidence_condition="full",
        gateway="a506589ed92fa4d8cb7d88e2a8cd0373b96fea47d241a219c5d6ae63ea1b5f99",
        request="d838f302416c74a27d49d20a1d80e776d946cf6a0c4319414ee23ca8a3bcf2f8",
        slot="4a742425e012c4765085aff563bc66402729c0479f278db2bb364aa7c9470247",
        payload="f71ac7b89481ae243febaeae7a4374ec1cae1c46dd19774c07165802b8563486",
        outcome="05f6b33cd9237ab8dae2b5137833ff227492b361c13ece9dd9ac8957d6caa268",
        mismatches=[
            {
                "target": target,
                "expected_evidence_id": evidence_id,
                "expected_json_pointer": pointer,
                "classification": "target_ordinal_token_copied_as_value",
                "matched_visible_locations": [],
            }
            for target, evidence_id, pointer in (
                (1, "ev-key-measurement", "/payload/observed_shares/Month-to-month"),
                (2, "ev-performance-summary", "/payload/delta/accuracy"),
                (3, "ev-key-measurement", "/payload/observed_shares/Month-to-month"),
                (4, "ev-performance-summary", "/payload/delta/macro_f1"),
            )
        ],
    ),
)


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def classify_source_reading_mismatch(
    *,
    observed_value: str,
    expected_value: str,
    target_count: int,
    visible_facts: dict[tuple[str, str], str],
) -> tuple[str, list[dict[str, str]]]:
    """Classify one mismatch without returning either numeric value."""

    try:
        if Decimal(observed_value) == Decimal(expected_value):
            return "numeric_reformat_only", []
    except (InvalidOperation, ValueError):
        pass
    matches = sorted(
        (evidence_id, pointer)
        for (evidence_id, pointer), value in visible_facts.items()
        if value == observed_value
    )
    if matches:
        return "wrong_visible_numeric_leaf", [
            {"evidence_id": evidence_id, "json_pointer": pointer}
            for evidence_id, pointer in matches
        ]
    if observed_value in {str(target) for target in range(1, target_count + 1)}:
        return "target_ordinal_token_copied_as_value", []
    return "nonvisible_or_unclassified_value", []


def _flatten_targets(slot: dict[str, Any]) -> list[tuple[int, str, str, str]]:
    parts = (part for claim in slot["expected"] for part in target_parts(claim))
    return [
        (target, evidence_id, pointer, value)
        for target, (evidence_id, pointer, value) in enumerate(parts, start=1)
    ]


def _analyze_failure_case(
    slot: dict[str, Any], outcome: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    targets = _flatten_targets(slot)
    readings = payload.get("readings")
    if source_payload_issue(payload, slot["expected"]) != "source_value_mismatch":
        raise ValueError("failed source payload does not have the registered mismatch")
    if not isinstance(readings, list) or len(readings) != len(targets):
        raise ValueError("failed source reading census differs")
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(slot["context"]))
    visible = facts(context)
    mismatches: list[dict[str, Any]] = []
    for reading, (target, evidence_id, pointer, expected_value) in zip(
        readings, targets, strict=True
    ):
        if reading["value"] == expected_value:
            continue
        classification, locations = classify_source_reading_mismatch(
            observed_value=reading["value"],
            expected_value=expected_value,
            target_count=len(targets),
            visible_facts=visible,
        )
        mismatches.append(
            {
                "target": target,
                "expected_evidence_id": evidence_id,
                "expected_json_pointer": pointer,
                "classification": classification,
                "matched_visible_locations": locations,
            }
        )
    schedule = slot["source_schedule"]
    return _case(
        sequence=outcome["sequence"],
        schedule_round=schedule["schedule_round"],
        variant=schedule["variant"],
        family_id=schedule["family_id"],
        mechanism=schedule["mechanism"],
        evidence_condition=schedule["evidence_condition"],
        gateway=outcome["gateway_request_identity_sha256"],
        request=outcome["source_request_sha256"],
        slot=outcome["slot_sha256"],
        payload=outcome["parsed_payload_sha256"],
        outcome=outcome["outcome_sha256"],
        mismatches=mismatches,
    )


def _build_closeout(
    *,
    failure_cases: tuple[dict[str, Any], ...],
    provider_attempts_with_usage: int,
    provider_attempts_without_usage: int,
    observed_provider_input_tokens: int,
    observed_provider_output_tokens: int,
    observed_provider_total_tokens: int,
    rate_limited_attempt_count: int,
    rate_limited_request_count: int,
    rate_limited_requests_accepted: int,
    rate_limited_failure_overlap_count: int,
) -> dict[str, Any]:
    classifications = Counter(
        mismatch["classification"] for case in failure_cases for mismatch in case["mismatches"]
    )
    return seal(
        {
            "schema_version": "claim-support-v3-source-cohort-failure-closeout/1",
            "status": "v3_1_source_cohort_closed_semantic_failure",
            "source_commit_ref": FAILED_SOURCE_COMMIT,
            "authorization_sha256": FAILED_AUTHORIZATION_SHA256,
            "plan_sha256": FAILED_PLAN_SHA256,
            "rehearsal_sha256": FAILED_REHEARSAL_SHA256,
            "qualification_receipt_sha256": FAILED_QUALIFICATION_RECEIPT_SHA256,
            "protocol_sha256": FAILED_PROTOCOL_SHA256,
            "design_sha256": FAILED_DESIGN_SHA256,
            "receipt_sha256": FAILED_RECEIPT_SHA256,
            "terminal_store_sha256": FAILED_STORE_SHA256,
            "terminal_request_count": 360,
            "parsed_count": 360,
            "accepted_count": 355,
            "technical_failure_count": 0,
            "semantic_failure_count": 5,
            "semantic_issue_counts": {"source_value_mismatch": 5},
            "source_claim_instance_count": 710,
            "expected_source_claim_instance_count": 720,
            "failure_sequences": list(FAILED_SEQUENCES),
            "failure_cases": list(failure_cases),
            "mismatched_reading_count": sum(case["mismatch_count"] for case in failure_cases),
            "mismatch_classification_counts": dict(sorted(classifications.items())),
            "failure_request_variant_counts": {"A2": 3, "A3": 1, "FULL": 1},
            "failure_request_mechanism_counts": {"data_drift": 3, "label_noise": 2},
            "failure_request_condition_counts": {"full": 2, "missing_key": 1, "noisy": 2},
            "implementation_mapping_defect_detected": False,
            "serialization_defect_detected": False,
            "numeric_reformat_only_count": classifications.get("numeric_reformat_only", 0),
            "provider_semantic_noncompliance_established": True,
            "provider_attempt_count": 385,
            "provider_attempts_with_usage": provider_attempts_with_usage,
            "provider_attempts_without_usage": provider_attempts_without_usage,
            "provider_usage_complete": False,
            "provider_usage_incomplete_reason": (
                "retryable_rate_limit_attempts_have_no_provider_usage_metadata"
            ),
            "observed_provider_input_token_count_for_attempts_with_usage": (
                observed_provider_input_tokens
            ),
            "observed_provider_output_token_count_for_attempts_with_usage": (
                observed_provider_output_tokens
            ),
            "observed_provider_total_token_count_for_attempts_with_usage": (
                observed_provider_total_tokens
            ),
            "rate_limited_attempt_count": rate_limited_attempt_count,
            "rate_limited_request_count": rate_limited_request_count,
            "rate_limited_requests_accepted": rate_limited_requests_accepted,
            "rate_limited_failure_overlap_count": rate_limited_failure_overlap_count,
            "rate_limit_recovery_caused_semantic_failures": False,
            "qualification_result_reinterpreted": False,
            "qualification_pass_guaranteed_cohort_perfection": False,
            "failures_preserved_without_adaptive_replacement": True,
            "accepted_sources_retrospectively_admitted": False,
            "retrospective_value_repair_permitted": False,
            "deterministic_substitution_would_change_measurement_semantics": True,
            "variant_superiority_claims_permitted": False,
            "failure_rate_generalization_permitted": False,
            "rerun_forbidden": True,
            "prospective_successor_required": True,
            "next_authorized_action": ("design_prospective_v3_2_source_measurement_role_amendment"),
            "new_provider_execution_authorized": False,
            "source_measurements_collected": True,
            "provider_calls_executed": True,
            **FALSE_FLAGS,
        },
        "closeout_sha256",
    )


def build_failure_closeout() -> dict[str, Any]:
    """Return the frozen public-safe closeout expected from the terminal audit."""

    return _build_closeout(
        failure_cases=EXPECTED_FAILURE_CASES,
        provider_attempts_with_usage=315,
        provider_attempts_without_usage=70,
        observed_provider_input_tokens=382_931,
        observed_provider_output_tokens=17_737,
        observed_provider_total_tokens=400_668,
        rate_limited_attempt_count=70,
        rate_limited_request_count=70,
        rate_limited_requests_accepted=70,
        rate_limited_failure_overlap_count=0,
    )


def verify_failure_closeout(root: Path) -> dict[str, Any]:
    tracked = read_document(root / FAILURE_CLOSEOUT_PATH, "closeout_sha256")
    expected = build_failure_closeout()
    if tracked != expected:
        raise ValueError("V3.1 source failure closeout differs from frozen audit")
    return tracked


def _validate_receipt(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    expected = {
        "status": "v3_1_source_cohort_complete_with_failures",
        "source_commit_ref": FAILED_SOURCE_COMMIT,
        "authorization_sha256": FAILED_AUTHORIZATION_SHA256,
        "plan_sha256": FAILED_PLAN_SHA256,
        "rehearsal_sha256": FAILED_REHEARSAL_SHA256,
        "qualification_receipt_sha256": FAILED_QUALIFICATION_RECEIPT_SHA256,
        "protocol_sha256": FAILED_PROTOCOL_SHA256,
        "design_sha256": FAILED_DESIGN_SHA256,
        "receipt_sha256": FAILED_RECEIPT_SHA256,
        "terminal_store_sha256": FAILED_STORE_SHA256,
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
    }
    if any(receipt.get(key) != value for key, value in expected.items()) or any(
        receipt.get(key) is not value for key, value in FALSE_FLAGS.items()
    ):
        raise ValueError("V3.1 source receipt differs from registered failed cohort")
    outcomes = receipt.get("outcomes")
    if (
        not isinstance(outcomes, list)
        or len(outcomes) != 360
        or [row.get("sequence") for row in outcomes] != list(range(1, 361))
    ):
        raise ValueError("V3.1 source outcome census differs")
    return outcomes


def audit_failed_source_cohort(
    root: Path, run: Path, verified_receipt: dict[str, Any]
) -> dict[str, Any]:
    """Reconstruct the registered closeout from authenticated terminal records."""

    outcomes = _validate_receipt(verified_receipt)
    design = build_design(root)
    if design.get("design_sha256") != FAILED_DESIGN_SHA256:
        raise ValueError("V3.1 source design differs from registered cohort")
    store = run / "attempt-store"
    failures: list[dict[str, Any]] = []
    rate_limited_requests = 0
    rate_limited_accepted = 0
    rate_limited_failure_overlap = 0
    attempts_with_usage = 0
    attempts_without_usage = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    rate_limited_attempts = 0
    for outcome, slot in zip(outcomes, design["slots"], strict=True):
        identity = outcome["gateway_request_identity_sha256"]
        reader = _reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        payload = reader.terminal_parsed_payload(identity)
        records = reader.terminal_attempt_records(identity)
        if (
            inventory.gateway_status != "parsed"
            or payload is None
            or len(records) != outcome["attempt_count"]
            or content_sha256(canonical_project_json(payload).encode("utf-8"))
            != outcome["parsed_payload_sha256"]
            or slot["source_schedule"]["sequence"] != outcome["sequence"]
            or slot["slot_sha256"] != outcome["slot_sha256"]
        ):
            raise ValueError("V3.1 source terminal binding differs")
        issue = source_payload_issue(payload, slot["expected"])
        if outcome["accepted"] is not (issue is None):
            raise ValueError("V3.1 source receipt acceptance differs from payload")
        rate_limited = sum(
            str(record.provider_failure_category) == "rate_limited" for record in records
        )
        if rate_limited:
            rate_limited_requests += 1
            rate_limited_accepted += int(outcome["accepted"])
            rate_limited_failure_overlap += int(not outcome["accepted"])
        if outcome["execution_route"] == "model_gateway":
            for record in records:
                rate_limited_attempts += int(
                    str(record.provider_failure_category) == "rate_limited"
                )
                if record.usage is None:
                    attempts_without_usage += 1
                else:
                    attempts_with_usage += 1
                    input_tokens += record.usage.input_tokens or 0
                    output_tokens += record.usage.output_tokens or 0
                    total_tokens += record.usage.total_tokens or 0
        elif any(record.usage is None or record.usage.total_tokens != 0 for record in records):
            raise ValueError("V3.1 deterministic usage record differs")
        if not outcome["accepted"]:
            failures.append(_analyze_failure_case(slot, outcome, payload))
    actual = _build_closeout(
        failure_cases=tuple(failures),
        provider_attempts_with_usage=attempts_with_usage,
        provider_attempts_without_usage=attempts_without_usage,
        observed_provider_input_tokens=input_tokens,
        observed_provider_output_tokens=output_tokens,
        observed_provider_total_tokens=total_tokens,
        rate_limited_attempt_count=rate_limited_attempts,
        rate_limited_request_count=rate_limited_requests,
        rate_limited_requests_accepted=rate_limited_accepted,
        rate_limited_failure_overlap_count=rate_limited_failure_overlap,
    )
    tracked = verify_failure_closeout(root)
    if actual != tracked:
        raise ValueError("V3.1 source terminal audit differs from tracked closeout")
    return actual


__all__ = [
    "FAILURE_CLOSEOUT_PATH",
    "audit_failed_source_cohort",
    "build_failure_closeout",
    "classify_source_reading_mismatch",
    "verify_failure_closeout",
]
