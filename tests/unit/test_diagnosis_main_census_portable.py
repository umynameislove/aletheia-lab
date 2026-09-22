from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.diagnosis import main_pipeline
from aletheia_lab.diagnosis._main_pipeline_budget import SharedProviderBudget
from aletheia_lab.diagnosis._main_pipeline_relations import DeterministicRelationAdapter
from aletheia_lab.diagnosis.main_execution import (
    DeterministicRehearsalClock,
    DiagnosisMainOfflineAdapter,
    rehearse_main_execution,
)
from aletheia_lab.diagnosis.main_pipeline import (
    DiagnosisMainPipelineError,
    _rehearse_main_pipeline_components,
)
from aletheia_lab.diagnosis.main_runtime import (
    MainRuntimeError,
    MainRuntimeStore,
    load_main_runtime_inputs,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusSources,
    build_diagnosis_main_census,
    serialize_census_artifact,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainMaterializationError,
    DiagnosisMainRelationResult,
    DiagnosisMainRelationResults,
    DiagnosisMainScoringContract,
    build_offline_relation_results,
    load_main_analysis_plan,
    load_main_scoring_contract,
    materialize_main_analysis_input,
    prepare_main_scoring,
    rehearse_main_materialization,
)
from aletheia_lab.evaluation.execution_contracts import (
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    GatewayExecutionResult,
    GatewayRequest,
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayPolicy,
    ProviderBinding,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _p2r_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for dataset_index, dataset_id in enumerate(("portable-a", "portable-b")):
        for mechanism_index, mechanism in enumerate(("data_drift", "preprocessing_bug")):
            for seed in range(5):
                identity = {
                    "dataset_id": dataset_id,
                    "mechanism": mechanism,
                    "seed": seed,
                }
                offset = dataset_index * 0.01 + mechanism_index * 0.02 + seed * 0.001
                records.append(
                    {
                        **identity,
                        "measurement_sha256": canonical_execution_sha256(identity),
                        "target_feature": f"feature-{dataset_index}-{mechanism_index}-{seed}",
                        "achieved_manipulation_magnitude": 0.1 + offset,
                        "manipulated_accuracy": 0.7 - offset,
                        "clean_accuracy": 0.8 + dataset_index * 0.01,
                        "protocol_sha256": canonical_execution_sha256({"protocol": mechanism}),
                        "model_sha256": canonical_execution_sha256({"model": dataset_id}),
                        "split_membership_sha256": canonical_execution_sha256(
                            {"split": dataset_id, "seed": seed}
                        ),
                        "nuisance_accuracy": 0.6 + offset,
                        "nuisance_effect_magnitude": 0.05 + offset,
                    }
                )
    return records


def _label_noise_attempt(dataset_id: str) -> dict[str, object]:
    summaries = []
    for direction_index, direction in enumerate(("yes_to_no", "no_to_yes")):
        for rate in (0.1, 0.2, 0.3):
            summaries.append(
                {
                    "direction": direction,
                    "conditional_rate": rate,
                    "replicate_count": 5,
                    "mean_relative_net_effect": (rate * (-1.0 if direction_index == 0 else 1.0)),
                    "sensitivity_only": True,
                    "can_rescue_primary": False,
                }
            )
    return {
        "outcome": {
            "dataset_id": dataset_id,
            "sensitivity_summaries": summaries,
        }
    }


def _portable_sources(tmp_path: Path) -> DiagnosisMainCensusSources:
    paths = {
        "p2r_confirmatory_measurements": tmp_path / "measurements.json",
        "p2_v3_3_label_noise_primary": tmp_path / "primary-attempt.json",
        "p2_v3_3_label_noise_replication": tmp_path / "replication-attempt.json",
        "p2_v3_3_label_noise_protocol": tmp_path / "protocol.json",
    }
    _write_json(paths["p2r_confirmatory_measurements"], _p2r_records())
    _write_json(paths["p2_v3_3_label_noise_primary"], _label_noise_attempt("portable-a"))
    _write_json(
        paths["p2_v3_3_label_noise_replication"],
        _label_noise_attempt("portable-b"),
    )
    _write_json(
        paths["p2_v3_3_label_noise_protocol"],
        {"schema_version": "portable-test-protocol/v1"},
    )

    contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_census_source_contract.json").read_text(
            encoding="utf-8"
        )
    )
    for artifact in contract["source_artifacts"]:
        artifact["file_sha256"] = content_sha256(paths[artifact["source_id"]].read_bytes())
    contract["source_contract_sha256"] = canonical_execution_sha256(
        {key: value for key, value in contract.items() if key != "source_contract_sha256"}
    )
    contract_path = tmp_path / "source-contract.json"
    _write_json(contract_path, contract)
    return DiagnosisMainCensusSources(
        source_contract=contract_path,
        p2r_measurements=paths["p2r_confirmatory_measurements"],
        label_noise_primary=paths["p2_v3_3_label_noise_primary"],
        label_noise_replication=paths["p2_v3_3_label_noise_replication"],
        label_noise_protocol=paths["p2_v3_3_label_noise_protocol"],
    )


def test_census_build_is_portable_without_private_preserved_artifacts(
    tmp_path: Path,
) -> None:
    sources = _portable_sources(tmp_path)

    first_packet, first_seal, first_qwen = build_diagnosis_main_census(sources)
    second_packet, second_seal, second_qwen = build_diagnosis_main_census(sources)

    assert serialize_census_artifact(first_packet) == serialize_census_artifact(second_packet)
    assert first_seal == second_seal
    assert first_qwen == second_qwen
    assert first_seal.family_count == 32
    assert first_seal.context_count == 128
    assert first_seal.controlled_request_count == 1024
    assert first_seal.dataset_count == 2
    assert first_seal.superfamily_count == 6
    assert first_seal.mechanism_counts == {
        "data_drift": 10,
        "label_noise": 12,
        "preprocessing_mismatch": 10,
    }
    assert len(first_packet.visible_contexts) == 128
    assert len(first_qwen.family_ids) == 12
    assert len(first_qwen.request_ids) == 72
    assert first_seal.private_packet_byte_sha256 == content_sha256(
        serialize_census_artifact(first_packet)
    )


def test_offline_batch_rehearsal_is_canonical_resumable_and_store_bound(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    packet, _, _ = build_diagnosis_main_census(_portable_sources(source_root))
    contract, fairness_freeze, response_contract = load_main_runtime_inputs(ROOT)
    contract_payload = contract.model_dump(mode="python", exclude={"runtime_contract_sha256"})
    contract_payload["analysis_census_sha256"] = packet.analysis_census.census_sha256
    contract = contract.__class__.model_validate(
        {
            **contract_payload,
            "runtime_contract_sha256": canonical_execution_sha256(contract_payload),
        }
    )
    selected_by_variant = {}
    for request in packet.analysis_census.requests:
        selected_by_variant.setdefault(request.variant, request.request_id)
    request_ids = tuple(selected_by_variant.values())
    store = MainRuntimeStore(tmp_path / "store")

    first = rehearse_main_execution(
        packet=packet,
        contract=contract,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        store=store,
        request_ids=request_ids,
    )
    persisted = {
        path.relative_to(store.root): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }
    second = rehearse_main_execution(
        packet=packet,
        contract=contract,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        store=store,
        request_ids=request_ids,
    )

    assert first == second
    assert first.status == "offline_rehearsal_complete"
    assert first.logical_request_count == 8
    assert first.provider_backed_logical_request_count == 7
    assert first.deterministic_logical_request_count == 1
    assert first.expected_provider_turn_count == 11
    assert first.completed_provider_turn_count == 11
    assert first.terminal_status_counts == {"completed": 7, "deterministic_completed": 1}
    assert persisted == {
        path.relative_to(store.root): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }

    different_selection = tuple(
        request.request_id for request in packet.analysis_census.requests[:8]
    )
    with pytest.raises(MainRuntimeError, match="bound to another execution batch"):
        rehearse_main_execution(
            packet=packet,
            contract=contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            store=store,
            request_ids=different_selection,
        )

    tampered_response = json.loads(json.dumps(response_contract))
    tampered_response["prompt_contracts"]["A1"] += " Mutated after freeze."
    with pytest.raises(MainRuntimeError, match="response contract differs"):
        rehearse_main_execution(
            packet=packet,
            contract=contract,
            response_contract=tampered_response,
            fairness_freeze=fairness_freeze,
            store=MainRuntimeStore(tmp_path / "tampered-store"),
            request_ids=request_ids,
        )

    unbound_store = MainRuntimeStore(tmp_path / "unbound-store")
    (unbound_store.root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MainRuntimeError, match="has no immutable batch binding"):
        rehearse_main_execution(
            packet=packet,
            contract=contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            store=unbound_store,
            request_ids=request_ids,
        )

    (store.root / request_ids[0] / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MainRuntimeError, match="contains an unexpected entry"):
        rehearse_main_execution(
            packet=packet,
            contract=contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            store=store,
            request_ids=request_ids,
        )


def test_full_materialization_rehearsal_is_blind_complete_and_fail_closed(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    packet, _, _ = build_diagnosis_main_census(_portable_sources(source_root))
    runtime_contract, fairness_freeze, response_contract = load_main_runtime_inputs(ROOT)
    scoring_contract = load_main_scoring_contract(
        ROOT,
        runtime_contract=runtime_contract,
        response_contract=response_contract,
    )

    runtime_payload = runtime_contract.model_dump(
        mode="python", exclude={"runtime_contract_sha256"}
    )
    runtime_payload["analysis_census_sha256"] = packet.analysis_census.census_sha256
    runtime_contract = runtime_contract.__class__.model_validate(
        {
            **runtime_payload,
            "runtime_contract_sha256": canonical_execution_sha256(runtime_payload),
        }
    )
    scoring_payload = scoring_contract.model_dump(mode="python", exclude={"contract_sha256"})
    scoring_payload["runtime_contract_sha256"] = runtime_contract.runtime_contract_sha256
    scoring_contract = DiagnosisMainScoringContract.model_validate(
        {
            **scoring_payload,
            "contract_sha256": canonical_execution_sha256(scoring_payload),
        }
    )

    store = MainRuntimeStore(tmp_path / "full-store")
    batch_result = rehearse_main_execution(
        packet=packet,
        contract=runtime_contract,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        store=store,
    )
    preparation = prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime_contract,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        scoring_contract=scoring_contract,
        batch_result=batch_result,
        store_root=store.root,
    )
    first_request = next(
        claim.relation_request for record in preparation.records for claim in record.claims
    )
    provider_payload = first_request.provider_payload()

    assert preparation.record_count == 1024
    assert preparation.technical_status_counts == {"success": 1024}
    assert preparation.emitted_claim_count == 896
    assert preparation.relation_request_count == 896
    assert sum(not record.claims for record in preparation.records) == 128
    assert set(provider_payload) == {"claim_text", "claim_type", "visible_evidence"}
    assert not {
        "mechanism",
        "evidence_condition",
        "variant",
        "hidden_ground_truth",
        "human_judgment",
        "main_outcome",
    }.intersection(provider_payload)

    relation_results = build_offline_relation_results(preparation)
    invalid_authorized_identity = {
        **relation_results.model_dump(mode="python", exclude={"results_sha256"}),
        "execution_mode": "authorized_execution",
    }
    with pytest.raises(ValueError, match="result census or identity changed"):
        DiagnosisMainRelationResults.model_validate(
            {
                **invalid_authorized_identity,
                "results": relation_results.results,
                "results_sha256": canonical_execution_sha256(invalid_authorized_identity),
            }
        )
    with pytest.raises(
        DiagnosisMainMaterializationError,
        match="cannot be published",
    ):
        materialize_main_analysis_input(
            preparation=preparation,
            relation_results=relation_results,
        )
    analysis_input = materialize_main_analysis_input(
        preparation=preparation,
        relation_results=relation_results,
        allow_offline_rehearsal=True,
    )
    claims = tuple(claim for record in analysis_input.records for claim in record.claims)
    assert len(analysis_input.records) == 1024
    assert len(claims) == 896
    assert {claim.support_label for claim in claims} == {"fully_supported"}

    receipt = rehearse_main_materialization(
        plan=load_main_analysis_plan(ROOT),
        census=packet.analysis_census,
        preparation=preparation,
    )
    repeated = rehearse_main_materialization(
        plan=load_main_analysis_plan(ROOT),
        census=packet.analysis_census,
        preparation=preparation,
    )
    assert receipt == repeated
    assert receipt.status == "offline_materialization_rehearsal_pass"
    assert receipt.provider_calls_executed is False
    assert receipt.registered_attempts_consumed == 0
    assert receipt.analysis_contract_accepted is True
    assert receipt.support_label_counts == {"fully_supported": 896}

    failed_id = relation_results.results[0].assignment_request_sha256
    failed_result = DiagnosisMainRelationResult(
        assignment_request_sha256=failed_id,
        terminal_status="technical_failure",
        response=None,
        issue_code="synthetic_transport_failure",
    )
    replaced = tuple(
        failed_result if item.assignment_request_sha256 == failed_id else item
        for item in relation_results.results
    )
    failed_identity = {
        **relation_results.model_dump(mode="python", exclude={"results_sha256"}),
        "results": tuple(item.model_dump(mode="json") for item in replaced),
    }
    failed_results = DiagnosisMainRelationResults.model_validate(
        {
            **failed_identity,
            "results": replaced,
            "results_sha256": canonical_execution_sha256(failed_identity),
        }
    )
    failed_input = materialize_main_analysis_input(
        preparation=preparation,
        relation_results=failed_results,
        allow_offline_rehearsal=True,
    )
    source_record = next(
        record
        for record in preparation.records
        if any(
            claim.relation_request.assignment_request_sha256 == failed_id for claim in record.claims
        )
    )
    failed_record = next(
        record for record in failed_input.records if record.request_id == source_record.request_id
    )
    assert len(failed_input.records) == 1024
    assert failed_record.technical_status == "unresolved"
    assert failed_record.output_status is None
    assert failed_record.claims == ()
    missing = relation_results.results[1:]
    missing_identity = {
        **relation_results.model_dump(mode="python", exclude={"results_sha256"}),
        "results": tuple(item.model_dump(mode="json") for item in missing),
    }
    missing_results = DiagnosisMainRelationResults.model_validate(
        {
            **missing_identity,
            "results": missing,
            "results_sha256": canonical_execution_sha256(missing_identity),
        }
    )
    with pytest.raises(DiagnosisMainMaterializationError, match="exactly cover"):
        materialize_main_analysis_input(
            preparation=preparation,
            relation_results=missing_results,
            allow_offline_rehearsal=True,
        )

    (store.root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DiagnosisMainMaterializationError, match="membership differs"):
        prepare_main_scoring(
            packet=packet,
            runtime_contract=runtime_contract,
            response_contract=response_contract,
            fairness_freeze=fairness_freeze,
            scoring_contract=scoring_contract,
            batch_result=batch_result,
            store_root=store.root,
        )


def test_offline_main_pipeline_is_end_to_end_resumable_and_network_incapable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "pipeline-sources"
    source_root.mkdir()
    packet, _, _ = build_diagnosis_main_census(_portable_sources(source_root))
    runtime, fairness, response = load_main_runtime_inputs(ROOT)
    frozen_runtime = runtime
    runtime_payload = runtime.model_dump(mode="python", exclude={"runtime_contract_sha256"})
    runtime_payload["analysis_census_sha256"] = packet.analysis_census.census_sha256
    runtime = runtime.__class__.model_validate(
        {
            **runtime_payload,
            "runtime_contract_sha256": canonical_execution_sha256(runtime_payload),
        }
    )
    scoring = load_main_scoring_contract(
        ROOT,
        runtime_contract=frozen_runtime,
        response_contract=response,
    )
    scoring_payload = scoring.model_dump(mode="python", exclude={"contract_sha256"})
    scoring_payload["runtime_contract_sha256"] = runtime.runtime_contract_sha256
    scoring = DiagnosisMainScoringContract.model_validate(
        {
            **scoring_payload,
            "contract_sha256": canonical_execution_sha256(scoring_payload),
        }
    )
    workspace = tmp_path / "pipeline-rehearsal"
    arguments = {
        "root": ROOT,
        "packet": packet,
        "workspace": workspace,
        "source_commit_ref": "1" * 40,
        "runtime": runtime,
        "fairness": fairness,
        "response": response,
        "scoring": scoring,
        "plan": load_main_analysis_plan(ROOT),
    }

    first = _rehearse_main_pipeline_components(**arguments)
    persisted = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    second = _rehearse_main_pipeline_components(**arguments)

    assert first == second
    assert first.status == "offline_end_to_end_preflight_pass"
    assert first.scientific_result_eligible is False
    assert first.provider_calls_executed is False
    assert first.registered_main_attempts_consumed == 0
    assert first.registered_relation_attempts_consumed == 0
    assert first.logical_request_count == 1024
    assert first.expected_provider_turn_count == 1408
    assert first.completed_provider_turn_count == 1408
    assert first.relation_request_count == 896
    assert first.provider_input_fields == (
        "claim_text",
        "claim_type",
        "visible_evidence",
    )
    assert persisted == {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }

    monkeypatch.setattr(
        main_pipeline, "load_main_runtime_inputs", lambda _root: (runtime, fairness, response)
    )
    monkeypatch.setattr(main_pipeline, "load_main_scoring_contract", lambda *_a, **_k: scoring)
    monkeypatch.setattr(main_pipeline, "SystemMonotonicClock", DeterministicRehearsalClock)
    monkeypatch.setattr(main_pipeline, "PacedProviderAdapter", lambda delegate, **_k: delegate)
    state = RepositoryExecutionState(
        branch="main",
        head_commit="1" * 40,
        origin_main_commit="1" * 40,
        clean=True,
    )
    run_dir = tmp_path / "synthetic-authorized-run"
    authorization_args = dict(
        root=ROOT,
        packet=packet,
        preflight=first,
        repository_state=state,
        run_dir=run_dir,
        authorized_at="2026-09-22T00:00:00Z",
        operator_cost_ceiling_usd=100.0,
        confirmed_preflight_sha256=first.preflight_sha256,
    )
    with pytest.raises(DiagnosisMainPipelineError, match="clean synchronized main"):
        main_pipeline.build_pipeline_authorization(
            **{**authorization_args, "repository_state": state.model_copy(update={"clean": False})}
        )
    authority = main_pipeline.build_pipeline_authorization(**authorization_args)
    execution_args = dict(
        root=ROOT,
        packet=packet,
        preflight=first,
        authorization=authority,
        repository_state=state,
        run_dir=run_dir,
        confirmed_authorization_sha256=authority.authorization_sha256,
    )

    def unavailable_client(**_kwargs: object) -> None:
        raise ValueError("synthetic client unavailable")

    monkeypatch.setattr(OpenAIChatCompletionsGatewayAdapter, "from_environment", unavailable_client)
    with pytest.raises(DiagnosisMainPipelineError, match="confirmation differs"):
        main_pipeline.execute_authorized_main_pipeline(
            **{**execution_args, "confirmed_authorization_sha256": "0" * 64}
        )
    with pytest.raises(ValueError, match="synthetic client unavailable"):
        main_pipeline.execute_authorized_main_pipeline(**execution_args)
    assert not (run_dir / "lease.json").exists()
    assert not (run_dir / "active-execution").exists()

    adapter_builds: list[str] = []

    def fake_client(*, model_policy: ModelPolicyReference, policy: OpenAIGatewayPolicy):
        # Exercise the real binding validator without creating a network client.
        OpenAIChatCompletionsGatewayAdapter(client=None, model_policy=model_policy, policy=policy)
        adapter_builds.append(model_policy.model_version_ref)
        if len(adapter_builds) == 1:
            return DiagnosisMainOfflineAdapter(fairness)
        return DeterministicRelationAdapter(ProviderBinding.from_model_policy(model_policy))

    monkeypatch.setattr(OpenAIChatCompletionsGatewayAdapter, "from_environment", fake_client)
    receipt = main_pipeline.execute_authorized_main_pipeline(**execution_args)
    assert receipt.status == "registered_main_analysis_complete"
    assert receipt.logical_request_count == 1024
    assert receipt.relation_request_count == 896
    assert receipt.main_terminal_status_counts == {"completed": 896, "deterministic_completed": 128}
    assert receipt.relation_terminal_status_counts == {"parsed": 896}
    assert receipt.provider_cost_committed_usd <= authority.operator_cost_ceiling_usd
    assert len(adapter_builds) == 2

    monkeypatch.setattr(OpenAIChatCompletionsGatewayAdapter, "from_environment", unavailable_client)
    assert main_pipeline.execute_authorized_main_pipeline(**execution_args) == receipt
    assert (
        main_pipeline.verify_completed_pipeline(run_dir=run_dir, authorization=authority) == receipt
    )

    report_path = run_dir / "analysis-report.json"
    report_payload = json.loads(report_path.read_text())
    report_payload["input_sha256"] = "0" * 64
    report_payload["report_sha256"] = canonical_execution_sha256(
        {key: value for key, value in report_payload.items() if key != "report_sha256"}
    )
    _write_json(report_path, report_payload)
    with pytest.raises(DiagnosisMainPipelineError, match="do not reconcile"):
        main_pipeline.verify_completed_pipeline(run_dir=run_dir, authorization=authority)

    turn_root = next((workspace / "main-store").glob("dmr-*/turn-*"))
    request = GatewayRequest.model_validate_json((turn_root / "request.json").read_bytes())
    result = GatewayExecutionResult.model_validate_json((turn_root / "result.json").read_bytes())
    restored_budget = SharedProviderBudget(100.0)
    restored_budget.restore(request, result.attempts)
    assert 0 < restored_budget.committed_usd < 100.0
    insufficient_budget = SharedProviderBudget(0.000001)
    insufficient_budget.restore(request, result.attempts)
    assert insufficient_budget.exhausted is True

    terminal = next((workspace / "relation-store" / "requests").glob("*/terminal/*.json"))
    terminal.unlink()
    with pytest.raises(
        DiagnosisMainPipelineError,
        match="incomplete relation request forbids automatic provider replay",
    ):
        _rehearse_main_pipeline_components(**arguments)
