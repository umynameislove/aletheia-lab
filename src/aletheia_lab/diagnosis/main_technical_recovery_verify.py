"""Read-only verification of a private diagnosis main technical recovery."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from aletheia_lab.diagnosis._main_runtime_contracts import MainLogicalTerminal
from aletheia_lab.diagnosis._main_runtime_store import MainRuntimeStore
from aletheia_lab.diagnosis.main_execution import (
    MainBatchBinding,
    MainBatchResult,
    _assert_store_scope,
)
from aletheia_lab.diagnosis.main_pipeline import load_private_main_packet
from aletheia_lab.diagnosis.main_runtime import load_main_runtime_inputs
from aletheia_lab.diagnosis.main_schema_smoke import (
    FORMAT_ONLY_REPAIR_SHA256,
    _original_schema_or_boundary_repair,
    _reject_duplicate_keys,
    main_provider_wire_schema,
    validate_self_hash,
)
from aletheia_lab.diagnosis.main_technical_recovery import (
    MainTechnicalRecoveryError,
    _checked_plan,
    _pilot_requests,
    _read_object,
    _tree_sha256,
    authority_from_plan,
    build_recovery_plan,
    checked_recovery_paths,
)
from aletheia_lab.evaluation.diagnosis_main_census import DiagnosisMainPrivateCensusPacket
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    load_main_scoring_contract,
    prepare_main_scoring,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import GatewayExecutionResult, GatewayRequest
from aletheia_lab.model_gateway.contracts import GatewayContractError
from aletheia_lab.model_gateway.schema import validate_response_payload
from aletheia_lab.project.identity import canonical_project_json, content_sha256


def _pilot_statuses(
    recovery: Path, packet: DiagnosisMainPrivateCensusPacket, count: int
) -> dict[str, int]:
    pilot = _pilot_requests(packet)[:count]
    store = recovery / "main-store"
    if (
        store.is_symlink()
        or not store.is_dir()
        or {item.name for item in store.iterdir()}
        != {"batch-binding.json", *(item.request_id for item in pilot)}
    ):
        raise MainTechnicalRecoveryError("stopped pilot store membership differs")
    _assert_store_scope(MainRuntimeStore(store), pilot)
    statuses: list[str] = []
    for logical in pilot:
        terminal = MainLogicalTerminal.model_validate_json(
            (store / logical.request_id / "terminal.json").read_bytes()
        )
        if terminal.logical_request_sha256 != logical.request_sha256:
            raise MainTechnicalRecoveryError("stopped pilot terminal identity differs")
        statuses.append(terminal.status)
    if statuses[:-1] != ["completed"] * (count - 1) or statuses[-1] == "completed":
        raise MainTechnicalRecoveryError("stopped pilot order differs")
    return dict(sorted(Counter(statuses).items()))


def _verify_lease(lease: dict[str, object], plan: dict[str, object], authority_sha256: str) -> None:
    validate_self_hash(lease, "lease_sha256")
    if (
        lease.get("schema_version") != "diagnosis-main-technical-recovery-lease/v1"
        or lease.get("plan_sha256") != plan["plan_sha256"]
        or lease.get("authority_sha256") != authority_sha256
        or lease.get("destination_sha256") != plan["destination_sha256"]
        or lease.get("one_execution_only") is not True
    ):
        raise MainTechnicalRecoveryError("recovery lease differs from the plan")


def _verify_pilot(
    root: Path, recovery: Path, plan: dict[str, object], packet: DiagnosisMainPrivateCensusPacket
) -> dict[str, object]:
    if (recovery / "receipt.json").exists() or (recovery / "main-batch-result.json").exists():
        raise MainTechnicalRecoveryError("stopped pilot cannot have a complete recovery")
    stopped = _read_object(recovery / "pilot-stop.json")
    lease = _read_object(recovery / "lease.json")
    validate_self_hash(stopped, "pilot_stop_sha256")
    runtime, _, _ = load_main_runtime_inputs(root)
    _verify_lease(lease, plan, authority_from_plan(plan, runtime).authority_sha256)
    count = stopped.get("completed_pilot_request_count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise MainTechnicalRecoveryError("stopped pilot count is invalid")
    if (
        stopped.get("status") != "pilot_failed_closed"
        or stopped.get("plan_sha256") != plan["plan_sha256"]
        or stopped.get("lease_sha256") != lease["lease_sha256"]
        or stopped.get("pilot_request_ids_sha256") != plan["pilot_request_ids_sha256"]
        or stopped.get("original_registered_attempt_mutated") is not False
        or not 1 <= count <= 7
        or stopped.get("terminal_status_counts") != _pilot_statuses(recovery, packet, count)
    ):
        raise MainTechnicalRecoveryError("stopped pilot evidence does not reconcile")
    return stopped


def _stored_provider_responses(recovery: Path) -> dict[str, tuple[bytes, str]]:
    responses: dict[str, tuple[bytes, str]] = {}
    for path in (recovery / "main-store").glob("dmr-*/turn-*/result.json"):
        result = GatewayExecutionResult.model_validate_json(path.read_bytes())
        if result.raw_response is None:
            continue
        request = GatewayRequest.model_validate_json(path.with_name("request.json").read_bytes())
        if request.initial_attempt.request_identity_sha256 != result.request_identity_sha256:
            raise MainTechnicalRecoveryError("stored provider turn identity differs")
        attempt_id = result.attempts[-1].attempt.attempt_id
        if attempt_id in responses:
            raise MainTechnicalRecoveryError("stored provider attempt is duplicated")
        responses[attempt_id] = (result.raw_response.content, request.response_schema_json)
    return responses


def _verify_repair_content(
    *, original: bytes, accepted: bytes, schema_json: str, record: dict[str, object]
) -> None:
    try:
        parsed = json.loads(original.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        schema = json.loads(schema_json)
        if not isinstance(parsed, dict) or not isinstance(schema, dict):
            raise ValueError("repair JSON shape differs")
        validate_response_payload(parsed, main_provider_wire_schema(schema_json))
        repaired, fields = _original_schema_or_boundary_repair(parsed, schema)
    except (UnicodeError, json.JSONDecodeError, GatewayContractError, ValueError) as exc:
        raise MainTechnicalRecoveryError("format repair cannot be reproduced") from exc
    if (
        not fields
        or accepted != canonical_project_json(repaired).encode("utf-8")
        or record.get("repaired_field_paths") != list(fields)
        or record.get("accepted_raw_sha256") != content_sha256(accepted)
        or record.get("accepted_payload_sha256") != canonical_execution_sha256(repaired)
    ):
        raise MainTechnicalRecoveryError("format repair differs from stored provider result")


def _verify_repair_records(recovery: Path, receipt: dict[str, object]) -> None:
    root = recovery / "repair-records"
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise MainTechnicalRecoveryError("format repair store is not a real directory")
    records = sorted(root.glob("*.json")) if root.exists() else []
    if root.exists() and {item.name for item in root.iterdir()} != {
        name for item in records for name in (item.name, item.with_suffix(".raw").name)
    }:
        raise MainTechnicalRecoveryError("format repair store contains unexpected artifacts")
    stored = _stored_provider_responses(recovery) if records else {}
    for item in records:
        record = _read_object(item)
        raw_path = item.with_suffix(".raw")
        if (
            record.get("schema_version") != "diagnosis-main-format-repair-record/v1"
            or record.get("attempt_id") != item.stem
            or record.get("policy_sha256") != FORMAT_ONLY_REPAIR_SHA256
            or raw_path.is_symlink()
            or not raw_path.is_file()
            or content_sha256(raw_path.read_bytes()) != record.get("original_raw_sha256")
            or not isinstance(record.get("repaired_field_paths"), list)
            or not record["repaired_field_paths"]
        ):
            raise MainTechnicalRecoveryError("format repair evidence differs")
        if item.stem not in stored:
            raise MainTechnicalRecoveryError("format repair has no stored provider result")
        accepted, schema_json = stored[item.stem]
        _verify_repair_content(
            original=raw_path.read_bytes(),
            accepted=accepted,
            schema_json=schema_json,
            record=record,
        )
    inventory = canonical_execution_sha256(
        tuple({"name": item.name, "sha256": content_sha256(item.read_bytes())} for item in records)
    )
    if inventory != receipt.get("format_repair_records_sha256") or len(records) != receipt.get(
        "format_repaired_attempt_count"
    ):
        raise MainTechnicalRecoveryError("format repair inventory differs")


def _verify_terminal_ledger(
    recovery: Path,
    predecessor: Path,
    packet: DiagnosisMainPrivateCensusPacket,
    batch: MainBatchResult,
) -> None:
    store = recovery / "main-store"
    if store.is_symlink() or not store.is_dir():
        raise MainTechnicalRecoveryError("recovery runtime store is unavailable")
    _assert_store_scope(MainRuntimeStore(store), packet.analysis_census.requests)
    ledger: list[dict[str, str]] = []
    statuses: Counter[str] = Counter()
    for logical in packet.analysis_census.requests:
        target = MainLogicalTerminal.model_validate_json(
            (store / logical.request_id / "terminal.json").read_bytes()
        )
        if (
            target.logical_request_id != logical.request_id
            or target.logical_request_sha256 != logical.request_sha256
            or target.variant != logical.variant
        ):
            raise MainTechnicalRecoveryError("recovered terminal identity differs")
        ledger.append({"request_id": logical.request_id, "terminal_sha256": target.terminal_sha256})
        statuses[target.status] += 1
        if logical.variant == "B0":
            source = MainLogicalTerminal.model_validate_json(
                (predecessor / "main-store" / logical.request_id / "terminal.json").read_bytes()
            )
            if source.terminal_sha256 != target.terminal_sha256:
                raise MainTechnicalRecoveryError("deterministic baseline changed in recovery")
    if batch.terminal_ledger_sha256 != canonical_execution_sha256(
        tuple(ledger)
    ) or batch.terminal_status_counts != dict(sorted(statuses.items())):
        raise MainTechnicalRecoveryError("recovered terminal ledger differs")


def _verify_complete(
    root: Path,
    recovery: Path,
    predecessor: Path,
    plan: dict[str, object],
    packet: DiagnosisMainPrivateCensusPacket,
) -> dict[str, object]:
    receipt = _read_object(recovery / "receipt.json")
    lease = _read_object(recovery / "lease.json")
    validate_self_hash(receipt, "receipt_sha256")
    batch = MainBatchResult.model_validate_json((recovery / "main-batch-result.json").read_bytes())
    binding = MainBatchBinding.model_validate_json(
        (recovery / "main-store/batch-binding.json").read_bytes()
    )
    runtime, fairness, response = load_main_runtime_inputs(root)
    authority = authority_from_plan(plan, runtime)
    _verify_lease(lease, plan, authority.authority_sha256)
    if (
        receipt.get("schema_version") != "diagnosis-main-technical-recovery-receipt/v1"
        or receipt.get("status") != "technical_recovery_terminalized"
        or receipt.get("plan_sha256") != plan["plan_sha256"]
        or receipt.get("lease_sha256") != lease["lease_sha256"]
        or receipt.get("batch_result_sha256") != batch.result_sha256
        or batch.batch_binding_sha256 != binding.binding_sha256
        or binding.authority_sha256 != authority.authority_sha256
        or binding.private_packet_sha256 != packet.packet_sha256
        or batch.logical_request_count != 1024
        or batch.provider_backed_logical_request_count != 896
        or batch.deterministic_logical_request_count != 128
        or receipt.get("terminal_status_counts") != batch.terminal_status_counts
        or receipt.get("predecessor_tree_sha256") != _tree_sha256(predecessor)
        or receipt.get("original_registered_attempt_mutated") is not False
        or receipt.get("relation_scoring_executed") is not False
    ):
        raise MainTechnicalRecoveryError("recovery receipt does not reconcile")
    _verify_terminal_ledger(recovery, predecessor, packet, batch)
    _verify_repair_records(recovery, receipt)
    scoring = load_main_scoring_contract(root, runtime_contract=runtime, response_contract=response)
    prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        scoring_contract=scoring,
        batch_result=batch,
        store_root=recovery / "main-store",
    )
    return receipt


def verify_recovery(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
) -> dict[str, object]:
    """Verify a prepared, pilot-stopped, or fully terminalized recovery."""

    packet_path, predecessor, smoke, recovery = checked_recovery_paths(
        root, packet_path, predecessor, smoke, recovery
    )
    plan, packet = _checked_plan(
        root=root, packet_path=packet_path, predecessor=predecessor, smoke=smoke, recovery=recovery
    )
    if (recovery / "pilot-stop.json").exists():
        return _verify_pilot(root, recovery, plan, packet)
    if (recovery / "receipt.json").exists():
        return _verify_complete(root, recovery, predecessor, plan, packet)
    if (recovery / "lease.json").exists():
        raise MainTechnicalRecoveryError("execution is incomplete; automatic replay is forbidden")
    return plan


def verify_sealed_recovery_from_pinned_source(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
) -> dict[str, object]:
    """Read a completed recovery after later, forward-only code commits.

    The original execution verifier deliberately requires its execution commit
    to be HEAD.  Downstream scoring must instead verify that same sealed plan
    against its *recorded* commit, without reopening the recovery for execution.
    """

    packet_path, predecessor, smoke, recovery = checked_recovery_paths(
        root, packet_path, predecessor, smoke, recovery
    )
    plan = _read_object(recovery / "plan.json")
    validate_self_hash(plan, "plan_sha256")
    ceiling = plan.get("operator_cost_ceiling_usd")
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)):
        raise MainTechnicalRecoveryError("sealed recovery cost ceiling is invalid")
    expected = build_recovery_plan(
        root=root,
        packet_path=packet_path,
        predecessor=predecessor,
        smoke=smoke,
        recovery=recovery,
        source_commit_ref=str(plan.get("source_commit_ref")),
        created_at=str(plan.get("created_at")),
        cost_ceiling_usd=float(ceiling),
    )
    if plan != expected or not (recovery / "receipt.json").is_file():
        raise MainTechnicalRecoveryError("sealed recovery plan or receipt differs")
    packet = load_private_main_packet(root, packet_path)
    return _verify_complete(root, recovery, predecessor, plan, packet)


__all__ = ["verify_recovery", "verify_sealed_recovery_from_pinned_source"]
