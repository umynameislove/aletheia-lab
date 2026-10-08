"""Scoped follow-up question and selection boundary contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"


def _parent(*, demo_only: bool = False) -> ProductView:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["demo_only"] = demo_only
    return ProductView.model_validate_json(json.dumps(payload))


def _technical_failure_parent() -> ProductView:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["demo_only"] = False
    payload["result"]["status"] = "technical_failure"
    payload["result"]["disposition"] = None
    payload["result"]["denominators"]["claims"] = 0
    payload["claims"] = []
    payload["conversation"]["turns"][-1]["missing_evidence"] = [
        item
        for item in payload["conversation"]["turns"][-1]["missing_evidence"]
        if item != COUNTERFACTUAL_NOT_AVAILABLE
    ]
    payload["graph"]["nodes"] = [
        node for node in payload["graph"]["nodes"] if node["kind"] in {"Snapshot", "EvidenceItem"}
    ]
    payload["graph"]["edges"] = [
        edge for edge in payload["graph"]["edges"] if edge["kind"] == "OBSERVED_IN"
    ]
    return ProductView.model_validate_json(json.dumps(payload))


def test_claim_selection_resolves_only_its_authorized_evidence() -> None:
    parent = _parent()
    claim = parent.claims[0]

    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="claim",
        selection_id=claim.id,
        question="What remains uncertain about this claim?",
    )

    assert selection.selection_kind == "claim"
    assert selection.selected_claim_id == claim.id
    assert selection.selected_node_kind is None
    assert selection.citation_ids == claim.citation_ids
    assert selection.counterevidence_ids == claim.counterevidence_ids
    assert selection.evidence_ids == tuple(
        sorted((*claim.citation_ids, *claim.counterevidence_ids))
    )


@pytest.mark.parametrize("node_kind", ["EvidenceItem", "AtomicClaim", "Snapshot", "Disposition"])
def test_node_selection_resolves_exact_visible_node(node_kind: str) -> None:
    parent = _parent()
    node = next(item for item in parent.graph.nodes if item.kind == node_kind)

    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="node",
        selection_id=node.id,
        question="What does this selected node establish?",
    )

    assert selection.selection_kind == "node"
    assert selection.selected_node_kind == node_kind
    assert set(selection.evidence_ids) <= {item.id for item in parent.evidence}
    if node_kind == "EvidenceItem":
        assert selection.evidence_ids == (node.source_id,)


@pytest.mark.parametrize("selection_kind", ["evidence", "Claim", "", "node "])
def test_selection_kind_is_closed_and_safe(selection_kind: str) -> None:
    parent = _parent()
    with pytest.raises(ProductError) as captured:
        resolve_product_follow_up_selection(
            parent,
            selection_kind=selection_kind,
            selection_id=parent.claims[0].id,
            question="Is this selection valid?",
        )

    assert captured.value.code == "invalid_selection_kind"
    if selection_kind:
        assert selection_kind not in captured.value.safe_message


def test_foreign_dangling_and_demo_selections_fail_closed() -> None:
    foreign_id = "p6-claim-" + "f" * 64
    with pytest.raises(ProductError) as foreign:
        resolve_product_follow_up_selection(
            _parent(),
            selection_kind="claim",
            selection_id=foreign_id,
            question="Resolve a foreign claim.",
        )
    assert foreign.value.code == "selection_not_available"
    assert foreign_id not in foreign.value.safe_message

    with pytest.raises(ProductError) as demo:
        resolve_product_follow_up_selection(
            _parent(demo_only=True),
            selection_kind="node",
            selection_id="demo-node-snapshot",
            question="Resolve a demo node.",
        )
    assert demo.value.code == "result_invalid"


@pytest.mark.parametrize(
    "question",
    [
        "",
        "  ",
        "trailing ",
        "line\nbreak",
        "control\x7fcharacter",
        "Cafe\u0301?",
        "x" * 2001,
    ],
)
def test_follow_up_reuses_strict_question_validation(question: str) -> None:
    parent = _parent()
    with pytest.raises(ProductError) as captured:
        resolve_product_follow_up_selection(
            parent,
            selection_kind="claim",
            selection_id=parent.claims[0].id,
            question=question,
        )

    assert captured.value.code == "invalid_question"
    if question:
        assert question not in captured.value.safe_message


@pytest.mark.parametrize("selection_kind", ["claim", "node"])
def test_follow_up_view_is_deterministic_and_preserves_parent_scope(
    selection_kind: str,
) -> None:
    parent = _parent()
    selection_id = (
        parent.claims[0].id
        if selection_kind == "claim"
        else next(node.id for node in parent.graph.nodes if node.kind == "EvidenceItem")
    )
    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind=selection_kind,
        selection_id=selection_id,
        question="What remains supported within this exact selection?",
    )

    first = build_product_follow_up_view(parent, selection)
    second = build_product_follow_up_view(parent, selection)

    assert first == second
    assert first.project == parent.project
    assert first.snapshot == parent.snapshot
    assert first.visibility == parent.visibility
    assert first.runtime == parent.runtime
    assert first.evidence == parent.evidence
    assert first.conversation.turns[:-1] == parent.conversation.turns
    assert first.claims[:-1] == parent.claims
    assert first.conversation.turns[-1].visible_evidence_ids == selection.evidence_ids
    assert first.claims[-1].turn_id == first.conversation.turns[-1].id
    assert first.claims[-1].claim_type == "uncertainty_statement"
    assert first.claims[-1].max_strength == "abstain"


def test_follow_up_preserves_citation_and_counterevidence_roles() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["demo_only"] = False
    counterevidence_id = "demo-evidence-counter"
    payload["evidence"].append(
        {
            "id": counterevidence_id,
            "kind": "regression_candidate",
            "title": "Counterevidence candidate",
            "text": "A separate authorized item counters the selected claim.",
            "reproduction_ref": {
                "record_id": "demo-event-counter",
                "record_kind": "regression_event",
            },
            "source_sha256": "f" * 64,
            "visibility": "diagnosis",
            "redacted": False,
        }
    )
    snapshot_node_id = next(
        node["id"] for node in payload["graph"]["nodes"] if node["kind"] == "Snapshot"
    )
    counterevidence_node_id = "demo-node-evidence-counter"
    payload["graph"]["nodes"].append(
        {
            "id": counterevidence_node_id,
            "kind": "EvidenceItem",
            "source_id": counterevidence_id,
            "visibility": "diagnosis",
        }
    )
    payload["graph"]["edges"].append(
        {
            "id": "demo-edge-observed-counter",
            "kind": "OBSERVED_IN",
            "source": counterevidence_node_id,
            "target": snapshot_node_id,
            "visibility": "diagnosis",
        }
    )
    claim = payload["claims"][0]
    claim["citation_ids"] = [payload["evidence"][0]["id"]]
    claim["counterevidence_ids"] = [counterevidence_id]
    parent = ProductView.model_validate_json(json.dumps(payload))
    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="claim",
        selection_id=parent.claims[0].id,
        question="Which evidence supports or counters this claim?",
    )

    child = build_product_follow_up_view(parent, selection)

    assert child.claims[-1].citation_ids == parent.claims[0].citation_ids
    assert child.claims[-1].counterevidence_ids == parent.claims[0].counterevidence_ids
    assert set(child.conversation.turns[-1].visible_evidence_ids) == {
        *parent.claims[0].citation_ids,
        *parent.claims[0].counterevidence_ids,
    }


def test_technical_failure_follow_up_does_not_create_a_diagnosis() -> None:
    parent = _technical_failure_parent()
    node = next(item for item in parent.graph.nodes if item.kind == "EvidenceItem")
    selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="node",
        selection_id=node.id,
        question="Can this technical failure support a diagnosis?",
    )

    child = build_product_follow_up_view(parent, selection)

    assert child.result.status == "technical_failure"
    assert child.result.disposition is None
    assert child.claims == ()
    assert child.conversation.turns[:-1] == parent.conversation.turns
    assert child.conversation.turns[-1].abstained is True
    assert COUNTERFACTUAL_NOT_AVAILABLE not in child.model_dump_json()
    assert {node.kind for node in child.graph.nodes} <= {"Snapshot", "EvidenceItem"}
    assert {edge.kind for edge in child.graph.edges} <= {"OBSERVED_IN"}


def test_follow_up_disposition_node_identity_is_scoped_to_the_child_turn() -> None:
    parent = _parent()
    first_selection = resolve_product_follow_up_selection(
        parent,
        selection_kind="claim",
        selection_id=parent.claims[0].id,
        question="What remains uncertain about this claim?",
    )
    first = build_product_follow_up_view(parent, first_selection)
    second_selection = resolve_product_follow_up_selection(
        first,
        selection_kind="claim",
        selection_id=first.claims[-1].id,
        question="What remains uncertain after that follow-up?",
    )
    second = build_product_follow_up_view(first, second_selection)

    parent_disposition = next(node for node in parent.graph.nodes if node.kind == "Disposition")
    first_disposition = next(node for node in first.graph.nodes if node.kind == "Disposition")
    second_disposition = next(node for node in second.graph.nodes if node.kind == "Disposition")

    assert len({parent_disposition.id, first_disposition.id, second_disposition.id}) == 3
    with pytest.raises(ProductError) as captured:
        resolve_product_follow_up_selection(
            second,
            selection_kind="node",
            selection_id=first_disposition.id,
            question="Can a disposition from another result be selected here?",
        )
    assert captured.value.code == "selection_not_available"
