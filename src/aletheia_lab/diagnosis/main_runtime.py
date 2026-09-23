"""Outcome-eligible, fail-closed runtime for the controlled diagnosis matrix.

This module turns one frozen logical request into its declared interaction
path.  B2, CodeGraph and FULL perform real provider-mediated selection turns;
the tool layer then retrieves only IDs from the original model-visible corpus.
Every gateway request is persisted before invocation, every gateway result is
persisted afterwards, and an incomplete turn is never replayed automatically.

Construction and preflight are local and outcome-blind.  Execution requires a
separate, self-hashed :class:`MainExecutionAuthority`; this module never mints
that authority and performs no provider call by itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from aletheia_lab.diagnosis._main_runtime_contexts import (
    all_original_ids,
    catalog_context,
    final_schema,
    graph_context,
    plain_context,
    selection_schema,
    validate_selection,
)
from aletheia_lab.diagnosis._main_runtime_contracts import (
    _ROUTE_TURNS,
    LOGICAL_TERMINAL_SCHEMA_VERSION,
    TURN_RECEIPT_SCHEMA_VERSION,
    LogicalStatus,
    MainExecutionAuthority,
    MainLogicalTerminal,
    MainRuntimeContract,
    MainRuntimeError,
    MainRuntimePreflight,
    MainTurnReceipt,
    NeverCancelled,
    ProviderVariant,
    TurnKind,
    _opaque,
    build_main_runtime_preflight,
    load_main_runtime_contract,
)
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_response import (
    DiagnosisMainResponseError,
    validate_main_provider_output,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    DiagnosisMainContext,
    DiagnosisMainExpectedRequest,
    DiagnosisMainFamily,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import (
    DiagnosisVariantFairnessFreeze,
    load_diagnosis_variant_freeze,
)
from aletheia_lab.model_gateway import (
    CancellationProbe,
    Clock,
    GatewayExecutionResult,
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderAdapter,
    RuntimePolicyReference,
    execute_gateway_request,
    prepare_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256


def _manifest(
    authority: MainExecutionAuthority,
    contract: MainRuntimeContract,
) -> EvaluationManifestReference:
    if (
        authority.runtime_contract_sha256 != contract.runtime_contract_sha256
        or authority.analysis_census_sha256 != contract.analysis_census_sha256
        or authority.analysis_plan_sha256 != contract.analysis_plan_sha256
        or authority.response_contract_sha256 != contract.response_contract_sha256
        or authority.manifest_content_sha256
        != canonical_execution_sha256(
            {
                "analysis_census_sha256": contract.analysis_census_sha256,
                "analysis_plan_sha256": contract.analysis_plan_sha256,
                "response_contract_sha256": contract.response_contract_sha256,
                "runtime_contract_sha256": contract.runtime_contract_sha256,
            }
        )
    ):
        raise MainRuntimeError("execution authority is not bound to the runtime freeze")
    project_sha = canonical_execution_sha256(
        {"project": "aletheia-diagnosis-main", "runtime": contract.runtime_contract_sha256}
    )
    snapshot_sha = canonical_execution_sha256({"source_commit_ref": authority.source_commit_ref})
    return EvaluationManifestReference.build(
        project_id=f"p3-project-{project_sha}",
        snapshot_id=f"p3-snapshot-{snapshot_sha}",
        manifest_content_sha256=authority.manifest_content_sha256,
        source_commit_ref=authority.source_commit_ref,
        authorization_state="authorized",
        authorization_ref=authority.authorization_ref,
        provenance_sha256=authority.authority_sha256,
        created_at=authority.authorized_at,
        frozen_at=authority.authorized_at,
        visibility="diagnosis",
    )


def _gateway_request(
    *,
    manifest: EvaluationManifestReference,
    family: DiagnosisMainFamily,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    provider_context: ModelVisibleEvidenceContext,
    variant: ProviderVariant,
    turn_ordinal: int,
    turn_kind: TurnKind,
    prompt: str,
    response_schema: dict[str, object],
    contract: MainRuntimeContract,
    freeze: DiagnosisVariantFairnessFreeze,
) -> GatewayRequest:
    schema_json = canonical_project_json(response_schema)
    route_content_sha = canonical_execution_sha256(
        next(item for item in contract.route_contracts if item["variant"] == variant)
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque({"logical_request": logical.request_sha256}),
        family_id=_opaque({"family": family.family_sha256}),
        mechanism_id=_opaque({"mechanism": family.mechanism}),
        dataset_id=_opaque({"dataset": family.dataset_id}),
        variant_id=_opaque({"variant": variant}),
        variant_content_sha256=route_content_sha,
        case_content_sha256=logical.request_sha256,
        evidence_bundle_id=f"p3-evidence-bundle-{provider_context.context_sha256}",
        evidence_content_sha256=provider_context.context_sha256,
        lineage_graph_id=f"p3-lineage-graph-{logical_context.context_sha256}",
        lineage_sha256=logical_context.context_sha256,
        visibility_projection_sha256=provider_context.context_sha256,
        provenance_sha256=canonical_execution_sha256(
            {
                "logical_request": logical.request_sha256,
                "turn_ordinal": turn_ordinal,
                "turn_kind": turn_kind,
            }
        ),
        visibility="diagnosis",
    )
    openai_policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=openai_policy.model_policy_sha256(),
        provider_ref=_opaque({"provider": "openai"}),
        model_ref=_opaque({"model": openai_policy.model}),
        model_version_ref=_opaque({"model_version": openai_policy.model_version}),
        resource_policy_ref=_opaque({"route": variant, "turn": turn_ordinal, "kind": turn_kind}),
        prompt_policy_ref=_opaque({"prompt_sha256": content_sha256(prompt.encode("utf-8"))}),
        response_schema_sha256=content_sha256(schema_json.encode("utf-8")),
        provenance_sha256=route_content_sha,
        visibility="diagnosis",
    )
    runtime = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque({"maximum_attempts": 2, "fallback": "forbidden"}),
        timeout_ns=60_000_000_000,
        max_attempts=2,
        max_response_bytes=32_768,
        provenance_sha256=contract.runtime_contract_sha256,
    )
    return prepare_gateway_request(
        manifest=manifest,
        case=case,
        model_policy=model_policy,
        context=provider_context,
        prompt_text=prompt,
        response_schema=response_schema,
        runtime_policy=runtime,
    )


def build_main_adapter_model_policy(
    authority: MainExecutionAuthority,
    contract: MainRuntimeContract,
    freeze: DiagnosisVariantFairnessFreeze,
) -> ModelPolicyReference:
    """Build the provider binding used by every main-study gateway request.

    Request-specific prompt, response-schema and route identities remain on
    each immutable request.  The transport adapter needs only the frozen
    provider/model binding and therefore receives this non-request identity.
    """

    manifest = _manifest(authority, contract)
    policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    return ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=policy.model_policy_sha256(),
        provider_ref=_opaque({"provider": "openai"}),
        model_ref=_opaque({"model": policy.model}),
        model_version_ref=_opaque({"model_version": policy.model_version}),
        resource_policy_ref=_opaque(
            {"boundary": "diagnosis-main-adapter", "runtime": contract.runtime_contract_sha256}
        ),
        prompt_policy_ref=_opaque({"boundary": "request_specific_prompts"}),
        response_schema_sha256=contract.response_contract_sha256,
        provenance_sha256=authority.authority_sha256,
        visibility="diagnosis",
    )


def _turn_receipt(
    *,
    logical: DiagnosisMainExpectedRequest,
    turn_ordinal: int,
    turn_kind: TurnKind,
    context: ModelVisibleEvidenceContext,
    request: GatewayRequest,
    result: GatewayExecutionResult,
    selected_ids: Sequence[str],
    original_ids: Sequence[str],
) -> MainTurnReceipt:
    selected = tuple(selected_ids)
    omitted = tuple(item for item in original_ids if item not in set(selected))
    payload = {
        "schema_version": TURN_RECEIPT_SCHEMA_VERSION,
        "logical_request_id": logical.request_id,
        "logical_request_sha256": logical.request_sha256,
        "turn_ordinal": turn_ordinal,
        "turn_kind": turn_kind,
        "visible_context_sha256": context.context_sha256,
        "gateway_request_identity_sha256": request.initial_attempt.request_identity_sha256,
        "gateway_result_sha256": canonical_execution_sha256(result.model_dump(mode="json")),
        "gateway_status": result.status,
        "selected_evidence_ids": selected,
        "omitted_evidence_ids": omitted,
    }
    return MainTurnReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def _logical_terminal(
    *,
    logical: DiagnosisMainExpectedRequest,
    variant: str,
    status: LogicalStatus,
    receipts: Sequence[MainTurnReceipt],
    final_context_sha256: str,
    final_raw_sha256: str | None,
    deterministic_payload: dict[str, object] | None,
    issue_code: str | None,
) -> MainLogicalTerminal:
    payload = {
        "schema_version": LOGICAL_TERMINAL_SCHEMA_VERSION,
        "logical_request_id": logical.request_id,
        "logical_request_sha256": logical.request_sha256,
        "context_id": logical.context_id,
        "variant": variant,
        "status": status,
        "expected_provider_turn_count": len(_ROUTE_TURNS[variant]),
        "completed_provider_turn_count": len(receipts),
        "turn_receipts": tuple(item.model_dump(mode="json") for item in receipts),
        "final_visible_context_sha256": final_context_sha256,
        "final_raw_response_sha256": final_raw_sha256,
        "deterministic_payload": deterministic_payload,
        "issue_code": issue_code,
    }
    return MainLogicalTerminal.model_validate(
        {
            **payload,
            "turn_receipts": tuple(receipts),
            "terminal_sha256": canonical_execution_sha256(payload),
        }
    )


def _execute_turn(
    *,
    logical: DiagnosisMainExpectedRequest,
    ordinal: int,
    kind: TurnKind,
    request: GatewayRequest,
    context: ModelVisibleEvidenceContext,
    selected_ids: Sequence[str],
    original_ids: Sequence[str],
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
    cancellation: CancellationProbe,
) -> tuple[GatewayExecutionResult, MainTurnReceipt]:
    resumed = store.begin_or_resume_turn(logical.request_id, ordinal, request)
    result = resumed or execute_gateway_request(
        request,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation,
    )
    if resumed is None:
        store.complete_turn(logical.request_id, ordinal, result)
    receipt = _turn_receipt(
        logical=logical,
        turn_ordinal=ordinal,
        turn_kind=kind,
        context=context,
        request=request,
        result=result,
        selected_ids=selected_ids,
        original_ids=original_ids,
    )
    return result, receipt


def _publish_terminal(
    store: MainRuntimeStore, terminal: MainLogicalTerminal
) -> MainLogicalTerminal:
    store.publish_terminal(terminal)
    return terminal


def _existing_terminal(
    store: MainRuntimeStore,
    logical: DiagnosisMainExpectedRequest,
) -> MainLogicalTerminal | None:
    existing = store.terminal(logical.request_id)
    if existing is None:
        return None
    if (
        existing.logical_request_sha256 != logical.request_sha256
        or existing.context_id != logical.context_id
        or existing.variant != logical.variant
    ):
        raise MainRuntimeError("persisted terminal differs from the frozen request")
    return existing


def _validate_context_bindings(
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    visible_context: ModelVisibleEvidenceContext,
) -> None:
    if logical.context_id != logical_context.context_id:
        raise MainRuntimeError("logical request and evaluator context differ")
    if visible_context.context_id != logical_context.context_id:
        raise MainRuntimeError("model-visible context and evaluator binding differ")


def _deterministic_terminal(
    logical: DiagnosisMainExpectedRequest,
    visible_context: ModelVisibleEvidenceContext,
    store: MainRuntimeStore,
) -> MainLogicalTerminal:
    deterministic: dict[str, object] = {
        "schema_version": "diagnosis-main-deterministic-output/v1",
        "rule_trace": [
            {"evidence_id": item.evidence_id, "content_sha256": item.content_sha256}
            for item in visible_context.items
        ],
        "bounded_result": "observed_records_listed_without_causal_inference",
    }
    terminal = _logical_terminal(
        logical=logical,
        variant="B0",
        status="deterministic_completed",
        receipts=(),
        final_context_sha256=visible_context.context_sha256,
        final_raw_sha256=None,
        deterministic_payload=deterministic,
        issue_code=None,
    )
    return _publish_terminal(store, terminal)


def _route_prompt(response_contract: Mapping[str, object], variant: ProviderVariant) -> str:
    prompt_contracts = response_contract.get("prompt_contracts")
    if not isinstance(prompt_contracts, dict) or not isinstance(prompt_contracts.get(variant), str):
        raise MainRuntimeError("response contract has no prompt for the route")
    return cast(str, prompt_contracts[variant])


def _selection_context(
    visible_context: ModelVisibleEvidenceContext,
    original_ids: tuple[str, ...],
    variant: ProviderVariant,
) -> ModelVisibleEvidenceContext:
    if variant in {"B2", "FULL"}:
        return catalog_context(visible_context)
    return graph_context(
        visible_context,
        original_ids,
        full=False,
        prior_turn_hashes=(),
    )


def _run_selection_turns(
    *,
    manifest: EvaluationManifestReference,
    family: DiagnosisMainFamily,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    visible_context: ModelVisibleEvidenceContext,
    variant: ProviderVariant,
    base_prompt: str,
    contract: MainRuntimeContract,
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
    cancellation: CancellationProbe,
    original_ids: tuple[str, ...],
) -> tuple[
    MainLogicalTerminal | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[MainTurnReceipt, ...],
]:
    selection_ids: tuple[str, ...] = ()
    prior_request_hashes: list[str] = []
    receipts: list[MainTurnReceipt] = []
    selection_turns = len(_ROUTE_TURNS[variant]) - 1
    for ordinal in range(1, selection_turns + 1):
        context = _selection_context(visible_context, original_ids, variant)
        prompt = (
            f"{base_prompt}\n\nRetrieval turn {ordinal}. Select one or more exact "
            "evidence IDs from the supplied catalog. Return only the registered "
            "selection JSON. Do not diagnose yet."
        )
        request = _gateway_request(
            manifest=manifest,
            family=family,
            logical=logical,
            logical_context=logical_context,
            provider_context=context,
            variant=variant,
            turn_ordinal=ordinal,
            turn_kind="selection",
            prompt=prompt,
            response_schema=selection_schema(original_ids),
            contract=contract,
            freeze=fairness_freeze,
        )
        result, receipt = _execute_turn(
            logical=logical,
            ordinal=ordinal,
            kind="selection",
            request=request,
            context=context,
            selected_ids=selection_ids,
            original_ids=original_ids,
            store=store,
            adapter=adapter,
            clock=clock,
            cancellation=cancellation,
        )
        receipts.append(receipt)
        prior_request_hashes.append(request.initial_attempt.request_identity_sha256)
        try:
            selection = validate_selection(result, available_ids=original_ids)
        except (MainRuntimeError, ValidationError):
            terminal = _logical_terminal(
                logical=logical,
                variant=variant,
                status="selection_failure",
                receipts=receipts,
                final_context_sha256=context.context_sha256,
                final_raw_sha256=(
                    result.raw_response.content_sha256 if result.raw_response is not None else None
                ),
                deterministic_payload=None,
                issue_code="selection_turn_failed_closed",
            )
            return _publish_terminal(store, terminal), (), (), ()
        selected = set((*selection_ids, *selection.requested_evidence_ids))
        selection_ids = tuple(item for item in original_ids if item in selected)
    if selection_turns == 0:
        selection_ids = original_ids
    return None, selection_ids, tuple(prior_request_hashes), tuple(receipts)


def _final_context(
    visible_context: ModelVisibleEvidenceContext,
    variant: ProviderVariant,
    selection_ids: tuple[str, ...],
    prior_request_hashes: tuple[str, ...],
) -> ModelVisibleEvidenceContext:
    if variant in {"B1", "B2"}:
        return plain_context(visible_context, selection_ids)
    if variant == "CodeGraph":
        return graph_context(
            visible_context,
            selection_ids,
            full=False,
            prior_turn_hashes=prior_request_hashes,
        )
    if variant == "FULL":
        return graph_context(
            visible_context,
            selection_ids,
            full=True,
            prior_turn_hashes=prior_request_hashes,
        )
    return visible_context


def _run_final_turn(
    *,
    manifest: EvaluationManifestReference,
    family: DiagnosisMainFamily,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    final_context: ModelVisibleEvidenceContext,
    original_ids: tuple[str, ...],
    selection_ids: tuple[str, ...],
    receipts: tuple[MainTurnReceipt, ...],
    variant: ProviderVariant,
    base_prompt: str,
    response_contract: Mapping[str, object],
    contract: MainRuntimeContract,
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
    cancellation: CancellationProbe,
) -> MainLogicalTerminal:
    final_ordinal = len(_ROUTE_TURNS[variant])
    prompt = (
        f"{base_prompt}\n\nFinal diagnosis turn. Use only the supplied final "
        "projection and return exactly one registered diagnosis JSON object."
    )
    request = _gateway_request(
        manifest=manifest,
        family=family,
        logical=logical,
        logical_context=logical_context,
        provider_context=final_context,
        variant=variant,
        turn_ordinal=final_ordinal,
        turn_kind="final",
        prompt=prompt,
        response_schema=final_schema(response_contract),
        contract=contract,
        freeze=fairness_freeze,
    )
    result, receipt = _execute_turn(
        logical=logical,
        ordinal=final_ordinal,
        kind="final",
        request=request,
        context=final_context,
        selected_ids=selection_ids,
        original_ids=original_ids,
        store=store,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation,
    )
    all_receipts = (*receipts, receipt)
    if result.status != "parsed" or result.raw_response is None:
        terminal = _logical_terminal(
            logical=logical,
            variant=variant,
            status="technical_failure",
            receipts=all_receipts,
            final_context_sha256=final_context.context_sha256,
            final_raw_sha256=(
                result.raw_response.content_sha256 if result.raw_response is not None else None
            ),
            deterministic_payload=None,
            issue_code=f"gateway_{result.status}",
        )
        return _publish_terminal(store, terminal)
    try:
        raw_text = result.raw_response.content.decode("utf-8", errors="strict")
        validate_main_provider_output(
            raw_text,
            variant=variant,
            visible_evidence_ids={item.evidence_id for item in final_context.items},
        )
    except (UnicodeDecodeError, DiagnosisMainResponseError, ValidationError, ValueError):
        terminal = _logical_terminal(
            logical=logical,
            variant=variant,
            status="semantic_failure",
            receipts=all_receipts,
            final_context_sha256=final_context.context_sha256,
            final_raw_sha256=result.raw_response.content_sha256,
            deterministic_payload=None,
            issue_code="variant_semantic_validation_failed",
        )
        return _publish_terminal(store, terminal)
    terminal = _logical_terminal(
        logical=logical,
        variant=variant,
        status="completed",
        receipts=all_receipts,
        final_context_sha256=final_context.context_sha256,
        final_raw_sha256=result.raw_response.content_sha256,
        deterministic_payload=None,
        issue_code=None,
    )
    return _publish_terminal(store, terminal)


def run_main_logical_request(
    *,
    logical: DiagnosisMainExpectedRequest,
    logical_context: DiagnosisMainContext,
    family: DiagnosisMainFamily,
    visible_context: ModelVisibleEvidenceContext,
    contract: MainRuntimeContract,
    authority: MainExecutionAuthority,
    response_contract: Mapping[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter | None,
    clock: Clock,
    cancellation: CancellationProbe | None = None,
) -> MainLogicalTerminal:
    """Execute one exact logical route or return its immutable terminal replay."""

    existing = _existing_terminal(store, logical)
    if existing is not None:
        return existing
    _validate_context_bindings(logical, logical_context, visible_context)
    manifest = _manifest(authority, contract)
    original_ids = all_original_ids(visible_context)
    if logical.variant == "B0":
        return _deterministic_terminal(logical, visible_context, store)
    if adapter is None:
        raise MainRuntimeError("provider-backed route requires an explicit adapter")
    variant: ProviderVariant = logical.variant
    base_prompt = _route_prompt(response_contract, variant)
    cancellation_probe = cancellation or NeverCancelled()
    failed, selection_ids, request_hashes, receipts = _run_selection_turns(
        manifest=manifest,
        family=family,
        logical=logical,
        logical_context=logical_context,
        visible_context=visible_context,
        variant=variant,
        base_prompt=base_prompt,
        contract=contract,
        fairness_freeze=fairness_freeze,
        store=store,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation_probe,
        original_ids=original_ids,
    )
    if failed is not None:
        return failed
    final_context = _final_context(visible_context, variant, selection_ids, request_hashes)
    return _run_final_turn(
        manifest=manifest,
        family=family,
        logical=logical,
        logical_context=logical_context,
        final_context=final_context,
        original_ids=original_ids,
        selection_ids=selection_ids,
        receipts=receipts,
        variant=variant,
        base_prompt=base_prompt,
        response_contract=response_contract,
        contract=contract,
        fairness_freeze=fairness_freeze,
        store=store,
        adapter=adapter,
        clock=clock,
        cancellation=cancellation_probe,
    )


def load_main_runtime_inputs(
    root: Path,
) -> tuple[MainRuntimeContract, DiagnosisVariantFairnessFreeze, dict[str, object]]:
    contract = load_main_runtime_contract(
        root / "configs/evaluation/diagnosis_main_runtime_contract.json"
    )
    freeze = load_diagnosis_variant_freeze(
        root / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
    )
    response_path = root / "configs/evaluation/diagnosis_main_response_contract.json"
    if response_path.is_symlink() or not response_path.is_file():
        raise MainRuntimeError("main response contract is unavailable")
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if (
        not isinstance(response, dict)
        or response.get("contract_sha256") != contract.response_contract_sha256
    ):
        raise MainRuntimeError("main response contract differs from runtime binding")
    return contract, freeze, cast(dict[str, object], response)


__all__ = [
    "MainExecutionAuthority",
    "MainLogicalTerminal",
    "MainRuntimeContract",
    "MainRuntimeError",
    "MainRuntimePreflight",
    "MainRuntimeStore",
    "NeverCancelled",
    "build_main_adapter_model_policy",
    "build_main_runtime_preflight",
    "load_main_runtime_contract",
    "load_main_runtime_inputs",
    "run_main_logical_request",
]
