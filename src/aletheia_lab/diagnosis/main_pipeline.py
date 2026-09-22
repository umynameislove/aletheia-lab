"""Outcome-blind orchestration for the complete diagnosis main-study pipeline.

The offline path exercises every stage with network-incapable adapters.  The
authorized path is callable only with an exact self-hashed authorization and a
second action-time confirmation.  Raw requests, responses, labels and reports
remain in a private run directory outside the repository.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ValidationError

from aletheia_lab.diagnosis._main_pipeline_budget import (
    INPUT_USD_PER_MILLION_TOKENS,
    OUTPUT_USD_PER_MILLION_TOKENS,
    BudgetedProviderAdapter,
    SharedProviderBudget,
)
from aletheia_lab.diagnosis._main_pipeline_contracts import (
    PIPELINE_AUTHORIZATION_SCHEMA_VERSION,
    PIPELINE_LEASE_SCHEMA_VERSION,
    PIPELINE_PREFLIGHT_SCHEMA_VERSION,
    PIPELINE_RECEIPT_SCHEMA_VERSION,
    DiagnosisMainPipelineAuthorization,
    DiagnosisMainPipelineError,
    DiagnosisMainPipelineLease,
    DiagnosisMainPipelinePreflight,
    DiagnosisMainPipelineReceipt,
)
from aletheia_lab.diagnosis._main_pipeline_relations import (
    RehearsalRelationAuthorization,
    build_pipeline_relation_requests,
    execute_pipeline_relation_stage,
    offline_relation_adapter,
    restore_relation_budget,
)
from aletheia_lab.diagnosis._main_runtime_contracts import (
    MainExecutionAuthority,
    MainRuntimeContract,
)
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_execution import (
    DeterministicRehearsalClock,
    MainBatchResult,
    rehearse_main_execution,
    run_authorized_main_execution,
)
from aletheia_lab.diagnosis.main_runtime import (
    build_main_adapter_model_policy,
    load_main_runtime_inputs,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    MINIMUM_PROVIDER_INTERVAL_MS,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PacedProviderAdapter,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
    analyse_diagnosis_main,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainCensusSeal,
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainRelationResults,
    DiagnosisMainScoringContract,
    DiagnosisMainScoringPreparation,
    load_main_analysis_plan,
    load_main_scoring_contract,
    materialize_main_analysis_input,
    prepare_main_scoring,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import DiagnosisVariantFairnessFreeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    GatewayExecutionResult,
    GatewayRequest,
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayPolicy,
    ProviderAdapter,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_REHEARSAL_SOURCE_COMMIT = "0" * 40
_PUBLIC_SEAL = Path("configs/evaluation/diagnosis_main_census_seal.json")
_ALLOWED_RUN_MEMBERS = {
    "authorization.json",
    "lease.json",
    "main-store",
    "main-batch-result.json",
    "scoring-preparation.json",
    "relation-store",
    "relation-results.json",
    "analysis-input.json",
    "analysis-report.json",
    "receipt.json",
    "active-execution",
}


def _serialized(model: BaseModel) -> bytes:
    return (canonical_project_json(model.model_dump(mode="json")) + "\n").encode("utf-8")


def _publish(path: Path, model: BaseModel) -> None:
    publish_immutable_file(path, _serialized(model))


def _destination_sha256(path: Path) -> str:
    return canonical_execution_sha256({"private_diagnosis_main_run": path.as_posix()})


def checked_pipeline_run_directory(root: Path, run_dir: Path) -> Path:
    """Require a private, non-linked destination with a closed membership."""

    try:
        destination = checked_private_path(run_dir.expanduser(), root)
    except ValueError as exc:
        raise DiagnosisMainPipelineError("pipeline run directory must remain private") from exc
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise DiagnosisMainPipelineError("pipeline destination is not a real directory")
        if any(
            member.name not in _ALLOWED_RUN_MEMBERS or member.is_symlink()
            for member in destination.iterdir()
        ):
            raise DiagnosisMainPipelineError("pipeline destination has unknown artifacts")
    else:
        destination.mkdir(parents=True)
    return destination


def load_private_main_packet(root: Path, path: Path) -> DiagnosisMainPrivateCensusPacket:
    """Load the private packet only after matching all public seal identities."""

    try:
        packet_path = checked_private_path(path.expanduser(), root)
    except ValueError as exc:
        raise DiagnosisMainPipelineError("private census packet must remain outside git") from exc
    seal_path = root / _PUBLIC_SEAL
    try:
        if packet_path.is_symlink() or not packet_path.is_file():
            raise OSError("private packet is unavailable")
        if seal_path.is_symlink() or not seal_path.is_file():
            raise OSError("public seal is unavailable")
        packet_bytes = packet_path.read_bytes()
        packet = DiagnosisMainPrivateCensusPacket.model_validate_json(packet_bytes)
        seal = DiagnosisMainCensusSeal.model_validate_json(seal_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise DiagnosisMainPipelineError("private packet or public seal is invalid") from exc
    if (
        content_sha256(packet_bytes) != seal.private_packet_byte_sha256
        or packet.packet_sha256 != seal.private_packet_canonical_sha256
        or packet.analysis_census.census_sha256 != seal.analysis_census_sha256
    ):
        raise DiagnosisMainPipelineError("private packet differs from the public seal")
    return packet


def rehearse_main_pipeline(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    workspace: Path,
    source_commit_ref: str,
) -> DiagnosisMainPipelinePreflight:
    """Run all 1,024 logical requests and all downstream stages without network I/O."""

    try:
        private_workspace = checked_private_path(workspace.expanduser(), root)
    except ValueError as exc:
        raise DiagnosisMainPipelineError("rehearsal workspace must remain private") from exc
    runtime, fairness, response = load_main_runtime_inputs(root)
    scoring = load_main_scoring_contract(root, runtime_contract=runtime, response_contract=response)
    plan = load_main_analysis_plan(root)
    return _rehearse_main_pipeline_components(
        root=root,
        packet=packet,
        workspace=private_workspace,
        source_commit_ref=source_commit_ref,
        runtime=runtime,
        fairness=fairness,
        response=response,
        scoring=scoring,
        plan=plan,
    )


def _rehearse_main_pipeline_components(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    workspace: Path,
    source_commit_ref: str,
    runtime: MainRuntimeContract,
    fairness: DiagnosisVariantFairnessFreeze,
    response: dict[str, object],
    scoring: DiagnosisMainScoringContract,
    plan: DiagnosisMainAnalysisPlan,
) -> DiagnosisMainPipelinePreflight:
    """Dependency-injected offline implementation used by portable conformance tests."""

    workspace.mkdir(parents=True, exist_ok=True)
    if workspace.is_symlink() or not workspace.is_dir():
        raise DiagnosisMainPipelineError("rehearsal workspace is unsafe")
    allowed = {"main-store", "relation-store"}
    if any(item.name not in allowed or item.is_symlink() for item in workspace.iterdir()):
        raise DiagnosisMainPipelineError("rehearsal workspace has unknown artifacts")
    main_store = MainRuntimeStore(workspace / "main-store")
    batch = rehearse_main_execution(
        packet=packet,
        contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        store=main_store,
    )
    preparation = prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        scoring_contract=scoring,
        batch_result=batch,
        store_root=main_store.root,
    )
    rehearsal_plan_sha = canonical_execution_sha256(
        {
            "boundary": "diagnosis-main-offline-pipeline/v1",
            "packet": packet.packet_sha256,
            "preparation": preparation.preparation_sha256,
        }
    )
    relation_authority = RehearsalRelationAuthorization(
        authorization_ref=f"ev-{rehearsal_plan_sha}"
    )
    prepared = build_pipeline_relation_requests(
        root,
        preparation,
        source_commit_ref=_REHEARSAL_SOURCE_COMMIT,
        authorization=relation_authority,
        plan_sha256=rehearsal_plan_sha,
    )
    relation_adapter = offline_relation_adapter(prepared)
    relations, _ = execute_pipeline_relation_stage(
        prepared=prepared,
        preparation=preparation,
        store_root=workspace / "relation-store",
        adapter=relation_adapter,
        clock=DeterministicRehearsalClock(),
    )
    analysis_input = materialize_main_analysis_input(
        preparation=preparation,
        relation_results=relations,
        allow_offline_rehearsal=True,
    )
    report = analyse_diagnosis_main(plan, packet.analysis_census, analysis_input)
    if report.status != "valid_registered_analysis":
        raise DiagnosisMainPipelineError("offline pipeline failed the analysis contract")
    payload = {
        "schema_version": PIPELINE_PREFLIGHT_SCHEMA_VERSION,
        "status": "offline_end_to_end_preflight_pass",
        "scientific_result_eligible": False,
        "protected_main_outcomes_opened": False,
        "provider_calls_executed": False,
        "registered_main_attempts_consumed": 0,
        "registered_relation_attempts_consumed": 0,
        "source_commit_ref": source_commit_ref,
        "private_packet_sha256": packet.packet_sha256,
        "analysis_census_sha256": packet.analysis_census.census_sha256,
        "runtime_contract_sha256": runtime.runtime_contract_sha256,
        "response_contract_sha256": runtime.response_contract_sha256,
        "scoring_contract_sha256": scoring.contract_sha256,
        "analysis_plan_sha256": plan.plan_sha256,
        "logical_request_count": batch.logical_request_count,
        "provider_backed_logical_request_count": batch.provider_backed_logical_request_count,
        "deterministic_logical_request_count": batch.deterministic_logical_request_count,
        "expected_provider_turn_count": batch.expected_provider_turn_count,
        "completed_provider_turn_count": batch.completed_provider_turn_count,
        "emitted_claim_count": preparation.emitted_claim_count,
        "relation_request_count": preparation.relation_request_count,
        "provider_input_fields": ("claim_text", "claim_type", "visible_evidence"),
        "main_resume_is_terminal_only": True,
        "relation_resume_is_terminal_only": True,
        "incomplete_request_replay_permitted": False,
        "batch_result_sha256": batch.result_sha256,
        "scoring_preparation_sha256": preparation.preparation_sha256,
        "relation_results_sha256": relations.results_sha256,
        "analysis_input_sha256": analysis_input.input_sha256,
        "analysis_report_sha256": report.report_sha256,
    }
    return DiagnosisMainPipelinePreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def build_pipeline_authorization(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    preflight: DiagnosisMainPipelinePreflight,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    authorized_at: str,
    operator_cost_ceiling_usd: float,
    confirmed_preflight_sha256: str,
) -> DiagnosisMainPipelineAuthorization:
    """Mint authority only from an exact, freshly confirmed offline preflight."""

    runtime, fairness, response = load_main_runtime_inputs(root)
    scoring = load_main_scoring_contract(root, runtime_contract=runtime, response_contract=response)
    plan = load_main_analysis_plan(root)
    checked_run = checked_pipeline_run_directory(root, run_dir)
    model_policy = OpenAIGatewayPolicy.from_fairness_policy(fairness.model_policies["main_llm_v1"])
    if (
        confirmed_preflight_sha256 != preflight.preflight_sha256
        or not repository_state.synchronized_main
        or repository_state.head_commit != preflight.source_commit_ref
        or preflight.private_packet_sha256 != packet.packet_sha256
        or preflight.analysis_census_sha256 != packet.analysis_census.census_sha256
        or preflight.runtime_contract_sha256 != runtime.runtime_contract_sha256
        or preflight.response_contract_sha256 != runtime.response_contract_sha256
        or preflight.scoring_contract_sha256 != scoring.contract_sha256
        or preflight.analysis_plan_sha256 != plan.plan_sha256
    ):
        raise DiagnosisMainPipelineError(
            "authorization requires the exact preflight on clean synchronized main"
        )
    payload: dict[str, object] = {
        "schema_version": PIPELINE_AUTHORIZATION_SCHEMA_VERSION,
        "execution_authorized": True,
        "authorized_at": authorized_at,
        "source_commit_ref": repository_state.head_commit,
        "preflight_sha256": preflight.preflight_sha256,
        "private_packet_sha256": packet.packet_sha256,
        "analysis_census_sha256": packet.analysis_census.census_sha256,
        "runtime_contract_sha256": runtime.runtime_contract_sha256,
        "response_contract_sha256": runtime.response_contract_sha256,
        "scoring_contract_sha256": scoring.contract_sha256,
        "analysis_plan_sha256": plan.plan_sha256,
        "destination_sha256": _destination_sha256(checked_run),
        "logical_request_count": 1024,
        "maximum_main_provider_turn_count": 1408,
        "maximum_relation_request_count": 4480,
        "maximum_provider_attempts_per_request": 2,
        "provider": model_policy.provider,
        "model": model_policy.model,
        "model_snapshot": model_policy.model_version,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION_TOKENS,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION_TOKENS,
        "pricing_source_url": "https://developers.openai.com/api/docs/models/gpt-4.1",
        "pricing_checked_on": "2026-09-22",
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "registered_main_attempts": 1,
        "registered_relation_attempts": 1,
        "raw_artifacts_private": True,
        "credential_stored": False,
    }
    digest = canonical_execution_sha256(payload)
    return DiagnosisMainPipelineAuthorization.model_validate(
        {
            **payload,
            "authorization_ref": f"ev-{digest}",
            "authorization_sha256": digest,
        }
    )


def validate_pipeline_authorization(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    preflight: DiagnosisMainPipelinePreflight,
    authorization: DiagnosisMainPipelineAuthorization,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> DiagnosisMainPipelineAuthorization:
    """Reconcile authority against current frozen inputs without provider access."""

    runtime, _, response = load_main_runtime_inputs(root)
    scoring = load_main_scoring_contract(root, runtime_contract=runtime, response_contract=response)
    plan = load_main_analysis_plan(root)
    checked = DiagnosisMainPipelineAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    checked_run = checked_pipeline_run_directory(root, run_dir)
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != checked.source_commit_ref
        or checked.preflight_sha256 != preflight.preflight_sha256
        or checked.private_packet_sha256 != packet.packet_sha256
        or checked.analysis_census_sha256 != packet.analysis_census.census_sha256
        or checked.runtime_contract_sha256 != runtime.runtime_contract_sha256
        or checked.response_contract_sha256 != runtime.response_contract_sha256
        or checked.scoring_contract_sha256 != scoring.contract_sha256
        or checked.analysis_plan_sha256 != plan.plan_sha256
        or checked.destination_sha256 != _destination_sha256(checked_run)
    ):
        raise DiagnosisMainPipelineError("pipeline authorization differs from current inputs")
    return checked


def publish_pipeline_authorization(
    run_dir: Path,
    authorization: DiagnosisMainPipelineAuthorization,
) -> None:
    """Persist the reviewed authority without opening the registered attempt."""

    _publish(run_dir / "authorization.json", authorization)


def _main_authority(
    authorization: DiagnosisMainPipelineAuthorization,
    runtime: MainRuntimeContract,
) -> MainExecutionAuthority:
    # Runtime retains the primary v2 identity; the forward v3 analysis plan
    # adds only the separately reported Qwen disposition.
    manifest_sha = canonical_execution_sha256(
        {
            "analysis_census_sha256": authorization.analysis_census_sha256,
            "analysis_plan_sha256": runtime.analysis_plan_sha256,
            "response_contract_sha256": authorization.response_contract_sha256,
            "runtime_contract_sha256": authorization.runtime_contract_sha256,
        }
    )
    payload = {
        "schema_version": "diagnosis-main-execution-authority/v1",
        "execution_authorized": True,
        "source_commit_ref": authorization.source_commit_ref,
        "analysis_census_sha256": authorization.analysis_census_sha256,
        "analysis_plan_sha256": runtime.analysis_plan_sha256,
        "response_contract_sha256": authorization.response_contract_sha256,
        "runtime_contract_sha256": authorization.runtime_contract_sha256,
        "manifest_content_sha256": manifest_sha,
        "authorization_ref": authorization.authorization_ref,
        "authorized_at": authorization.authorized_at,
    }
    return MainExecutionAuthority.model_validate(
        {**payload, "authority_sha256": canonical_execution_sha256(payload)}
    )


def _acquire_or_resume_lease(
    run_dir: Path,
    authorization: DiagnosisMainPipelineAuthorization,
) -> DiagnosisMainPipelineLease:
    payload = {
        "schema_version": PIPELINE_LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "destination_sha256": authorization.destination_sha256,
        "registered_main_attempts_consumed": 1,
        "registered_relation_attempts_reserved": 1,
    }
    expected = DiagnosisMainPipelineLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )
    path = run_dir / "lease.json"
    if path.exists():
        try:
            observed = DiagnosisMainPipelineLease.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise DiagnosisMainPipelineError("pipeline lease is invalid") from exc
        if observed != expected:
            raise DiagnosisMainPipelineError("pipeline lease belongs to another authority")
        return observed
    _publish(path, expected)
    return expected


def _load_private_model(model: type[BaseModel], path: Path) -> BaseModel:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("artifact is unavailable")
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise DiagnosisMainPipelineError("private pipeline artifact is invalid") from exc


def _restore_main_budget(store_root: Path, budget: SharedProviderBudget) -> None:
    """Reject incomplete turns up front and restore cost from sealed turn results."""

    if not store_root.exists():
        return
    for turn_root in sorted(store_root.glob("dmr-*/turn-*")):
        if turn_root.is_symlink() or not turn_root.is_dir():
            raise DiagnosisMainPipelineError("main turn store is unsafe")
        request_path = turn_root / "request.json"
        result_path = turn_root / "result.json"
        if request_path.is_symlink() or result_path.is_symlink():
            raise DiagnosisMainPipelineError("main turn store contains a linked artifact")
        if request_path.exists() != result_path.exists():
            raise DiagnosisMainPipelineError(
                "incomplete main turn forbids automatic provider replay"
            )
        if not request_path.exists():
            continue
        try:
            request = GatewayRequest.model_validate_json(request_path.read_bytes())
            result = GatewayExecutionResult.model_validate_json(result_path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise DiagnosisMainPipelineError("persisted main turn is invalid") from exc
        budget.restore(request, result.attempts)


def _require_budget_available(budget: SharedProviderBudget, stage: str) -> None:
    if budget.exhausted:
        raise DiagnosisMainPipelineError(
            f"provider budget exhausted during {stage}; registered attempt stopped"
        )


def verify_completed_pipeline(
    *,
    run_dir: Path,
    authorization: DiagnosisMainPipelineAuthorization,
) -> DiagnosisMainPipelineReceipt:
    """Read and cross-check a completed private run without provider access."""

    receipt = DiagnosisMainPipelineReceipt.model_validate(
        _load_private_model(DiagnosisMainPipelineReceipt, run_dir / "receipt.json")
    )
    lease = DiagnosisMainPipelineLease.model_validate(
        _load_private_model(DiagnosisMainPipelineLease, run_dir / "lease.json")
    )
    batch = MainBatchResult.model_validate(
        _load_private_model(MainBatchResult, run_dir / "main-batch-result.json")
    )
    preparation = DiagnosisMainScoringPreparation.model_validate(
        _load_private_model(DiagnosisMainScoringPreparation, run_dir / "scoring-preparation.json")
    )
    relations = DiagnosisMainRelationResults.model_validate(
        _load_private_model(DiagnosisMainRelationResults, run_dir / "relation-results.json")
    )
    analysis_input = DiagnosisMainAnalysisInput.model_validate(
        _load_private_model(DiagnosisMainAnalysisInput, run_dir / "analysis-input.json")
    )
    report = DiagnosisMainAnalysisReport.model_validate(
        _load_private_model(DiagnosisMainAnalysisReport, run_dir / "analysis-report.json")
    )
    if (
        receipt.authorization_sha256 != authorization.authorization_sha256
        or receipt.lease_sha256 != lease.lease_sha256
        or lease.authorization_sha256 != authorization.authorization_sha256
        or lease.destination_sha256 != authorization.destination_sha256
        or receipt.source_commit_ref != authorization.source_commit_ref
        or batch.execution_mode != "authorized_execution"
        or receipt.logical_request_count != batch.logical_request_count
        or receipt.main_terminal_status_counts != batch.terminal_status_counts
        or receipt.batch_result_sha256 != batch.result_sha256
        or preparation.batch_result_sha256 != batch.result_sha256
        or preparation.analysis_plan_sha256 != authorization.analysis_plan_sha256
        or preparation.census_sha256 != authorization.analysis_census_sha256
        or preparation.scoring_contract_sha256 != authorization.scoring_contract_sha256
        or receipt.scoring_preparation_sha256 != preparation.preparation_sha256
        or receipt.relation_request_count != preparation.relation_request_count
        or receipt.relation_results_sha256 != relations.results_sha256
        or receipt.analysis_input_sha256 != analysis_input.input_sha256
        or analysis_input.analysis_plan_sha256 != authorization.analysis_plan_sha256
        or analysis_input.census_sha256 != authorization.analysis_census_sha256
        or receipt.analysis_report_sha256 != report.report_sha256
        or report.status != "valid_registered_analysis"
        or report.analysis_plan_sha256 != authorization.analysis_plan_sha256
        or report.census_sha256 != authorization.analysis_census_sha256
        or report.input_sha256 != analysis_input.input_sha256
        or relations.preparation_sha256 != preparation.preparation_sha256
        or receipt.operator_cost_ceiling_usd != authorization.operator_cost_ceiling_usd
    ):
        raise DiagnosisMainPipelineError("completed pipeline artifacts do not reconcile")
    return receipt


def execute_authorized_main_pipeline(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    preflight: DiagnosisMainPipelinePreflight,
    authorization: DiagnosisMainPipelineAuthorization,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    confirmed_authorization_sha256: str,
) -> DiagnosisMainPipelineReceipt:
    """Consume or resume the sole authorized run and publish only private artifacts."""

    checked_run = checked_pipeline_run_directory(root, run_dir)
    with _exclusive_execution(checked_run):
        return _execute_authorized_pipeline(
            root=root,
            packet=packet,
            preflight=preflight,
            authorization=authorization,
            repository_state=repository_state,
            checked_run=checked_run,
            confirmed_authorization_sha256=confirmed_authorization_sha256,
        )


@contextmanager
def _exclusive_execution(run_dir: Path) -> Iterator[None]:
    """Only one process may dispatch; an interrupted owner leaves a closed lock."""

    lock = run_dir / "active-execution"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise DiagnosisMainPipelineError(
            "pipeline execution is active or interrupted; concurrent dispatch is forbidden"
        ) from exc
    try:
        yield
    finally:
        lock.rmdir()


def _execute_authorized_pipeline(
    *,
    root: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    preflight: DiagnosisMainPipelinePreflight,
    authorization: DiagnosisMainPipelineAuthorization,
    repository_state: RepositoryExecutionState,
    checked_run: Path,
    confirmed_authorization_sha256: str,
) -> DiagnosisMainPipelineReceipt:
    checked = validate_pipeline_authorization(
        root=root,
        packet=packet,
        preflight=preflight,
        authorization=authorization,
        repository_state=repository_state,
        run_dir=checked_run,
    )
    if confirmed_authorization_sha256 != checked.authorization_sha256:
        raise DiagnosisMainPipelineError("action-time authorization confirmation differs")
    if (checked_run / "receipt.json").exists():
        return verify_completed_pipeline(run_dir=checked_run, authorization=checked)
    authorization_path = checked_run / "authorization.json"
    if not authorization_path.exists():
        publish_pipeline_authorization(checked_run, checked)
    elif (
        DiagnosisMainPipelineAuthorization.model_validate(
            _load_private_model(DiagnosisMainPipelineAuthorization, authorization_path)
        )
        != checked
    ):
        raise DiagnosisMainPipelineError("persisted pipeline authorization differs")
    runtime, fairness, response = load_main_runtime_inputs(root)
    scoring = load_main_scoring_contract(root, runtime_contract=runtime, response_contract=response)
    plan = load_main_analysis_plan(root)
    main_authority = _main_authority(checked, runtime)
    main_policy = OpenAIGatewayPolicy.from_fairness_policy(fairness.model_policies["main_llm_v1"])
    budget = SharedProviderBudget(checked.operator_cost_ceiling_usd)
    main_store = MainRuntimeStore(checked_run / "main-store")
    _restore_main_budget(main_store.root, budget)
    _require_budget_available(budget, "main resume accounting")
    main_delegate = OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=build_main_adapter_model_policy(main_authority, runtime, fairness),
        policy=main_policy,
    )
    main_adapter = BudgetedProviderAdapter(main_delegate, budget)
    lease = _acquire_or_resume_lease(checked_run, checked)
    clock = SystemMonotonicClock()
    batch = run_authorized_main_execution(
        packet=packet,
        contract=runtime,
        authority=main_authority,
        response_contract=response,
        fairness_freeze=fairness,
        store=main_store,
        adapter=main_adapter,
        clock=clock,
    )
    _require_budget_available(budget, "main execution")
    _publish(checked_run / "main-batch-result.json", batch)
    preparation = prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        scoring_contract=scoring,
        batch_result=batch,
        store_root=main_store.root,
    )
    _publish(checked_run / "scoring-preparation.json", preparation)
    prepared = build_pipeline_relation_requests(
        root,
        preparation,
        source_commit_ref=checked.source_commit_ref,
        authorization=checked,
        plan_sha256=checked.authorization_sha256,
    )
    restore_relation_budget(prepared, checked_run / "relation-store", budget)
    _require_budget_available(budget, "relation resume accounting")
    relation_adapter: ProviderAdapter | None = None
    if prepared:
        delegate = OpenAIChatCompletionsGatewayAdapter.from_environment(
            model_policy=prepared[0].request.initial_attempt.model_policy,
            policy=main_policy,
        )
        relation_adapter = BudgetedProviderAdapter(
            PacedProviderAdapter(
                delegate,
                minimum_interval_seconds=MINIMUM_PROVIDER_INTERVAL_MS / 1000,
            ),
            budget,
        )
    relations, relation_counts = execute_pipeline_relation_stage(
        prepared=prepared,
        preparation=preparation,
        store_root=checked_run / "relation-store",
        adapter=relation_adapter,
        clock=clock,
    )
    _require_budget_available(budget, "relation scoring")
    _publish(checked_run / "relation-results.json", relations)
    analysis_input = materialize_main_analysis_input(
        preparation=preparation,
        relation_results=relations,
    )
    _publish(checked_run / "analysis-input.json", analysis_input)
    report = analyse_diagnosis_main(plan, packet.analysis_census, analysis_input)
    if report.status != "valid_registered_analysis":
        raise DiagnosisMainPipelineError("registered analysis rejected the complete input")
    _publish(checked_run / "analysis-report.json", report)
    payload = {
        "schema_version": PIPELINE_RECEIPT_SCHEMA_VERSION,
        "status": "registered_main_analysis_complete",
        "authorization_sha256": checked.authorization_sha256,
        "lease_sha256": lease.lease_sha256,
        "source_commit_ref": checked.source_commit_ref,
        "logical_request_count": batch.logical_request_count,
        "relation_request_count": preparation.relation_request_count,
        "main_terminal_status_counts": batch.terminal_status_counts,
        "relation_terminal_status_counts": relation_counts,
        "registered_main_attempts_consumed": 1,
        "registered_relation_attempts_consumed": int(bool(prepared)),
        "provider_calls_executed": True,
        "operator_cost_ceiling_usd": checked.operator_cost_ceiling_usd,
        "provider_cost_committed_usd": budget.committed_usd,
        "provider_budget_exhausted": False,
        "raw_artifacts_private": True,
        "private_paths_embedded": False,
        "batch_result_sha256": batch.result_sha256,
        "scoring_preparation_sha256": preparation.preparation_sha256,
        "relation_results_sha256": relations.results_sha256,
        "analysis_input_sha256": analysis_input.input_sha256,
        "analysis_report_sha256": report.report_sha256,
    }
    receipt = DiagnosisMainPipelineReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )
    _publish(checked_run / "receipt.json", receipt)
    return receipt


__all__ = [
    "DiagnosisMainPipelineAuthorization",
    "DiagnosisMainPipelineError",
    "DiagnosisMainPipelinePreflight",
    "DiagnosisMainPipelineReceipt",
    "build_pipeline_authorization",
    "checked_pipeline_run_directory",
    "execute_authorized_main_pipeline",
    "load_private_main_packet",
    "publish_pipeline_authorization",
    "rehearse_main_pipeline",
    "validate_pipeline_authorization",
    "verify_completed_pipeline",
]
