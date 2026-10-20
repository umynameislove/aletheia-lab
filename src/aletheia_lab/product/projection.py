"""Deterministic product graph projection over authorized P3 lineage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence import ProductEvidenceItem, ProductEvidenceProjection
from aletheia_lab.product.view import ProductGraph, ProductGraphEdge, ProductGraphNode
from aletheia_lab.project.identity import SHA256_PATTERN, canonical_project_sha256
from aletheia_lab.project.lineage import (
    ProjectLineageGraph,
    ProjectLineageNode,
    project_lineage,
    project_lineage_table,
)

_GRAPH_INVALID_MESSAGE: Final[str] = "Stored graph provenance failed its integrity check."
COUNTERFACTUAL_STATUS_NOT_AVAILABLE: Final[Literal["not_available"]] = "not_available"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class ProductGraphTextPath(_StrictFrozenModel):
    """Text rendering of one graph edge using the exact graph identifiers."""

    edge_id: str
    source_node_id: str
    relationship: str
    target_node_id: str
    text: str

    @model_validator(mode="after")
    def _text_uses_exact_graph_ids(self) -> Self:
        expected = (
            f"{self.source_node_id} -[{self.relationship}:{self.edge_id}]-> {self.target_node_id}"
        )
        if self.text != expected:
            raise ValueError("product textual path does not use its graph identifiers")
        return self


class ProductGraphProjection(_StrictFrozenModel):
    """Canonical source shared by graph, row-oriented table and text surfaces."""

    p3_graph_id: str
    p3_table_sha256: str = Field(pattern=SHA256_PATTERN)
    counterfactual_status: Literal["not_available"]
    graph: ProductGraph
    node_rows: tuple[ProductGraphNode, ...]
    edge_rows: tuple[ProductGraphEdge, ...]
    textual_paths: tuple[ProductGraphTextPath, ...]
    projection_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _surfaces_reconcile(self) -> Self:
        if self.node_rows != self.graph.nodes or self.edge_rows != self.graph.edges:
            raise ValueError("product graph and table rows do not reconcile")
        if tuple(path.edge_id for path in self.textual_paths) != tuple(
            edge.id for edge in self.graph.edges
        ):
            raise ValueError("product textual paths do not reconcile with graph edges")
        for path, edge in zip(self.textual_paths, self.graph.edges, strict=True):
            if (
                path.source_node_id,
                path.relationship,
                path.target_node_id,
            ) != (edge.source, edge.kind, edge.target):
                raise ValueError("product textual path differs from its graph edge")
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if self.projection_sha256 != canonical_project_sha256(payload):
            raise ValueError("product graph projection hash does not reconcile")
        return self


def build_product_graph_projection(
    lineage: ProjectLineageGraph,
    evidence: ProductEvidenceProjection,
    *,
    snapshot_id: str,
    claim_citations: Mapping[str, tuple[str, ...]],
    disposition_id: str | None,
    result_scope_id: str | None = None,
) -> ProductGraphProjection:
    """Build one visibility-safe product graph from verified P3 provenance."""

    try:
        checked_evidence = ProductEvidenceProjection.model_validate(
            evidence.model_dump(mode="python")
        )
        projected_lineage = project_lineage(lineage, "diagnosis")
        lineage_table = project_lineage_table(lineage, "diagnosis")
        if (
            checked_evidence.visibility != "diagnosis"
            or checked_evidence.project_id != projected_lineage.project_id
            or lineage_table.graph_id != projected_lineage.graph_id
        ):
            raise ValueError("product graph inputs belong to different projections")
        lineage_nodes = _verify_lineage_sources(projected_lineage, checked_evidence, snapshot_id)
        _verify_lineage_relationships(projected_lineage, checked_evidence, lineage_nodes)
        observed_evidence_ids = _observed_evidence_ids(checked_evidence)
        graph = build_product_graph(
            snapshot_id=snapshot_id,
            evidence_ids=tuple(item.evidence_id for item in checked_evidence.items),
            claim_citations=claim_citations,
            disposition_id=disposition_id,
            result_scope_id=result_scope_id,
            observed_evidence_ids=observed_evidence_ids,
        )
        paths = tuple(
            ProductGraphTextPath(
                edge_id=edge.id,
                source_node_id=edge.source,
                relationship=edge.kind,
                target_node_id=edge.target,
                text=f"{edge.source} -[{edge.kind}:{edge.id}]-> {edge.target}",
            )
            for edge in graph.edges
        )
        payload = {
            "p3_graph_id": projected_lineage.graph_id,
            "p3_table_sha256": lineage_table.table_sha256,
            "counterfactual_status": COUNTERFACTUAL_STATUS_NOT_AVAILABLE,
            "graph": graph.model_dump(mode="json"),
            "node_rows": [node.model_dump(mode="json") for node in graph.nodes],
            "edge_rows": [edge.model_dump(mode="json") for edge in graph.edges],
            "textual_paths": [path.model_dump(mode="json") for path in paths],
        }
        return ProductGraphProjection(
            p3_graph_id=projected_lineage.graph_id,
            p3_table_sha256=lineage_table.table_sha256,
            counterfactual_status=COUNTERFACTUAL_STATUS_NOT_AVAILABLE,
            graph=graph,
            node_rows=graph.nodes,
            edge_rows=graph.edges,
            textual_paths=paths,
            projection_sha256=canonical_project_sha256(payload),
        )
    except ProductError:
        raise
    except (TypeError, ValueError):
        raise ProductError("graph_projection_invalid", _GRAPH_INVALID_MESSAGE) from None


def _verify_lineage_sources(
    lineage: ProjectLineageGraph,
    evidence: ProductEvidenceProjection,
    snapshot_id: str,
) -> dict[str, ProjectLineageNode]:
    if not any(node.kind == "snapshot" and node.source_id == snapshot_id for node in lineage.nodes):
        raise ValueError("product snapshot is absent from authorized lineage")
    after_snapshot_ids = {
        item.source_id for item in evidence.items if item.role == "after_snapshot"
    }
    if after_snapshot_ids and after_snapshot_ids != {snapshot_id}:
        raise ValueError("product evidence belongs to a different analyzed snapshot")
    expected_kinds = {
        "before_snapshot": "snapshot",
        "after_snapshot": "snapshot",
        "snapshot_comparison": "snapshot_comparison",
        "metric_change": "metric_change",
        "regression_candidate": "regression_candidate",
    }
    matched_nodes: dict[str, ProjectLineageNode] = {}
    for item in evidence.items:
        matches = tuple(
            node
            for node in lineage.nodes
            if node.kind == expected_kinds[item.role] and node.source_id == item.source_id
        )
        if len(matches) != 1:
            raise ValueError("product evidence is absent from authorized lineage")
        if item.role not in {"before_snapshot", "after_snapshot"} and (
            matches[0].source_sha256 != item.source_sha256
        ):
            raise ValueError("product evidence hash differs from authorized lineage")
        matched_nodes[item.evidence_id] = matches[0]
    return matched_nodes


def _verify_lineage_relationships(
    lineage: ProjectLineageGraph,
    evidence: ProductEvidenceProjection,
    nodes: Mapping[str, ProjectLineageNode],
) -> None:
    by_role: dict[str, list[ProductEvidenceItem]] = {}
    for item in evidence.items:
        by_role.setdefault(item.role, []).append(item)
    singleton_roles = (
        "before_snapshot",
        "after_snapshot",
        "snapshot_comparison",
        "regression_candidate",
    )
    if any(len(by_role.get(role, ())) != 1 for role in singleton_roles) or not by_role.get(
        "metric_change"
    ):
        raise ValueError("product evidence roles do not match regression lineage")

    before = by_role["before_snapshot"][0]
    after = by_role["after_snapshot"][0]
    comparison = by_role["snapshot_comparison"][0]
    event = by_role["regression_candidate"][0]
    metrics = by_role["metric_change"]
    snapshot_links = tuple(sorted((before.evidence_id, after.evidence_id)))
    if comparison.provenance_links != snapshot_links or any(
        item.provenance_links != snapshot_links for item in metrics
    ):
        raise ValueError("product evidence snapshot provenance does not reconcile")
    expected_event_links = tuple(
        sorted((comparison.evidence_id, *(item.evidence_id for item in metrics)))
    )
    if event.provenance_links != expected_event_links:
        raise ValueError("product evidence event provenance does not reconcile")

    bundle_nodes = tuple(
        node
        for node in lineage.nodes
        if node.kind == "evidence_bundle" and node.source_id == evidence.evidence_bundle_id
    )
    if len(bundle_nodes) != 1 or bundle_nodes[
        0
    ].source_sha256 != evidence.evidence_bundle_id.removeprefix("p3-evidence-bundle-"):
        raise ValueError("product evidence bundle is absent from authorized lineage")
    project_node = next(node for node in lineage.nodes if node.kind == "project")
    required_edges = {
        (project_node.node_id, nodes[before.evidence_id].node_id, "contains"),
        (project_node.node_id, nodes[after.evidence_id].node_id, "contains"),
        (
            nodes[comparison.evidence_id].node_id,
            nodes[before.evidence_id].node_id,
            "compares_before",
        ),
        (
            nodes[comparison.evidence_id].node_id,
            nodes[after.evidence_id].node_id,
            "compares_after",
        ),
        (
            nodes[comparison.evidence_id].node_id,
            nodes[event.evidence_id].node_id,
            "qualifies",
        ),
        (bundle_nodes[0].node_id, nodes[event.evidence_id].node_id, "supports"),
    }
    required_edges.update(
        (
            nodes[comparison.evidence_id].node_id,
            nodes[item.evidence_id].node_id,
            "reports",
        )
        for item in metrics
    )
    required_edges.update(
        (nodes[item.evidence_id].node_id, nodes[event.evidence_id].node_id, "supports")
        for item in metrics
    )
    lineage_edges = {
        (edge.source_node_id, edge.target_node_id, edge.relationship) for edge in lineage.edges
    }
    if not required_edges <= lineage_edges:
        raise ValueError("product evidence relationships are absent from authorized lineage")


def _observed_evidence_ids(evidence: ProductEvidenceProjection) -> tuple[str, ...]:
    after = next(item for item in evidence.items if item.role == "after_snapshot")
    return tuple(
        sorted(
            item.evidence_id
            for item in evidence.items
            if item.evidence_id == after.evidence_id or after.evidence_id in item.provenance_links
        )
    )


def build_product_graph(
    *,
    snapshot_id: str,
    evidence_ids: tuple[str, ...],
    claim_citations: Mapping[str, tuple[str, ...]],
    disposition_id: str | None,
    result_scope_id: str | None = None,
    observed_evidence_ids: tuple[str, ...] | None = None,
) -> ProductGraph:
    known_evidence = set(evidence_ids)
    observed_ids = evidence_ids if observed_evidence_ids is None else observed_evidence_ids
    ordered_claims = tuple(sorted(claim_citations.items()))
    if len(observed_ids) != len(set(observed_ids)) or not set(observed_ids) <= known_evidence:
        raise ValueError("product observed evidence is outside the authorized projection")
    if any(not set(citations) <= known_evidence for _, citations in ordered_claims):
        raise ValueError("product claim cites evidence outside the authorized projection")
    if bool(ordered_claims) != (disposition_id is not None):
        raise ValueError("product disposition presence does not match its claims")
    if disposition_id is None and result_scope_id is not None:
        raise ValueError("product result scope requires a disposition")

    snapshot_node_id = _scoped_id("p6-node", {"kind": "Snapshot", "source_id": snapshot_id})
    evidence_nodes = {
        evidence_id: _scoped_id("p6-node", {"kind": "EvidenceItem", "source_id": evidence_id})
        for evidence_id in sorted(evidence_ids)
    }
    claim_nodes = {
        claim_id: _scoped_id("p6-node", {"kind": "AtomicClaim", "source_id": claim_id})
        for claim_id, _ in ordered_claims
    }
    nodes = [
        ProductGraphNode(
            id=snapshot_node_id,
            kind="Snapshot",
            source_id=snapshot_id,
            visibility="diagnosis",
        ),
        *(
            ProductGraphNode(
                id=node_id,
                kind="EvidenceItem",
                source_id=evidence_id,
                visibility="diagnosis",
            )
            for evidence_id, node_id in evidence_nodes.items()
        ),
        *(
            ProductGraphNode(
                id=node_id,
                kind="AtomicClaim",
                source_id=claim_id,
                visibility="diagnosis",
            )
            for claim_id, node_id in claim_nodes.items()
        ),
    ]
    disposition_node_id: str | None = None
    if disposition_id is not None:
        disposition_node_id = _scoped_id(
            "p6-node",
            {
                "kind": "Disposition",
                "source_id": disposition_id,
                "result_scope_id": result_scope_id or disposition_id,
            },
        )
        nodes.append(
            ProductGraphNode(
                id=disposition_node_id,
                kind="Disposition",
                source_id=disposition_id,
                visibility="diagnosis",
            )
        )

    edges = [
        _edge("OBSERVED_IN", evidence_nodes[evidence_id], snapshot_node_id)
        for evidence_id in sorted(observed_ids)
    ]
    for claim_id, citations in ordered_claims:
        edges.extend(
            _edge("CITES", claim_nodes[claim_id], evidence_nodes[evidence_id])
            for evidence_id in sorted(citations)
        )
        if disposition_node_id is not None:
            edges.append(_edge("ASSIGNED_DISPOSITION", claim_nodes[claim_id], disposition_node_id))
    return ProductGraph(
        nodes=tuple(sorted(nodes, key=lambda node: node.id)),
        edges=tuple(sorted(edges, key=lambda edge: edge.id)),
    )


def _edge(kind: str, source: str, target: str) -> ProductGraphEdge:
    return ProductGraphEdge(
        id=_scoped_id("p6-edge", {"kind": kind, "source": source, "target": target}),
        kind=kind,
        source=source,
        target=target,
        visibility="diagnosis",
    )


def _scoped_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_project_sha256(payload)}"


__all__ = [
    "COUNTERFACTUAL_STATUS_NOT_AVAILABLE",
    "ProductGraphProjection",
    "ProductGraphTextPath",
    "build_product_graph",
    "build_product_graph_projection",
]
