from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.claim_corpus_protocol import (
    ELIGIBLE_VARIANTS,
    EXPECTED_BLOCKERS,
    LABEL_ORDER,
    MECHANISMS,
    ClaimCorpusProtocolError,
    ClaimSupportCorpusProtocol,
    audit_claim_support_corpus_protocol,
    load_claim_support_corpus_feasibility_receipt,
    load_claim_support_corpus_protocol,
    verify_tracked_claim_support_corpus_protocol,
)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "configs/evaluation/claim_support_corpus_protocol.json"
RECEIPT_PATH = ROOT / "configs/evaluation/claim_support_corpus_feasibility_receipt.json"


def _protocol() -> ClaimSupportCorpusProtocol:
    return load_claim_support_corpus_protocol(PROTOCOL_PATH)


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["protocol_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "protocol_sha256"}
    )
    return payload


def _copy_bound_artifacts(protocol: ClaimSupportCorpusProtocol, root: Path) -> None:
    for artifact in protocol.bound_artifacts:
        source = ROOT / artifact.path
        target = root / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["manifest_sha256"] = canonical_sha256(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_tracked_protocol_and_receipt_reconcile_without_opening_outcomes() -> None:
    receipt = verify_tracked_claim_support_corpus_protocol(ROOT, PROTOCOL_PATH, RECEIPT_PATH)

    assert receipt == load_claim_support_corpus_feasibility_receipt(RECEIPT_PATH)
    assert receipt.blocker_codes == EXPECTED_BLOCKERS
    assert receipt.status == "corpus_protocol_frozen_source_expansion_required"
    assert not receipt.materialization_ready
    assert not receipt.development_claim_pool_materialized
    assert not receipt.validation_sample_materialized
    assert not receipt.automatic_labels_generated
    assert not receipt.human_annotations_collected
    assert not receipt.main_or_sealed_outcomes_opened


def test_family_census_proves_current_inventory_cannot_fill_the_sample() -> None:
    protocol = _protocol()
    receipt = audit_claim_support_corpus_protocol(protocol, ROOT)

    assert tuple(item.mechanism for item in receipt.mechanism_census) == MECHANISMS
    assert tuple(item.declared_primary_families for item in receipt.mechanism_census) == (5, 0, 0)
    assert protocol.family_census.primary_family_total == 15
    assert protocol.family_census.reserve_family_total == 6
    assert receipt.current_maximum_selectable_claims_per_label == 25
    assert receipt.target_claims_per_label == 50
    assert "insufficient_development_family_census" in receipt.blocker_codes


def test_variant_boundary_excludes_only_incomparable_external_native_output() -> None:
    policy = _protocol().variant_eligibility

    assert policy.eligible_variants == ELIGIBLE_VARIANTS
    assert policy.excluded_variant == "B3"
    assert policy.excluded_output_pooling_forbidden
    assert "external_native_output" in policy.exclusion_reason


def test_automatic_label_semantics_and_blinding_are_exact() -> None:
    policy = _protocol().automatic_label_policy

    assert policy.ordered_labels == LABEL_ORDER
    assert tuple(item.label for item in policy.precedence) == LABEL_ORDER
    assert policy.permitted_input_fields == ("claim_text", "claim_type", "visible_evidence")
    assert set(policy.withheld_input_fields) == {
        "mechanism",
        "evidence_condition",
        "variant",
        "hidden_ground_truth",
        "human_judgment",
        "main_outcome",
    }
    assert not policy.human_judgment_may_rewrite_automatic_label
    assert policy.model_as_human_rater_forbidden


def test_atomic_claim_boundary_forbids_post_hoc_free_text_segmentation() -> None:
    policy = _protocol().atomic_claim_policy

    assert policy.extraction_source == "schema_native_atomic_claim_fields_only"
    assert policy.punctuation_or_sentence_splitting_forbidden
    assert policy.free_text_fallback_forbidden
    assert policy.maximum_atomic_claims_per_output == 5
    assert policy.visible_evidence_ids_required
    assert policy.source_record_sha256_required


def test_contingency_cannot_expand_or_replace_cases_from_observed_labels() -> None:
    policy = _protocol().contingency_policy

    assert policy.reserve_use == "pre_execution_technical_ineligibility_only"
    assert not policy.automatic_label_may_trigger_reserve
    assert not policy.human_judgment_may_trigger_reserve
    assert policy.output_driven_early_stopping_forbidden
    assert policy.label_specific_expansion_forbidden
    assert policy.adaptive_family_generation_forbidden
    assert policy.insufficient_completed_stratum_action == "block_without_padding_or_replacement"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("source_boundary", "source_partition", "main"),
        ("atomic_claim_policy", "free_text_fallback_forbidden", False),
        ("contingency_policy", "automatic_label_may_trigger_reserve", True),
        ("automatic_label_policy", "model_as_human_rater_forbidden", False),
    ),
)
def test_rehashed_scientific_boundary_weakening_is_still_rejected(
    section: str,
    field: str,
    value: object,
) -> None:
    payload = _protocol().model_dump(mode="python")
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = value

    with pytest.raises(ValidationError):
        ClaimSupportCorpusProtocol.model_validate(_rehash(payload))


def test_bound_artifact_change_fails_before_census_interpretation(tmp_path: Path) -> None:
    protocol = _protocol()
    _copy_bound_artifacts(protocol, tmp_path)
    fault_path = tmp_path / "configs/benchmark/fault_types.yaml"
    fault_path.write_text(fault_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ClaimCorpusProtocolError, match="bound artifact content changed"):
        audit_claim_support_corpus_protocol(protocol, tmp_path)


def test_unhashed_placeholder_manifests_cannot_clear_readiness_blockers(tmp_path: Path) -> None:
    protocol = _protocol()
    _copy_bound_artifacts(protocol, tmp_path)
    schema_path = tmp_path / protocol.atomic_claim_policy.required_schema_manifest_path
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text("{}\n", encoding="utf-8")
    instrument_path = (
        tmp_path / protocol.automatic_label_policy.required_implementation_manifest_path
    )
    instrument_path.write_text("{}\n", encoding="utf-8")

    receipt = audit_claim_support_corpus_protocol(protocol, tmp_path)

    assert "diagnosis_output_v2_schema_pending" in receipt.blocker_codes
    assert "automatic_instrument_manifest_pending" in receipt.blocker_codes


def test_self_hashing_outcome_blind_manifests_clear_only_their_own_blockers(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    _copy_bound_artifacts(protocol, tmp_path)
    _write_manifest(
        tmp_path / protocol.atomic_claim_policy.required_schema_manifest_path,
        {
            "schema_version": "diagnosis-output-schema-manifest/v1",
            "schema_ref": "diagnosis-output/2",
            "frozen_before_provider_calls": True,
            "provider_calls_executed": False,
        },
    )
    _write_manifest(
        tmp_path / protocol.automatic_label_policy.required_implementation_manifest_path,
        {
            "schema_version": "claim-support-automatic-instrument-manifest/v1",
            "corpus_protocol_sha256": protocol.protocol_sha256,
            "frozen_before_claim_materialization": True,
            "claim_pool_materialized": False,
            "automatic_labels_generated": False,
        },
    )

    receipt = audit_claim_support_corpus_protocol(protocol, tmp_path)

    assert "diagnosis_output_v2_schema_pending" not in receipt.blocker_codes
    assert "automatic_instrument_manifest_pending" not in receipt.blocker_codes
    assert "insufficient_development_family_census" in receipt.blocker_codes
    assert "reserve_family_census_pending" in receipt.blocker_codes
    assert not receipt.materialization_ready
