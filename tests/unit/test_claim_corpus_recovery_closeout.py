from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_construction import load_claim_pool_preparation
from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimNormalizationRecord,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_corpus_recovery_closeout import (
    RecoveryExecutionCloseout,
    RecoveryRequestDisposition,
    _summaries,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]


def _dispositions() -> tuple[RecoveryRequestDisposition, ...]:
    census = ClaimCorpusRequestCensus.model_validate_json((ROOT / REQUEST_CENSUS_PATH).read_bytes())
    records: list[RecoveryRequestDisposition] = []
    for ordinal, request in enumerate(census.primary_requests, start=1):
        parsed = ordinal <= 282
        attempt_count = 1 if ordinal <= 260 else 2
        if ordinal <= 116:
            claim_count = 4
        elif parsed:
            claim_count = 3
        else:
            claim_count = 0
        payload: dict[str, object] = {
            "request_ordinal": ordinal,
            "request_sha256": request.request_sha256,
            "request_identity_sha256": canonical_execution_sha256(
                {"recovery": request.request_sha256}
            ),
            "mechanism": request.mechanism,
            "family_id": request.family_id,
            "evidence_condition": request.evidence_condition,
            "variant": request.variant,
            "gateway_status": "parsed" if parsed else "retry_exhausted",
            "attempt_count": attempt_count,
            "issue_code": None if parsed else "retry_exhausted",
            "normalized_output_count": int(parsed),
            "claim_candidate_count": claim_count,
        }
        records.append(
            RecoveryRequestDisposition.model_validate(
                {
                    **payload,
                    "disposition_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    return tuple(records)


def _closeout_payload() -> dict[str, object]:
    records = _dispositions()
    return {
        "schema_version": "claim-corpus-recovery-closeout/v1",
        "status": "recovery_execution_closed_missingness_preserved",
        "source_commit_ref": "1" * 40,
        "authorization_sha256": "2" * 64,
        "protocol_sha256": "3" * 64,
        "transport_sha256": "4" * 64,
        "execution_plan_sha256": "5" * 64,
        "recovery_receipt_sha256": "6" * 64,
        "recovery_terminal_store_sha256": "7" * 64,
        "evidence_census_sha256": "8" * 64,
        "terminal_request_count": 360,
        "parsed_terminal_count": 282,
        "technical_failure_terminal_count": 78,
        "technical_attempt_count": 460,
        "normalized_output_count": 282,
        "claim_candidate_count": 962,
        "first_technical_failure_ordinal": 283,
        "consecutive_parsed_prefix_count": 282,
        "execution_order_dimensions": (
            "mechanism",
            "family",
            "evidence_condition",
            "variant",
        ),
        "missingness_exchangeability_status": ("not_established_execution_order_confounded"),
        "mechanism_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "mechanism")
        ),
        "condition_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "evidence_condition")
        ),
        "variant_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "variant")
        ),
        "family_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "family_id")
        ),
        "request_dispositions": tuple(item.model_dump(mode="json") for item in records),
        "failures_preserved_in_denominator": True,
        "recovery_rerun_forbidden": True,
        "reserve_activation_forbidden_after_execution": True,
        "availability_may_not_be_interpreted_as_mechanism_performance": True,
        "ready_for_recovery_pool_preparation": True,
        "relation_assignments_generated": False,
        "automatic_labels_generated": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }


def _recovery_preparation() -> RecoveryClaimPoolPreparation:
    census = ClaimCorpusRequestCensus.model_validate_json((ROOT / REQUEST_CENSUS_PATH).read_bytes())
    records: list[ClaimNormalizationRecord] = []
    for request in census.primary_requests:
        payload: dict[str, object] = {
            "request_sha256": request.request_sha256,
            "request_identity_sha256": canonical_execution_sha256(
                {"recovery": request.request_sha256}
            ),
            "variant": request.variant,
            "gateway_status": "retry_exhausted",
            "normalization_status": "technical_failure",
            "source_record_sha256": None,
            "issue_sha256": "9" * 64,
            "normalized_output": None,
            "relation_request_sha256s": (),
            "blocker_code": "technical_terminal",
        }
        records.append(
            ClaimNormalizationRecord.model_validate(
                {**payload, "record_sha256": canonical_execution_sha256(payload)}
            )
        )
    payload = {
        "schema_version": "claim-pool-recovery-preparation/v1",
        "source_commit_ref": "1" * 40,
        "authorization_sha256": "2" * 64,
        "execution_plan_sha256": "3" * 64,
        "recovery_receipt_sha256": "4" * 64,
        "recovery_closeout_sha256": "5" * 64,
        "recovery_terminal_store_sha256": "6" * 64,
        "evidence_census_sha256": "7" * 64,
        "evidence_semantics_policy_sha256": "8" * 64,
        "terminal_request_count": 360,
        "parsed_terminal_count": 0,
        "technical_failure_terminal_count": 360,
        "normalized_output_count": 0,
        "normalization_rejection_count": 0,
        "completed_output_count": 0,
        "abstained_output_count": 0,
        "claim_candidate_count": 0,
        "relation_request_count": 0,
        "canonical_claim_text_count": 0,
        "repeated_claim_instance_count": 0,
        "records": tuple(item.model_dump(mode="json") for item in records),
        "relation_requests": (),
        "failures_preserved_in_denominator": True,
        "recovery_rerun_performed": False,
        "free_text_recovery_performed": False,
        "automatic_labels_generated": False,
        "corpus_entries_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return RecoveryClaimPoolPreparation.model_validate(
        {
            **payload,
            "records": tuple(records),
            "preparation_sha256": canonical_execution_sha256(payload),
        }
    )


def test_recovery_closeout_preserves_the_complete_denominator_and_risk_boundary() -> None:
    payload = _closeout_payload()
    closeout = RecoveryExecutionCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
    )

    assert closeout.terminal_request_count == 360
    assert closeout.parsed_terminal_count == 282
    assert closeout.technical_failure_terminal_count == 78
    assert closeout.technical_attempt_count == 460
    assert closeout.claim_candidate_count == 962
    assert closeout.missingness_exchangeability_status == (
        "not_established_execution_order_confounded"
    )
    assert closeout.availability_may_not_be_interpreted_as_mechanism_performance
    assert closeout.ready_for_recovery_pool_preparation
    assert not closeout.claims_materialized
    assert not closeout.blind_packets_generated


def test_recovery_closeout_rejects_a_rehashed_inconsistent_missingness_table() -> None:
    payload = _closeout_payload()
    summaries = list(payload["mechanism_summaries"])  # type: ignore[arg-type]
    first = dict(summaries[0])
    first["claim_candidate_count"] = int(first["claim_candidate_count"]) + 1
    summaries[0] = first
    payload["mechanism_summaries"] = tuple(summaries)

    with pytest.raises(ValueError, match="missingness tables"):
        RecoveryExecutionCloseout.model_validate(
            {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
        )


def test_recovery_disposition_rejects_failure_with_claim_candidates() -> None:
    disposition = _dispositions()[-1]
    payload = disposition.model_dump(mode="python", exclude={"disposition_sha256"})
    payload["claim_candidate_count"] = 1

    with pytest.raises(ValueError, match="cannot contribute"):
        RecoveryRequestDisposition.model_validate(
            {
                **payload,
                "disposition_sha256": canonical_execution_sha256(payload),
            }
        )


def test_recovery_preparation_preserves_all_failed_requests_in_denominator() -> None:
    preparation = _recovery_preparation()

    assert len(preparation.records) == 360
    assert preparation.technical_failure_terminal_count == 360
    assert preparation.relation_requests == ()
    assert preparation.failures_preserved_in_denominator


def test_recovery_preparation_round_trips_through_publication_loader(tmp_path: Path) -> None:
    preparation = _recovery_preparation()
    path = tmp_path / "claim-pool-recovery-preparation.json"
    path.write_text(preparation.model_dump_json(), encoding="utf-8")

    loaded = load_claim_pool_preparation(path)

    assert loaded == preparation


def test_recovery_preparation_rejects_rehashed_duplicate_count_mismatch() -> None:
    preparation = _recovery_preparation()
    payload = preparation.model_dump(mode="python", exclude={"preparation_sha256"})
    payload["repeated_claim_instance_count"] = 1

    with pytest.raises(ValueError, match="census does not reconcile"):
        RecoveryClaimPoolPreparation.model_validate(
            {**payload, "preparation_sha256": canonical_execution_sha256(payload)}
        )
