from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusFamilyInventory,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionError,
    RepositoryExecutionState,
    build_execution_preflight,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceBinding,
    build_visible_evidence_item,
    validate_request_evidence_binding,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.observed_evidence import (
    materialize_observed_evidence,
    validate_condition_semantics,
)
from aletheia_lab.evaluation.observed_evidence_receipt import (
    ObservedEvidenceReceipt,
    validate_observed_evidence_receipt,
)

ROOT = Path(__file__).resolve().parents[2]
CENSUS_PATH = ROOT / "configs/evaluation/claim_support_observed_evidence_census.json"
RECEIPT_PATH = ROOT / "configs/evaluation/claim_support_observed_evidence_receipt.json"
REQUEST_PATH = ROOT / "configs/evaluation/claim_support_request_census.json"
INVENTORY_PATH = ROOT / "configs/evaluation/claim_support_family_inventory.json"


@pytest.fixture(scope="module")
def observed() -> tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt]:
    census = ObservedEvidenceCensus.model_validate_json(CENSUS_PATH.read_bytes())
    receipt = ObservedEvidenceReceipt.model_validate_json(RECEIPT_PATH.read_bytes())
    return validate_condition_semantics(census), validate_observed_evidence_receipt(
        census, receipt
    )


def test_tracked_census_is_a_fresh_measured_reconstruction(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, _ = observed

    assert materialize_observed_evidence(ROOT) == census


def test_census_has_exact_registered_mechanism_family_condition_product(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, receipt = observed
    inventory = ClaimCorpusFamilyInventory.model_validate_json(INVENTORY_PATH.read_bytes())
    primary = {item.family_id: item for item in inventory.families if item.role == "primary"}

    assert len(census.bindings) == receipt.context_count == 45
    assert len(primary) == receipt.primary_family_count == 15
    assert Counter(item.mechanism for item in primary.values()) == Counter(
        {"data_drift": 5, "preprocessing_mismatch": 5, "label_noise": 5}
    )
    assert Counter(item.evidence_condition for item in census.bindings) == Counter(
        {"full": 15, "missing_key": 15, "noisy": 15}
    )
    assert len({item.visible_context.context_sha256 for item in census.bindings}) == 45
    assert len({item.source_projection_sha256 for item in census.bindings}) == 15


def test_context_budgets_and_condition_transformations_are_exact(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, receipt = observed
    groups: dict[str, dict[str, ClaimEvidenceBinding]] = {}
    for binding in census.bindings:
        groups.setdefault(binding.family_id, {})[binding.evidence_condition] = binding

    for conditions in groups.values():
        full = conditions["full"]
        missing = conditions["missing_key"]
        noisy = conditions["noisy"]
        full_items = {item.evidence_id: item for item in full.visible_context.items}
        missing_items = {item.evidence_id: item for item in missing.visible_context.items}
        noisy_items = {item.evidence_id: item for item in noisy.visible_context.items}
        assert set(missing_items) == set(full_items) - {"ev-key-measurement"}
        assert all(missing_items[key] == full_items[key] for key in missing_items)
        assert set(noisy_items) == set(full_items) | {"ev-secondary-observation"}
        assert all(noisy_items[key] == full_items[key] for key in full_items)

    encoded_sizes = [
        len(canonical_execution_json(item.model_payload()).encode("utf-8"))
        for item in census.bindings
    ]
    assert max(len(item.visible_context.items) for item in census.bindings) <= 32
    assert max(encoded_sizes) <= 12_000
    assert max(encoded_sizes) == receipt.maximum_context_utf8_bytes_observed


def test_visible_payload_is_outcome_blind_and_not_placeholder_data(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, _ = observed
    visible = canonical_execution_json(
        tuple(item.model_payload() for item in census.bindings)
    ).casefold()

    for forbidden in (
        "answer_key",
        "automatic_label",
        "hidden_ground_truth",
        "main_outcome",
        "sealed_outcome",
        "data_drift",
        "preprocessing_mismatch",
        "label_noise",
        "placeholder",
        "synthetic_fixture",
    ):
        assert forbidden not in visible
    assert all(not item.hidden_ground_truth_present for item in census.bindings)
    assert all(not item.evaluator_outcome_present for item in census.bindings)


def test_tamper_wrong_family_wrong_condition_and_leakage_fail_closed(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, _ = observed
    requests = ClaimCorpusRequestCensus.model_validate_json(REQUEST_PATH.read_bytes())
    binding = census.bindings[0]

    payload = census.model_dump(mode="python")
    payload["bindings"][0]["visible_context"]["items"][0]["content"] += " tampered"
    with pytest.raises(ValidationError):
        ObservedEvidenceCensus.model_validate(payload)

    wrong_family = next(
        item
        for item in requests.primary_requests
        if item.family_id != binding.family_id
        and item.evidence_condition == binding.evidence_condition
    )
    wrong_condition = next(
        item
        for item in requests.primary_requests
        if item.family_id == binding.family_id
        and item.evidence_condition != binding.evidence_condition
    )
    with pytest.raises(ClaimCorpusContractError, match="different request context"):
        validate_request_evidence_binding(wrong_family, binding)
    with pytest.raises(ClaimCorpusContractError, match="different request context"):
        validate_request_evidence_binding(wrong_condition, binding)
    with pytest.raises(ValidationError, match="evaluator-only metadata"):
        build_visible_evidence_item(
            evidence_id="unsafe",
            kind="artifact",
            title="Observed evidence",
            content="This reveals the hidden ground truth.",
            source_content_sha256="a" * 64,
        )


def test_receipt_reconciles_exact_local_tokens_costs_and_zero_outcomes(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    _, receipt = observed

    assert receipt.diagnosis_input_token_count_exact == 296_071
    assert sum(receipt.diagnosis_input_token_count_by_variant.values()) == 296_071
    assert receipt.diagnosis_model_request_count == 315
    assert receipt.input_usd_per_million_tokens == 2.0
    assert receipt.output_usd_per_million_tokens == 8.0
    assert receipt.relation_request_ceiling == 1_800
    assert receipt.diagnosis_input_cost_estimate_usd == 0.592142
    assert receipt.one_attempt_total_cost_ceiling_usd == 53.944142
    assert len(receipt.source_artifact_paths_by_projection_sha256) == 15
    assert not receipt.provider_billed_input_tokens_available
    assert not receipt.provider_calls_executed
    assert not receipt.diagnosis_outputs_generated
    assert not receipt.claims_materialized
    assert not receipt.automatic_labels_generated
    assert not receipt.human_annotations_collected
    assert not receipt.main_or_sealed_outcomes_opened


def test_preflight_clears_only_evidence_blocker_and_exposes_accounting(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, receipt = observed
    preflight = build_execution_preflight(
        ROOT,
        repository_state=RepositoryExecutionState(
            branch="main",
            head_commit="1" * 40,
            origin_main_commit="1" * 40,
            clean=True,
        ),
        credential_present=True,
        evidence_census=census,
        evidence_receipt=receipt,
    )

    assert "observed_evidence_census_pending" not in preflight.live_blockers
    assert preflight.live_blockers == ("variant_execution_authorization_pending",)
    assert preflight.exact_input_token_count_known
    assert preflight.exact_input_token_count == 296_071
    assert preflight.exact_cost_estimate_available
    assert preflight.one_attempt_total_cost_ceiling_usd == 53.944142
    assert not preflight.provider_calls_executed


def test_preflight_recomputes_and_rejects_self_consistent_false_token_receipt(
    observed: tuple[ObservedEvidenceCensus, ObservedEvidenceReceipt],
) -> None:
    census, receipt = observed
    payload = receipt.model_dump(mode="python", exclude={"receipt_sha256"})
    token_count = receipt.diagnosis_input_token_count_exact + 1
    by_variant = dict(receipt.diagnosis_input_token_count_by_variant)
    by_variant["A1"] += 1
    input_cost = round(token_count * receipt.input_usd_per_million_tokens / 1_000_000, 6)
    payload["diagnosis_input_token_count_exact"] = token_count
    payload["diagnosis_input_token_count_by_variant"] = by_variant
    payload["diagnosis_input_cost_estimate_usd"] = input_cost
    payload["one_attempt_total_cost_ceiling_usd"] = round(
        input_cost
        + receipt.diagnosis_output_cost_ceiling_usd
        + receipt.relation_input_cost_ceiling_usd
        + receipt.relation_output_cost_ceiling_usd,
        6,
    )
    forged = ObservedEvidenceReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )

    with pytest.raises(ClaimCorpusExecutionError, match="does not match"):
        build_execution_preflight(
            ROOT,
            repository_state=RepositoryExecutionState(
                branch="main",
                head_commit="1" * 40,
                origin_main_commit="1" * 40,
                clean=True,
            ),
            credential_present=True,
            evidence_census=census,
            evidence_receipt=forged,
        )
