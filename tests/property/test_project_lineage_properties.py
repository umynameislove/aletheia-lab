"""Generative invariants for deterministic project lineage."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.project.lineage import (
    build_lineage_edge,
    build_lineage_graph,
    build_lineage_node,
    project_lineage,
)

_PROJECT = "p3-project-" + "1" * 64


def _members():  # type: ignore[no-untyped-def]
    project = build_lineage_node(
        project_id=_PROJECT,
        kind="project",
        source_id=_PROJECT,
        source_sha256="1" * 64,
        visibility="public",
    )
    diagnosis = build_lineage_node(
        project_id=_PROJECT,
        kind="snapshot",
        source_id="p3-snapshot-" + "2" * 64,
        source_sha256="2" * 64,
        visibility="diagnosis",
    )
    evaluator = build_lineage_node(
        project_id=_PROJECT,
        kind="project_item",
        source_id="p3-item-" + "3" * 64,
        source_sha256="3" * 64,
        visibility="evaluator",
    )
    nodes = (project, diagnosis, evaluator)
    edges = (
        build_lineage_edge(project, diagnosis, "contains"),
        build_lineage_edge(diagnosis, evaluator, "contains"),
    )
    return nodes, edges


@given(st.permutations((0, 1, 2)), st.permutations((0, 1)))
def test_member_order_never_changes_graph_identity(
    node_order: list[int], edge_order: list[int]
) -> None:
    nodes, edges = _members()
    expected = build_lineage_graph(_PROJECT, nodes, edges)
    actual = build_lineage_graph(
        _PROJECT,
        tuple(nodes[index] for index in node_order),
        tuple(edges[index] for index in edge_order),
    )
    assert actual == expected


@given(st.sampled_from(("public", "diagnosis", "evaluator")))
def test_every_projected_edge_has_both_endpoints(visibility: str) -> None:
    nodes, edges = _members()
    graph = build_lineage_graph(_PROJECT, nodes, edges)
    projected = project_lineage(graph, visibility)  # type: ignore[arg-type]
    node_ids = {value.node_id for value in projected.nodes}
    assert all(
        value.source_node_id in node_ids and value.target_node_id in node_ids
        for value in projected.edges
    )
