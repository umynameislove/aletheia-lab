"""Product evidence DTO identity and visibility projection contracts."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence import (
    ProductEvidenceProjection,
    ProductEvidenceReferences,
    build_product_evidence_projection,
    resolve_product_evidence_references,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.regression import (
    EvidenceRole,
    ProjectEvidenceBundle,
    ProjectEvidenceReference,
)

_PROJECT_ID = "p3-project-" + "1" * 64
_EVENT_ID = "p3-event-" + "2" * 64
_FOREIGN_PROJECT_ID = "p3-project-" + "8" * 64
_FOREIGN_EVENT_ID = "p3-event-" + "9" * 64


def _reference(
    role: EvidenceRole,
    suffix: str,
    *,
    visibility: Literal["public", "diagnosis", "evaluator"],
    redaction_state: Literal["none", "withheld"] = "none",
    provenance_links: tuple[str, ...] = (),
    source_id: str | None = None,
) -> ProjectEvidenceReference:
    resolved_source_id = f"source-{suffix}" if source_id is None else source_id
    source_sha256 = suffix * 64
    payload = {
        "role": role,
        "source_id": resolved_source_id,
        "source_sha256": source_sha256,
        "visibility": visibility,
        "redaction_state": redaction_state,
        "provenance_links": sorted(provenance_links),
    }
    return ProjectEvidenceReference(
        evidence_id=f"p3-evidence-{canonical_project_sha256(payload)}",
        role=role,
        source_id=resolved_source_id,
        source_sha256=source_sha256,
        visibility=visibility,
        redaction_state=redaction_state,
        provenance_links=provenance_links,
    )


def _bundle(
    *,
    project_id: str = _PROJECT_ID,
    event_id: str = _EVENT_ID,
    suffixes: tuple[str, str, str, str, str] = ("3", "4", "5", "6", "7"),
) -> ProjectEvidenceBundle:
    before_suffix, after_suffix, comparison_suffix, metric_suffix, event_suffix = suffixes
    before = _reference("before_snapshot", before_suffix, visibility="public")
    after = _reference("after_snapshot", after_suffix, visibility="diagnosis")
    snapshots = tuple(sorted((before.evidence_id, after.evidence_id)))
    comparison = _reference(
        "snapshot_comparison",
        comparison_suffix,
        visibility="diagnosis",
        provenance_links=snapshots,
    )
    metric = _reference(
        "metric_change",
        metric_suffix,
        visibility="evaluator",
        redaction_state="withheld",
        provenance_links=snapshots,
    )
    event = _reference(
        "regression_candidate",
        event_suffix,
        visibility="evaluator",
        provenance_links=tuple(sorted((comparison.evidence_id, metric.evidence_id))),
        source_id=event_id,
    )
    items = tuple(
        sorted((before, after, comparison, metric, event), key=lambda item: item.evidence_id)
    )
    payload = {
        "schema_version": "project-evidence-bundle/v1",
        "project_id": project_id,
        "event_id": event_id,
        "items": [item.model_dump(mode="json") for item in items],
    }
    digest = canonical_project_sha256(payload)
    return ProjectEvidenceBundle(
        evidence_bundle_id=f"p3-evidence-bundle-{digest}",
        project_id=project_id,
        event_id=event_id,
        items=items,
        bundle_sha256=digest,
    )


def test_projection_is_deterministic_self_hashed_and_visibility_monotonic() -> None:
    bundle = _bundle()

    public = build_product_evidence_projection(bundle, visibility="public")
    diagnosis = build_product_evidence_projection(bundle, visibility="diagnosis")
    evaluator = build_product_evidence_projection(bundle, visibility="evaluator")

    assert public == build_product_evidence_projection(bundle, visibility="public")
    assert (
        {item.evidence_id for item in public.items}
        < {item.evidence_id for item in diagnosis.items}
        < {item.evidence_id for item in evaluator.items}
    )
    assert [item.role for item in public.items] == ["before_snapshot"]
    assert {item.role for item in diagnosis.items} == {
        "before_snapshot",
        "after_snapshot",
        "snapshot_comparison",
    }
    assert diagnosis.missing_categories == (
        "metric_change",
        "regression_candidate",
    )
    assert diagnosis.omitted_categories == (
        "metric_change",
        "regression_candidate",
    )
    assert evaluator.missing_categories == ()
    assert evaluator.omitted_categories == ()


def test_diagnosis_projection_omits_withheld_ids_and_keeps_provenance_closed() -> None:
    bundle = _bundle()
    withheld = next(item for item in bundle.items if item.redaction_state == "withheld")

    projection = build_product_evidence_projection(bundle, visibility="diagnosis")
    serialized = projection.model_dump_json()
    visible_ids = {item.evidence_id for item in projection.items}

    assert withheld.evidence_id not in serialized
    assert withheld.source_id not in serialized
    assert all(link in visible_ids for item in projection.items for link in item.provenance_links)
    assert "withheld" not in serialized


def test_projection_rejects_dangling_provenance_and_hash_tampering() -> None:
    projection = build_product_evidence_projection(_bundle(), visibility="diagnosis")

    dangling = projection.model_dump(mode="python")
    first = projection.items[0].model_copy(
        update={"provenance_links": ("p3-evidence-" + "0" * 64,)}
    )
    dangling["items"] = (first, *projection.items[1:])
    with pytest.raises(ValidationError, match="dangling"):
        ProductEvidenceProjection.model_validate(dangling)

    tampered = projection.model_dump(mode="python")
    tampered["projection_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="hash"):
        ProductEvidenceProjection.model_validate(tampered)


def test_references_are_canonical_and_bound_to_one_projection() -> None:
    projection = build_product_evidence_projection(_bundle(), visibility="diagnosis")
    visible_ids = tuple(item.evidence_id for item in reversed(projection.items))
    citation_id = visible_ids[0]
    counterevidence_id = visible_ids[1]

    references = resolve_product_evidence_references(
        projection,
        citation_ids=(citation_id,),
        counterevidence_ids=(counterevidence_id,),
        visible_evidence_ids=visible_ids,
    )

    assert references.citation_ids == (citation_id,)
    assert references.counterevidence_ids == (counterevidence_id,)
    assert references.visible_evidence_ids == tuple(sorted(visible_ids))
    assert references == ProductEvidenceReferences.model_validate_json(references.model_dump_json())


def test_reference_roles_must_be_disjoint_and_within_visible_scope() -> None:
    projection = build_product_evidence_projection(_bundle(), visibility="diagnosis")
    first_id, second_id, *_ = (item.evidence_id for item in projection.items)

    invalid_scopes = (
        {
            "citation_ids": (first_id,),
            "counterevidence_ids": (first_id,),
            "visible_evidence_ids": (first_id, second_id),
        },
        {
            "citation_ids": (first_id,),
            "counterevidence_ids": (),
            "visible_evidence_ids": (second_id,),
        },
    )
    for scope in invalid_scopes:
        with pytest.raises(ProductError) as captured:
            resolve_product_evidence_references(projection, **scope)
        assert captured.value.code == "evidence_reference_invalid"
        assert first_id not in captured.value.safe_message


def test_hidden_foreign_and_dangling_references_fail_closed() -> None:
    bundle = _bundle()
    projection = build_product_evidence_projection(bundle, visibility="diagnosis")
    hidden_id = next(item.evidence_id for item in bundle.items if item.visibility == "evaluator")
    foreign_bundle = _bundle(
        project_id=_FOREIGN_PROJECT_ID,
        event_id=_FOREIGN_EVENT_ID,
        suffixes=("a", "b", "c", "d", "e"),
    )
    foreign_id = foreign_bundle.items[0].evidence_id
    dangling_id = "p3-evidence-" + "0" * 64

    for rejected_id in (hidden_id, foreign_id, dangling_id):
        with pytest.raises(ProductError) as captured:
            resolve_product_evidence_references(
                projection,
                citation_ids=(rejected_id,),
                counterevidence_ids=(),
                visible_evidence_ids=(rejected_id,),
            )
        assert captured.value.code == "evidence_reference_invalid"
        assert rejected_id not in captured.value.safe_message


def test_reference_resolution_rejects_tampered_projection_safely() -> None:
    projection = build_product_evidence_projection(_bundle(), visibility="diagnosis")
    tampered = projection.model_copy(update={"projection_sha256": "0" * 64})

    with pytest.raises(ProductError) as captured:
        resolve_product_evidence_references(
            tampered,
            citation_ids=(),
            counterevidence_ids=(),
            visible_evidence_ids=(),
        )

    assert captured.value.code == "evidence_projection_invalid"
    assert captured.value.safe_message == "Stored evidence failed its integrity check."
