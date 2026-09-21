"""Canonical, resumable batch orchestration for the diagnosis main census.

The offline rehearsal path is deliberately incapable of constructing a network
adapter.  The authorized path accepts an already-created authority and an
explicit provider adapter, but never mints authority or reads credentials.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis._main_runtime_contracts import (
    _ROUTE_TURNS,
    MainExecutionAuthority,
    MainLogicalTerminal,
    MainRuntimeContract,
    MainRuntimeError,
)
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_runtime import (
    build_main_runtime_preflight,
    run_main_logical_request,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import DiagnosisMainExpectedRequest
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import DiagnosisVariantFairnessFreeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    Clock,
    OpenAIGatewayPolicy,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
)
from aletheia_lab.project.identity import SHA256_PATTERN

BATCH_BINDING_SCHEMA_VERSION: Final = "diagnosis-main-batch-binding/v1"
BATCH_RESULT_SCHEMA_VERSION: Final = "diagnosis-main-batch-result/v1"
_REHEARSAL_SOURCE_COMMIT: Final = "0" * 40

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ExecutionMode = Literal["offline_rehearsal", "authorized_execution"]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class MainBatchBinding(_StrictFrozenModel):
    """Immutable identity preventing a store from crossing execution boundaries."""

    schema_version: Literal["diagnosis-main-batch-binding/v1"] = BATCH_BINDING_SCHEMA_VERSION
    execution_mode: ExecutionMode
    private_packet_sha256: Sha256
    analysis_census_sha256: Sha256
    runtime_contract_sha256: Sha256
    response_contract_sha256: Sha256
    fairness_freeze_sha256: Sha256
    authority_sha256: Sha256
    ordered_requests_sha256: Sha256
    logical_request_count: int = Field(ge=1, le=1024)
    binding_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"binding_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.binding_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("main batch binding identity does not match")
        return self


class MainBatchResult(_StrictFrozenModel):
    """Content-only completion summary; raw prompts and responses stay in the store."""

    schema_version: Literal["diagnosis-main-batch-result/v1"] = BATCH_RESULT_SCHEMA_VERSION
    status: Literal["offline_rehearsal_complete", "authorized_execution_terminalized"]
    execution_mode: ExecutionMode
    batch_binding_sha256: Sha256
    logical_request_count: int = Field(ge=1, le=1024)
    provider_backed_logical_request_count: int = Field(ge=0, le=896)
    deterministic_logical_request_count: int = Field(ge=0, le=128)
    expected_provider_turn_count: int = Field(ge=0, le=1408)
    completed_provider_turn_count: int = Field(ge=0, le=1408)
    terminal_status_counts: dict[str, int]
    terminal_ledger_sha256: Sha256
    result_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"result_sha256"})

    @model_validator(mode="after")
    def _result_reconciles(self) -> Self:
        if (
            any(value < 0 for value in self.terminal_status_counts.values())
            or sum(self.terminal_status_counts.values()) != self.logical_request_count
        ):
            raise ValueError("main batch terminal counts do not reconcile")
        expected_status = (
            "offline_rehearsal_complete"
            if self.execution_mode == "offline_rehearsal"
            else "authorized_execution_terminalized"
        )
        if self.status != expected_status:
            raise ValueError("main batch status differs from execution mode")
        if self.execution_mode == "offline_rehearsal" and (
            self.completed_provider_turn_count != self.expected_provider_turn_count
            or set(self.terminal_status_counts) - {"completed", "deterministic_completed"}
        ):
            raise ValueError("offline rehearsal did not complete every declared route")
        if self.result_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("main batch result identity does not match")
        return self


class DeterministicRehearsalClock:
    """Stable offline clock used only to make fake receipts reproducible."""

    def __init__(self) -> None:
        self._value = 0

    def now_ns(self) -> int:
        self._value += 1
        return self._value


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def _serialized(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _validate_authority(
    authority: MainExecutionAuthority,
    contract: MainRuntimeContract,
) -> None:
    expected_manifest = canonical_execution_sha256(
        {
            "analysis_census_sha256": contract.analysis_census_sha256,
            "analysis_plan_sha256": contract.analysis_plan_sha256,
            "response_contract_sha256": contract.response_contract_sha256,
            "runtime_contract_sha256": contract.runtime_contract_sha256,
        }
    )
    if (
        authority.analysis_census_sha256 != contract.analysis_census_sha256
        or authority.analysis_plan_sha256 != contract.analysis_plan_sha256
        or authority.response_contract_sha256 != contract.response_contract_sha256
        or authority.runtime_contract_sha256 != contract.runtime_contract_sha256
        or authority.manifest_content_sha256 != expected_manifest
    ):
        raise MainRuntimeError("execution authority is not bound to the runtime freeze")


def _validate_model_and_response_bindings(
    contract: MainRuntimeContract,
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    response_contract: dict[str, object],
) -> None:
    declared_response_hash = response_contract.get("contract_sha256")
    observed_response_hash = canonical_execution_sha256(
        {key: value for key, value in response_contract.items() if key != "contract_sha256"}
    )
    if (
        declared_response_hash != contract.response_contract_sha256
        or observed_response_hash != contract.response_contract_sha256
    ):
        raise MainRuntimeError("execution response contract differs from the runtime binding")
    policy = OpenAIGatewayPolicy.from_fairness_policy(fairness_freeze.model_policies["main_llm_v1"])
    expected_model_policy = {
        "provider": policy.provider,
        "api": policy.api,
        "model_family": policy.model,
        "model_snapshot": policy.model_version,
        "sdk_version": policy.sdk_version,
        "temperature": policy.temperature,
        "top_p": policy.top_p,
        "seed": policy.seed,
        "maximum_output_tokens": policy.max_output_tokens,
        "timeout_seconds": policy.timeout_seconds,
        "provider_attempt_ceiling": policy.provider_attempt_ceiling,
        "hidden_sdk_retries": policy.hidden_sdk_retries,
        "silent_model_or_endpoint_switch": policy.silent_provider_or_model_switch,
    }
    if contract.main_model_policy != expected_model_policy:
        raise MainRuntimeError("fairness model policy differs from runtime binding")


def _bind_store(store: MainRuntimeStore, binding: MainBatchBinding) -> None:
    path = store.root / "batch-binding.json"
    expected = _serialized(binding)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise MainRuntimeError("runtime store is bound to another execution batch")
        return
    if any(store.root.iterdir()):
        raise MainRuntimeError("non-empty runtime store has no immutable batch binding")
    publish_immutable_file(path, expected)


def _assert_store_scope(
    store: MainRuntimeStore,
    requests: Sequence[DiagnosisMainExpectedRequest],
) -> None:
    expected = {item.request_id: len(_ROUTE_TURNS[item.variant]) for item in requests}
    for path in store.root.iterdir():
        if path.name == "batch-binding.json":
            if path.is_symlink() or not path.is_file():
                raise MainRuntimeError("runtime batch binding is not a regular file")
            continue
        if path.name not in expected:
            raise MainRuntimeError("runtime store contains an unexpected entry")
        _assert_request_store_scope(path, expected[path.name])


def _assert_request_store_scope(path: Path, turn_count: int) -> None:
    if path.is_symlink() or not path.is_dir():
        raise MainRuntimeError("logical request store is not a real directory")
    allowed = {"terminal.json"} | {f"turn-{ordinal:02d}" for ordinal in range(1, turn_count + 1)}
    for child in path.iterdir():
        if child.name not in allowed or child.is_symlink():
            raise MainRuntimeError("logical request store contains an unexpected entry")
        if child.name == "terminal.json":
            if not child.is_file():
                raise MainRuntimeError("logical terminal is not a regular file")
            continue
        _assert_turn_store_scope(child)


def _assert_turn_store_scope(path: Path) -> None:
    if not path.is_dir():
        raise MainRuntimeError("logical turn store is not a real directory")
    for artifact in path.iterdir():
        if (
            artifact.name not in {"request.json", "result.json"}
            or artifact.is_symlink()
            or not artifact.is_file()
        ):
            raise MainRuntimeError("logical turn contains an unexpected artifact")


class DiagnosisMainOfflineAdapter:
    """Route-aware deterministic adapter with no network or environment access."""

    def __init__(self, fairness_freeze: DiagnosisVariantFairnessFreeze) -> None:
        policy = OpenAIGatewayPolicy.from_fairness_policy(
            fairness_freeze.model_policies["main_llm_v1"]
        )
        self._binding = ProviderBinding(
            provider_ref=_opaque({"provider": policy.provider}),
            model_ref=_opaque({"model": policy.model}),
            model_version_ref=_opaque({"model_version": policy.model_version}),
        )

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        context = json.loads(call.context_json)["payload"]
        evidence_ids = [item["evidence_id"] for item in context["items"]]
        if not evidence_ids:
            raise MainRuntimeError("offline rehearsal received an empty evidence projection")
        if "diagnosis-main-selection-output/v1" in call.response_schema_json:
            payload: dict[str, object] = {
                "schema_version": "diagnosis-main-selection-output/v1",
                "requested_evidence_ids": [evidence_ids[0]],
            }
        else:
            folded_prompt = call.prompt_text.casefold()
            citation_required = "cite" in folded_prompt and not any(
                marker in folded_prompt
                for marker in ("do not cite", "without evidence-id citations")
            )
            payload = {
                "schema_version": "diagnosis-main-provider-output/1",
                "output_status": "completed",
                "atomic_claims": [
                    {
                        "claim_local_id": "claim-1",
                        "claim_type": "evidence_statement",
                        "claim_text": "The visible record contains an observed measurement.",
                        "material_parts": [
                            {
                                "part_id": "part-1",
                                "text": ("The visible record contains an observed measurement."),
                            }
                        ],
                        "visible_evidence_ids": ([evidence_ids[0]] if citation_required else []),
                    }
                ],
                "abstention_reason": None,
            }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_opaque(
                {
                    "offline_rehearsal_request": call.request_identity_sha256,
                    "attempt": call.attempt_ordinal,
                }
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _build_rehearsal_authority(
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
) -> MainExecutionAuthority:
    manifest_sha256 = canonical_execution_sha256(
        {
            "analysis_census_sha256": contract.analysis_census_sha256,
            "analysis_plan_sha256": contract.analysis_plan_sha256,
            "response_contract_sha256": contract.response_contract_sha256,
            "runtime_contract_sha256": contract.runtime_contract_sha256,
        }
    )
    payload = {
        "schema_version": "diagnosis-main-execution-authority/v1",
        "execution_authorized": True,
        "source_commit_ref": _REHEARSAL_SOURCE_COMMIT,
        "analysis_census_sha256": contract.analysis_census_sha256,
        "analysis_plan_sha256": contract.analysis_plan_sha256,
        "response_contract_sha256": contract.response_contract_sha256,
        "runtime_contract_sha256": contract.runtime_contract_sha256,
        "manifest_content_sha256": manifest_sha256,
        "authorization_ref": _opaque(
            {
                "role": "offline_rehearsal_only",
                "private_packet_sha256": packet.packet_sha256,
                "runtime_contract_sha256": contract.runtime_contract_sha256,
            }
        ),
        "authorized_at": "1970-01-01T00:00:00Z",
    }
    return MainExecutionAuthority.model_validate(
        {**payload, "authority_sha256": canonical_execution_sha256(payload)}
    )


def _selected_requests(
    packet: DiagnosisMainPrivateCensusPacket,
    request_ids: Sequence[str] | None,
) -> tuple[DiagnosisMainExpectedRequest, ...]:
    requests = packet.analysis_census.requests
    if request_ids is None:
        return requests
    selected_ids = tuple(request_ids)
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise MainRuntimeError("rehearsal request selection must be non-empty and unique")
    selected_set = set(selected_ids)
    selected = tuple(item for item in requests if item.request_id in selected_set)
    if len(selected) != len(selected_ids):
        raise MainRuntimeError("rehearsal request selection contains an unknown request")
    return selected


def _batch_binding(
    *,
    execution_mode: ExecutionMode,
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    authority: MainExecutionAuthority,
    requests: Sequence[DiagnosisMainExpectedRequest],
) -> MainBatchBinding:
    request_payload = tuple(
        {
            "request_id": item.request_id,
            "request_sha256": item.request_sha256,
        }
        for item in requests
    )
    payload = {
        "schema_version": BATCH_BINDING_SCHEMA_VERSION,
        "execution_mode": execution_mode,
        "private_packet_sha256": packet.packet_sha256,
        "analysis_census_sha256": packet.analysis_census.census_sha256,
        "runtime_contract_sha256": contract.runtime_contract_sha256,
        "response_contract_sha256": contract.response_contract_sha256,
        "fairness_freeze_sha256": canonical_execution_sha256(
            fairness_freeze.model_dump(mode="json")
        ),
        "authority_sha256": authority.authority_sha256,
        "ordered_requests_sha256": canonical_execution_sha256(request_payload),
        "logical_request_count": len(request_payload),
    }
    return MainBatchBinding.model_validate(
        {**payload, "binding_sha256": canonical_execution_sha256(payload)}
    )


def _run_batch(
    *,
    execution_mode: ExecutionMode,
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
    authority: MainExecutionAuthority,
    response_contract: dict[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
    request_ids: Sequence[str] | None,
) -> MainBatchResult:
    _validate_authority(authority, contract)
    build_main_runtime_preflight(packet, contract)
    if packet.analysis_census.census_sha256 != contract.analysis_census_sha256:
        raise MainRuntimeError("execution packet differs from the runtime census")
    _validate_model_and_response_bindings(contract, fairness_freeze, response_contract)
    requests = _selected_requests(packet, request_ids)
    if execution_mode == "authorized_execution" and len(requests) != 1024:
        raise MainRuntimeError("authorized execution requires the complete frozen census")
    if execution_mode == "offline_rehearsal" and not isinstance(
        adapter, DiagnosisMainOfflineAdapter
    ):
        raise MainRuntimeError("offline rehearsal requires the network-incapable adapter")

    binding = _batch_binding(
        execution_mode=execution_mode,
        packet=packet,
        contract=contract,
        fairness_freeze=fairness_freeze,
        authority=authority,
        requests=requests,
    )
    _bind_store(store, binding)
    _assert_store_scope(store, requests)

    families = {item.family_id: item for item in packet.analysis_census.families}
    contexts = {item.context_id: item for item in packet.analysis_census.contexts}
    visible = {item.context_id: item for item in packet.visible_contexts}
    terminals: list[MainLogicalTerminal] = []
    for logical in requests:
        logical_context = contexts[logical.context_id]
        terminals.append(
            run_main_logical_request(
                logical=logical,
                logical_context=logical_context,
                family=families[logical_context.case_family_id],
                visible_context=visible[logical.context_id],
                contract=contract,
                authority=authority,
                response_contract=response_contract,
                fairness_freeze=fairness_freeze,
                store=store,
                adapter=adapter,
                clock=clock,
            )
        )
    _assert_store_scope(store, requests)

    ledger = tuple(
        {
            "request_id": terminal.logical_request_id,
            "terminal_sha256": terminal.terminal_sha256,
        }
        for terminal in terminals
    )
    status_counts = dict(sorted(Counter(item.status for item in terminals).items()))
    provider_requests = sum(item.variant != "B0" for item in requests)
    deterministic_requests = len(requests) - provider_requests
    payload = {
        "schema_version": BATCH_RESULT_SCHEMA_VERSION,
        "status": (
            "offline_rehearsal_complete"
            if execution_mode == "offline_rehearsal"
            else "authorized_execution_terminalized"
        ),
        "execution_mode": execution_mode,
        "batch_binding_sha256": binding.binding_sha256,
        "logical_request_count": len(requests),
        "provider_backed_logical_request_count": provider_requests,
        "deterministic_logical_request_count": deterministic_requests,
        "expected_provider_turn_count": sum(len(_ROUTE_TURNS[item.variant]) for item in requests),
        "completed_provider_turn_count": sum(
            item.completed_provider_turn_count for item in terminals
        ),
        "terminal_status_counts": status_counts,
        "terminal_ledger_sha256": canonical_execution_sha256(ledger),
    }
    return MainBatchResult.model_validate(
        {**payload, "result_sha256": canonical_execution_sha256(payload)}
    )


def rehearse_main_execution(
    *,
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
    response_contract: dict[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    request_ids: Sequence[str] | None = None,
) -> MainBatchResult:
    """Run a full or explicitly bounded fake rehearsal with zero external I/O."""

    return _run_batch(
        execution_mode="offline_rehearsal",
        packet=packet,
        contract=contract,
        authority=_build_rehearsal_authority(packet, contract),
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        store=store,
        adapter=DiagnosisMainOfflineAdapter(fairness_freeze),
        clock=DeterministicRehearsalClock(),
        request_ids=request_ids,
    )


def run_authorized_main_execution(
    *,
    packet: DiagnosisMainPrivateCensusPacket,
    contract: MainRuntimeContract,
    authority: MainExecutionAuthority,
    response_contract: dict[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    store: MainRuntimeStore,
    adapter: ProviderAdapter,
    clock: Clock,
) -> MainBatchResult:
    """Terminalize the exact 1,024-request census under external authority."""

    if authority.source_commit_ref == _REHEARSAL_SOURCE_COMMIT:
        raise MainRuntimeError("offline rehearsal authority cannot authorize main execution")
    return _run_batch(
        execution_mode="authorized_execution",
        packet=packet,
        contract=contract,
        authority=authority,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
        store=store,
        adapter=adapter,
        clock=clock,
        request_ids=None,
    )


__all__ = [
    "DiagnosisMainOfflineAdapter",
    "MainBatchBinding",
    "MainBatchResult",
    "rehearse_main_execution",
    "run_authorized_main_execution",
]
