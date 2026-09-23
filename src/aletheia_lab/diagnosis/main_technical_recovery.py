"""Separate, outcome-blind recovery of the 896 failed diagnosis main requests.

This module never edits the registered run.  The original attempt remains a
technical failure; a later run is identified as a forward recovery, even when
its complete 1,024-row store can be passed to the frozen scoring code.
"""

from __future__ import annotations

import json
import os
import stat
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from aletheia_lab.diagnosis._main_pipeline_budget import (
    BudgetedProviderAdapter,
    SharedProviderBudget,
)
from aletheia_lab.diagnosis._main_pipeline_contracts import DiagnosisMainPipelineAuthorization
from aletheia_lab.diagnosis._main_runtime_contracts import (
    _ROUTE_TURNS,
    MainExecutionAuthority,
    MainLogicalTerminal,
    MainRuntimeContract,
)
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_execution import (
    MainBatchBinding,
    MainBatchResult,
    _assert_store_scope,
    _batch_binding,
    _bind_store,
    run_authorized_main_execution,
)
from aletheia_lab.diagnosis.main_pipeline import load_private_main_packet, verify_completed_pipeline
from aletheia_lab.diagnosis.main_runtime import (
    build_main_adapter_model_policy,
    load_main_runtime_inputs,
    run_main_logical_request,
)
from aletheia_lab.diagnosis.main_schema_smoke import (
    EXPECTED_FAILED_MAIN_COUNTS,
    FORMAT_ONLY_REPAIR_SHA256,
    MAIN_RECOVERY_SCHEMA_VERSION,
    MAIN_RECOVERY_TRANSPORT_V2_SHA256,
    OpenAIMainRecoveryAdapter,
    _original_schema_or_boundary_repair,
    _reject_duplicate_keys,
    main_provider_wire_schema,
    validate_self_hash,
)
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_live import SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.diagnosis_main_analysis import DiagnosisMainExpectedRequest
from aletheia_lab.evaluation.diagnosis_main_census import DiagnosisMainPrivateCensusPacket
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    OpenAIGatewayPolicy,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
)
from aletheia_lab.model_gateway.contracts import GatewayContractError
from aletheia_lab.model_gateway.schema import validate_response_payload
from aletheia_lab.project.identity import canonical_project_json, content_sha256

PLAN_VERSION = "diagnosis-main-technical-recovery-plan/v1"
RECEIPT_VERSION = "diagnosis-main-technical-recovery-receipt/v1"
PASSING_SMOKE_RECEIPT_SHA256 = "62d8bc0c8e596df9fb0717bad9052a34b74a65b422c29e7633c062b7490e2fc4"
_ALLOWED_MEMBERS = {
    "plan.json",
    "lease.json",
    "main-store",
    "pilot-stop.json",
    "main-batch-result.json",
    "repair-records",
    "receipt.json",
}
_PILOT_VARIANTS = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")


def _permissions_expose_others(path: Path) -> bool:
    return os.name != "nt" and bool(stat.S_IMODE(path.stat().st_mode) & 0o077)


class MainTechnicalRecoveryError(ValueError):
    """Safe error at the recovery boundary; never contains provider text."""


def _read_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise MainTechnicalRecoveryError(f"required artifact is unavailable: {path.name}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MainTechnicalRecoveryError(f"required artifact is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise MainTechnicalRecoveryError(f"required artifact is not an object: {path.name}")
    return value


def _publish(path: Path, value: Mapping[str, object]) -> None:
    payload = (canonical_project_json(dict(value)) + "\n").encode("utf-8")
    if publish_immutable_file(path, payload) != "created":
        raise MainTechnicalRecoveryError(f"immutable artifact already exists: {path.name}")


def _tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise MainTechnicalRecoveryError("preserved run is not a real directory")
    inventory: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise MainTechnicalRecoveryError("preserved run contains a symbolic link")
        if path.is_file():
            inventory.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": content_sha256(path.read_bytes()),
                }
            )
    return canonical_execution_sha256(inventory)


def checked_recovery_paths(
    root: Path, packet: Path, predecessor: Path, smoke: Path, recovery: Path
) -> tuple[Path, Path, Path, Path]:
    paths = tuple(
        checked_private_path(item.expanduser(), root)
        for item in (packet, predecessor, smoke, recovery)
    )
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise MainTechnicalRecoveryError("private recovery paths overlap")
    if recovery.exists() and (
        recovery.is_symlink()
        or not recovery.is_dir()
        or _permissions_expose_others(recovery)
        or any(
            item.name not in _ALLOWED_MEMBERS or item.is_symlink() for item in recovery.iterdir()
        )
    ):
        raise MainTechnicalRecoveryError("recovery destination contains an unsafe artifact")
    return cast(tuple[Path, Path, Path, Path], paths)


def _predecessor_facts(
    predecessor: Path, packet: DiagnosisMainPrivateCensusPacket
) -> tuple[str, str, str, str]:
    authorization = DiagnosisMainPipelineAuthorization.model_validate_json(
        (predecessor / "authorization.json").read_bytes()
    )
    receipt = verify_completed_pipeline(run_dir=predecessor, authorization=authorization)
    batch = MainBatchResult.model_validate_json(
        (predecessor / "main-batch-result.json").read_bytes()
    )
    binding = MainBatchBinding.model_validate_json(
        (predecessor / "main-store/batch-binding.json").read_bytes()
    )
    if (
        receipt.main_terminal_status_counts != EXPECTED_FAILED_MAIN_COUNTS
        or receipt.relation_request_count != 0
        or batch.batch_binding_sha256 != binding.binding_sha256
        or binding.private_packet_sha256 != packet.packet_sha256
        or binding.analysis_census_sha256 != packet.analysis_census.census_sha256
        or receipt.logical_request_count != 1024
    ):
        raise MainTechnicalRecoveryError("predecessor is not the sealed 896-failure run")
    failed: list[dict[str, str]] = []
    deterministic: list[dict[str, str]] = []
    ledger: list[dict[str, str]] = []
    request_ids = {item.request_id for item in packet.analysis_census.requests}
    store = predecessor / "main-store"
    if store.is_symlink() or not store.is_dir():
        raise MainTechnicalRecoveryError("predecessor runtime store is unavailable")
    _assert_store_scope(MainRuntimeStore(store), packet.analysis_census.requests)
    if {item.name for item in store.iterdir()} != {"batch-binding.json", *request_ids}:
        raise MainTechnicalRecoveryError("predecessor store census differs")
    for logical in packet.analysis_census.requests:
        terminal = MainLogicalTerminal.model_validate_json(
            (store / logical.request_id / "terminal.json").read_bytes()
        )
        if (
            terminal.logical_request_id != logical.request_id
            or terminal.logical_request_sha256 != logical.request_sha256
        ):
            raise MainTechnicalRecoveryError("predecessor terminal identity differs")
        entry = {"request_id": logical.request_id, "terminal_sha256": terminal.terminal_sha256}
        ledger.append(entry)
        if logical.variant == "B0" and terminal.status == "deterministic_completed":
            deterministic.append(entry)
        elif logical.variant != "B0" and terminal.status == "technical_failure":
            failed.append(
                {"request_id": logical.request_id, "request_sha256": logical.request_sha256}
            )
        else:
            raise MainTechnicalRecoveryError(
                "predecessor failure selection differs from frozen census"
            )
    if (
        len(failed) != 896
        or len(deterministic) != 128
        or batch.terminal_ledger_sha256 != canonical_execution_sha256(tuple(ledger))
    ):
        raise MainTechnicalRecoveryError("predecessor terminal ledger differs")
    return (
        receipt.receipt_sha256,
        _tree_sha256(predecessor),
        canonical_execution_sha256(tuple(failed)),
        canonical_execution_sha256(tuple(deterministic)),
    )


def _smoke_facts(smoke: Path, predecessor_receipt: str, predecessor_tree: str) -> str:
    if (
        smoke.is_symlink()
        or not smoke.is_dir()
        or {item.name for item in smoke.iterdir()} != {"plan.json", "lease.json", "receipt.json"}
    ):
        raise MainTechnicalRecoveryError("passing smoke artifact set is incomplete")
    plan, lease, receipt = (
        _read_object(smoke / name) for name in ("plan.json", "lease.json", "receipt.json")
    )
    for artifact, field in (
        (plan, "plan_sha256"),
        (lease, "lease_sha256"),
        (receipt, "receipt_sha256"),
    ):
        validate_self_hash(artifact, field)
    if (
        receipt["receipt_sha256"] != PASSING_SMOKE_RECEIPT_SHA256
        or receipt.get("status") != "pass"
        or receipt.get("provider_call_count") != 1
        or receipt.get("sdk_retries") != 0
        or receipt.get("format_repaired_fields") != []
        or receipt.get("recovery_authorized") is not False
        or receipt.get("predecessor_mutated") is not False
        or receipt.get("plan_sha256") != plan["plan_sha256"]
        or receipt.get("lease_sha256") != lease["lease_sha256"]
        or plan.get("predecessor_receipt_sha256") != predecessor_receipt
        or plan.get("predecessor_tree_sha256") != predecessor_tree
        or plan.get("transport_sha256") != MAIN_RECOVERY_TRANSPORT_V2_SHA256
        or plan.get("format_only_repair_policy_sha256") != FORMAT_ONLY_REPAIR_SHA256
    ):
        raise MainTechnicalRecoveryError("smoke does not authorize the declared technical boundary")
    return str(receipt["receipt_sha256"])


def _clean_main_commit(root: Path) -> str:
    state = inspect_repository_state(root)
    if not state.synchronized_main:
        raise MainTechnicalRecoveryError("recovery requires clean synchronized main")
    return state.head_commit


def build_recovery_plan(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
    source_commit_ref: str,
    created_at: str,
    cost_ceiling_usd: float,
) -> dict[str, object]:
    """Bind the exact failed census without opening response or analysis content."""

    packet_path, predecessor, smoke, recovery = checked_recovery_paths(
        root, packet_path, predecessor, smoke, recovery
    )
    if (
        not 0 < cost_ceiling_usd <= 175
        or len(source_commit_ref) != 40
        or any(character not in "0123456789abcdef" for character in source_commit_ref)
    ):
        raise MainTechnicalRecoveryError("source commit or operator cost ceiling is invalid")
    packet = load_private_main_packet(root, packet_path)
    runtime, fairness, response = load_main_runtime_inputs(root)
    if (
        packet.analysis_census.census_sha256 != runtime.analysis_census_sha256
        or response.get("contract_sha256") != runtime.response_contract_sha256
    ):
        raise MainTechnicalRecoveryError("recovery differs from frozen main contracts")
    predecessor_receipt, predecessor_tree, failed_ids_sha, deterministic_sha = _predecessor_facts(
        predecessor, packet
    )
    smoke_receipt = _smoke_facts(smoke, predecessor_receipt, predecessor_tree)
    pilot = _pilot_requests(packet)
    payload: dict[str, object] = {
        "schema_version": PLAN_VERSION,
        "purpose": "forward_technical_recovery_not_registered_rerun",
        "source_commit_ref": source_commit_ref,
        "created_at": created_at,
        "destination_sha256": canonical_execution_sha256(
            {"private_recovery_dir": recovery.as_posix()}
        ),
        "private_packet_sha256": packet.packet_sha256,
        "analysis_census_sha256": packet.analysis_census.census_sha256,
        "runtime_contract_sha256": runtime.runtime_contract_sha256,
        "response_contract_sha256": runtime.response_contract_sha256,
        "fairness_freeze_sha256": canonical_execution_sha256(fairness.model_dump(mode="json")),
        "predecessor_receipt_sha256": predecessor_receipt,
        "predecessor_tree_sha256": predecessor_tree,
        "passing_smoke_receipt_sha256": smoke_receipt,
        "failed_request_ids_sha256": failed_ids_sha,
        "deterministic_terminal_ledger_sha256": deterministic_sha,
        "pilot_request_ids_sha256": canonical_execution_sha256(
            tuple(item.request_id for item in pilot)
        ),
        "pilot_provider_request_count": len(pilot),
        "pilot_continue_rule": "all_seven_logical_requests_completed",
        "logical_request_count": 1024,
        "recovery_provider_request_count": 896,
        "deterministic_request_count": 128,
        "maximum_provider_turn_count": sum(128 * len(_ROUTE_TURNS[item]) for item in _ROUTE_TURNS),
        "maximum_provider_attempt_count": 2
        * sum(128 * len(_ROUTE_TURNS[item]) for item in _ROUTE_TURNS),
        "model_snapshot": "gpt-4.1-2025-04-14",
        "maximum_output_tokens_per_attempt": 600,
        "sdk_retries": 0,
        "transport_sha256": MAIN_RECOVERY_TRANSPORT_V2_SHA256,
        "format_only_repair_policy_sha256": FORMAT_ONLY_REPAIR_SHA256,
        "operator_cost_ceiling_usd": cost_ceiling_usd,
        "predecessor_mutation_permitted": False,
        "scientific_design_changed": False,
        "relation_scoring_included": False,
    }
    return {**payload, "plan_sha256": canonical_execution_sha256(payload)}


def _pilot_requests(
    packet: DiagnosisMainPrivateCensusPacket,
) -> tuple[DiagnosisMainExpectedRequest, ...]:
    selected = tuple(
        next(item for item in packet.analysis_census.requests if item.variant == variant)
        for variant in _PILOT_VARIANTS
    )
    if len({item.request_id for item in selected}) != len(_PILOT_VARIANTS):
        raise MainTechnicalRecoveryError("pilot route coverage differs")
    return selected


def authority_from_plan(
    plan: Mapping[str, object], runtime: MainRuntimeContract
) -> MainExecutionAuthority:
    payload = {
        "schema_version": "diagnosis-main-execution-authority/v1",
        "execution_authorized": True,
        "source_commit_ref": plan["source_commit_ref"],
        "analysis_census_sha256": plan["analysis_census_sha256"],
        "analysis_plan_sha256": runtime.analysis_plan_sha256,
        "response_contract_sha256": plan["response_contract_sha256"],
        "runtime_contract_sha256": plan["runtime_contract_sha256"],
        "manifest_content_sha256": canonical_execution_sha256(
            {
                "analysis_census_sha256": plan["analysis_census_sha256"],
                "analysis_plan_sha256": runtime.analysis_plan_sha256,
                "response_contract_sha256": plan["response_contract_sha256"],
                "runtime_contract_sha256": plan["runtime_contract_sha256"],
            }
        ),
        "authorization_ref": f"ev-{plan['plan_sha256']}",
        "authorized_at": plan["created_at"],
    }
    return MainExecutionAuthority.model_validate(
        {**payload, "authority_sha256": canonical_execution_sha256(payload)}
    )


class FormatOnlyMainAdapter:
    """Retain original provider bytes privately before substituting a permitted trim."""

    def __init__(self, delegate: ProviderAdapter, repair_root: Path) -> None:
        self._delegate = delegate
        self._repair_root = repair_root

    @property
    def binding(self) -> ProviderBinding:
        return self._delegate.binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        envelope = self._delegate.invoke(call)
        if f'"{MAIN_RECOVERY_SCHEMA_VERSION}"' not in call.response_schema_json:
            return envelope
        raw = envelope.raw_response.content
        try:
            parsed = json.loads(
                raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys
            )
            schema = json.loads(call.response_schema_json)
            if not isinstance(parsed, dict) or not isinstance(schema, dict):
                return envelope
            validate_response_payload(parsed, main_provider_wire_schema(call.response_schema_json))
            accepted, fields = _original_schema_or_boundary_repair(parsed, schema)
        except (UnicodeError, json.JSONDecodeError, GatewayContractError, ValueError):
            return envelope
        if not fields:
            return envelope
        accepted_raw = canonical_project_json(accepted).encode("utf-8")
        name = call.attempt_id
        self._repair_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self._repair_root.is_symlink() or _permissions_expose_others(self._repair_root):
            raise MainTechnicalRecoveryError("format repair store is unsafe")
        if publish_immutable_file(self._repair_root / f"{name}.raw", raw) != "created":
            raise MainTechnicalRecoveryError("format repair raw bytes already exist")
        record: dict[str, object] = {
            "schema_version": "diagnosis-main-format-repair-record/v1",
            "attempt_id": name,
            "original_raw_sha256": envelope.raw_response.content_sha256,
            "accepted_raw_sha256": content_sha256(accepted_raw),
            "accepted_payload_sha256": canonical_execution_sha256(accepted),
            "repaired_field_paths": list(fields),
            "policy_sha256": FORMAT_ONLY_REPAIR_SHA256,
        }
        _publish(self._repair_root / f"{name}.json", record)
        return ProviderEnvelope(
            request_identity_sha256=envelope.request_identity_sha256,
            binding=envelope.binding,
            provider_attempt_ref=envelope.provider_attempt_ref,
            response_mode=envelope.response_mode,
            raw_response=RawResponseArtifact.from_bytes(accepted_raw),
            usage=envelope.usage,
        )


def prepare_recovery(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
    cost_ceiling_usd: float,
) -> dict[str, object]:
    commit = _clean_main_commit(root)
    created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    plan = build_recovery_plan(
        root=root,
        packet_path=packet_path,
        predecessor=predecessor,
        smoke=smoke,
        recovery=recovery,
        source_commit_ref=commit,
        created_at=created_at,
        cost_ceiling_usd=cost_ceiling_usd,
    )
    if recovery.exists() and any(recovery.iterdir()):
        raise MainTechnicalRecoveryError("recovery destination must be absent or empty")
    recovery.mkdir(parents=True, mode=0o700, exist_ok=True)
    _publish(recovery / "plan.json", plan)
    return plan


def _checked_plan(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
) -> tuple[dict[str, object], DiagnosisMainPrivateCensusPacket]:
    plan = _read_object(recovery / "plan.json")
    validate_self_hash(plan, "plan_sha256")
    commit = _clean_main_commit(root)
    cost_ceiling = plan.get("operator_cost_ceiling_usd")
    if isinstance(cost_ceiling, bool) or not isinstance(cost_ceiling, (int, float)):
        raise MainTechnicalRecoveryError("recovery cost ceiling is invalid")
    expected = build_recovery_plan(
        root=root,
        packet_path=packet_path,
        predecessor=predecessor,
        smoke=smoke,
        recovery=recovery,
        source_commit_ref=commit,
        created_at=str(plan.get("created_at")),
        cost_ceiling_usd=float(cost_ceiling),
    )
    if plan != expected:
        raise MainTechnicalRecoveryError("recovery plan differs from current evidence or code")
    return plan, load_private_main_packet(root, packet_path)


def execute_recovery(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
    confirmed_plan_sha256: str,
) -> dict[str, object]:
    packet_path, predecessor, smoke, recovery = checked_recovery_paths(
        root, packet_path, predecessor, smoke, recovery
    )
    plan, packet = _checked_plan(
        root=root, packet_path=packet_path, predecessor=predecessor, smoke=smoke, recovery=recovery
    )
    if plan["plan_sha256"] != confirmed_plan_sha256:
        raise MainTechnicalRecoveryError("action-time recovery plan confirmation differs")
    if {item.name for item in recovery.iterdir()} != {"plan.json"}:
        raise MainTechnicalRecoveryError("recovery already started or contains unknown artifacts")
    runtime, fairness, response = load_main_runtime_inputs(root)
    authority = authority_from_plan(plan, runtime)
    policy = OpenAIGatewayPolicy.from_fairness_policy(fairness.model_policies["main_llm_v1"])
    delegate = OpenAIMainRecoveryAdapter.from_environment(
        model_policy=build_main_adapter_model_policy(authority, runtime, fairness), policy=policy
    )
    budget = SharedProviderBudget(cast(float, plan["operator_cost_ceiling_usd"]))
    lease = {
        "schema_version": "diagnosis-main-technical-recovery-lease/v1",
        "plan_sha256": plan["plan_sha256"],
        "authority_sha256": authority.authority_sha256,
        "destination_sha256": plan["destination_sha256"],
        "one_execution_only": True,
    }
    lease_sha256 = canonical_execution_sha256(lease)
    _publish(recovery / "lease.json", {**lease, "lease_sha256": lease_sha256})
    adapter = BudgetedProviderAdapter(
        FormatOnlyMainAdapter(delegate, recovery / "repair-records"), budget
    )
    store = MainRuntimeStore(recovery / "main-store")
    _bind_store(
        store,
        _batch_binding(
            execution_mode="authorized_execution",
            packet=packet,
            contract=runtime,
            fairness_freeze=fairness,
            authority=authority,
            requests=packet.analysis_census.requests,
        ),
    )
    families = {item.family_id: item for item in packet.analysis_census.families}
    contexts = {item.context_id: item for item in packet.analysis_census.contexts}
    visible = {item.context_id: item for item in packet.visible_contexts}
    clock = SystemMonotonicClock()
    pilot_terminals: list[MainLogicalTerminal] = []
    for logical in _pilot_requests(packet):
        logical_context = contexts[logical.context_id]
        pilot_terminals.append(
            run_main_logical_request(
                logical=logical,
                logical_context=logical_context,
                family=families[logical_context.case_family_id],
                visible_context=visible[logical.context_id],
                contract=runtime,
                authority=authority,
                response_contract=response,
                fairness_freeze=fairness,
                store=store,
                adapter=adapter,
                clock=clock,
            )
        )
        if pilot_terminals[-1].status != "completed":
            stopped: dict[str, object] = {
                "schema_version": "diagnosis-main-technical-recovery-pilot-stop/v1",
                "status": "pilot_failed_closed",
                "plan_sha256": plan["plan_sha256"],
                "lease_sha256": lease_sha256,
                "pilot_request_ids_sha256": plan["pilot_request_ids_sha256"],
                "completed_pilot_request_count": len(pilot_terminals),
                "terminal_status_counts": dict(
                    sorted(Counter(item.status for item in pilot_terminals).items())
                ),
                "provider_cost_committed_usd_at_frozen_rates": budget.committed_usd,
                "original_registered_attempt_mutated": False,
            }
            result = {**stopped, "pilot_stop_sha256": canonical_execution_sha256(stopped)}
            _publish(recovery / "pilot-stop.json", result)
            return result
    batch = run_authorized_main_execution(
        packet=packet,
        contract=runtime,
        authority=authority,
        response_contract=response,
        fairness_freeze=fairness,
        store=store,
        adapter=adapter,
        clock=clock,
    )
    _publish(recovery / "main-batch-result.json", batch.model_dump(mode="json"))
    repair_root = recovery / "repair-records"
    repairs = sorted(repair_root.glob("*.json")) if repair_root.exists() else []
    payload: dict[str, object] = {
        "schema_version": RECEIPT_VERSION,
        "status": "technical_recovery_terminalized",
        "plan_sha256": plan["plan_sha256"],
        "lease_sha256": lease_sha256,
        "predecessor_receipt_sha256": plan["predecessor_receipt_sha256"],
        "predecessor_tree_sha256": plan["predecessor_tree_sha256"],
        "batch_result_sha256": batch.result_sha256,
        "terminal_status_counts": batch.terminal_status_counts,
        "format_repaired_attempt_count": len(repairs),
        "format_repair_records_sha256": canonical_execution_sha256(
            tuple(
                {"name": item.name, "sha256": content_sha256(item.read_bytes())} for item in repairs
            )
        ),
        "operator_cost_ceiling_usd": plan["operator_cost_ceiling_usd"],
        "provider_cost_committed_usd_at_frozen_rates": budget.committed_usd,
        "budget_exhausted": budget.exhausted,
        "original_registered_attempt_mutated": False,
        "relation_scoring_executed": False,
    }
    receipt = {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    _publish(recovery / "receipt.json", receipt)
    return receipt


__all__ = [
    "FormatOnlyMainAdapter",
    "MainTechnicalRecoveryError",
    "authority_from_plan",
    "build_recovery_plan",
    "execute_recovery",
    "prepare_recovery",
]
