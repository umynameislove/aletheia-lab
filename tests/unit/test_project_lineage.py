"""Typed lineage identity, topology and visibility contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aletheia_lab.project.lineage import (
    LineageAttribute,
    ProjectLineageEdge,
    ProjectLineageError,
    ProjectLineageGraph,
    build_lineage_edge,
    build_lineage_graph,
    build_lineage_node,
    project_lineage,
    project_lineage_table,
)

_PROJECT = "p3-project-" + "1" * 64


def _nodes():  # type: ignore[no-untyped-def]
    project = build_lineage_node(
        project_id=_PROJECT,
        kind="project",
        source_id=_PROJECT,
        source_sha256="1" * 64,
        visibility="public",
    )
    snapshot = build_lineage_node(
        project_id=_PROJECT,
        kind="snapshot",
        source_id="p3-snapshot-" + "2" * 64,
        source_sha256="2" * 64,
        visibility="diagnosis",
        attributes=(LineageAttribute(key="state", value="before"),),
    )
    secret = build_lineage_node(
        project_id=_PROJECT,
        kind="project_item",
        source_id="p3-item-" + "3" * 64,
        source_sha256="3" * 64,
        visibility="evaluator",
        attributes=(LineageAttribute(key="source_type", value="config"),),
    )
    return project, snapshot, secret


def _graph() -> ProjectLineageGraph:
    project, snapshot, secret = _nodes()
    return build_lineage_graph(
        _PROJECT,
        (secret, project, snapshot),
        (
            build_lineage_edge(project, snapshot, "contains"),
            build_lineage_edge(snapshot, secret, "contains"),
        ),
    )


def test_lineage_is_deterministic_typed_and_has_no_causal_relationship() -> None:
    first = _graph()
    project, snapshot, secret = _nodes()
    second = build_lineage_graph(
        _PROJECT,
        (project, snapshot, secret),
        (
            build_lineage_edge(snapshot, secret, "contains"),
            build_lineage_edge(project, snapshot, "contains"),
        ),
    )

    assert first == second
    assert first.graph_id == second.graph_id
    assert "causes" not in first.model_dump_json()
    edge = first.edges[0].model_dump(mode="python")
    edge["relationship"] = "causes"
    with pytest.raises(ValidationError):
        ProjectLineageEdge.model_validate(edge)


def test_visibility_projection_removes_hidden_nodes_and_incident_edges() -> None:
    graph = _graph()
    public = project_lineage(graph, "public")
    diagnosis = project_lineage(graph, "diagnosis")
    evaluator = project_lineage(graph, "evaluator")

    assert [value.kind for value in public.nodes] == ["project"]
    assert {value.kind for value in diagnosis.nodes} == {"project", "snapshot"}
    assert {value.kind for value in evaluator.nodes} == {
        "project",
        "snapshot",
        "project_item",
    }
    assert not public.edges
    assert len(diagnosis.edges) == 1
    assert len(evaluator.edges) == 2
    assert all(
        edge.source_node_id in {node.node_id for node in diagnosis.nodes}
        and edge.target_node_id in {node.node_id for node in diagnosis.nodes}
        for edge in diagnosis.edges
    )


def test_edge_cannot_downgrade_endpoint_visibility_or_cross_projects() -> None:
    project, _, secret = _nodes()
    with pytest.raises(ProjectLineageError, match="visibility"):
        build_lineage_edge(project, secret, "contains", visibility="public")

    foreign = build_lineage_node(
        project_id="p3-project-" + "9" * 64,
        kind="project",
        source_id="p3-project-" + "9" * 64,
        source_sha256="9" * 64,
        visibility="public",
    )
    with pytest.raises(ProjectLineageError, match="different projects"):
        build_lineage_edge(project, foreign, "contains")


def test_graph_rejects_dangling_foreign_and_forged_members() -> None:
    graph = _graph()
    payload = graph.model_dump(mode="python")
    payload["nodes"] = graph.nodes[:-1]
    with pytest.raises(ValidationError, match="dangling"):
        ProjectLineageGraph.model_validate(payload)

    payload = graph.model_dump(mode="python")
    payload["graph_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="identity"):
        ProjectLineageGraph.model_validate(payload)

    forged = graph.nodes[0].model_copy(update={"source_sha256": "0" * 64})
    payload = graph.model_dump(mode="python")
    payload["nodes"] = (forged, *graph.nodes[1:])
    with pytest.raises(ValidationError, match="node ID"):
        ProjectLineageGraph.model_validate(payload)


def test_table_projection_is_deterministic_and_visibility_bounded() -> None:
    graph = _graph()
    first = project_lineage_table(graph, "diagnosis")
    second = project_lineage_table(graph, "diagnosis")

    assert first == second
    assert first.table_sha256 == second.table_sha256
    assert all(row["kind"] != "project_item" for row in first.node_rows)
    assert all("visibility" not in row for row in (*first.node_rows, *first.edge_rows))

    payload = first.model_dump(mode="python")
    payload["node_rows"][0]["visibility"] = "evaluator"
    with pytest.raises(ValidationError, match="exact public schema"):
        type(first).model_validate(payload)
