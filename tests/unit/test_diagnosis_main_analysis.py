from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import aletheia_lab.evaluation.diagnosis_main_materialization as materialization
from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    _validate_census_contexts,
    _validate_census_families,
    _validate_census_requests,
    _validate_counterevidence_pairings,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ALL_CONDITIONS,
    CONTROLLED_VARIANTS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisError,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainClaim,
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
    DiagnosisMainObservedRecord,
    _validate_observed_contracts,
    analyse_diagnosis_main,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    _CITATION_REQUIRED_CLAIMS,
    _CITATION_REQUIRED_VARIANTS,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "configs/evaluation/diagnosis_main_analysis_plan_v3.json"


def _plan() -> DiagnosisMainAnalysisPlan:
    return DiagnosisMainAnalysisPlan.model_validate_json(PLAN_PATH.read_bytes())


def _mechanism_and_dataset(index: int) -> tuple[str, str]:
    if index <= 10:
        return "data_drift", "dataset-a" if index <= 5 else "dataset-b"
    if index <= 20:
        return "preprocessing_mismatch", "dataset-a" if index <= 15 else "dataset-b"
    return "label_noise", "dataset-a" if index <= 26 else "dataset-b"


def _family(index: int) -> DiagnosisMainFamily:
    mechanism, dataset = _mechanism_and_dataset(index)
    payload = {
        "family_id": f"family-{index:02d}",
        "mechanism": mechanism,
        "dataset_id": dataset,
        "source_unit_id": f"source-unit-{index:02d}",
        "source_record_sha256": canonical_execution_sha256(
            {"source_unit": index, "mechanism": mechanism, "dataset": dataset}
        ),
        "source_artifact_sha256": canonical_execution_sha256(
            {"source_artifact": mechanism, "dataset": dataset}
        ),
        "intervention_template_id": f"template-{mechanism}",
        "superfamily_id": f"superfamily-{dataset}-{mechanism}",
        "source_partition": "prior_registered_outcome",
    }
    return DiagnosisMainFamily.model_validate(
        {**payload, "family_sha256": canonical_execution_sha256(payload)}
    )


def _counterevidence_pairs(
    families: tuple[DiagnosisMainFamily, ...],
) -> dict[str, str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for family in families:
        groups[family.superfamily_id].append(family.family_id)
    pairs: dict[str, str] = {}
    for family_ids in groups.values():
        ordered = sorted(family_ids)
        for offset, family_id in enumerate(ordered):
            pairs[family_id] = ordered[(offset + 1) % len(ordered)]
    return pairs


def _context(
    family: DiagnosisMainFamily,
    condition: str,
    *,
    counterevidence_source: str | None,
) -> DiagnosisMainContext:
    response_modes = {
        "full": "diagnose",
        "missing_key": "abstain_or_request_evidence",
        "noisy": "diagnose_with_uncertainty",
        "counterevidence": "acknowledge_conflict_or_abstain",
    }
    payload = {
        "context_id": f"context-{family.family_id}-{condition}",
        "case_family_id": family.family_id,
        "evidence_condition": condition,
        "expected_response_mode": response_modes[condition],
        "visible_context_sha256": canonical_execution_sha256(
            {"visible": family.family_id, "condition": condition}
        ),
        "normalized_content_sha256": canonical_execution_sha256(
            {"normalized": family.family_id, "condition": condition}
        ),
        "semantic_cluster_id": f"semantic-{family.family_id}",
        "counterevidence_source_family_id": counterevidence_source,
    }
    return DiagnosisMainContext.model_validate(
        {**payload, "context_sha256": canonical_execution_sha256(payload)}
    )


def _request(context: DiagnosisMainContext, variant: str) -> DiagnosisMainExpectedRequest:
    payload = {
        "request_id": f"request-{context.context_id}-{variant}",
        "context_id": context.context_id,
        "variant": variant,
    }
    return DiagnosisMainExpectedRequest.model_validate(
        {**payload, "request_sha256": canonical_execution_sha256(payload)}
    )


def _census() -> DiagnosisMainAnalysisCensus:
    families = tuple(_family(index) for index in range(1, 33))
    pairs = _counterevidence_pairs(families)
    contexts = tuple(
        sorted(
            (
                _context(
                    family,
                    condition,
                    counterevidence_source=(
                        pairs[family.family_id] if condition == "counterevidence" else None
                    ),
                )
                for family in families
                for condition in ALL_CONDITIONS
            ),
            key=lambda item: item.context_id,
        )
    )
    requests = tuple(
        sorted(
            (_request(context, variant) for context in contexts for variant in CONTROLLED_VARIANTS),
            key=lambda item: item.request_id,
        )
    )
    payload = {
        "schema_version": "diagnosis-main-analysis-census/v2",
        "source_partition": "sealed_main",
        "family_count": 32,
        "context_count": 128,
        "request_count": 1024,
        "exact_duplicate_context_count": 0,
        "cross_family_semantic_duplicate_count": 0,
        "families": tuple(item.model_dump(mode="json") for item in families),
        "contexts": tuple(item.model_dump(mode="json") for item in contexts),
        "requests": tuple(item.model_dump(mode="json") for item in requests),
    }
    return DiagnosisMainAnalysisCensus.model_validate(
        {
            **payload,
            "families": families,
            "contexts": contexts,
            "requests": requests,
            "census_sha256": canonical_execution_sha256(payload),
        }
    )


def _claim(label: str, *, required: bool) -> DiagnosisMainClaim:
    return DiagnosisMainClaim(
        claim_id="claim-1",
        claim_type="cause_assertion",
        support_label=label,  # type: ignore[arg-type]
        citation_required=required,
        citation_present=required,
        citation_ids_valid=required,
    )


def _input(
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    *,
    omit_last: bool = False,
    fail_first_a3_full: bool = False,
    abstain_a3_full: bool = False,
    abstain_b1_counterevidence: bool = False,
) -> DiagnosisMainAnalysisInput:
    records: list[DiagnosisMainObservedRecord] = []
    failed = False
    citation_required_variants = {"A2", "A3", "CodeGraph", "FULL"}
    for request in census.requests:
        if (
            fail_first_a3_full
            and request.variant == "A3"
            and "-full-" in request.request_id
            and not failed
        ):
            records.append(
                DiagnosisMainObservedRecord(
                    request_id=request.request_id,
                    request_sha256=request.request_sha256,
                    technical_status="parse_failure",
                    output_status=None,
                    claims=(),
                )
            )
            failed = True
            continue
        should_abstain = (
            (request.variant == "A3" and "-missing_key-" in request.request_id)
            or (abstain_a3_full and request.variant == "A3" and "-full-" in request.request_id)
            or (
                abstain_b1_counterevidence
                and request.variant == "B1"
                and "-counterevidence-" in request.request_id
            )
        )
        if should_abstain:
            records.append(
                DiagnosisMainObservedRecord(
                    request_id=request.request_id,
                    request_sha256=request.request_sha256,
                    technical_status="success",
                    output_status="abstained",
                    claims=(),
                )
            )
            continue
        label = "unsupported" if request.variant == "B1" else "fully_supported"
        records.append(
            DiagnosisMainObservedRecord(
                request_id=request.request_id,
                request_sha256=request.request_sha256,
                technical_status="success",
                output_status="completed",
                claims=(
                    _claim(
                        label,
                        required=request.variant in citation_required_variants,
                    ),
                ),
            )
        )
    if omit_last:
        records.pop()
    payload = {
        "schema_version": "diagnosis-main-analysis-input/v2",
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "records": tuple(item.model_dump(mode="json") for item in records),
    }
    return DiagnosisMainAnalysisInput.model_validate(
        {
            **payload,
            "records": tuple(records),
            "input_sha256": canonical_execution_sha256(payload),
        }
    )


def test_versioned_plan_is_hash_locked_outcome_blind_and_noncausal() -> None:
    plan = _plan()

    assert plan.protected_outcomes_opened is False
    assert plan.scientific_scope == "finite_frozen_benchmark_policy_comparison"
    assert plan.causal_effect_claim_permitted is False
    assert plan.superpopulation_generalization_permitted is False
    assert plan.formal_null_hypothesis_test == "none_descriptive_registered_rule"
    assert plan.primary_variants == ("B1", "A3")
    assert plan.complete_case_primary_permitted is False
    assert plan.family_count == 32
    assert plan.context_count == 128
    assert plan.controlled_request_count == 1024
    assert plan.secondary_claim_status == "descriptive_only"
    assert plan.aggregation_order[-1] == "family_to_finite_census_equal_family_weight"


def test_materializer_citation_sets_match_frozen_response_contract() -> None:
    policy = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text()
    )["citation_policy"]
    assert set(policy["required_for_cause_and_evidence_claims"]) == _CITATION_REQUIRED_VARIANTS
    assert set(policy["required_claim_types"]) == _CITATION_REQUIRED_CLAIMS


@pytest.mark.parametrize("variant", CONTROLLED_VARIANTS)
@pytest.mark.parametrize(
    "claim_type",
    (
        "cause_assertion",
        "evidence_statement",
        "uncertainty_statement",
        "recommended_action",
        "other",
    ),
)
def test_analysis_citation_check_matches_frozen_claim_type_policy(
    variant: str, claim_type: str
) -> None:
    policy = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text()
    )["citation_policy"]
    assert claim_type in (*policy["required_claim_types"], *policy["optional_claim_types"])
    citation_required = (
        variant in policy["required_for_cause_and_evidence_claims"]
        and claim_type in policy["required_claim_types"]
    )
    request = _request(_context(_family(1), "full", counterevidence_source=None), variant)
    record = DiagnosisMainObservedRecord(
        request_id=request.request_id,
        request_sha256=request.request_sha256,
        technical_status="success",
        output_status="completed",
        claims=(
            DiagnosisMainClaim(
                claim_id="claim-1",
                claim_type=claim_type,  # type: ignore[arg-type]
                support_label="fully_supported",
                citation_required=citation_required,
                citation_present=citation_required,
                citation_ids_valid=citation_required,
            ),
        ),
    )
    _validate_observed_contracts({request.request_id: request}, {record.request_id: record})


@pytest.mark.parametrize(
    ("variant", "claim_type", "citation_required"),
    (
        ("A3", "cause_assertion", False),
        ("A3", "uncertainty_statement", True),
        ("B1", "cause_assertion", True),
    ),
)
def test_analysis_citation_check_rejects_policy_mismatch(
    variant: str, claim_type: str, citation_required: bool
) -> None:
    request = _request(_context(_family(1), "full", counterevidence_source=None), variant)
    record = DiagnosisMainObservedRecord(
        request_id=request.request_id,
        request_sha256=request.request_sha256,
        technical_status="success",
        output_status="completed",
        claims=(
            DiagnosisMainClaim(
                claim_id="claim-1",
                claim_type=claim_type,  # type: ignore[arg-type]
                support_label="fully_supported",
                citation_required=citation_required,
                citation_present=citation_required,
                citation_ids_valid=citation_required,
            ),
        ),
    )
    with pytest.raises(DiagnosisMainAnalysisError, match="citation policy"):
        _validate_observed_contracts({request.request_id: request}, {record.request_id: record})


def test_finite_census_primary_keeps_claim_to_family_aggregation() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(plan, census, _input(plan, census))

    assert report.status == "valid_registered_analysis"
    assert report.causal_effect_claimed is False
    assert report.formal_p_value_reported is False
    assert report.primary_effect is not None
    assert report.primary_effect.family_count == 32
    assert report.primary_effect.estimate == 1.0
    assert report.primary_effect.lower == 1.0
    assert report.primary_effect.upper == 1.0
    assert report.primary_disposition == "benchmark_local_support"
    assert report.stability_diagnostics is not None
    assert report.stability_diagnostics.superfamily_count == 6
    assert report.raw_claim_count == 992


def test_terminal_parse_failure_remains_in_denominator_as_worst_case() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, fail_first_a3_full=True),
    )

    assert report.status == "valid_registered_analysis"
    assert report.technical_status_counts["parse_failure"] == 1
    assert report.primary_effect is not None
    assert report.primary_effect.estimate < 1.0
    assert report.primary_effect.estimate > 0.98


def test_condition_aware_abstention_separates_safety_from_claim_harm() -> None:
    plan = _plan()
    census = _census()
    baseline = analyse_diagnosis_main(plan, census, _input(plan, census))
    false_full = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, abstain_a3_full=True),
    )
    safe_counter = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, abstain_b1_counterevidence=True),
    )

    assert baseline.primary_effect is not None
    assert baseline.primary_effect.estimate == 1.0
    assert false_full.primary_effect is not None
    assert false_full.primary_effect.estimate == pytest.approx(2.0 / 3.0)
    assert safe_counter.counterevidence_effect is not None
    assert safe_counter.counterevidence_effect.estimate == 0.0
    assert safe_counter.abstention_rates is not None
    assert safe_counter.abstention_rates["counterevidence.B1"] == 1.0


def test_missing_terminal_invalidates_instead_of_complete_case_rescue() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(
        plan,
        census,
        _input(plan, census, omit_last=True),
    )

    assert report.status == "invalid_registered_execution"
    assert report.primary_effect is None
    assert report.primary_disposition == "invalid_result"
    assert len(report.missing_request_ids) == 1


def test_census_rejects_an_incomplete_eight_path_matrix() -> None:
    payload = _census().model_dump(mode="json")
    payload["requests"].pop()
    payload["census_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "census_sha256"}
    )

    with pytest.raises(ValueError, match="request_count does not match"):
        DiagnosisMainAnalysisCensus.model_validate_json(json.dumps(payload))


def test_census_rejects_cross_family_semantic_duplicates() -> None:
    payload = _census().model_dump(mode="json")
    first_cluster = payload["contexts"][0]["semantic_cluster_id"]
    target = next(
        context
        for context in payload["contexts"]
        if context["case_family_id"] != payload["contexts"][0]["case_family_id"]
    )
    target["semantic_cluster_id"] = first_cluster
    target["context_sha256"] = canonical_execution_sha256(
        {key: value for key, value in target.items() if key != "context_sha256"}
    )
    payload["census_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "census_sha256"}
    )

    with pytest.raises(ValueError, match="semantic duplicate clusters"):
        DiagnosisMainAnalysisCensus.model_validate_json(json.dumps(payload))


def test_frozen_plan_and_item_identities_reject_tampering() -> None:
    plan = _plan()
    for update, message in (
        ({"primary_variants": ("A3", "B1")}, "ordered B1 minus A3"),
        ({"primary_conditions": ("noisy", "full", "missing_key")}, "condition"),
        ({"harmful_labels": ("unsupported", "contradicted")}, "harmful"),
        ({"partial_support_primary_weight": 0.5}, "numeric contract"),
        ({"plan_sha256": "0" * 64}, "identity"),
    ):
        with pytest.raises(ValueError, match=message):
            plan.model_copy(update=update)._identity_and_order_reconcile()

    family = _family(1)
    for update, message in (
        ({"family_id": "family-01 "}, "trimmed"),
        ({"family_sha256": "0" * 64}, "identity"),
    ):
        with pytest.raises(ValueError, match=message):
            family.model_copy(update=update)._identity_reconciles()

    context = _context(family, "full", counterevidence_source=None)
    for update, message in (
        ({"context_id": " context-1"}, "trimmed"),
        ({"expected_response_mode": "abstain_or_request_evidence"}, "response mode"),
        ({"counterevidence_source_family_id": "family-02"}, "only counterevidence"),
        ({"context_sha256": "0" * 64}, "identity"),
    ):
        with pytest.raises(ValueError, match=message):
            context.model_copy(update=update)._identity_reconciles()
    with pytest.raises(ValueError, match="different registered family"):
        context.model_copy(
            update={
                "evidence_condition": "counterevidence",
                "expected_response_mode": "acknowledge_conflict_or_abstain",
                "counterevidence_source_family_id": family.family_id,
            }
        )._identity_reconciles()

    request = _request(context, "A1")
    with pytest.raises(ValueError, match="identity"):
        request.model_copy(update={"request_sha256": "0" * 64})._request_identity_reconciles()
    with pytest.raises(ValueError, match="trimmed"):
        request.model_copy(
            update={
                "request_id": " request-1",
                "request_sha256": canonical_execution_sha256(
                    {"request_id": " request-1", "context_id": context.context_id, "variant": "A1"}
                ),
            }
        )._request_identity_reconciles()


def test_frozen_census_rejects_balance_pairing_and_request_matrix_drift() -> None:
    census = _census()
    families = census.families
    contexts = census.contexts
    requests = census.requests
    family_ids = tuple(item.family_id for item in families)
    context_ids = tuple(item.context_id for item in contexts)

    family_failures = (
        (tuple(reversed(families)), "sorted"),
        (families[:-1], "family_count"),
        (
            (families[0].model_copy(update={"mechanism": "label_noise"}), *families[1:]),
            "allocation",
        ),
        (tuple(item.model_copy(update={"dataset_id": "dataset-a"}) for item in families), "both"),
        (tuple(item.model_copy(update={"superfamily_id": "same"}) for item in families), "six"),
    )
    for items, message in family_failures:
        with pytest.raises(ValueError, match=message):
            _validate_census_families(items, 32)

    first_counter = next(item for item in contexts if item.evidence_condition == "counterevidence")
    with pytest.raises(ValueError, match="unknown family"):
        _validate_counterevidence_pairings(
            (first_counter.model_copy(update={"counterevidence_source_family_id": "unknown"}),),
            families,
        )
    target = next(item for item in families if item.family_id == first_counter.case_family_id)
    foreign = next(
        item
        for item in families
        if item.dataset_id != target.dataset_id or item.mechanism != target.mechanism
    )
    with pytest.raises(ValueError, match="within dataset and mechanism"):
        _validate_counterevidence_pairings(
            (
                first_counter.model_copy(
                    update={"counterevidence_source_family_id": foreign.family_id}
                ),
            ),
            families,
        )

    context_failures = (
        (tuple(reversed(contexts)), "sorted"),
        (contexts[:-1], "context_count"),
        (
            (contexts[0].model_copy(update={"case_family_id": "unknown"}), *contexts[1:]),
            "unknown family",
        ),
        (
            (contexts[0].model_copy(update={"evidence_condition": "noisy"}), *contexts[1:]),
            "four evidence siblings",
        ),
        (
            (
                contexts[0].model_copy(
                    update={"visible_context_sha256": contexts[1].visible_context_sha256}
                ),
                *contexts[1:],
            ),
            "duplicate contexts",
        ),
    )
    for items, message in context_failures:
        with pytest.raises(ValueError, match=message):
            _validate_census_contexts(items, families, family_ids, 128)

    request_failures = (
        (tuple(reversed(requests)), "sorted"),
        ((requests[0], requests[0], *requests[2:]), "unique"),
        (requests[:-1], "request_count"),
        (
            (requests[0].model_copy(update={"context_id": "unknown"}), *requests[1:]),
            "unknown context",
        ),
        (
            (requests[0].model_copy(update={"variant": requests[1].variant}), *requests[1:]),
            "matrix",
        ),
    )
    for items, message in request_failures:
        with pytest.raises(ValueError, match=message):
            _validate_census_requests(items, context_ids, 1024)

    with pytest.raises(ValueError, match="census identity"):
        census.model_copy(update={"census_sha256": "0" * 64})._census_reconciles()


def test_observed_record_and_input_shapes_fail_closed() -> None:
    request = _request(_context(_family(1), "full", counterevidence_source=None), "A1")
    claim = _claim("fully_supported", required=False)
    with pytest.raises(ValueError, match="trimmed"):
        claim.model_copy(update={"claim_id": " claim-1"})._claim_fields_reconcile()
    with pytest.raises(ValueError, match="absent citations"):
        claim.model_copy(
            update={"citation_present": False, "citation_ids_valid": True}
        )._claim_fields_reconcile()

    record = DiagnosisMainObservedRecord(
        request_id=request.request_id,
        request_sha256=request.request_sha256,
        technical_status="success",
        output_status="completed",
        claims=(claim,),
    )
    for update, message in (
        ({"request_id": " "}, "trimmed"),
        ({"output_status": None}, "output status"),
        ({"technical_status": "parse_failure"}, "technical failures"),
        ({"output_status": "abstained"}, "abstained outputs"),
        ({"claims": (claim, claim)}, "unique"),
    ):
        with pytest.raises(ValueError, match=message):
            record.model_copy(update=update)._terminal_shape_reconciles()

    plan = _plan()
    census = _census()
    analysis_input = _input(plan, census)
    with pytest.raises(ValueError, match="sorted"):
        analysis_input.model_copy(
            update={"records": tuple(reversed(analysis_input.records))}
        )._input_reconciles()
    with pytest.raises(ValueError, match="duplicate"):
        analysis_input.model_copy(update={"records": (record, record)})._input_reconciles()
    with pytest.raises(ValueError, match="identity"):
        analysis_input.model_copy(update={"input_sha256": "0" * 64})._input_reconciles()


def test_analysis_report_rejects_invalid_result_overclaim_and_hash_drift() -> None:
    plan = _plan()
    census = _census()
    report = analyse_diagnosis_main(plan, census, _input(plan, census))
    assert report.status == "valid_registered_analysis"
    with pytest.raises(ValueError, match="request reconciliation"):
        report.model_copy(update={"status": "invalid_registered_execution"})._report_reconciles()
    invalid = report.model_copy(
        update={
            "status": "invalid_registered_execution",
            "missing_request_ids": ("missing-request",),
        }
    )
    with pytest.raises(ValueError, match="cannot publish"):
        invalid._report_reconciles()
    empty_estimates = {
        "primary_effect": None,
        "partial_support_sensitivity_effect": None,
        "stability_diagnostics": None,
        "mechanism_primary_effects": None,
        "condition_primary_effects": None,
        "counterevidence_effect": None,
        "emitted_claim_summaries": None,
        "abstention_rates": None,
        "missing_key_causal_overclaim": None,
        "full_evidence_false_abstention_a3": None,
    }
    with pytest.raises(ValueError, match="empty invalid disposition"):
        invalid.model_copy(update=empty_estimates)._report_reconciles()
    with pytest.raises(ValueError, match="report identity"):
        report.model_copy(update={"report_sha256": "0" * 64})._report_reconciles()


def test_materialization_distinguishes_technical_failure_from_model_output() -> None:
    def terminal(status: str, gateway_status: str | None = None) -> SimpleNamespace:
        receipts = (
            () if gateway_status is None else (SimpleNamespace(gateway_status=gateway_status),)
        )
        return SimpleNamespace(status=status, turn_receipts=receipts)

    assert materialization._technical_status(terminal("completed")) == "success"
    assert materialization._technical_status(terminal("deterministic_completed")) == "success"
    assert materialization._technical_status(terminal("semantic_failure")) == "parse_failure"
    assert (
        materialization._technical_status(terminal("technical_failure", "timed_out"))
        == "provider_failure"
    )
    assert (
        materialization._technical_status(terminal("technical_failure", "parse_failed"))
        == "parse_failure"
    )
    assert materialization._technical_status(terminal("technical_failure")) == "unresolved"


def test_materialization_requires_canonical_files_and_exact_store_membership(
    tmp_path: Path,
) -> None:
    plan = _plan()
    path = tmp_path / "plan.json"
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="unavailable"):
        materialization._read_canonical_model(path, DiagnosisMainAnalysisPlan)
    path.write_bytes(materialization._model_bytes(plan))
    assert materialization._read_canonical_model(path, DiagnosisMainAnalysisPlan) == plan
    path.write_text(json.dumps(plan.model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="canonical"):
        materialization._read_canonical_model(path, DiagnosisMainAnalysisPlan)
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="invalid"):
        materialization._read_canonical_model(path, DiagnosisMainAnalysisPlan)

    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="unavailable"):
        materialization._read_json(tmp_path / "missing.json")
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="object"):
        materialization._read_json(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="invalid"):
        materialization._read_json(path)

    store = tmp_path / "store"
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="real directory"):
        materialization._validate_store_root(store, {"request-1"})
    store.mkdir()
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="membership"):
        materialization._validate_store_root(store, {"request-1"})
    (store / "batch-binding.json").touch()
    (store / "request-1").mkdir()
    assert materialization._validate_store_root(store, {"request-1"}) == store.resolve()
    (store / "untracked").touch()
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="membership"):
        materialization._validate_store_root(store, {"request-1"})


def test_materialization_rejects_incomplete_turns_and_wrong_terminal_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="unavailable"):
        materialization.load_main_analysis_plan(tmp_path)
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="unavailable"):
        materialization._load_batch_binding(tmp_path)

    request_root = tmp_path / "request-1"
    terminal = SimpleNamespace(
        logical_request_id="request-1",
        logical_request_sha256="a" * 64,
        completed_provider_turn_count=1,
        turn_receipts=(),
    )
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="logical request"):
        materialization._load_terminal_bundle(tmp_path, "request-1", "a" * 64)
    request_root.mkdir()
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="turn directory"):
        materialization._load_turn(request_root, terminal, 1)
    turn_root = request_root / "turn-01"
    turn_root.mkdir()
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="membership"):
        materialization._load_turn(request_root, terminal, 1)

    (request_root / "terminal.json").touch()
    monkeypatch.setattr(materialization, "_read_canonical_model", lambda *_args: terminal)
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="another request"):
        materialization._load_terminal_bundle(tmp_path, "request-1", "b" * 64)
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="membership"):
        materialization._load_terminal_bundle(tmp_path, "request-1", "a" * 64)


def test_completed_materialization_does_not_invent_provider_output() -> None:
    terminal = SimpleNamespace(
        status="deterministic_completed",
        logical_request_id="request-1",
        logical_request_sha256="a" * 64,
        terminal_sha256="b" * 64,
    )
    record = materialization._prepare_completed_record(terminal, ())
    assert record.technical_status == "success"
    assert record.output_status == "completed"
    assert record.claims == ()

    terminal.status = "completed"
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="no final turn"):
        materialization._prepare_completed_record(terminal, ())
    terminal.status = "technical_failure"
    terminal.turn_receipts = ()
    failure = materialization._prepare_record(terminal, ())
    assert failure.technical_status == "unresolved"
    assert failure.output_status is None
    assert failure.claims == ()


def test_offline_relation_results_cannot_masquerade_as_main_analysis() -> None:
    preparation = SimpleNamespace(
        execution_mode="offline_rehearsal",
        preparation_sha256="a" * 64,
        records=(),
    )
    relations = SimpleNamespace(
        execution_mode="offline_rehearsal",
        preparation_sha256="a" * 64,
        results=(),
    )
    with pytest.raises(
        materialization.DiagnosisMainMaterializationError, match="offline rehearsal"
    ):
        materialization.materialize_main_analysis_input(
            preparation=preparation, relation_results=relations
        )
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="another"):
        materialization.materialize_main_analysis_input(
            preparation=preparation,
            relation_results=SimpleNamespace(
                execution_mode="offline_rehearsal",
                preparation_sha256="b" * 64,
            ),
            allow_offline_rehearsal=True,
        )
    with pytest.raises(
        materialization.DiagnosisMainMaterializationError, match="offline rehearsal"
    ):
        materialization.build_offline_relation_results(
            SimpleNamespace(execution_mode="authorized_execution")
        )
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="offline"):
        materialization.rehearse_main_materialization(
            plan=_plan(),
            census=_census(),
            preparation=SimpleNamespace(execution_mode="authorized_execution"),
        )

    preparation.execution_mode = "authorized_execution"
    relations.execution_mode = "authorized_execution"
    preparation.records = (
        SimpleNamespace(
            claims=(
                SimpleNamespace(
                    relation_request=SimpleNamespace(assignment_request_sha256="c" * 64)
                ),
            )
        ),
    )
    with pytest.raises(materialization.DiagnosisMainMaterializationError, match="exactly cover"):
        materialization.materialize_main_analysis_input(
            preparation=preparation, relation_results=relations
        )
