"""Typed, deterministic and visibility-safe lineage for project diagnosis.

Lineage records provenance and observed associations.  It intentionally has no
causal edge: a regression candidate remains causally unverified until a later
scientific protocol supplies evidence for a causal claim.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import (
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
    normalize_text,
)
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
)
from aletheia_lab.project.snapshots import ProjectSnapshot

LINEAGE_NODE_SCHEMA_VERSION: Final[Literal["project-lineage-node/v1"]] = (
    "project-lineage-node/v1"
)
LINEAGE_EDGE_SCHEMA_VERSION: Final[Literal["project-lineage-edge/v1"]] = (
    "project-lineage-edge/v1"
)
LINEAGE_GRAPH_SCHEMA_VERSION: Final[Literal["project-lineage-graph/v1"]] = (
    "project-lineage-graph/v1"
)
LINEAGE_TABLE_SCHEMA_VERSION: Final[Literal["project-lineage-table/v1"]] = (
    "project-lineage-table/v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
LineageVisibility = Literal["public", "diagnosis", "evaluator"]
LineageNodeKind = Literal[
    "project",
    "snapshot",
    "project_item",
    "metric_observation",
    "snapshot_comparison",
    "item_change",
    "metric_change",
    "regression_candidate",
    "evidence_bundle",
    "evidence_reference",
]
LineageRelationship = Literal[
    "contains",
    "observes",
    "compares_before",
    "compares_after",
    "reports",
    "qualifies",
    "supports",
]
LineageScalar: TypeAlias = str | int | float | bool | None
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_NODE_ID_PATTERN: Final[str] = r"^p3-lineage-node-[0-9a-f]{64}$"
_EDGE_ID_PATTERN: Final[str] = r"^p3-lineage-edge-[0-9a-f]{64}$"
_GRAPH_ID_PATTERN: Final[str] = r"^p3-lineage-graph-[0-9a-f]{64}$"
_VISIBILITY_RANK: Final[dict[LineageVisibility, int]] = {
    "public": 0,
    "diagnosis": 1,
    "evaluator": 2,
}


class ProjectLineageError(ValueError):
    """Raised when lineage cannot be constructed without ambiguity or leakage."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


class LineageAttribute(_StrictFrozenModel):
    """A small typed attribute; raw payloads are deliberately excluded."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: LineageScalar

    @field_validator("value")
    @classmethod
    def _bounded_value(cls, value: LineageScalar) -> LineageScalar:
        if isinstance(value, str):
            return normalize_text(value, label="lineage attribute", max_length=512)
        return value


def _canonical_attributes(values: tuple[LineageAttribute, ...]) -> tuple[LineageAttribute, ...]:
    keys = tuple(value.key for value in values)
    if len(keys) != len(set(keys)):
        raise ValueError("lineage attribute keys must be unique")
    return tuple(sorted(values, key=lambda value: value.key))


def _node_payload(
    *,
    project_id: str,
    kind: LineageNodeKind,
    source_id: str,
    source_sha256: str,
    visibility: LineageVisibility,
    attributes: tuple[LineageAttribute, ...],
) -> dict[str, object]:
    return {
        "schema_version": LINEAGE_NODE_SCHEMA_VERSION,
        "project_id": project_id,
        "kind": kind,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "visibility": visibility,
        "attributes": [value.model_dump(mode="json") for value in attributes],
    }


class ProjectLineageNode(_StrictFrozenModel):
    schema_version: Literal["project-lineage-node/v1"] = LINEAGE_NODE_SCHEMA_VERSION
    node_id: str = Field(pattern=_NODE_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    kind: LineageNodeKind
    source_id: str = Field(min_length=1, max_length=256)
    source_sha256: Sha256
    visibility: LineageVisibility
    attributes: tuple[LineageAttribute, ...] = ()

    @field_validator("source_id")
    @classmethod
    def _canonical_source_id(cls, value: str) -> str:
        return normalize_text(value, label="lineage source ID", max_length=256)

    @field_validator("attributes")
    @classmethod
    def _ordered_attributes(
        cls, values: tuple[LineageAttribute, ...]
    ) -> tuple[LineageAttribute, ...]:
        return _canonical_attributes(values)

    @model_validator(mode="after")
    def _identity_matches(self) -> ProjectLineageNode:
        payload = _node_payload(
            project_id=self.project_id,
            kind=self.kind,
            source_id=self.source_id,
            source_sha256=self.source_sha256,
            visibility=self.visibility,
            attributes=self.attributes,
        )
        if self.node_id != f"p3-lineage-node-{canonical_project_sha256(payload)}":
            raise ValueError("lineage node ID does not match canonical node")
        return self


def build_lineage_node(
    *,
    project_id: str,
    kind: LineageNodeKind,
    source_id: str,
    source_sha256: str,
    visibility: LineageVisibility,
    attributes: tuple[LineageAttribute, ...] = (),
) -> ProjectLineageNode:
    ordered = _canonical_attributes(attributes)
    payload = _node_payload(
        project_id=project_id,
        kind=kind,
        source_id=source_id,
        source_sha256=source_sha256,
        visibility=visibility,
        attributes=ordered,
    )
    return ProjectLineageNode(
        node_id=f"p3-lineage-node-{canonical_project_sha256(payload)}",
        project_id=project_id,
        kind=kind,
        source_id=source_id,
        source_sha256=source_sha256,
        visibility=visibility,
        attributes=ordered,
    )


def _edge_payload(
    *,
    project_id: str,
    source_node_id: str,
    target_node_id: str,
    relationship: LineageRelationship,
    visibility: LineageVisibility,
) -> dict[str, object]:
    return {
        "schema_version": LINEAGE_EDGE_SCHEMA_VERSION,
        "project_id": project_id,
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "relationship": relationship,
        "visibility": visibility,
    }


class ProjectLineageEdge(_StrictFrozenModel):
    schema_version: Literal["project-lineage-edge/v1"] = LINEAGE_EDGE_SCHEMA_VERSION
    edge_id: str = Field(pattern=_EDGE_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    source_node_id: str = Field(pattern=_NODE_ID_PATTERN)
    target_node_id: str = Field(pattern=_NODE_ID_PATTERN)
    relationship: LineageRelationship
    visibility: LineageVisibility

    @model_validator(mode="after")
    def _identity_matches(self) -> ProjectLineageEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("lineage self-edges are forbidden")
        payload = _edge_payload(
            project_id=self.project_id,
            source_node_id=self.source_node_id,
            target_node_id=self.target_node_id,
            relationship=self.relationship,
            visibility=self.visibility,
        )
        if self.edge_id != f"p3-lineage-edge-{canonical_project_sha256(payload)}":
            raise ValueError("lineage edge ID does not match canonical edge")
        return self


def build_lineage_edge(
    source: ProjectLineageNode,
    target: ProjectLineageNode,
    relationship: LineageRelationship,
    *,
    visibility: LineageVisibility | None = None,
) -> ProjectLineageEdge:
    source = _checked(source)
    target = _checked(target)
    if source.project_id != target.project_id:
        raise ProjectLineageError("lineage edge endpoints belong to different projects")
    minimum_rank = max(_VISIBILITY_RANK[source.visibility], _VISIBILITY_RANK[target.visibility])
    resolved = (
        next(key for key, rank in _VISIBILITY_RANK.items() if rank == minimum_rank)
        if visibility is None
        else visibility
    )
    if _VISIBILITY_RANK[resolved] < minimum_rank:
        raise ProjectLineageError("lineage edge visibility would expose a restricted endpoint")
    payload = _edge_payload(
        project_id=source.project_id,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relationship=relationship,
        visibility=resolved,
    )
    return ProjectLineageEdge(
        edge_id=f"p3-lineage-edge-{canonical_project_sha256(payload)}",
        project_id=source.project_id,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relationship=relationship,
        visibility=resolved,
    )


def _graph_payload(
    project_id: str,
    nodes: tuple[ProjectLineageNode, ...],
    edges: tuple[ProjectLineageEdge, ...],
) -> dict[str, object]:
    return {
        "schema_version": LINEAGE_GRAPH_SCHEMA_VERSION,
        "project_id": project_id,
        "nodes": [value.model_dump(mode="json") for value in nodes],
        "edges": [value.model_dump(mode="json") for value in edges],
    }


class ProjectLineageGraph(_StrictFrozenModel):
    schema_version: Literal["project-lineage-graph/v1"] = LINEAGE_GRAPH_SCHEMA_VERSION
    graph_id: str = Field(pattern=_GRAPH_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    nodes: tuple[ProjectLineageNode, ...]
    edges: tuple[ProjectLineageEdge, ...]
    graph_sha256: Sha256

    @field_validator("nodes")
    @classmethod
    def _ordered_nodes(
        cls, values: tuple[ProjectLineageNode, ...]
    ) -> tuple[ProjectLineageNode, ...]:
        ids = tuple(value.node_id for value in values)
        if not values or len(ids) != len(set(ids)):
            raise ValueError("lineage nodes must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.node_id))

    @field_validator("edges")
    @classmethod
    def _ordered_edges(
        cls, values: tuple[ProjectLineageEdge, ...]
    ) -> tuple[ProjectLineageEdge, ...]:
        ids = tuple(value.edge_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("lineage edges must be unique")
        return tuple(sorted(values, key=lambda value: value.edge_id))

    @model_validator(mode="after")
    def _graph_reconciles(self) -> ProjectLineageGraph:
        if any(value.project_id != self.project_id for value in self.nodes) or any(
            value.project_id != self.project_id for value in self.edges
        ):
            raise ValueError("lineage graph contains a foreign project member")
        nodes = {value.node_id: value for value in self.nodes}
        project_nodes = [value for value in self.nodes if value.kind == "project"]
        if (
            len(project_nodes) != 1
            or project_nodes[0].source_id != self.project_id
            or project_nodes[0].visibility != "public"
        ):
            raise ValueError("lineage graph requires one public project root")
        for edge in self.edges:
            source = nodes.get(edge.source_node_id)
            target = nodes.get(edge.target_node_id)
            if source is None or target is None:
                raise ValueError("lineage graph contains a dangling edge")
            required = max(_VISIBILITY_RANK[source.visibility], _VISIBILITY_RANK[target.visibility])
            if _VISIBILITY_RANK[edge.visibility] < required:
                raise ValueError("lineage edge visibility exposes a restricted endpoint")
        payload = _graph_payload(self.project_id, self.nodes, self.edges)
        digest = canonical_project_sha256(payload)
        if self.graph_sha256 != digest or self.graph_id != f"p3-lineage-graph-{digest}":
            raise ValueError("lineage graph identity does not reconcile")
        return self


def build_lineage_graph(
    project_id: str,
    nodes: tuple[ProjectLineageNode, ...],
    edges: tuple[ProjectLineageEdge, ...],
) -> ProjectLineageGraph:
    ordered_nodes = tuple(sorted((_checked(value) for value in nodes), key=lambda x: x.node_id))
    ordered_edges = tuple(sorted((_checked(value) for value in edges), key=lambda x: x.edge_id))
    payload = _graph_payload(project_id, ordered_nodes, ordered_edges)
    digest = canonical_project_sha256(payload)
    return ProjectLineageGraph(
        graph_id=f"p3-lineage-graph-{digest}",
        project_id=project_id,
        nodes=ordered_nodes,
        edges=ordered_edges,
        graph_sha256=digest,
    )


def project_lineage(graph: ProjectLineageGraph, visibility: LineageVisibility) -> ProjectLineageGraph:
    """Project a graph without retaining edges to hidden nodes."""

    checked = _checked(graph)
    rank = _VISIBILITY_RANK[visibility]
    nodes = tuple(value for value in checked.nodes if _VISIBILITY_RANK[value.visibility] <= rank)
    node_ids = {value.node_id for value in nodes}
    edges = tuple(
        value
        for value in checked.edges
        if _VISIBILITY_RANK[value.visibility] <= rank
        and value.source_node_id in node_ids
        and value.target_node_id in node_ids
    )
    return build_lineage_graph(checked.project_id, nodes, edges)


class ProjectLineageTable(_StrictFrozenModel):
    schema_version: Literal["project-lineage-table/v1"] = LINEAGE_TABLE_SCHEMA_VERSION
    graph_id: str = Field(pattern=_GRAPH_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    visibility: LineageVisibility
    node_rows: tuple[dict[str, object], ...]
    edge_rows: tuple[dict[str, object], ...]
    table_sha256: Sha256

    @field_validator("node_rows")
    @classmethod
    def _canonical_node_rows(
        cls, values: tuple[dict[str, object], ...]
    ) -> tuple[dict[str, object], ...]:
        required = {"node_id", "kind", "source_id", "source_sha256", "attributes"}
        if any(set(value) != required for value in values):
            raise ValueError("lineage table node rows must use the exact public schema")
        ids = tuple(value["node_id"] for value in values)
        if any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("lineage table node rows require unique string IDs")
        return tuple(sorted(values, key=lambda value: str(value["node_id"])))

    @field_validator("edge_rows")
    @classmethod
    def _canonical_edge_rows(
        cls, values: tuple[dict[str, object], ...]
    ) -> tuple[dict[str, object], ...]:
        required = {"edge_id", "source_node_id", "target_node_id", "relationship"}
        if any(set(value) != required for value in values):
            raise ValueError("lineage table edge rows must use the exact public schema")
        ids = tuple(value["edge_id"] for value in values)
        if any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("lineage table edge rows require unique string IDs")
        return tuple(sorted(values, key=lambda value: str(value["edge_id"])))

    @model_validator(mode="after")
    def _table_reconciles(self) -> ProjectLineageTable:
        node_ids = {value["node_id"] for value in self.node_rows}
        if any(
            edge["source_node_id"] not in node_ids or edge["target_node_id"] not in node_ids
            for edge in self.edge_rows
        ):
            raise ValueError("lineage table contains a dangling edge")
        payload = self.model_dump(mode="json", exclude={"table_sha256"})
        if self.table_sha256 != canonical_project_sha256(payload):
            raise ValueError("lineage table hash does not reconcile")
        return self


def project_lineage_table(
    graph: ProjectLineageGraph, visibility: LineageVisibility
) -> ProjectLineageTable:
    """Return deterministic row-oriented JSON for dashboards and exports."""

    view = project_lineage(graph, visibility)
    node_rows: tuple[dict[str, object], ...] = tuple(
        {
            "node_id": node.node_id,
            "kind": node.kind,
            "source_id": node.source_id,
            "source_sha256": node.source_sha256,
            "attributes": {value.key: value.value for value in node.attributes},
        }
        for node in view.nodes
    )
    edge_rows: tuple[dict[str, object], ...] = tuple(
        {
            "edge_id": edge.edge_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "relationship": edge.relationship,
        }
        for edge in view.edges
    )
    values: dict[str, object] = {
        "schema_version": LINEAGE_TABLE_SCHEMA_VERSION,
        "graph_id": view.graph_id,
        "project_id": view.project_id,
        "visibility": visibility,
        "node_rows": node_rows,
        "edge_rows": edge_rows,
    }
    return ProjectLineageTable(
        schema_version=LINEAGE_TABLE_SCHEMA_VERSION,
        graph_id=view.graph_id,
        project_id=view.project_id,
        visibility=visibility,
        node_rows=node_rows,
        edge_rows=edge_rows,
        table_sha256=canonical_project_sha256(values),
    )


def build_regression_lineage(
    before: ProjectSnapshot,
    after: ProjectSnapshot,
    comparison: ProjectSnapshotComparison,
    event: ProjectRegressionEvent,
    evidence: ProjectEvidenceBundle,
) -> ProjectLineageGraph:
    """Build the canonical non-causal lineage graph for one regression candidate."""

    before, after = _checked(before), _checked(after)
    comparison, event, evidence = _checked(comparison), _checked(event), _checked(evidence)
    if not (
        before.project_id == after.project_id == comparison.project_id == event.project_id
        == evidence.project_id
        and comparison.before_snapshot_id == before.snapshot_id
        and comparison.after_snapshot_id == after.snapshot_id
        and event.comparison_id == comparison.comparison_id
        and evidence.event_id == event.event_id
    ):
        raise ProjectLineageError("regression lineage sources do not reconcile")
    project_id = before.project_id
    project = build_lineage_node(
        project_id=project_id,
        kind="project",
        source_id=project_id,
        source_sha256=canonical_project_sha256({"project_id": project_id}),
        visibility="public",
    )
    before_node = build_lineage_node(
        project_id=project_id,
        kind="snapshot",
        source_id=before.snapshot_id,
        source_sha256=before.record_sha256,
        visibility="diagnosis",
        attributes=(LineageAttribute(key="state", value="before"),),
    )
    after_node = build_lineage_node(
        project_id=project_id,
        kind="snapshot",
        source_id=after.snapshot_id,
        source_sha256=after.record_sha256,
        visibility="diagnosis",
        attributes=(LineageAttribute(key="state", value="after"),),
    )
    comparison_node = build_lineage_node(
        project_id=project_id,
        kind="snapshot_comparison",
        source_id=comparison.comparison_id,
        source_sha256=comparison.comparison_sha256,
        visibility="diagnosis",
        attributes=(LineageAttribute(key="status", value=comparison.status),),
    )
    event_node = build_lineage_node(
        project_id=project_id,
        kind="regression_candidate",
        source_id=event.event_id,
        source_sha256=event.event_sha256,
        visibility="diagnosis",
        attributes=(LineageAttribute(key="causal_status", value=event.causal_status),),
    )
    evidence_node = build_lineage_node(
        project_id=project_id,
        kind="evidence_bundle",
        source_id=evidence.evidence_bundle_id,
        source_sha256=evidence.bundle_sha256,
        visibility="diagnosis",
    )
    nodes: list[ProjectLineageNode] = [
        project,
        before_node,
        after_node,
        comparison_node,
        event_node,
        evidence_node,
    ]
    edges: list[ProjectLineageEdge] = [
        build_lineage_edge(project, before_node, "contains"),
        build_lineage_edge(project, after_node, "contains"),
        build_lineage_edge(comparison_node, before_node, "compares_before"),
        build_lineage_edge(comparison_node, after_node, "compares_after"),
        build_lineage_edge(comparison_node, event_node, "qualifies"),
        build_lineage_edge(evidence_node, event_node, "supports"),
    ]
    for snapshot, snapshot_node in ((before, before_node), (after, after_node)):
        for item in snapshot.items:
            item_visibility: LineageVisibility = {
                "outbound": "public",
                "diagnosis": "diagnosis",
                "local_only": "evaluator",
            }[item.visibility]  # type: ignore[assignment]
            item_node = build_lineage_node(
                project_id=project_id,
                kind="project_item",
                source_id=item.project_item_id,
                source_sha256=item.observation_sha256,
                visibility=item_visibility,
                attributes=(LineageAttribute(key="source_type", value=item.source_type),),
            )
            if item_node.node_id not in {value.node_id for value in nodes}:
                nodes.append(item_node)
            edges.append(build_lineage_edge(snapshot_node, item_node, "contains"))
    for item_change in comparison.item_changes:
        change_node = build_lineage_node(
            project_id=project_id,
            kind="item_change",
            source_id=item_change.change_id,
            source_sha256=item_change.change_id.removeprefix("p3-change-"),
            visibility="diagnosis",
            attributes=(LineageAttribute(key="kind", value=item_change.kind),),
        )
        nodes.append(change_node)
        edges.append(build_lineage_edge(comparison_node, change_node, "reports"))
    for metric_change in comparison.metric_changes:
        change_node = build_lineage_node(
            project_id=project_id,
            kind="metric_change",
            source_id=metric_change.metric_change_id,
            source_sha256=metric_change.metric_change_id.removeprefix("p3-metric-change-"),
            visibility="diagnosis",
            attributes=(LineageAttribute(key="kind", value=metric_change.kind),),
        )
        nodes.append(change_node)
        edges.append(build_lineage_edge(comparison_node, change_node, "reports"))
        edges.append(build_lineage_edge(change_node, event_node, "supports"))
    return build_lineage_graph(project_id, tuple(nodes), tuple(edges))
