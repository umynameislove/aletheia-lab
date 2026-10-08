"""Safe selection resolution for deterministic scoped product follow-ups."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.results import load_product_result, persist_product_result
from aletheia_lab.product.view import (
    ProductClaim,
    ProductConversation,
    ProductDenominators,
    ProductResult,
    ProductTurn,
    ProductView,
)
from aletheia_lab.project.identity import canonical_project_sha256

SelectionKind: TypeAlias = Literal["claim", "node"]

_SELECTION_KIND_MESSAGE: Final[str] = "The follow-up selection kind is not valid."
_SELECTION_UNAVAILABLE_MESSAGE: Final[str] = (
    "The follow-up selection is not available in this result."
)
_RESULT_INVALID_MESSAGE: Final[str] = "The product result is not valid for follow-up."
_PENDING_RESULT_ID: Final[str] = "p6-result-" + "0" * 64


@dataclass(frozen=True, slots=True)
class ProductFollowUpSelection:
    """One exact selection resolved only against an immutable parent view."""

    selection_kind: SelectionKind
    selection_id: str
    selected_claim_id: str | None
    selected_node_kind: str | None
    citation_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    question: str


def resolve_product_follow_up_selection(
    parent: ProductView,
    *,
    selection_kind: str,
    selection_id: str,
    question: str,
) -> ProductFollowUpSelection:
    """Resolve a claim or node without fuzzy matching or evidence-scope expansion."""

    checked_question = checked_product_question(question)
    checked_parent = _checked_parent(parent)
    if selection_kind not in {"claim", "node"}:
        raise ProductError("invalid_selection_kind", _SELECTION_KIND_MESSAGE)
    if not isinstance(selection_id, str):
        raise ProductError("selection_not_available", _SELECTION_UNAVAILABLE_MESSAGE)
    if selection_kind == "claim":
        return _claim_selection(checked_parent, selection_id, checked_question)
    return _node_selection(checked_parent, selection_id, checked_question)


def _claim_selection(
    parent: ProductView,
    selection_id: str,
    question: str,
) -> ProductFollowUpSelection:
    claim = next((item for item in parent.claims if item.id == selection_id), None)
    if claim is None:
        raise ProductError("selection_not_available", _SELECTION_UNAVAILABLE_MESSAGE)
    return ProductFollowUpSelection(
        selection_kind="claim",
        selection_id=selection_id,
        selected_claim_id=claim.id,
        selected_node_kind=None,
        citation_ids=claim.citation_ids,
        counterevidence_ids=claim.counterevidence_ids,
        evidence_ids=tuple(sorted((*claim.citation_ids, *claim.counterevidence_ids))),
        question=question,
    )


def _node_selection(
    parent: ProductView,
    selection_id: str,
    question: str,
) -> ProductFollowUpSelection:
    node = next((item for item in parent.graph.nodes if item.id == selection_id), None)
    if node is None:
        raise ProductError("selection_not_available", _SELECTION_UNAVAILABLE_MESSAGE)
    claim_id: str | None = None
    citation_ids: tuple[str, ...] = ()
    counterevidence_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    if node.kind == "EvidenceItem":
        citation_ids = (node.source_id,)
        evidence_ids = citation_ids
    elif node.kind == "AtomicClaim":
        claim = next((item for item in parent.claims if item.id == node.source_id), None)
        if claim is None:
            raise ProductError("selection_not_available", _SELECTION_UNAVAILABLE_MESSAGE)
        claim_id = claim.id
        citation_ids = claim.citation_ids
        counterevidence_ids = claim.counterevidence_ids
        evidence_ids = tuple(sorted((*citation_ids, *counterevidence_ids)))
    return ProductFollowUpSelection(
        selection_kind="node",
        selection_id=selection_id,
        selected_claim_id=claim_id,
        selected_node_kind=node.kind,
        citation_ids=citation_ids,
        counterevidence_ids=counterevidence_ids,
        evidence_ids=evidence_ids,
        question=question,
    )


def build_product_follow_up_view(
    parent: ProductView,
    selection: ProductFollowUpSelection,
) -> ProductView:
    """Build one deterministic child view without mutating or widening its parent."""

    checked_parent = _checked_parent(parent)
    resolved = resolve_product_follow_up_selection(
        checked_parent,
        selection_kind=selection.selection_kind,
        selection_id=selection.selection_id,
        question=selection.question,
    )
    if resolved != selection:
        raise ProductError("selection_not_available", _SELECTION_UNAVAILABLE_MESSAGE)
    identity = {
        "parent_result_id": checked_parent.result.id,
        "selection_kind": resolved.selection_kind,
        "selection_id": resolved.selection_id,
        "question": resolved.question,
    }
    turn_id = _scoped_id("p6-turn", identity)
    missing_evidence = checked_parent.conversation.turns[-1].missing_evidence
    technical_failure = checked_parent.result.status == "technical_failure"
    new_claim = None if technical_failure else _follow_up_claim(turn_id, resolved, missing_evidence)
    claims = checked_parent.claims + (() if new_claim is None else (new_claim,))
    result = (
        _technical_failure_result(checked_parent)
        if technical_failure
        else _complete_result(checked_parent, len(claims))
    )
    turn = ProductTurn(
        id=turn_id,
        snapshot_id=checked_parent.snapshot.id,
        result_id=_PENDING_RESULT_ID,
        runtime=checked_parent.runtime,
        question=resolved.question,
        answer=(
            "The scoped follow-up remains a technical failure; no diagnosis was produced."
            if technical_failure
            else "The selected evidence does not establish a cause; the scoped follow-up abstains."
        ),
        abstained=True,
        visible_evidence_ids=resolved.evidence_ids,
        missing_evidence=missing_evidence,
    )
    graph_nodes = {node.id: node for node in checked_parent.graph.nodes}
    observed_evidence_ids = tuple(
        sorted(
            graph_nodes[edge.source].source_id
            for edge in checked_parent.graph.edges
            if edge.kind == "OBSERVED_IN"
        )
    )
    graph = build_product_graph(
        snapshot_id=checked_parent.snapshot.id,
        evidence_ids=tuple(item.id for item in checked_parent.evidence),
        claim_citations={claim.id: claim.citation_ids for claim in claims},
        disposition_id=None if technical_failure else _PENDING_RESULT_ID,
        result_scope_id=None if technical_failure else turn_id,
        observed_evidence_ids=observed_evidence_ids,
    )
    return ProductView(
        schema_version=checked_parent.schema_version,
        demo_only=False,
        mode=checked_parent.mode,
        visibility=checked_parent.visibility,
        project=checked_parent.project,
        snapshot=checked_parent.snapshot,
        runtime=checked_parent.runtime,
        result=result,
        conversation=ProductConversation(
            id=checked_parent.conversation.id,
            turns=(*checked_parent.conversation.turns, turn),
        ),
        claims=claims,
        evidence=checked_parent.evidence,
        graph=graph,
    )


def follow_up_product_result(
    store_root: Path,
    result_id: str,
    selection_kind: str,
    selection_id: str,
    question: str,
) -> ProductView:
    """Persist one scoped immutable child of an integrity-checked result."""

    parent = load_product_result(store_root, result_id)
    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind=selection_kind,
        selection_id=selection_id,
        question=question,
    )
    child = build_product_follow_up_view(parent, selection)
    return persist_product_result(
        store_root,
        child,
        parent_result_id=parent.result.id,
    )


def _checked_parent(parent: ProductView) -> ProductView:
    try:
        checked = ProductView.model_validate(parent.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        raise ProductError("result_invalid", _RESULT_INVALID_MESSAGE) from None
    if checked.demo_only:
        raise ProductError("result_invalid", _RESULT_INVALID_MESSAGE)
    return checked


def _follow_up_claim(
    turn_id: str,
    selection: ProductFollowUpSelection,
    missing_evidence: tuple[str, ...],
) -> ProductClaim:
    claim_id = _scoped_id(
        "p6-claim",
        {
            "turn_id": turn_id,
            "citation_ids": list(selection.citation_ids),
            "counterevidence_ids": list(selection.counterevidence_ids),
        },
    )
    return ProductClaim(
        id=claim_id,
        turn_id=turn_id,
        text="The scoped follow-up does not establish a cause from the selected evidence.",
        claim_type="uncertainty_statement",
        epistemic_state="abstained",
        support="not_estimated",
        citation_ids=selection.citation_ids,
        counterevidence_ids=selection.counterevidence_ids,
        missing_evidence=missing_evidence,
        max_strength="abstain",
    )


def _complete_result(parent: ProductView, claim_count: int) -> ProductResult:
    return ProductResult(
        id=_PENDING_RESULT_ID,
        status="complete",
        disposition="abstain",
        causal_status="unverified",
        summary="The scoped follow-up remains bounded to the parent's authorized evidence.",
        denominators=ProductDenominators(
            independent_families=None,
            contexts=parent.result.denominators.contexts,
            outputs=parent.result.denominators.outputs,
            claims=claim_count,
        ),
        allowed_wording="The selected evidence remains available within the parent result.",
        forbidden_wording="The follow-up establishes a cause or introduces new evidence.",
        caveat="Deterministic offline follow-up; conversation history is not evidence.",
    )


def _technical_failure_result(parent: ProductView) -> ProductResult:
    return ProductResult(
        id=_PENDING_RESULT_ID,
        status="technical_failure",
        disposition=None,
        causal_status="unverified",
        summary="The scoped follow-up remains a technical failure; no diagnosis was produced.",
        denominators=ProductDenominators(
            independent_families=None,
            contexts=parent.result.denominators.contexts,
            outputs=parent.result.denominators.outputs,
            claims=0,
        ),
        allowed_wording="The scoped follow-up could not produce a diagnosis.",
        forbidden_wording="The technical failure establishes a project diagnosis.",
        caveat="No claim was generated from the technical failure.",
    )


def _scoped_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_project_sha256(payload)}"


__all__ = [
    "ProductFollowUpSelection",
    "SelectionKind",
    "build_product_follow_up_view",
    "follow_up_product_result",
    "resolve_product_follow_up_selection",
]
