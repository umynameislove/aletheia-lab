"""Project historical state authorization over a scoped immutable upload snapshot."""

from __future__ import annotations

from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.view import (
    HISTORICAL_STATE_MODEL,
    ProductClaim,
    ProductConversation,
    ProductDenominators,
    ProductHistoricalState,
    ProductHistoricalStateEvidence,
    ProductHistoricalStateEvidenceItem,
    ProductProject,
    ProductReproductionRef,
    ProductResult,
    ProductRuntime,
    ProductSnapshotV2,
    ProductTurn,
    ProductViewV2,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricDefinitionV2

from .historical_state_evidence import HistoricalState, evaluate_historical_state
from .upload_analysis import analysis_identity
from .upload_snapshot import AnyUploadSnapshot, HistoricalStateUploadSnapshot, parse_upload_snapshot


def _state(state: HistoricalState | None) -> ProductHistoricalState | None:
    if state is None:
        return None
    return ProductHistoricalState(
        namespace=state.namespace, generation=state.generation, state_sha256=state.state_sha256,
    )


def analyze_historical_state(snapshot: AnyUploadSnapshot, question: str) -> ProductViewV2:
    snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
    question = checked_product_question(question)
    if not isinstance(snapshot, HistoricalStateUploadSnapshot):
        raise ValueError("Historical state evidence is unavailable for this snapshot.")

    evidence_input = snapshot.historical_state
    receipt, contract = evidence_input.receipt, evidence_input.contract
    decision = evaluate_historical_state(evidence_input)
    identity = analysis_identity(snapshot.workspace_id, snapshot.snapshot_id, question, HISTORICAL_STATE_MODEL)
    digest = canonical_project_sha256(identity)
    result_id, turn_id = f"p6-result-{digest}", f"p6-turn-{digest}"
    evidence_id = "p6-evidence-" + canonical_project_sha256({
        "snapshot": snapshot.snapshot_id, "model": HISTORICAL_STATE_MODEL,
    })
    claim_id = f"p6-claim-{digest}"
    runtime = ProductRuntime(provider="local_evidence", model=HISTORICAL_STATE_MODEL, external_call=False)
    unresolved = decision.verdict not in {"allowed", "denied"}
    missing = (decision.reason,) if unresolved else ()
    summary = f"The historical state decision is {decision.verdict}. {decision.reason}"
    evidence = ProductHistoricalStateEvidenceItem(
        id=evidence_id, kind="historical_state", title="Observed model state",
        text=(
            f"Operation {receipt.operation_id}, request {receipt.request_id}, consumer {receipt.consumer_id}. "
            f"Producer evidence mode is {receipt.execution_mode}. {decision.reason}"
        ),
        reproduction_ref=ProductReproductionRef(record_id=snapshot.snapshot_id, record_kind="snapshot"),
        source_sha256=snapshot.source_sha256, visibility="diagnosis", redacted=False,
        historical_state=ProductHistoricalStateEvidence(
            operation_id=receipt.operation_id, request_id=receipt.request_id, consumer_id=receipt.consumer_id,
            execution_mode=receipt.execution_mode, producer_version=receipt.producer_version,
            source_receipt_sha256=receipt.source_receipt_sha256,
            receipt_sha256=canonical_project_sha256(receipt.model_dump(mode="json")),
            contract_sha256=canonical_project_sha256(contract.model_dump(mode="json")) if contract is not None else None,
            graph_sha256=receipt.graph_sha256, observed_state=_state(receipt.observation),
            allowed_state=_state(contract.allowed_state) if contract is not None else None,
            output=receipt.output, current_alias=receipt.current_alias, verdict=decision.verdict, reason=decision.reason,
        ),
    )
    claim = ProductClaim(
        id=claim_id, turn_id=turn_id, text=summary,
        claim_type="uncertainty_statement" if unresolved else "evidence_statement",
        epistemic_state="abstained" if unresolved else "observed",
        support="not_estimated" if unresolved else "fully_supported",
        citation_ids=(evidence_id,), counterevidence_ids=(), missing_evidence=missing,
        max_strength="abstain" if unresolved else "observation",
    )
    projected_snapshot = ProductSnapshotV2.model_validate({
        "id": snapshot.snapshot_id, "baseline_id": snapshot.snapshot_id,
        "sha256": snapshot.snapshot_id.removeprefix("upload-snapshot-"),
        "metric_definitions": tuple(
            {**item.model_dump(mode="python"), "unit": item.unit if isinstance(item, MetricDefinitionV2) else None}
            for item in snapshot.mapping.metric_definitions
        ),
        "metric_changes": (),
    })
    return ProductViewV2(
        demo_only=False, mode="project_audit", visibility="diagnosis",
        project=ProductProject(id=snapshot.mapping.project_id, display_name="Uploaded project"),
        snapshot=projected_snapshot, runtime=runtime,
        result=ProductResult(
            id=result_id, status="complete", disposition="abstain" if unresolved else "supported",
            causal_status="unverified", summary=summary, allowed_wording=summary,
            denominators=ProductDenominators(independent_families=None, contexts=1, outputs=1, claims=1),
            forbidden_wording="This verdict establishes a causal diagnosis or confirms a downstream action.",
            caveat="A scoped historical contract decision from admitted producer evidence. No downstream action is issued.",
        ),
        conversation=ProductConversation(id=f"p6-conversation-{digest}", turns=(ProductTurn(
            id=turn_id, snapshot_id=snapshot.snapshot_id, result_id=result_id, runtime=runtime,
            question=question, answer=summary, abstained=unresolved, visible_evidence_ids=(evidence_id,),
            missing_evidence=missing,
        ),)),
        claims=(claim,), evidence=(evidence,),
        graph=build_product_graph(
            snapshot_id=snapshot.snapshot_id, evidence_ids=(evidence_id,),
            claim_citations={claim_id: (evidence_id,)}, disposition_id=result_id,
            result_scope_id=turn_id, observed_evidence_ids=(evidence_id,),
        ),
    )
