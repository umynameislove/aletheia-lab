"""Outcome-blind materialization of diagnosis terminals into analysis input.

The module has no provider adapter and cannot authorize execution.  It verifies
the complete immutable diagnosis store, prepares blind relation requests for
every emitted claim, and compiles terminal relation results into the already
frozen :class:`DiagnosisMainAnalysisInput` contract.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from aletheia_lab.diagnosis._main_runtime_contracts import (
    _ROUTE_TURNS,
    MainLogicalTerminal,
    MainRuntimeContract,
)
from aletheia_lab.diagnosis.main_execution import MainBatchBinding, MainBatchResult
from aletheia_lab.diagnosis.main_response import (
    MainAtomicClaim,
    validate_main_provider_output,
)
from aletheia_lab.evaluation._diagnosis_main_materialization_contracts import (
    MATERIALIZATION_REHEARSAL_SCHEMA_VERSION,
    RELATION_RESULTS_SCHEMA_VERSION,
    SCORING_PREPARATION_SCHEMA_VERSION,
    DiagnosisMainMaterializationRehearsal,
    DiagnosisMainPreparedClaim,
    DiagnosisMainPreparedRecord,
    DiagnosisMainRelationResult,
    DiagnosisMainRelationResults,
    DiagnosisMainScoringContract,
    DiagnosisMainScoringPreparation,
)
from aletheia_lab.evaluation.claim_corpus_readiness import instrument_manifest
from aletheia_lab.evaluation.claim_evidence_semantics import (
    RELATION_ASSIGNMENT_PROMPT,
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
    EvidenceRelationDecision,
    ModelVisibleEvidenceContext,
    load_evidence_semantics_policy,
    relation_assignment_response_schema,
    visible_relations_from_assignment,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ANALYSIS_INPUT_SCHEMA_VERSION,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisError,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainClaim,
    DiagnosisMainObservedRecord,
    TechnicalStatus,
    analyse_diagnosis_main,
)
from aletheia_lab.evaluation.diagnosis_main_census import (
    DiagnosisMainPrivateCensusPacket,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import DiagnosisVariantFairnessFreeze
from aletheia_lab.model_gateway import GatewayExecutionResult, GatewayRequest
from aletheia_lab.project.identity import content_sha256

SCORING_CONTRACT_PATH: Final = Path("configs/evaluation/diagnosis_main_scoring_contract.json")
ANALYSIS_PLAN_PATH: Final = Path("configs/evaluation/diagnosis_main_analysis_plan_v3.json")
INSTRUMENT_MANIFEST_PATH: Final = Path(
    "configs/evaluation/claim_support_automatic_instrument_manifest.json"
)
EVIDENCE_POLICY_PATH: Final = Path(
    "configs/evaluation/claim_support_evidence_semantics_policy.json"
)
CORPUS_PROTOCOL_PATH: Final = Path("configs/evaluation/claim_support_corpus_protocol.json")
INSTRUMENT_FIXTURES_PATH: Final = Path("configs/evaluation/claim_support_instrument_fixtures.json")

_CITATION_REQUIRED_VARIANTS: Final = frozenset(("A2", "A3", "CodeGraph", "FULL"))
_CITATION_REQUIRED_CLAIMS: Final = frozenset(("cause_assertion", "evidence_statement"))
_PROVIDER_FAILURE_STATUSES: Final = frozenset(
    ("provider_failed", "timed_out", "retry_exhausted", "cancelled")
)
_PARSE_FAILURE_STATUSES: Final = frozenset(("parsed", "parse_failed", "oversized_response"))

ModelT = TypeVar("ModelT", bound=BaseModel)


class DiagnosisMainMaterializationError(ValueError):
    """Raised when terminal-to-analysis compilation cannot preserve the freeze."""


def _model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _read_canonical_model(path: Path, model: type[ModelT]) -> ModelT:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainMaterializationError("required immutable model is unavailable")
    try:
        artifact_bytes = path.read_bytes()
        value = model.model_validate_json(artifact_bytes)
    except (OSError, ValidationError, ValueError) as exc:
        raise DiagnosisMainMaterializationError("required immutable model is invalid") from exc
    if artifact_bytes != _model_bytes(value):
        raise DiagnosisMainMaterializationError("immutable model bytes are not canonical")
    return value


def _read_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise DiagnosisMainMaterializationError("required JSON artifact is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosisMainMaterializationError("required JSON artifact is invalid") from exc
    if not isinstance(payload, dict):
        raise DiagnosisMainMaterializationError("required JSON artifact must be an object")
    return cast(dict[str, object], payload)


def load_main_analysis_plan(root: Path) -> DiagnosisMainAnalysisPlan:
    try:
        return DiagnosisMainAnalysisPlan.model_validate_json(
            (root / ANALYSIS_PLAN_PATH).read_bytes()
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise DiagnosisMainMaterializationError("main analysis plan is unavailable") from exc


def load_main_scoring_contract(
    root: Path,
    *,
    runtime_contract: MainRuntimeContract,
    response_contract: Mapping[str, object],
) -> DiagnosisMainScoringContract:
    """Load the tracked scoring boundary and verify every inherited instrument."""

    checked_root = root.resolve()
    try:
        contract = DiagnosisMainScoringContract.model_validate_json(
            (checked_root / SCORING_CONTRACT_PATH).read_bytes()
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise DiagnosisMainMaterializationError("main scoring contract is unavailable") from exc
    plan = load_main_analysis_plan(checked_root)
    instrument_path = checked_root / INSTRUMENT_MANIFEST_PATH
    policy_path = checked_root / EVIDENCE_POLICY_PATH
    instrument = _read_json(instrument_path)
    protocol = _read_json(checked_root / CORPUS_PROTOCOL_PATH)
    fixtures = _read_json(checked_root / INSTRUMENT_FIXTURES_PATH)
    protocol_sha = protocol.get("protocol_sha256")
    if not isinstance(protocol_sha, str):
        raise DiagnosisMainMaterializationError("corpus protocol identity is unavailable")
    expected_instrument = json.loads(
        json.dumps(instrument_manifest(checked_root, protocol_sha, fixtures))
    )
    policy = load_evidence_semantics_policy(checked_root)
    response_sha = response_contract.get("contract_sha256")
    instrument_sha = instrument.get("manifest_sha256")
    expected_response_schema_sha = canonical_execution_sha256(relation_assignment_response_schema())
    if (
        instrument != expected_instrument
        or instrument_sha != contract.automatic_instrument_manifest_sha256
        or content_sha256(instrument_path.read_bytes())
        != contract.automatic_instrument_manifest_file_sha256
        or content_sha256(policy_path.read_bytes())
        != contract.evidence_semantics_policy_file_sha256
        or policy.policy_sha256 != contract.evidence_semantics_policy_sha256
        or plan.plan_sha256 != contract.analysis_plan_sha256
        or runtime_contract.runtime_contract_sha256 != contract.runtime_contract_sha256
        or response_sha != contract.response_contract_sha256
        or policy.provider != contract.judge_provider
        or policy.model != contract.judge_model
        or policy.model_snapshot != contract.judge_model_snapshot
        or policy.temperature != contract.judge_temperature
        or policy.seed != contract.judge_seed
        or policy.maximum_output_tokens != contract.maximum_output_tokens_per_relation
        or policy.maximum_attempts != contract.maximum_provider_attempts_per_relation
        or content_sha256(RELATION_ASSIGNMENT_PROMPT.encode("utf-8"))
        != contract.relation_prompt_sha256
        or expected_response_schema_sha != contract.relation_response_schema_sha256
    ):
        raise DiagnosisMainMaterializationError(
            "main scoring contract differs from its frozen inherited instruments"
        )
    return contract


def _load_batch_binding(store_root: Path) -> MainBatchBinding:
    return _read_canonical_model(store_root / "batch-binding.json", MainBatchBinding)


def _validate_batch_binding(
    *,
    binding: MainBatchBinding,
    batch_result: MainBatchResult,
    packet: DiagnosisMainPrivateCensusPacket,
    runtime_contract: MainRuntimeContract,
    response_contract: Mapping[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
) -> None:
    request_payload = tuple(
        {"request_id": item.request_id, "request_sha256": item.request_sha256}
        for item in packet.analysis_census.requests
    )
    if (
        binding.execution_mode != batch_result.execution_mode
        or binding.private_packet_sha256 != packet.packet_sha256
        or binding.analysis_census_sha256 != packet.analysis_census.census_sha256
        or binding.runtime_contract_sha256 != runtime_contract.runtime_contract_sha256
        or binding.response_contract_sha256 != response_contract.get("contract_sha256")
        or binding.fairness_freeze_sha256
        != canonical_execution_sha256(fairness_freeze.model_dump(mode="json"))
        or binding.ordered_requests_sha256 != canonical_execution_sha256(request_payload)
        or binding.logical_request_count != len(request_payload)
        or batch_result.batch_binding_sha256 != binding.binding_sha256
        or batch_result.logical_request_count != len(request_payload)
        or len(request_payload) != 1024
    ):
        raise DiagnosisMainMaterializationError(
            "runtime store, result, packet, and frozen contracts do not reconcile"
        )


def _validate_store_root(store_root: Path, request_ids: set[str]) -> Path:
    if store_root.is_symlink() or not store_root.is_dir():
        raise DiagnosisMainMaterializationError("runtime store must be a real directory")
    resolved = store_root.resolve()
    expected = {"batch-binding.json", *request_ids}
    if {item.name for item in resolved.iterdir()} != expected:
        raise DiagnosisMainMaterializationError("runtime store census membership differs")
    return resolved


def _load_turn(
    request_root: Path,
    terminal: MainLogicalTerminal,
    ordinal: int,
) -> tuple[GatewayRequest, GatewayExecutionResult]:
    turn_root = request_root / f"turn-{ordinal:02d}"
    if turn_root.is_symlink() or not turn_root.is_dir():
        raise DiagnosisMainMaterializationError("runtime turn directory is unavailable")
    if {item.name for item in turn_root.iterdir()} != {"request.json", "result.json"}:
        raise DiagnosisMainMaterializationError("runtime turn membership differs")
    request = _read_canonical_model(turn_root / "request.json", GatewayRequest)
    result = _read_canonical_model(turn_root / "result.json", GatewayExecutionResult)
    receipt = terminal.turn_receipts[ordinal - 1]
    if (
        receipt.logical_request_id != terminal.logical_request_id
        or receipt.logical_request_sha256 != terminal.logical_request_sha256
        or receipt.turn_ordinal != ordinal
        or receipt.gateway_request_identity_sha256
        != request.initial_attempt.request_identity_sha256
        or receipt.gateway_result_sha256
        != canonical_execution_sha256(result.model_dump(mode="json"))
        or receipt.gateway_status != result.status
        or receipt.visible_context_sha256 != request.context.context_sha256
        or result.request_identity_sha256 != request.initial_attempt.request_identity_sha256
    ):
        raise DiagnosisMainMaterializationError("runtime turn differs from terminal receipt")
    return request, result


def _load_terminal_bundle(
    store_root: Path,
    request_id: str,
    request_sha256: str,
) -> tuple[
    MainLogicalTerminal,
    tuple[tuple[GatewayRequest, GatewayExecutionResult], ...],
]:
    request_root = store_root / request_id
    if request_root.is_symlink() or not request_root.is_dir():
        raise DiagnosisMainMaterializationError("logical request store is unavailable")
    terminal = _read_canonical_model(request_root / "terminal.json", MainLogicalTerminal)
    if (
        terminal.logical_request_id != request_id
        or terminal.logical_request_sha256 != request_sha256
    ):
        raise DiagnosisMainMaterializationError("terminal belongs to another request")
    expected_members = {"terminal.json"} | {
        f"turn-{ordinal:02d}" for ordinal in range(1, terminal.completed_provider_turn_count + 1)
    }
    if {item.name for item in request_root.iterdir()} != expected_members:
        raise DiagnosisMainMaterializationError("logical request store membership differs")
    turns = tuple(
        _load_turn(request_root, terminal, ordinal)
        for ordinal in range(1, terminal.completed_provider_turn_count + 1)
    )
    return terminal, turns


def _technical_status(terminal: MainLogicalTerminal) -> TechnicalStatus:
    if terminal.status in {"completed", "deterministic_completed"}:
        return "success"
    if terminal.status == "semantic_failure":
        return "parse_failure"
    gateway_status = terminal.turn_receipts[-1].gateway_status if terminal.turn_receipts else None
    if gateway_status in _PROVIDER_FAILURE_STATUSES:
        return "provider_failure"
    if gateway_status in _PARSE_FAILURE_STATUSES:
        return "parse_failure"
    return "unresolved"


def _build_relation_request(
    *,
    source_output_sha256: str,
    claim: MainAtomicClaim,
    final_context: ModelVisibleEvidenceContext,
) -> ClaimRelationAssignmentRequest:
    identity_payload = {
        "schema_version": "claim-relation-assignment-request/v1",
        "source_output_sha256": source_output_sha256,
        "claim_local_id": claim.claim_local_id,
        "provider_payload": {
            "claim_text": claim.claim_text,
            "claim_type": claim.claim_type,
            "visible_evidence": tuple(item.model_dump(mode="json") for item in final_context.items),
        },
        "visible_context_sha256": final_context.context_sha256,
    }
    digest = canonical_execution_sha256(identity_payload)
    return ClaimRelationAssignmentRequest(
        assignment_request_id=f"ccrel-{digest}",
        source_output_sha256=source_output_sha256,
        claim_local_id=claim.claim_local_id,
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        visible_evidence=final_context.items,
        visible_context_sha256=final_context.context_sha256,
        assignment_request_sha256=digest,
    )


def _prepare_claim(
    *,
    variant: str,
    claim: MainAtomicClaim,
    source_output_sha256: str,
    final_context: ModelVisibleEvidenceContext,
) -> DiagnosisMainPreparedClaim:
    visible_ids = {item.evidence_id for item in final_context.items}
    citations = claim.visible_evidence_ids
    citation_required = (
        variant in _CITATION_REQUIRED_VARIANTS and claim.claim_type in _CITATION_REQUIRED_CLAIMS
    )
    request = _build_relation_request(
        source_output_sha256=source_output_sha256,
        claim=claim,
        final_context=final_context,
    )
    return DiagnosisMainPreparedClaim(
        claim_id=request.assignment_request_id,
        claim_type=claim.claim_type,
        citation_required=citation_required,
        citation_present=bool(citations),
        citation_ids_valid=bool(citations) and set(citations).issubset(visible_ids),
        relation_request=request,
    )


def _prepare_completed_record(
    terminal: MainLogicalTerminal,
    turns: tuple[tuple[GatewayRequest, GatewayExecutionResult], ...],
) -> DiagnosisMainPreparedRecord:
    if terminal.status == "deterministic_completed":
        return DiagnosisMainPreparedRecord(
            request_id=terminal.logical_request_id,
            request_sha256=terminal.logical_request_sha256,
            terminal_sha256=terminal.terminal_sha256,
            technical_status="success",
            output_status="completed",
            claims=(),
        )
    if not turns:
        raise DiagnosisMainMaterializationError("completed provider terminal has no final turn")
    request, result = turns[-1]
    if (
        terminal.turn_receipts[-1].turn_kind != "final"
        or not isinstance(request.context, ModelVisibleEvidenceContext)
        or request.context.context_sha256 != terminal.final_visible_context_sha256
        or result.status != "parsed"
        or result.raw_response is None
        or result.parsed_response is None
        or result.raw_response.content_sha256 != terminal.final_raw_response_sha256
    ):
        raise DiagnosisMainMaterializationError("completed terminal lacks its exact final output")
    try:
        raw_text = result.raw_response.content.decode("utf-8", errors="strict")
        output = validate_main_provider_output(
            raw_text,
            variant=cast(AnyMainVariant, terminal.variant),
            visible_evidence_ids={item.evidence_id for item in request.context.items},
        )
    except (UnicodeDecodeError, ValidationError, ValueError) as exc:
        raise DiagnosisMainMaterializationError("completed terminal output is invalid") from exc
    if (
        canonical_execution_sha256(output.model_dump(mode="json"))
        != result.parsed_response.content_sha256
    ):
        raise DiagnosisMainMaterializationError("parsed output differs from retained raw bytes")
    source_output_sha256 = canonical_execution_sha256(
        {
            "logical_request_sha256": terminal.logical_request_sha256,
            "parsed_output_sha256": result.parsed_response.content_sha256,
        }
    )
    claims = tuple(
        _prepare_claim(
            variant=terminal.variant,
            claim=claim,
            source_output_sha256=source_output_sha256,
            final_context=request.context,
        )
        for claim in output.atomic_claims
    )
    return DiagnosisMainPreparedRecord(
        request_id=terminal.logical_request_id,
        request_sha256=terminal.logical_request_sha256,
        terminal_sha256=terminal.terminal_sha256,
        technical_status="success",
        output_status=output.output_status,
        claims=claims,
    )


AnyMainVariant = Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]


def _prepare_record(
    terminal: MainLogicalTerminal,
    turns: tuple[tuple[GatewayRequest, GatewayExecutionResult], ...],
) -> DiagnosisMainPreparedRecord:
    technical = _technical_status(terminal)
    if technical == "success":
        return _prepare_completed_record(terminal, turns)
    return DiagnosisMainPreparedRecord(
        request_id=terminal.logical_request_id,
        request_sha256=terminal.logical_request_sha256,
        terminal_sha256=terminal.terminal_sha256,
        technical_status=technical,
        output_status=None,
        claims=(),
    )


def prepare_main_scoring(
    *,
    packet: DiagnosisMainPrivateCensusPacket,
    runtime_contract: MainRuntimeContract,
    response_contract: Mapping[str, object],
    fairness_freeze: DiagnosisVariantFairnessFreeze,
    scoring_contract: DiagnosisMainScoringContract,
    batch_result: MainBatchResult,
    store_root: Path,
) -> DiagnosisMainScoringPreparation:
    """Verify the complete terminal store and prepare every blind claim relation."""

    binding = _load_batch_binding(store_root)
    _validate_batch_binding(
        binding=binding,
        batch_result=batch_result,
        packet=packet,
        runtime_contract=runtime_contract,
        response_contract=response_contract,
        fairness_freeze=fairness_freeze,
    )
    if (
        scoring_contract.runtime_contract_sha256 != runtime_contract.runtime_contract_sha256
        or scoring_contract.response_contract_sha256 != response_contract.get("contract_sha256")
    ):
        raise DiagnosisMainMaterializationError("scoring contract differs from runtime")
    requests = packet.analysis_census.requests
    checked_root = _validate_store_root(store_root, {item.request_id for item in requests})
    terminals: dict[str, MainLogicalTerminal] = {}
    prepared: list[DiagnosisMainPreparedRecord] = []
    for logical in requests:
        terminal, turns = _load_terminal_bundle(
            checked_root,
            logical.request_id,
            logical.request_sha256,
        )
        if (
            terminal.context_id != logical.context_id
            or terminal.variant != logical.variant
            or terminal.expected_provider_turn_count != len(_ROUTE_TURNS[logical.variant])
        ):
            raise DiagnosisMainMaterializationError("terminal differs from frozen census")
        terminals[logical.request_id] = terminal
        prepared.append(_prepare_record(terminal, turns))
    ledger = tuple(
        {
            "request_id": logical.request_id,
            "terminal_sha256": terminals[logical.request_id].terminal_sha256,
        }
        for logical in requests
    )
    status_counts = dict(sorted(Counter(item.status for item in terminals.values()).items()))
    completed_turns = sum(item.completed_provider_turn_count for item in terminals.values())
    terminal_ledger_sha = canonical_execution_sha256(ledger)
    if (
        terminal_ledger_sha != batch_result.terminal_ledger_sha256
        or status_counts != batch_result.terminal_status_counts
        or completed_turns != batch_result.completed_provider_turn_count
    ):
        raise DiagnosisMainMaterializationError("terminal census differs from batch result")
    records = tuple(sorted(prepared, key=lambda item: item.request_id))
    claim_count = sum(len(item.claims) for item in records)
    identity_payload = {
        "schema_version": SCORING_PREPARATION_SCHEMA_VERSION,
        "execution_mode": batch_result.execution_mode,
        "analysis_plan_sha256": scoring_contract.analysis_plan_sha256,
        "census_sha256": packet.analysis_census.census_sha256,
        "runtime_contract_sha256": runtime_contract.runtime_contract_sha256,
        "response_contract_sha256": response_contract["contract_sha256"],
        "scoring_contract_sha256": scoring_contract.contract_sha256,
        "batch_binding_sha256": binding.binding_sha256,
        "batch_result_sha256": batch_result.result_sha256,
        "terminal_ledger_sha256": terminal_ledger_sha,
        "record_count": len(records),
        "emitted_claim_count": claim_count,
        "relation_request_count": claim_count,
        "technical_status_counts": dict(
            sorted(Counter(item.technical_status for item in records).items())
        ),
        "relation_provider_calls_executed": False,
        "analysis_executed": False,
        "records": tuple(item.model_dump(mode="json") for item in records),
    }
    try:
        return DiagnosisMainScoringPreparation.model_validate(
            {
                **identity_payload,
                "records": records,
                "preparation_sha256": canonical_execution_sha256(identity_payload),
            }
        )
    except ValidationError as exc:
        raise DiagnosisMainMaterializationError("main scoring preparation is invalid") from exc


def build_offline_relation_results(
    preparation: DiagnosisMainScoringPreparation,
) -> DiagnosisMainRelationResults:
    """Build schema-valid fake relations for transport rehearsal only."""

    if preparation.execution_mode != "offline_rehearsal":
        raise DiagnosisMainMaterializationError(
            "fake relation results require an offline rehearsal preparation"
        )
    results: list[DiagnosisMainRelationResult] = []
    for record in preparation.records:
        for claim in record.claims:
            decisions = tuple(
                EvidenceRelationDecision(
                    evidence_id=item.evidence_id,
                    relation_polarity=("supports" if index == 0 else "neutral"),
                    relation_scope=("entire" if index == 0 else "none"),
                )
                for index, item in enumerate(claim.relation_request.visible_evidence)
            )
            response_payload = {
                "schema_version": "claim-relation-assignment-response/v1",
                "assignment_request_sha256": (claim.relation_request.assignment_request_sha256),
                "decisions": tuple(item.model_dump(mode="json") for item in decisions),
            }
            response = ClaimRelationAssignmentResponse.model_validate(
                {
                    **response_payload,
                    "response_sha256": canonical_execution_sha256(response_payload),
                }
            )
            results.append(
                DiagnosisMainRelationResult(
                    assignment_request_sha256=(claim.relation_request.assignment_request_sha256),
                    terminal_status="parsed",
                    response=response,
                    issue_code=None,
                )
            )
    ordered = tuple(sorted(results, key=lambda item: item.assignment_request_sha256))
    identity_payload = {
        "schema_version": RELATION_RESULTS_SCHEMA_VERSION,
        "execution_mode": "offline_rehearsal",
        "preparation_sha256": preparation.preparation_sha256,
        "provider_calls_executed": False,
        "registered_relation_attempts_consumed": 0,
        "results": tuple(item.model_dump(mode="json") for item in ordered),
    }
    return DiagnosisMainRelationResults.model_validate(
        {
            **identity_payload,
            "results": ordered,
            "results_sha256": canonical_execution_sha256(identity_payload),
        }
    )


def _materialize_claim(
    claim: DiagnosisMainPreparedClaim,
    result: DiagnosisMainRelationResult,
) -> DiagnosisMainClaim:
    if result.response is None:
        raise DiagnosisMainMaterializationError("parsed relation response is unavailable")
    relations = visible_relations_from_assignment(
        claim.relation_request,
        result.response,
    )
    label = classify_visible_support(
        claim_text=claim.relation_request.claim_text,
        claim_type=claim.claim_type,
        visible_evidence=relations,
    )
    return DiagnosisMainClaim(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type,
        support_label=label,
        citation_required=claim.citation_required,
        citation_present=claim.citation_present,
        citation_ids_valid=claim.citation_ids_valid,
    )


def _materialize_record(
    record: DiagnosisMainPreparedRecord,
    results: Mapping[str, DiagnosisMainRelationResult],
) -> DiagnosisMainObservedRecord:
    if record.technical_status != "success" or not record.claims:
        return DiagnosisMainObservedRecord(
            request_id=record.request_id,
            request_sha256=record.request_sha256,
            technical_status=record.technical_status,
            output_status=record.output_status,
            claims=(),
        )
    relation_results = tuple(
        results[item.relation_request.assignment_request_sha256] for item in record.claims
    )
    if any(item.terminal_status != "parsed" for item in relation_results):
        return DiagnosisMainObservedRecord(
            request_id=record.request_id,
            request_sha256=record.request_sha256,
            technical_status="unresolved",
            output_status=None,
            claims=(),
        )
    claims = tuple(
        _materialize_claim(claim, result)
        for claim, result in zip(record.claims, relation_results, strict=True)
    )
    return DiagnosisMainObservedRecord(
        request_id=record.request_id,
        request_sha256=record.request_sha256,
        technical_status="success",
        output_status=record.output_status,
        claims=claims,
    )


def materialize_main_analysis_input(
    *,
    preparation: DiagnosisMainScoringPreparation,
    relation_results: DiagnosisMainRelationResults,
    allow_offline_rehearsal: bool = False,
) -> DiagnosisMainAnalysisInput:
    """Compile exact relation terminals into the frozen analysis-input schema."""

    if (
        relation_results.preparation_sha256 != preparation.preparation_sha256
        or relation_results.execution_mode != preparation.execution_mode
    ):
        raise DiagnosisMainMaterializationError(
            "relation results belong to another scoring preparation"
        )
    if preparation.execution_mode == "offline_rehearsal" and not allow_offline_rehearsal:
        raise DiagnosisMainMaterializationError(
            "offline rehearsal data cannot be published as main analysis input"
        )
    expected = {
        claim.relation_request.assignment_request_sha256
        for record in preparation.records
        for claim in record.claims
    }
    observed = {item.assignment_request_sha256 for item in relation_results.results}
    if observed != expected:
        raise DiagnosisMainMaterializationError(
            "relation result census does not exactly cover emitted claims"
        )
    results_by_id = {item.assignment_request_sha256: item for item in relation_results.results}
    records = tuple(_materialize_record(record, results_by_id) for record in preparation.records)
    identity_payload = {
        "schema_version": ANALYSIS_INPUT_SCHEMA_VERSION,
        "analysis_plan_sha256": preparation.analysis_plan_sha256,
        "census_sha256": preparation.census_sha256,
        "records": tuple(item.model_dump(mode="json") for item in records),
    }
    try:
        return DiagnosisMainAnalysisInput.model_validate(
            {
                **identity_payload,
                "records": records,
                "input_sha256": canonical_execution_sha256(identity_payload),
            }
        )
    except ValidationError as exc:
        raise DiagnosisMainMaterializationError("main analysis input is invalid") from exc


def rehearse_main_materialization(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    preparation: DiagnosisMainScoringPreparation,
) -> DiagnosisMainMaterializationRehearsal:
    """Exercise fake relations and the analysis contract without retaining outcomes."""

    if preparation.execution_mode != "offline_rehearsal":
        raise DiagnosisMainMaterializationError(
            "materialization rehearsal requires an offline execution store"
        )
    relations = build_offline_relation_results(preparation)
    analysis_input = materialize_main_analysis_input(
        preparation=preparation,
        relation_results=relations,
        allow_offline_rehearsal=True,
    )
    try:
        report = analyse_diagnosis_main(plan, census, analysis_input)
    except DiagnosisMainAnalysisError as exc:
        raise DiagnosisMainMaterializationError(
            "offline materialization does not satisfy the analysis contract"
        ) from exc
    if report.status != "valid_registered_analysis":
        raise DiagnosisMainMaterializationError(
            "offline materialization was rejected by the analysis contract"
        )
    claims = tuple(claim for record in analysis_input.records for claim in record.claims)
    payload = {
        "schema_version": MATERIALIZATION_REHEARSAL_SCHEMA_VERSION,
        "status": "offline_materialization_rehearsal_pass",
        "scientific_result_eligible": False,
        "provider_calls_executed": False,
        "registered_attempts_consumed": 0,
        "logical_request_count": len(analysis_input.records),
        "emitted_claim_count": preparation.emitted_claim_count,
        "relation_request_count": preparation.relation_request_count,
        "materialized_claim_count": len(claims),
        "technical_status_counts": dict(
            sorted(Counter(item.technical_status for item in analysis_input.records).items())
        ),
        "support_label_counts": dict(
            sorted(Counter(item.support_label for item in claims).items())
        ),
        "preparation_sha256": preparation.preparation_sha256,
        "relation_results_sha256": relations.results_sha256,
        "analysis_input_sha256": analysis_input.input_sha256,
        "analysis_contract_accepted": True,
        "analysis_report_sha256": report.report_sha256,
    }
    return DiagnosisMainMaterializationRehearsal.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


__all__ = [
    "DiagnosisMainMaterializationError",
    "DiagnosisMainMaterializationRehearsal",
    "DiagnosisMainPreparedClaim",
    "DiagnosisMainPreparedRecord",
    "DiagnosisMainRelationResult",
    "DiagnosisMainRelationResults",
    "DiagnosisMainScoringContract",
    "DiagnosisMainScoringPreparation",
    "build_offline_relation_results",
    "load_main_analysis_plan",
    "load_main_scoring_contract",
    "materialize_main_analysis_input",
    "prepare_main_scoring",
    "rehearse_main_materialization",
]
