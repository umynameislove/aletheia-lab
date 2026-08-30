from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.instrument_validation import (
    LABEL_ORDER,
    BlindClaim,
    ClaimPoolEntry,
    FinalClaimJudgment,
    InstrumentValidationError,
    OutcomeBlindPreparationReceipt,
    VisibleEvidenceExcerpt,
    compile_validation_report,
    load_preparation_receipt,
    load_validation_protocol,
    prepare_validation_packets,
    select_validation_sample,
)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "configs/evaluation/claim_support_validation_protocol.json"
RECEIPT_PATH = ROOT / "configs/evaluation/claim_support_validation_preparation_receipt.json"


def _evidence(index: int) -> VisibleEvidenceExcerpt:
    payload = {
        "evidence_id": f"evidence-{index:016x}",
        "artifact_ref": f"development/artifact-{index % 20}.json",
        "excerpt": f"Development-visible evidence excerpt {index}.",
    }
    return VisibleEvidenceExcerpt(**payload, excerpt_sha256=canonical_sha256(payload))


def _entry(index: int, label_index: int) -> ClaimPoolEntry:
    claim_types = (
        "cause_assertion",
        "evidence_statement",
        "uncertainty_statement",
        "recommended_action",
        "other",
    )
    conditions = ("full", "missing_key", "noisy", "misleading")
    variants = ("B1", "A3")
    payload = {
        "schema_version": "claim-support-pool-entry/v1",
        "claim_id": f"claim-{index:016x}",
        "output_id": f"output-{index // 2:016x}",
        "case_family_id": f"family-{index % 40:016x}",
        "claim_text": f"Atomic development claim {index}.",
        "claim_type": claim_types[index % len(claim_types)],
        "evidence_condition": conditions[index % len(conditions)],
        "variant": variants[index % len(variants)],
        "automatic_label": LABEL_ORDER[label_index],
        "visible_evidence": (_evidence(index).model_dump(),),
        "source_partition": "development",
        "source_record_sha256": f"{index + 1000:064x}"[-64:],
    }
    return ClaimPoolEntry(**payload, entry_sha256=canonical_sha256(payload))


def _pool() -> tuple[ClaimPoolEntry, ...]:
    return tuple(_entry(label_index * 1000 + offset, label_index) for label_index in range(4) for offset in range(60))


def _judgments(*, degraded: bool = False) -> tuple[FinalClaimJudgment, ...]:
    records: list[FinalClaimJudgment] = []
    for label_index, label in enumerate(LABEL_ORDER):
        for offset in range(50):
            adjudicated = label
            if degraded and label in {"partially_supported", "fully_supported"} and offset < 25:
                adjudicated = "contradicted"
            records.append(
                FinalClaimJudgment(
                    blind_claim_id=f"blind-claim-{label_index * 50 + offset:064x}",
                    claim_id=f"claim-{label_index * 50 + offset:016x}",
                    case_family_id=f"family-{offset % 20:016x}",
                    automatic_label=label,
                    rater_1_label=adjudicated,
                    rater_2_label=adjudicated,
                    adjudicated_label=adjudicated,
                )
            )
    return tuple(records)


def test_tracked_protocol_and_preparation_receipt_are_outcome_blind() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)
    receipt = load_preparation_receipt(RECEIPT_PATH)

    assert receipt.protocol_sha256 == protocol.protocol_sha256
    assert protocol.sample_target == 200
    assert protocol.automatic_label_quota == 50
    assert receipt.status == "outcome_blind_preparation_complete_human_validation_pending"
    assert not receipt.development_claim_pool_materialized
    assert not receipt.human_annotations_collected
    assert not receipt.validation_metrics_generated
    assert not receipt.main_or_sealed_outcomes_opened


def test_sample_is_balanced_deterministic_and_permutation_invariant() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)
    pool = _pool()

    selected = select_validation_sample(pool, protocol)
    reversed_selected = select_validation_sample(tuple(reversed(pool)), protocol)

    assert len(selected) == 200
    assert [entry.entry_sha256 for entry in selected] == [
        entry.entry_sha256 for entry in reversed_selected
    ]
    assert {
        label: sum(entry.automatic_label == label for entry in selected) for label in LABEL_ORDER
    } == {label: 50 for label in LABEL_ORDER}


def test_insufficient_label_stratum_fails_without_padding() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)
    pool = tuple(entry for entry in _pool() if entry.automatic_label != "contradicted")
    pool += tuple(_entry(9000 + offset, 0) for offset in range(49))

    with pytest.raises(InstrumentValidationError, match="insufficient eligible"):
        select_validation_sample(pool, protocol)


def test_blind_packets_withhold_evaluator_and_condition_metadata() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)
    rater_1, rater_2, mapping, receipt = prepare_validation_packets(_pool(), protocol)

    assert rater_1.claims == rater_2.claims
    assert rater_1.packet_sha256 != rater_2.packet_sha256
    assert receipt.sample_count == 200
    assert receipt.human_annotations_collected is False
    assert mapping.mapping_sha256 == receipt.evaluator_mapping_sha256
    blind_keys = set(rater_1.claims[0].model_dump())
    assert blind_keys == {"schema_version", "blind_claim_id", "claim_text", "visible_evidence"}
    assert not blind_keys & {
        "automatic_label",
        "claim_id",
        "output_id",
        "case_family_id",
        "claim_type",
        "evidence_condition",
        "variant",
    }


def test_blind_claim_rejects_hidden_ground_truth() -> None:
    with pytest.raises(ValidationError, match="hidden_ground_truth"):
        BlindClaim(
            blind_claim_id=f"blind-claim-{'a' * 64}",
            claim_text="A bounded claim.",
            visible_evidence=(),
            hidden_ground_truth="data_drift",  # type: ignore[call-arg]
        )


def test_complete_high_agreement_study_passes_all_gates() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)

    report = compile_validation_report(_judgments(), protocol)

    assert report.status == "pass"
    assert report.quadratic_weighted_kappa.estimate == pytest.approx(1.0)
    assert report.automatic_macro_f1.estimate == pytest.approx(1.0)
    assert report.false_supported_rate.estimate == 0.0
    assert report.contradicted_to_supported_rate.estimate == 0.0


def test_high_risk_false_support_blocks_scientific_use() -> None:
    protocol = load_validation_protocol(PROTOCOL_PATH)

    report = compile_validation_report(_judgments(degraded=True), protocol)

    assert report.status == "blocked"
    assert not report.macro_f1_gate_passed
    assert not report.false_supported_gate_passed
    assert not report.contradicted_to_supported_gate_passed


def test_preparation_receipt_cannot_claim_human_results() -> None:
    payload = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    payload["human_annotations_collected"] = True
    payload.pop("receipt_sha256")
    payload["receipt_sha256"] = canonical_sha256(payload)

    with pytest.raises(ValidationError):
        OutcomeBlindPreparationReceipt.model_validate_json(json.dumps(payload))
