"""Integration test from P3 evidence projection to an outbound-safe context."""

from __future__ import annotations

from aletheia_lab.context.evaluation_context import build_evaluation_context
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.regression import (
    EvidenceRole,
    ProjectEvidenceBundle,
    ProjectEvidenceReference,
    project_diagnosis_project_evidence,
)


def _sha256(character: str) -> str:
    return character * 64


def _opaque(character: str) -> str:
    return f"ev-{_sha256(character)}"


def _reference(
    *,
    role: EvidenceRole,
    source_id: str,
    source_sha256: str,
    provenance_links: tuple[str, ...] = (),
) -> ProjectEvidenceReference:
    values = {
        "role": role,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "visibility": "diagnosis",
        "redaction_state": "none",
        "provenance_links": sorted(provenance_links),
    }
    return ProjectEvidenceReference(
        evidence_id=f"p3-evidence-{canonical_project_sha256(values)}",
        role=role,
        source_id=source_id,
        source_sha256=source_sha256,
        visibility="diagnosis",
        provenance_links=provenance_links,
    )


def test_p3_whitelist_projection_builds_a_payload_without_source_paths() -> None:
    project_id = f"p3-project-{_sha256('1')}"
    before = _reference(
        role="before_snapshot",
        source_id=f"p3-snapshot-{_sha256('2')}",
        source_sha256=_sha256("3"),
    )
    after = _reference(
        role="after_snapshot",
        source_id=f"p3-snapshot-{_sha256('4')}",
        source_sha256=_sha256("5"),
    )
    snapshot_links = tuple(sorted((before.evidence_id, after.evidence_id)))
    comparison = _reference(
        role="snapshot_comparison",
        source_id=f"p3-comparison-{_sha256('6')}",
        source_sha256=_sha256("7"),
        provenance_links=snapshot_links,
    )
    metric = _reference(
        role="metric_change",
        source_id=f"p3-metric-change-{_sha256('8')}",
        source_sha256=_sha256("8"),
        provenance_links=snapshot_links,
    )
    event_id = f"p3-event-{_sha256('9')}"
    event = _reference(
        role="regression_candidate",
        source_id=event_id,
        source_sha256=_sha256("a"),
        provenance_links=tuple(sorted((comparison.evidence_id, metric.evidence_id))),
    )
    items = tuple(sorted((before, after, comparison, metric, event), key=lambda item: item.evidence_id))
    bundle_payload = {
        "schema_version": "project-evidence-bundle/v1",
        "project_id": project_id,
        "event_id": event_id,
        "items": [item.model_dump(mode="json") for item in items],
    }
    bundle_sha256 = canonical_project_sha256(bundle_payload)
    bundle = ProjectEvidenceBundle(
        evidence_bundle_id=f"p3-evidence-bundle-{bundle_sha256}",
        project_id=project_id,
        event_id=event_id,
        items=items,
        bundle_sha256=bundle_sha256,
    )
    projection = project_diagnosis_project_evidence(bundle)
    manifest = EvaluationManifestReference.build(
        project_id=project_id,
        snapshot_id=f"p3-snapshot-{_sha256('b')}",
        manifest_content_sha256=_sha256("c"),
        source_commit_ref="d" * 40,
        authorization_state="authorized",
        authorization_ref=_opaque("e"),
        provenance_sha256=_sha256("f"),
        created_at="2026-08-29T00:00:00Z",
        frozen_at="2026-08-29T00:00:01Z",
        visibility="diagnosis",
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque("0"),
        family_id=_opaque("1"),
        mechanism_id=_opaque("2"),
        dataset_id=_opaque("3"),
        variant_id=_opaque("4"),
        variant_content_sha256=_sha256("5"),
        case_content_sha256=_sha256("6"),
        evidence_bundle_id=projection.evidence_bundle_id,
        evidence_content_sha256=bundle.bundle_sha256,
        lineage_graph_id=f"p3-lineage-graph-{_sha256('7')}",
        lineage_sha256=_sha256("8"),
        visibility_projection_sha256=projection.view_sha256,
        provenance_sha256=_sha256("9"),
        visibility="diagnosis",
    )

    context = build_evaluation_context(
        case=case,
        evidence_view=projection,
        selected_evidence_ids=tuple(item.evidence_id for item in projection.items),
    )
    serialized = context.model_dump_json()

    assert len(context.selected_evidence) == len(projection.items)
    assert "source_id" not in serialized
    assert "relative_path" not in serialized
    assert "C:\\" not in serialized
    assert "/var/" not in serialized
