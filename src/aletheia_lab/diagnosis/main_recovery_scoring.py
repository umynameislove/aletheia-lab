"""Forward-only claim–evidence scoring of the sealed diagnosis recovery v2.

No function in this module calls the diagnosis executor.  The only provider
requests it can construct are blinded claim-relation assignments.
"""

from __future__ import annotations

import json
import os
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from aletheia_lab.diagnosis._main_pipeline_budget import (
    INPUT_USD_PER_MILLION_TOKENS,
    OUTPUT_USD_PER_MILLION_TOKENS,
    BudgetedProviderAdapter,
    SharedProviderBudget,
)
from aletheia_lab.diagnosis._main_pipeline_contracts import DiagnosisMainPipelineError
from aletheia_lab.diagnosis._main_pipeline_relations import (
    RehearsalRelationAuthorization,
    _read_result,
    _reader,
    build_pipeline_relation_requests,
    execute_pipeline_relation_stage,
    restore_relation_budget,
)
from aletheia_lab.diagnosis.main_execution import MainBatchResult
from aletheia_lab.diagnosis.main_pipeline import load_private_main_packet
from aletheia_lab.diagnosis.main_runtime import load_main_runtime_inputs
from aletheia_lab.diagnosis.main_schema_smoke import validate_self_hash
from aletheia_lab.diagnosis.main_technical_recovery import (
    _clean_main_commit,
    _read_object,
    checked_recovery_paths,
)
from aletheia_lab.diagnosis.main_technical_recovery_verify import (
    verify_sealed_recovery_from_pinned_source,
)
from aletheia_lab.evaluation.claim_corpus_live import SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.claim_evidence_semantics import load_evidence_semantics_policy
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    MINIMUM_PROVIDER_INTERVAL_MS,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PacedProviderAdapter,
    PreparedRelationRequest,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import DiagnosisMainAnalysisInput
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainRelationResults,
    DiagnosisMainScoringPreparation,
    load_main_scoring_contract,
    materialize_main_analysis_input,
    prepare_main_scoring,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    ClaimRelationProviderContext,
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayPolicy,
)
from aletheia_lab.project.identity import canonical_project_json

PLAN_VERSION = "diagnosis-main-recovery-relation-plan/v1"
RECEIPT_VERSION = "diagnosis-main-recovery-relation-receipt/v1"
RECOVERY_RECEIPT_SHA256 = "7e57c55a355015bdb4f6c51b8b4020339111332b50322ff96952183c96af92df"
SCORING_PREPARATION_SHA256 = "47238f44d8959a60e0f5c3b4dc56693691760ee354c8bcd473cda94bdec8189d"
RELATION_REQUEST_COUNT = 3181
OPERATOR_COST_CEILING_USD = 90.0
_ALLOWED_MEMBERS = {
    "plan.json",
    "lease.json",
    "relation-store",
    "relation-results.json",
    "analysis-input.json",
    "receipt.json",
}


class MainRecoveryScoringError(ValueError):
    """Safe failure at the forward scoring boundary."""


@dataclass(frozen=True)
class _Sources:
    root: Path
    packet_path: Path
    predecessor: Path
    smoke: Path
    recovery: Path
    scoring: Path


@dataclass(frozen=True)
class _Facts:
    preparation: DiagnosisMainScoringPreparation
    packet_sha256: str
    census_sha256: str
    recovery_plan_sha256: str
    assignment_ids_sha256: str
    provider_payloads_sha256: str
    policy_sha256: str
    worst_case_reservation_usd: Decimal
    max_attempts: int
    max_output_tokens: int
    max_visible_evidence_items: int


def _checked_sources(
    *,
    root: Path,
    packet_path: Path,
    predecessor: Path,
    smoke: Path,
    recovery: Path,
    scoring: Path,
) -> _Sources:
    root = root.resolve()
    packet_path, predecessor, smoke, recovery = checked_recovery_paths(
        root, packet_path, predecessor, smoke, recovery
    )
    scoring = checked_private_path(scoring.expanduser(), root)
    for other in (packet_path, predecessor, smoke, recovery):
        if scoring == other or scoring.is_relative_to(other) or other.is_relative_to(scoring):
            raise MainRecoveryScoringError("scoring destination overlaps sealed evidence")
    if scoring.exists() and (
        scoring.is_symlink()
        or not scoring.is_dir()
        or (os.name != "nt" and stat.S_IMODE(scoring.stat().st_mode) & 0o077)
        or any(item.name not in _ALLOWED_MEMBERS or item.is_symlink() for item in scoring.iterdir())
    ):
        raise MainRecoveryScoringError("scoring destination contains an unsafe artifact")
    return _Sources(root, packet_path, predecessor, smoke, recovery, scoring)


def _publish(path: Path, payload: dict[str, object]) -> None:
    content = (canonical_project_json(payload) + "\n").encode("utf-8")
    publish_immutable_file(path, content)


def _facts(source: _Sources) -> _Facts:
    receipt = verify_sealed_recovery_from_pinned_source(
        root=source.root,
        packet_path=source.packet_path,
        predecessor=source.predecessor,
        smoke=source.smoke,
        recovery=source.recovery,
    )
    if (
        receipt.get("receipt_sha256") != RECOVERY_RECEIPT_SHA256
        or receipt.get("relation_scoring_executed") is not False
        or receipt.get("terminal_status_counts")
        != {"completed": 829, "deterministic_completed": 128, "technical_failure": 67}
    ):
        raise MainRecoveryScoringError("recovery v2 is not the sealed scoring source")
    recovery_plan = _read_object(source.recovery / "plan.json")
    packet = load_private_main_packet(source.root, source.packet_path)
    runtime, fairness, response = load_main_runtime_inputs(source.root)
    scoring_contract = load_main_scoring_contract(
        source.root, runtime_contract=runtime, response_contract=response
    )
    batch = MainBatchResult.model_validate_json(
        (source.recovery / "main-batch-result.json").read_bytes()
    )
    preparation = prepare_main_scoring(
        packet=packet,
        runtime_contract=runtime,
        response_contract=response,
        fairness_freeze=fairness,
        scoring_contract=scoring_contract,
        batch_result=batch,
        store_root=source.recovery / "main-store",
    )
    if (
        preparation.preparation_sha256 != SCORING_PREPARATION_SHA256
        or preparation.relation_request_count != RELATION_REQUEST_COUNT
        or preparation.record_count != 1024
    ):
        raise MainRecoveryScoringError("recovery scoring census differs from locked facts")
    assignments = tuple(
        claim.relation_request for record in preparation.records for claim in record.claims
    )
    maximum_visible_evidence_items = max(len(item.visible_evidence) for item in assignments)
    if maximum_visible_evidence_items != 5:
        raise MainRecoveryScoringError("visible-evidence request ceiling differs")
    policy = load_evidence_semantics_policy(source.root)
    if policy.model_snapshot != "gpt-4.1-2025-04-14":
        raise MainRecoveryScoringError("relation model snapshot differs")
    sample = build_pipeline_relation_requests(
        source.root,
        preparation,
        source_commit_ref=str(recovery_plan["source_commit_ref"]),
        authorization=RehearsalRelationAuthorization("ev-" + "0" * 64),
        plan_sha256="0" * 64,
    )
    if len(sample) != RELATION_REQUEST_COUNT or any(
        not isinstance(item.request.context, ClaimRelationProviderContext)
        or set(item.request.context.model_payload())
        != {"claim_text", "claim_type", "visible_evidence"}
        for item in sample
    ):
        raise MainRecoveryScoringError("provider-visible relation payload differs")
    maximum_attempts = {item.request.runtime_policy.max_attempts for item in sample}
    if maximum_attempts != {2} or policy.maximum_output_tokens != 600:
        raise MainRecoveryScoringError("relation retry or output policy differs")
    worst_case = sum(
        (
            SharedProviderBudget._request_reservation(item.request)
            * item.request.runtime_policy.max_attempts
            for item in sample
        ),
        start=Decimal("0"),
    )
    if worst_case > Decimal(str(OPERATOR_COST_CEILING_USD)):
        raise MainRecoveryScoringError("cost ceiling cannot cover the bounded request census")
    return _Facts(
        preparation=preparation,
        packet_sha256=packet.packet_sha256,
        census_sha256=packet.analysis_census.census_sha256,
        recovery_plan_sha256=str(recovery_plan["plan_sha256"]),
        assignment_ids_sha256=canonical_execution_sha256(
            tuple(item.assignment_request_sha256 for item in assignments)
        ),
        provider_payloads_sha256=canonical_execution_sha256(
            tuple(item.provider_payload() for item in assignments)
        ),
        policy_sha256=policy.policy_sha256,
        worst_case_reservation_usd=worst_case,
        max_attempts=2,
        max_output_tokens=600,
        max_visible_evidence_items=maximum_visible_evidence_items,
    )


def _plan(
    source: _Sources, facts: _Facts, *, source_commit_ref: str, created_at: str
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": PLAN_VERSION,
        "purpose": "forward_relation_scoring_without_diagnosis_replay",
        "source_commit_ref": source_commit_ref,
        "created_at": created_at,
        "destination_sha256": canonical_execution_sha256(
            {"private_scoring_dir": source.scoring.as_posix()}
        ),
        "private_packet_sha256": facts.packet_sha256,
        "analysis_census_sha256": facts.census_sha256,
        "recovery_plan_sha256": facts.recovery_plan_sha256,
        "recovery_receipt_sha256": RECOVERY_RECEIPT_SHA256,
        "scoring_preparation_sha256": facts.preparation.preparation_sha256,
        "assignment_request_ids_sha256": facts.assignment_ids_sha256,
        "provider_payloads_sha256": facts.provider_payloads_sha256,
        "relation_policy_sha256": facts.policy_sha256,
        "relation_request_count": RELATION_REQUEST_COUNT,
        "model_snapshot": "gpt-4.1-2025-04-14",
        "provider_input_fields": ["claim_text", "claim_type", "visible_evidence"],
        "maximum_attempts_per_request": facts.max_attempts,
        "maximum_output_tokens_per_attempt": facts.max_output_tokens,
        "maximum_visible_evidence_items": facts.max_visible_evidence_items,
        "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION_TOKENS,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION_TOKENS,
        "worst_case_reservation_usd_at_frozen_rates": str(facts.worst_case_reservation_usd),
        "operator_cost_ceiling_usd": OPERATOR_COST_CEILING_USD,
        "diagnosis_provider_calls_permitted": False,
        "recovery_mutation_permitted": False,
    }
    return {**payload, "plan_sha256": canonical_execution_sha256(payload)}


def _summary(facts: _Facts) -> dict[str, object]:
    return {
        "status": "offline_scoring_preflight_pass",
        "provider_calls_executed": False,
        "recovery_receipt_sha256": RECOVERY_RECEIPT_SHA256,
        "scoring_preparation_sha256": facts.preparation.preparation_sha256,
        "relation_request_count": RELATION_REQUEST_COUNT,
        "maximum_provider_attempt_count": RELATION_REQUEST_COUNT * facts.max_attempts,
        "maximum_visible_evidence_items": facts.max_visible_evidence_items,
        "worst_case_reservation_usd_at_frozen_rates": str(facts.worst_case_reservation_usd),
        "operator_cost_ceiling_usd": OPERATOR_COST_CEILING_USD,
        "provider_input_fields": ["claim_text", "claim_type", "visible_evidence"],
        "model_snapshot": "gpt-4.1-2025-04-14",
        "diagnosis_replay_permitted": False,
    }


def inspect_scoring(**paths: Path) -> dict[str, object]:
    """Read-only offline census, privacy boundary, and worst-case cost check."""

    return _summary(_facts(_checked_sources(**paths)))


def prepare_scoring(**paths: Path) -> dict[str, object]:
    """Publish a private plan only on the clean, synchronized execution commit."""

    source = _checked_sources(**paths)
    commit = _clean_main_commit(source.root)
    if source.scoring.exists() and any(source.scoring.iterdir()):
        raise MainRecoveryScoringError("scoring destination must be absent or empty")
    facts = _facts(source)
    plan = _plan(
        source,
        facts,
        source_commit_ref=commit,
        created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    source.scoring.mkdir(parents=True, mode=0o700, exist_ok=True)
    _publish(source.scoring / "plan.json", plan)
    return {**_summary(facts), "plan_sha256": plan["plan_sha256"]}


def _checked_plan(source: _Sources, facts: _Facts) -> dict[str, object]:
    plan = _read_object(source.scoring / "plan.json")
    validate_self_hash(plan, "plan_sha256")
    expected = _plan(
        source,
        facts,
        source_commit_ref=str(plan.get("source_commit_ref")),
        created_at=str(plan.get("created_at")),
    )
    if plan != expected:
        raise MainRecoveryScoringError("scoring plan differs from sealed evidence")
    return plan


def _prepared(
    source: _Sources, facts: _Facts, plan: dict[str, object]
) -> tuple[PreparedRelationRequest, ...]:
    prepared = build_pipeline_relation_requests(
        source.root,
        facts.preparation,
        source_commit_ref=str(plan["source_commit_ref"]),
        authorization=RehearsalRelationAuthorization(
            authorization_ref=f"ev-{plan['plan_sha256']}",
            authorized_at=str(plan["created_at"]),
        ),
        plan_sha256=str(plan["plan_sha256"]),
    )
    total = sum(
        (
            SharedProviderBudget._request_reservation(item.request)
            * item.request.runtime_policy.max_attempts
            for item in prepared
        ),
        start=Decimal("0"),
    )
    if len(prepared) != RELATION_REQUEST_COUNT or total != facts.worst_case_reservation_usd:
        raise MainRecoveryScoringError("authorized relation census or cost differs")
    return prepared


def read_pipeline_relation_stage(
    *,
    prepared: tuple[PreparedRelationRequest, ...],
    preparation: DiagnosisMainScoringPreparation,
    store_root: Path,
) -> tuple[DiagnosisMainRelationResults, dict[str, int]]:
    """Read complete scoring terminals without creating a store or invoking an adapter."""

    if len(prepared) != preparation.relation_request_count or not prepared:
        raise DiagnosisMainPipelineError("sealed relation request census differs")
    request_root = store_root / "requests"
    authority_root = store_root / "authorities"
    if (
        store_root.is_symlink()
        or not store_root.is_dir()
        or {item.name for item in store_root.iterdir()} != {"requests", "authorities"}
        or any(path.is_symlink() or not path.is_dir() for path in (request_root, authority_root))
    ):
        raise DiagnosisMainPipelineError("sealed relation store is incomplete")
    by_identity = {item.request.initial_attempt.request_identity_sha256: item for item in prepared}
    if {item.name for item in request_root.iterdir()} != set(by_identity) or {
        item.name for item in authority_root.iterdir()
    } != {f"{identity}.json" for identity in by_identity}:
        raise DiagnosisMainPipelineError("sealed relation store membership differs")
    inventories = []
    for identity, item in by_identity.items():
        shard = request_root / identity
        authority = authority_root / f"{identity}.json"
        if (
            shard.is_symlink()
            or not shard.is_dir()
            or authority.is_symlink()
            or not authority.is_file()
            or json.loads(authority.read_bytes()) != item.authority.model_dump(mode="json")
        ):
            raise DiagnosisMainPipelineError("sealed relation authority differs")
        inventories.append(_reader(store_root, identity).terminal_inventory(identity))
    counts: dict[str, int] = {
        str(status): count
        for status, count in sorted(Counter(item.gateway_status for item in inventories).items())
    }
    ordered = tuple(
        sorted(
            (_read_result(item, store_root) for item in prepared),
            key=lambda item: item.assignment_request_sha256,
        )
    )
    authorized = preparation.execution_mode == "authorized_execution"
    payload = {
        "schema_version": "diagnosis-main-relation-results/v1",
        "execution_mode": preparation.execution_mode,
        "preparation_sha256": preparation.preparation_sha256,
        "provider_calls_executed": authorized,
        "registered_relation_attempts_consumed": int(authorized),
        "results": tuple(item.model_dump(mode="json") for item in ordered),
    }
    results = DiagnosisMainRelationResults.model_validate(
        {
            **payload,
            "results": ordered,
            "results_sha256": canonical_execution_sha256(payload),
        }
    )
    return results, counts


def _lease(plan: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "diagnosis-main-recovery-relation-lease/v1",
        "plan_sha256": plan["plan_sha256"],
        "recovery_receipt_sha256": RECOVERY_RECEIPT_SHA256,
        "destination_sha256": plan["destination_sha256"],
        "relation_request_count": RELATION_REQUEST_COUNT,
        "diagnosis_provider_calls_permitted": False,
        "resume_only_terminal_requests": True,
    }
    return {**payload, "lease_sha256": canonical_execution_sha256(payload)}


def _receipt(
    *,
    plan: dict[str, object],
    lease: dict[str, object],
    facts: _Facts,
    relations: DiagnosisMainRelationResults,
    counts: dict[str, int],
    analysis_input: DiagnosisMainAnalysisInput,
    budget: SharedProviderBudget,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": RECEIPT_VERSION,
        "status": "forward_relation_scoring_terminalized",
        "plan_sha256": plan["plan_sha256"],
        "lease_sha256": lease["lease_sha256"],
        "recovery_receipt_sha256": RECOVERY_RECEIPT_SHA256,
        "scoring_preparation_sha256": facts.preparation.preparation_sha256,
        "relation_request_count": RELATION_REQUEST_COUNT,
        "relation_terminal_status_counts": counts,
        "relation_results_sha256": relations.results_sha256,
        "analysis_input_sha256": analysis_input.input_sha256,
        "provider_cost_committed_usd_at_frozen_rates": budget.committed_usd,
        "operator_cost_ceiling_usd": OPERATOR_COST_CEILING_USD,
        "diagnosis_provider_calls_executed": False,
        "recovery_mutated": False,
        "raw_artifacts_private": True,
    }
    return {**payload, "receipt_sha256": canonical_execution_sha256(payload)}


def execute_scoring(*, confirmed_plan_sha256: str, **paths: Path) -> dict[str, object]:
    """Dispatch only the locked relation census, never a diagnosis request."""

    source = _checked_sources(**paths)
    commit = _clean_main_commit(source.root)
    facts = _facts(source)
    plan = _checked_plan(source, facts)
    if plan["plan_sha256"] != confirmed_plan_sha256 or plan["source_commit_ref"] != commit:
        raise MainRecoveryScoringError("action-time plan confirmation or execution commit differs")
    members = {item.name for item in source.scoring.iterdir()}
    if members - _ALLOWED_MEMBERS or "receipt.json" in members:
        raise MainRecoveryScoringError("scoring run is already complete or has unknown artifacts")
    prepared = _prepared(source, facts, plan)
    policy = OpenAIGatewayPolicy.from_fairness_policy(
        load_main_runtime_inputs(source.root)[1].model_policies["main_llm_v1"]
    )
    delegate = OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=prepared[0].request.initial_attempt.model_policy,
        policy=policy,
    )
    budget = SharedProviderBudget(OPERATOR_COST_CEILING_USD)
    restore_relation_budget(prepared, source.scoring / "relation-store", budget)
    if budget.exhausted:
        raise MainRecoveryScoringError("restored relation cost exceeds the frozen ceiling")
    expected_lease = _lease(plan)
    if "lease.json" in members:
        lease = _read_object(source.scoring / "lease.json")
        validate_self_hash(lease, "lease_sha256")
        if lease != expected_lease:
            raise MainRecoveryScoringError("scoring execution lease differs")
    else:
        if members != {"plan.json"}:
            raise MainRecoveryScoringError("relation store exists without an execution lease")
        _publish(source.scoring / "lease.json", expected_lease)
    adapter = BudgetedProviderAdapter(
        PacedProviderAdapter(
            delegate, minimum_interval_seconds=MINIMUM_PROVIDER_INTERVAL_MS / 1000
        ),
        budget,
    )
    relations, counts = execute_pipeline_relation_stage(
        prepared=prepared,
        preparation=facts.preparation,
        store_root=source.scoring / "relation-store",
        adapter=adapter,
        clock=SystemMonotonicClock(),
    )
    if budget.exhausted:
        raise MainRecoveryScoringError("relation scoring reached the operator cost ceiling")
    analysis_input = materialize_main_analysis_input(
        preparation=facts.preparation, relation_results=relations
    )
    _publish(source.scoring / "relation-results.json", relations.model_dump(mode="json"))
    _publish(source.scoring / "analysis-input.json", analysis_input.model_dump(mode="json"))
    receipt = _receipt(
        plan=plan,
        lease=expected_lease,
        facts=facts,
        relations=relations,
        counts=counts,
        analysis_input=analysis_input,
        budget=budget,
    )
    _publish(source.scoring / "receipt.json", receipt)
    return receipt


def verify_scoring(**paths: Path) -> dict[str, object]:
    """Rebuild the private scoring result from terminal artifacts without API calls."""

    source = _checked_sources(**paths)
    facts = _facts(source)
    plan = _checked_plan(source, facts)
    lease = _read_object(source.scoring / "lease.json")
    receipt = _read_object(source.scoring / "receipt.json")
    validate_self_hash(lease, "lease_sha256")
    validate_self_hash(receipt, "receipt_sha256")
    if lease != _lease(plan) or receipt.get("status") != "forward_relation_scoring_terminalized":
        raise MainRecoveryScoringError("scoring lease or receipt differs")
    prepared = _prepared(source, facts, plan)
    budget = SharedProviderBudget(OPERATOR_COST_CEILING_USD)
    restore_relation_budget(prepared, source.scoring / "relation-store", budget)
    if budget.exhausted:
        raise MainRecoveryScoringError("sealed relation cost exceeds the operator ceiling")
    relations, counts = read_pipeline_relation_stage(
        prepared=prepared,
        preparation=facts.preparation,
        store_root=source.scoring / "relation-store",
    )
    analysis_input = materialize_main_analysis_input(
        preparation=facts.preparation, relation_results=relations
    )
    saved_relations = json.loads((source.scoring / "relation-results.json").read_bytes())
    saved_input = json.loads((source.scoring / "analysis-input.json").read_bytes())
    if (
        saved_relations != relations.model_dump(mode="json")
        or saved_input != analysis_input.model_dump(mode="json")
        or receipt
        != _receipt(
            plan=plan,
            lease=lease,
            facts=facts,
            relations=relations,
            counts=counts,
            analysis_input=analysis_input,
            budget=budget,
        )
    ):
        raise MainRecoveryScoringError("sealed relation scoring artifacts do not reconcile")
    return receipt


__all__ = [
    "MainRecoveryScoringError",
    "execute_scoring",
    "inspect_scoring",
    "prepare_scoring",
    "verify_scoring",
]
