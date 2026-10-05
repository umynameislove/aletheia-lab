"""Deterministic offline ProductView construction from diagnosis-visible evidence."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence import (
    ProductEvidenceItem,
    ProductEvidenceProjection,
    resolve_product_evidence_references,
)
from aletheia_lab.product.evidence_store import load_diagnosis_evidence_projection
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import ProductView
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import EvidenceRole
from aletheia_lab.project.snapshots import ProjectSnapshot

_PENDING_RESULT_ID: Final[str] = "p6-result-" + "0" * 64
_QUESTION_INVALID_MESSAGE: Final[str] = "The analysis question is not valid."
_SOURCE_INTEGRITY_MESSAGE: Final[str] = "Stored project evidence failed its integrity check."
_ROLE_PRESENTATION: Final[dict[EvidenceRole, tuple[str, str, str]]] = {
    "before_snapshot": (
        "snapshot",
        "Baseline project snapshot",
        "A baseline snapshot is bound to this regression evidence generation.",
    ),
    "after_snapshot": (
        "snapshot",
        "Observed project snapshot",
        "The analyzed project snapshot is bound to this regression evidence generation.",
    ),
    "snapshot_comparison": (
        "snapshot_comparison",
        "Stored snapshot comparison",
        "The stored comparison records a change between two immutable project snapshots.",
    ),
    "metric_change": (
        "metric_observation",
        "Observed metric change",
        "The stored evidence records an observed metric change without assigning a cause.",
    ),
    "regression_candidate": (
        "regression_candidate",
        "Regression candidate",
        "The stored event is a non-causal regression candidate with unverified causal status.",
    ),
}
_CITATION_ROLE_ORDER: Final[tuple[EvidenceRole, ...]] = (
    "snapshot_comparison",
    "metric_change",
    "regression_candidate",
)


def analyze_product_mock(
    store_root: Path,
    project_id: str,
    snapshot_id: str,
    question: str,
) -> ProductView:
    """Create and persist one deterministic result without network or hidden evidence."""

    checked_question = _checked_question(question)
    projection = load_diagnosis_evidence_projection(
        store_root,
        project_id=project_id,
        snapshot_id=snapshot_id,
    )
    snapshot = _load_snapshot(store_root, project_id, snapshot_id)
    baseline_id = _singleton_source_id(projection, "before_snapshot")
    visible_ids = tuple(item.evidence_id for item in projection.items)
    citation_ids = _citation_ids(projection)
    references = resolve_product_evidence_references(
        projection,
        citation_ids=citation_ids,
        counterevidence_ids=(),
        visible_evidence_ids=visible_ids,
    )
    missing_evidence = _missing_evidence(projection)
    identity = {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "question": checked_question,
        "projection_sha256": projection.projection_sha256,
    }
    turn_id = _scoped_id("p6-turn", identity)
    claim_id = _scoped_id(
        "p6-claim",
        {**identity, "turn_id": turn_id, "citation_ids": list(references.citation_ids)},
    )
    conversation_id = _scoped_id(
        "p6-conversation",
        {"project_id": project_id, "snapshot_id": snapshot_id},
    )
    evidence = tuple(_product_evidence(item) for item in projection.items)
    runtime = {
        "provider": "deterministic_mock",
        "model": "p6-deterministic-mock/v1",
        "external_call": False,
    }
    payload = {
        "schema_version": "p6-product-view/v1",
        "demo_only": False,
        "mode": "project_audit",
        "visibility": "diagnosis",
        "project": {"id": project_id, "display_name": "Imported project"},
        "snapshot": {
            "id": snapshot_id,
            "baseline_id": baseline_id,
            "sha256": snapshot.state_sha256,
        },
        "runtime": runtime,
        "result": {
            "id": _PENDING_RESULT_ID,
            "status": "complete",
            "disposition": "abstain",
            "causal_status": "unverified",
            "summary": (
                "A stored project change is visible, but the authorized evidence does not "
                "establish one cause."
            ),
            "denominators": {
                "independent_families": None,
                "contexts": 1,
                "outputs": 1,
                "claims": 1,
            },
            "allowed_wording": "The stored evidence records an observed project change.",
            "forbidden_wording": "One visible project artifact caused the observed change.",
            "caveat": (
                "Deterministic offline project audit; no causal ground truth or model-quality "
                "result."
            ),
        },
        "conversation": {
            "id": conversation_id,
            "turns": [
                {
                    "id": turn_id,
                    "snapshot_id": snapshot_id,
                    "result_id": _PENDING_RESULT_ID,
                    "runtime": runtime,
                    "question": checked_question,
                    "answer": (
                        "The change is observed in diagnosis-visible evidence; its cause remains "
                        "unverified."
                    ),
                    "abstained": True,
                    "visible_evidence_ids": list(references.visible_evidence_ids),
                    "missing_evidence": list(missing_evidence),
                }
            ],
        },
        "claims": [
            {
                "id": claim_id,
                "turn_id": turn_id,
                "text": "The stored project evidence records an observed snapshot change.",
                "epistemic_state": "observed",
                "support": "fully_supported",
                "citation_ids": list(references.citation_ids),
                "counterevidence_ids": list(references.counterevidence_ids),
                "missing_evidence": list(missing_evidence),
                "max_strength": "observation",
            }
        ],
        "evidence": list(evidence),
        "graph": _minimal_graph(
            snapshot_id=snapshot_id,
            evidence_ids=references.visible_evidence_ids,
            citation_ids=references.citation_ids,
            claim_id=claim_id,
        ),
    }
    view = ProductView.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
    return persist_product_result(store_root, view)


def _checked_question(question: str) -> str:
    if (
        not isinstance(question, str)
        or not question
        or question != question.strip()
        or len(question) > 2000
        or unicodedata.normalize("NFC", question) != question
        or any(ord(character) < 32 or ord(character) == 127 for character in question)
    ):
        raise ProductError("invalid_question", _QUESTION_INVALID_MESSAGE)
    return question


def _load_snapshot(store_root: Path, project_id: str, snapshot_id: str) -> ProjectSnapshot:
    try:
        with ProjectStore(store_root) as store:
            snapshot = store.load(snapshot_id)
        if not isinstance(snapshot, ProjectSnapshot) or snapshot.project_id != project_id:
            raise ValueError("snapshot scope mismatch")
        return snapshot
    except ProductError:
        raise
    except Exception:
        raise ProductError("store_integrity_error", _SOURCE_INTEGRITY_MESSAGE) from None


def _singleton_source_id(projection: ProductEvidenceProjection, role: EvidenceRole) -> str:
    matches = tuple(item.source_id for item in projection.items if item.role == role)
    if len(matches) != 1:
        raise ProductError("evidence_not_available", "Required evidence is not available.")
    return matches[0]


def _citation_ids(projection: ProductEvidenceProjection) -> tuple[str, ...]:
    by_role = {item.role: item.evidence_id for item in projection.items}
    selected = tuple(by_role[role] for role in _CITATION_ROLE_ORDER if role in by_role)
    if not selected:
        raise ProductError("evidence_not_available", "Required evidence is not available.")
    return selected


def _missing_evidence(projection: ProductEvidenceProjection) -> tuple[str, ...]:
    missing = tuple(
        f"Missing evidence category: {role.replace('_', ' ')}"
        for role in projection.missing_categories
    )
    omitted = tuple(
        f"Omitted evidence category: {role.replace('_', ' ')}"
        for role in projection.omitted_categories
    )
    return (*missing, *omitted)


def _product_evidence(item: ProductEvidenceItem) -> dict[str, object]:
    kind, title, text = _ROLE_PRESENTATION[item.role]
    return {
        "id": item.evidence_id,
        "kind": kind,
        "title": title,
        "text": text,
        "relative_path": f"project-store/{item.role}.json",
        "source_sha256": item.source_sha256,
        "visibility": "diagnosis",
        "redacted": False,
    }


def _minimal_graph(
    *,
    snapshot_id: str,
    evidence_ids: tuple[str, ...],
    citation_ids: tuple[str, ...],
    claim_id: str,
) -> dict[str, object]:
    snapshot_node = _scoped_id("p6-node", {"kind": "Snapshot", "source_id": snapshot_id})
    claim_node = _scoped_id("p6-node", {"kind": "AtomicClaim", "source_id": claim_id})
    disposition_node = _scoped_id(
        "p6-node", {"kind": "Disposition", "source_id": _PENDING_RESULT_ID}
    )
    evidence_nodes = {
        evidence_id: _scoped_id("p6-node", {"kind": "EvidenceItem", "source_id": evidence_id})
        for evidence_id in evidence_ids
    }
    nodes = [
        {"id": snapshot_node, "kind": "Snapshot", "source_id": snapshot_id},
        {"id": claim_node, "kind": "AtomicClaim", "source_id": claim_id},
        {
            "id": disposition_node,
            "kind": "Disposition",
            "source_id": _PENDING_RESULT_ID,
        },
        *(
            {"id": node_id, "kind": "EvidenceItem", "source_id": evidence_id}
            for evidence_id, node_id in evidence_nodes.items()
        ),
    ]
    edges = [
        _edge("OBSERVED_IN", evidence_nodes[evidence_id], snapshot_node)
        for evidence_id in evidence_ids
    ]
    edges.extend(
        _edge("CITES", claim_node, evidence_nodes[evidence_id]) for evidence_id in citation_ids
    )
    edges.append(_edge("ASSIGNED_DISPOSITION", claim_node, disposition_node))
    return {
        "nodes": [
            {**node, "visibility": "diagnosis"}
            for node in sorted(nodes, key=lambda value: str(value["id"]))
        ],
        "edges": sorted(edges, key=lambda value: str(value["id"])),
    }


def _edge(kind: str, source: str, target: str) -> dict[str, object]:
    return {
        "id": _scoped_id("p6-edge", {"kind": kind, "source": source, "target": target}),
        "kind": kind,
        "source": source,
        "target": target,
        "visibility": "diagnosis",
    }


def _scoped_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_project_sha256(payload)}"


__all__ = ["analyze_product_mock"]
