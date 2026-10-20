"""Deterministic P3-backed product graph projection contracts."""

from __future__ import annotations

import json

import pytest

from aletheia_lab.product import ProductError
from aletheia_lab.product.evidence import (
    PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION,
    ProductEvidenceItem,
    ProductEvidenceProjection,
)
from aletheia_lab.product.projection import (
    ProductGraphProjection,
    build_product_graph_projection,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.lineage import (
    ProjectLineageGraph,
    build_lineage_edge,
    build_lineage_graph,
    build_lineage_node,
)

_PROJECT_ID = "p3-project-" + "1" * 64
_SNAPSHOT_ID = "p3-snapshot-" + "2" * 64
_CLAIM_ID = "p6-claim-" + "3" * 64
_RESULT_ID = "p6-result-" + "4" * 64
_BUNDLE_ID = "p3-evidence-bundle-" + "5" * 64


def _sources() -> tuple[tuple[str, str, str], ...]:
    return (
        ("before_snapshot", "p3-snapshot-" + "6" * 64, "6" * 64),
        ("after_snapshot", _SNAPSHOT_ID, "2" * 64),
        ("snapshot_comparison", "p3-comparison-" + "7" * 64, "7" * 64),
        ("metric_change", "p3-metric-change-" + "8" * 64, "8" * 64),
        ("regression_candidate", "p3-regression-event-" + "9" * 64, "9" * 64),
    )


def _lineage(
    *,
    reverse: bool = False,
    hidden_metric: bool = False,
    snapshot_record_sha256: str | None = None,
) -> ProjectLineageGraph:
    project = build_lineage_node(
        project_id=_PROJECT_ID,
        kind="project",
        source_id=_PROJECT_ID,
        source_sha256="1" * 64,
        visibility="public",
    )
    kinds = {
        "before_snapshot": "snapshot",
        "after_snapshot": "snapshot",
        "snapshot_comparison": "snapshot_comparison",
        "metric_change": "metric_change",
        "regression_candidate": "regression_candidate",
    }
    source_nodes = [
        build_lineage_node(
            project_id=_PROJECT_ID,
            kind=kinds[role],  # type: ignore[arg-type]
            source_id=source_id,
            source_sha256=(
                snapshot_record_sha256
                if role == "after_snapshot" and snapshot_record_sha256 is not None
                else source_sha256
            ),
            visibility=("evaluator" if hidden_metric and role == "metric_change" else "diagnosis"),
        )
        for role, source_id, source_sha256 in _sources()
    ]
    by_role = {role: node for (role, _, _), node in zip(_sources(), source_nodes, strict=True)}
    bundle = build_lineage_node(
        project_id=_PROJECT_ID,
        kind="evidence_bundle",
        source_id=_BUNDLE_ID,
        source_sha256="5" * 64,
        visibility="diagnosis",
    )
    edges = [
        build_lineage_edge(project, by_role["before_snapshot"], "contains"),
        build_lineage_edge(project, by_role["after_snapshot"], "contains"),
        build_lineage_edge(
            by_role["snapshot_comparison"], by_role["before_snapshot"], "compares_before"
        ),
        build_lineage_edge(
            by_role["snapshot_comparison"], by_role["after_snapshot"], "compares_after"
        ),
        build_lineage_edge(by_role["snapshot_comparison"], by_role["metric_change"], "reports"),
        build_lineage_edge(
            by_role["snapshot_comparison"], by_role["regression_candidate"], "qualifies"
        ),
        build_lineage_edge(by_role["metric_change"], by_role["regression_candidate"], "supports"),
        build_lineage_edge(bundle, by_role["regression_candidate"], "supports"),
    ]
    nodes = [project, *source_nodes, bundle]
    if reverse:
        nodes.reverse()
        edges.reverse()
    return build_lineage_graph(_PROJECT_ID, tuple(nodes), tuple(edges))


def _evidence(
    *, reverse: bool = False, metric_sha256: str | None = None
) -> ProductEvidenceProjection:
    items = [
        ProductEvidenceItem(
            evidence_id=f"p3-evidence-{index:064x}",
            role=role,  # type: ignore[arg-type]
            source_id=source_id,
            source_sha256=(
                metric_sha256
                if role == "metric_change" and metric_sha256 is not None
                else source_sha256
            ),
            provenance_links=(),
            visibility="diagnosis",
            redaction_state="none",
        )
        for index, (role, source_id, source_sha256) in enumerate(_sources(), start=1)
    ]
    by_role = {item.role: item for item in items}
    snapshot_links = tuple(
        sorted(
            (
                by_role["before_snapshot"].evidence_id,
                by_role["after_snapshot"].evidence_id,
            )
        )
    )
    items = [
        item.model_copy(
            update={
                "provenance_links": (
                    snapshot_links
                    if item.role in {"snapshot_comparison", "metric_change"}
                    else tuple(
                        sorted(
                            (
                                by_role["snapshot_comparison"].evidence_id,
                                by_role["metric_change"].evidence_id,
                            )
                        )
                    )
                    if item.role == "regression_candidate"
                    else ()
                )
            }
        )
        for item in items
    ]
    if reverse:
        items.reverse()
    payload = {
        "schema_version": PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION,
        "evidence_bundle_id": _BUNDLE_ID,
        "project_id": _PROJECT_ID,
        "visibility": "diagnosis",
        "items": [
            item.model_dump(mode="json")
            for item in sorted(items, key=lambda item: item.evidence_id)
        ],
        "missing_categories": [],
        "omitted_categories": [],
    }
    return ProductEvidenceProjection(
        schema_version=PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION,
        evidence_bundle_id=_BUNDLE_ID,
        project_id=_PROJECT_ID,
        visibility="diagnosis",
        items=tuple(items),
        missing_categories=(),
        omitted_categories=(),
        projection_sha256=canonical_project_sha256(payload),
    )


def _projection(*, reverse: bool = False) -> ProductGraphProjection:
    evidence = _evidence(reverse=reverse)
    citations = tuple(item.evidence_id for item in evidence.items if item.role != "before_snapshot")
    if reverse:
        citations = tuple(reversed(citations))
    return build_product_graph_projection(
        _lineage(reverse=reverse),
        evidence,
        snapshot_id=_SNAPSHOT_ID,
        claim_citations={_CLAIM_ID: citations},
        disposition_id=_RESULT_ID,
    )


def test_projection_is_permutation_stable_and_uses_one_canonical_surface() -> None:
    first = _projection()
    second = _projection(reverse=True)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.projection_sha256 == second.projection_sha256
    assert first.counterfactual_status == "not_available"
    assert first.node_rows == first.graph.nodes
    assert first.edge_rows == first.graph.edges
    assert tuple(path.edge_id for path in first.textual_paths) == tuple(
        edge.id for edge in first.graph.edges
    )
    assert all(path.source_node_id in path.text for path in first.textual_paths)
    assert all(path.target_node_id in path.text for path in first.textual_paths)
    assert all(path.edge_id in path.text for path in first.textual_paths)


def test_projection_has_valid_noncausal_endpoints_and_hides_evaluator_lineage() -> None:
    evidence = _evidence()
    projection = build_product_graph_projection(
        _lineage(snapshot_record_sha256="a" * 64),
        evidence,
        snapshot_id=_SNAPSHOT_ID,
        claim_citations={_CLAIM_ID: (evidence.items[0].evidence_id,)},
        disposition_id=_RESULT_ID,
    )
    node_ids = {node.id for node in projection.graph.nodes}

    assert all(
        edge.source in node_ids and edge.target in node_ids for edge in projection.graph.edges
    )
    assert {edge.kind for edge in projection.graph.edges} <= {
        "OBSERVED_IN",
        "CITES",
        "ASSIGNED_DISPOSITION",
    }
    payload = json.dumps(projection.model_dump(mode="json"), sort_keys=True)
    assert "CAUSES" not in payload
    assert "RELATED_TO" not in payload
    assert "evaluator" not in payload


def test_projection_fails_closed_for_tampered_or_hidden_provenance() -> None:
    with pytest.raises(ProductError) as mismatched:
        evidence = _evidence(metric_sha256="f" * 64)
        build_product_graph_projection(
            _lineage(),
            evidence,
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={_CLAIM_ID: (evidence.items[0].evidence_id,)},
            disposition_id=_RESULT_ID,
        )
    assert mismatched.value.code == "graph_projection_invalid"
    assert _SNAPSHOT_ID not in mismatched.value.safe_message

    with pytest.raises(ProductError) as hidden:
        evidence = _evidence()
        build_product_graph_projection(
            _lineage(hidden_metric=True),
            evidence,
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={_CLAIM_ID: (evidence.items[0].evidence_id,)},
            disposition_id=_RESULT_ID,
        )
    assert hidden.value.code == "graph_projection_invalid"
    assert "metric_change" not in hidden.value.safe_message

    lineage = _lineage()
    without_edges = build_lineage_graph(lineage.project_id, lineage.nodes, ())
    with pytest.raises(ProductError) as missing_edges:
        evidence = _evidence()
        build_product_graph_projection(
            without_edges,
            evidence,
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={_CLAIM_ID: (evidence.items[0].evidence_id,)},
            disposition_id=_RESULT_ID,
        )
    assert missing_edges.value.code == "graph_projection_invalid"


def test_projection_rejects_dangling_claim_citation_with_safe_error() -> None:
    with pytest.raises(ProductError) as captured:
        build_product_graph_projection(
            _lineage(),
            _evidence(),
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={_CLAIM_ID: ("p3-evidence-" + "f" * 64,)},
            disposition_id=_RESULT_ID,
        )

    assert captured.value.code == "graph_projection_invalid"
    assert "p3-evidence" not in captured.value.safe_message

    evidence = _evidence()
    with pytest.raises(ProductError) as duplicate:
        build_product_graph_projection(
            _lineage(),
            evidence,
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={
                _CLAIM_ID: (evidence.items[0].evidence_id, evidence.items[0].evidence_id)
            },
            disposition_id=_RESULT_ID,
        )
    assert duplicate.value.code == "graph_projection_invalid"


def test_projection_rejects_foreign_project_and_wrong_snapshot_scope() -> None:
    evidence = _evidence()
    foreign_project_id = "p3-project-" + "f" * 64
    foreign_payload = evidence.model_dump(mode="json", exclude={"projection_sha256"})
    foreign_payload["project_id"] = foreign_project_id
    foreign = evidence.model_copy(
        update={
            "project_id": foreign_project_id,
            "projection_sha256": canonical_project_sha256(foreign_payload),
        }
    )
    with pytest.raises(ProductError) as foreign_error:
        build_product_graph_projection(
            _lineage(),
            foreign,
            snapshot_id=_SNAPSHOT_ID,
            claim_citations={},
            disposition_id=None,
        )
    assert foreign_error.value.code == "graph_projection_invalid"
    assert foreign_project_id not in foreign_error.value.safe_message

    baseline_id = next(item.source_id for item in evidence.items if item.role == "before_snapshot")
    with pytest.raises(ProductError) as snapshot_error:
        build_product_graph_projection(
            _lineage(),
            evidence,
            snapshot_id=baseline_id,
            claim_citations={},
            disposition_id=None,
        )
    assert snapshot_error.value.code == "graph_projection_invalid"
    assert baseline_id not in snapshot_error.value.safe_message


def test_projection_without_claims_has_only_technical_failure_graph_inputs() -> None:
    projection = build_product_graph_projection(
        _lineage(),
        _evidence(),
        snapshot_id=_SNAPSHOT_ID,
        claim_citations={},
        disposition_id=None,
    )

    assert {node.kind for node in projection.graph.nodes} == {"Snapshot", "EvidenceItem"}
    assert {edge.kind for edge in projection.graph.edges} == {"OBSERVED_IN"}
    after_id = next(item.evidence_id for item in _evidence().items if item.role == "after_snapshot")
    expected_observed = {
        item.evidence_id
        for item in _evidence().items
        if item.evidence_id == after_id or after_id in item.provenance_links
    }
    node_by_id = {node.id: node for node in projection.graph.nodes}
    assert {
        node_by_id[edge.source].source_id for edge in projection.graph.edges
    } == expected_observed
    assert projection.counterfactual_status == "not_available"
