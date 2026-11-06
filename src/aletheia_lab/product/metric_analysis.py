"""Actual local analysis of content-bound project metric observations.

This reports measurements and caller-defined adverse changes. It does not infer
causes, invoke a model, execute project code or change imported artifacts.
"""

from __future__ import annotations

from pathlib import Path

from aletheia_lab.product.analysis import build_product_analysis
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import (
    ProductClaim,
    ProductConversation,
    ProductDenominators,
    ProductEvidence,
    ProductMetricChange,
    ProductRuntime,
    ProductTurn,
    ProductView,
)
from aletheia_lab.project.identity import canonical_project_sha256


def metric_observation_text(change: ProductMetricChange) -> str:
    """Render exact validated operands; added/removed observations have no delta."""
    operand = change.after if change.after is not None else change.before
    if operand is None:
        raise ProductError("store_integrity_error", "Metric operands are unavailable.")
    scope = f"Run {operand.run_id}, metric {change.metric_name}, step {operand.step}"
    if change.before is None:
        return f"{scope}: added value {operand.value}; no comparable previous value."
    if change.after is None:
        return f"{scope}: removed previous value {operand.value}; no comparable current value."
    return (
        f"{scope}: {change.before.value} -> {change.after.value}; "
        f"delta {change.delta}; classification {change.adverse_status}."
    )


def analyze_product_metrics(
    store_root: Path,
    project_id: str,
    snapshot_id: str,
    question: str,
) -> ProductView:
    base = build_product_analysis(store_root, project_id, snapshot_id, question)
    runtime = ProductRuntime(
        provider="local_evidence", model="snapshot-metric-analysis/v1", external_call=False
    )
    parent_turn = base.conversation.turns[0]
    # Every change has an independently scoped evidence reference validated by
    # the snapshot/comparison loader. No missing source is replaced by a label.
    by_id = {item.id: item for item in base.evidence}
    facts: list[tuple[str, str]] = []
    for change in base.snapshot.metric_changes:
        if change.evidence_id not in by_id:
            raise ProductError("store_integrity_error", "Metric evidence is unavailable.")
        facts.append((change.evidence_id, metric_observation_text(change)))
    if not facts:
        facts = [
            (
                base.claims[0].citation_ids[0],
                "The imported snapshot has no comparable metric change in this analysis scope.",
            )
        ]
    # Keep the full metric census in snapshot.metric_changes. Refuse oversized
    # claim renderings rather than silently omit favorable or adverse rows.
    if len(facts) > 20 or any(len(text) > 1000 for _, text in facts):
        raise ProductError("analysis_too_large", "The metric analysis exceeds this view limit.")
    turn_id = "p6-turn-" + canonical_project_sha256(
        {
            "project": project_id,
            "snapshot": snapshot_id,
            "question": question,
            "runtime": runtime.model_dump(mode="json"),
            "facts": facts,
        }
    )
    missing = parent_turn.missing_evidence
    claims = tuple(
        ProductClaim(
            id="p6-claim-"
            + canonical_project_sha256({"turn": turn_id, "evidence": eid, "text": text}),
            turn_id=turn_id,
            text=text,
            claim_type="evidence_statement",
            epistemic_state="observed",
            support="fully_supported",
            citation_ids=(eid,),
            counterevidence_ids=(),
            missing_evidence=missing,
            max_strength="observation",
        )
        for eid, text in facts
    )
    # Put the actual metric operands in cited evidence, so the readable cited
    # subset supports the numeric statement as well as its content-addressed source.
    texts = dict(facts)
    evidence = tuple(
        ProductEvidence.model_validate(
            {
                **item.model_dump(mode="python"),
                "text": texts.get(item.id, item.text),
            }
        )
        for item in base.evidence
    )
    summary = (
        f"Computed {len(base.snapshot.metric_changes)} metric changes from stored observations."
    )
    answer = "\n".join(text for _, text in facts) + "\nThese observations do not establish a cause."
    if len(answer) > 4000:
        raise ProductError("analysis_too_large", "The metric analysis exceeds this view limit.")
    result = base.result.model_copy(
        update={
            "summary": summary,
            "allowed_wording": summary,
            "caveat": "Actual local metric analysis; causal attribution and human usability are unverified.",
            "denominators": ProductDenominators(
                independent_families=None, contexts=1, outputs=1, claims=len(claims)
            ),
        }
    )
    turn = ProductTurn.model_validate(
        {
            **parent_turn.model_dump(mode="python"),
            "id": turn_id,
            "runtime": runtime,
            "answer": answer,
            "abstained": True,
        }
    )
    # Preserve the validated source projection. Evidence about a prior snapshot
    # remains available for citations without becoming observed in this snapshot.
    base_nodes = {node.id: node for node in base.graph.nodes}
    observed_evidence_ids = tuple(
        base_nodes[edge.source].source_id
        for edge in base.graph.edges
        if edge.kind == "OBSERVED_IN"
    )
    graph = build_product_graph(
        snapshot_id=snapshot_id,
        evidence_ids=tuple(item.id for item in evidence),
        claim_citations={item.id: item.citation_ids for item in claims},
        disposition_id=result.id,
        result_scope_id=turn_id,
        observed_evidence_ids=observed_evidence_ids,
    )
    checked = ProductView.model_validate(
        {
            **base.model_dump(mode="python"),
            "runtime": runtime,
            "result": result,
            "conversation": ProductConversation(id=base.conversation.id, turns=(turn,)),
            "claims": claims,
            "evidence": evidence,
            "graph": graph,
        }
    )
    return persist_product_result(store_root, checked)
