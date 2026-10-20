"""Immutable, visibility-bounded evidence DTOs for the P6 product layer."""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.identity import (
    PROJECT_EVIDENCE_BUNDLE_ID_PATTERN,
    PROJECT_EVIDENCE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
)
from aletheia_lab.project.regression import (
    EvidenceRole,
    ProjectEvidenceBundle,
    ProjectEvidenceReference,
)

PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION: Final[Literal["p6-evidence-projection/v1"]] = (
    "p6-evidence-projection/v1"
)

EvidenceVisibility: TypeAlias = Literal["public", "diagnosis", "evaluator"]
EvidenceRedactionState: TypeAlias = Literal["none", "withheld"]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

_SOURCE_ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_VISIBILITY_RANK: Final[dict[EvidenceVisibility, int]] = {
    "public": 0,
    "diagnosis": 1,
    "evaluator": 2,
}
_ROLE_ORDER: Final[dict[EvidenceRole, int]] = {
    "before_snapshot": 0,
    "after_snapshot": 1,
    "snapshot_comparison": 2,
    "metric_change": 3,
    "regression_candidate": 4,
}
_REQUIRED_ROLES: Final[frozenset[EvidenceRole]] = frozenset(_ROLE_ORDER)
_REFERENCE_INVALID_MESSAGE: Final[str] = "The evidence selection is not available in this view."
_PROJECTION_INVALID_MESSAGE: Final[str] = "Stored evidence failed its integrity check."


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class ProductEvidenceItem(_StrictFrozenModel):
    """One payload-free evidence reference safe for an authorized projection."""

    evidence_id: str = Field(pattern=PROJECT_EVIDENCE_ID_PATTERN)
    role: EvidenceRole
    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    source_sha256: Sha256
    provenance_links: tuple[str, ...]
    visibility: EvidenceVisibility
    redaction_state: EvidenceRedactionState

    @field_validator("provenance_links")
    @classmethod
    def _canonical_links(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("product evidence provenance links must be unique")
        if any(
            not isinstance(value, str) or re.fullmatch(PROJECT_EVIDENCE_ID_PATTERN, value) is None
            for value in values
        ):
            raise ValueError("product evidence provenance link is invalid")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _redaction_reconciles(self) -> ProductEvidenceItem:
        if self.redaction_state == "withheld" and self.visibility != "evaluator":
            raise ValueError("withheld product evidence must remain evaluator-only")
        return self


def _canonical_roles(
    values: tuple[EvidenceRole, ...],
    *,
    label: str,
) -> tuple[EvidenceRole, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    return tuple(sorted(values, key=_ROLE_ORDER.__getitem__))


class ProductEvidenceProjection(_StrictFrozenModel):
    """A self-hashed projection containing no hidden evidence identifiers."""

    schema_version: Literal["p6-evidence-projection/v1"] = (
        PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION
    )
    evidence_bundle_id: str = Field(pattern=PROJECT_EVIDENCE_BUNDLE_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    visibility: EvidenceVisibility
    items: tuple[ProductEvidenceItem, ...]
    missing_categories: tuple[EvidenceRole, ...]
    omitted_categories: tuple[EvidenceRole, ...]
    projection_sha256: Sha256

    @field_validator("items")
    @classmethod
    def _canonical_items(
        cls,
        values: tuple[ProductEvidenceItem, ...],
    ) -> tuple[ProductEvidenceItem, ...]:
        ids = tuple(value.evidence_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("product evidence items must have unique identifiers")
        return tuple(sorted(values, key=lambda value: value.evidence_id))

    @field_validator("missing_categories")
    @classmethod
    def _canonical_missing(
        cls,
        values: tuple[EvidenceRole, ...],
    ) -> tuple[EvidenceRole, ...]:
        return _canonical_roles(values, label="missing evidence categories")

    @field_validator("omitted_categories")
    @classmethod
    def _canonical_omitted(
        cls,
        values: tuple[EvidenceRole, ...],
    ) -> tuple[EvidenceRole, ...]:
        return _canonical_roles(values, label="omitted evidence categories")

    @model_validator(mode="after")
    def _projection_reconciles(self) -> ProductEvidenceProjection:
        allowed_rank = _VISIBILITY_RANK[self.visibility]
        if any(_VISIBILITY_RANK[item.visibility] > allowed_rank for item in self.items):
            raise ValueError("product evidence projection contains a hidden item")
        known_ids = {item.evidence_id for item in self.items}
        if any(link not in known_ids for item in self.items for link in item.provenance_links):
            raise ValueError("product evidence projection contains a dangling provenance link")
        visible_roles = {item.role for item in self.items}
        if visible_roles & set(self.missing_categories):
            raise ValueError("visible evidence cannot also be marked missing")
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if self.projection_sha256 != canonical_project_sha256(payload):
            raise ValueError("product evidence projection hash does not reconcile")
        return self


def _canonical_evidence_ids(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    if any(re.fullmatch(PROJECT_EVIDENCE_ID_PATTERN, value) is None for value in values):
        raise ValueError(f"{label} contains an invalid identifier")
    return tuple(sorted(values))


class ProductEvidenceReferences(_StrictFrozenModel):
    """Canonical evidence references bound to one authorized projection."""

    projection_sha256: Sha256
    citation_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    visible_evidence_ids: tuple[str, ...]
    references_sha256: Sha256

    @field_validator("citation_ids")
    @classmethod
    def _canonical_citations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_evidence_ids(values, label="citation IDs")

    @field_validator("counterevidence_ids")
    @classmethod
    def _canonical_counterevidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_evidence_ids(values, label="counterevidence IDs")

    @field_validator("visible_evidence_ids")
    @classmethod
    def _canonical_visible(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_evidence_ids(values, label="visible evidence IDs")

    @model_validator(mode="after")
    def _references_reconcile(self) -> ProductEvidenceReferences:
        citations = set(self.citation_ids)
        counterevidence = set(self.counterevidence_ids)
        visible = set(self.visible_evidence_ids)
        if citations & counterevidence:
            raise ValueError("citation and counterevidence IDs must be disjoint")
        if not (citations | counterevidence) <= visible:
            raise ValueError("cited evidence must belong to the visible evidence scope")
        payload = self.model_dump(mode="json", exclude={"references_sha256"})
        if self.references_sha256 != canonical_project_sha256(payload):
            raise ValueError("product evidence reference hash does not reconcile")
        return self


def _authorized_references(
    bundle: ProjectEvidenceBundle,
    visibility: EvidenceVisibility,
) -> tuple[ProjectEvidenceReference, ...]:
    allowed_rank = _VISIBILITY_RANK[visibility]
    selected = {
        item.evidence_id: item
        for item in bundle.items
        if _VISIBILITY_RANK[item.visibility] <= allowed_rank
    }
    while True:
        closed = {
            evidence_id: item
            for evidence_id, item in selected.items()
            if all(link in selected for link in item.provenance_links)
        }
        if closed.keys() == selected.keys():
            return tuple(sorted(closed.values(), key=lambda item: item.evidence_id))
        selected = closed


def build_product_evidence_projection(
    bundle: ProjectEvidenceBundle,
    *,
    visibility: EvidenceVisibility,
) -> ProductEvidenceProjection:
    """Project one validated P3 bundle without leaking hidden IDs or dangling links."""

    checked = ProjectEvidenceBundle.model_validate(bundle.model_dump(mode="python"))
    references = _authorized_references(checked, visibility)
    visible_ids = {item.evidence_id for item in references}
    visible_roles = {item.role for item in references}
    omitted_roles = {item.role for item in checked.items if item.evidence_id not in visible_ids}
    items = tuple(
        ProductEvidenceItem(
            evidence_id=item.evidence_id,
            role=item.role,
            source_id=item.source_id,
            source_sha256=item.source_sha256,
            provenance_links=item.provenance_links,
            visibility=item.visibility,
            redaction_state=item.redaction_state,
        )
        for item in references
    )
    missing_categories = tuple(sorted(_REQUIRED_ROLES - visible_roles, key=_ROLE_ORDER.__getitem__))
    omitted_categories = tuple(sorted(omitted_roles, key=_ROLE_ORDER.__getitem__))
    payload = {
        "schema_version": PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION,
        "evidence_bundle_id": checked.evidence_bundle_id,
        "project_id": checked.project_id,
        "visibility": visibility,
        "items": [item.model_dump(mode="json") for item in items],
        "missing_categories": list(missing_categories),
        "omitted_categories": list(omitted_categories),
    }
    return ProductEvidenceProjection(
        schema_version=PRODUCT_EVIDENCE_PROJECTION_SCHEMA_VERSION,
        evidence_bundle_id=checked.evidence_bundle_id,
        project_id=checked.project_id,
        visibility=visibility,
        items=items,
        missing_categories=missing_categories,
        omitted_categories=omitted_categories,
        projection_sha256=canonical_project_sha256(payload),
    )


def build_diagnosis_evidence_projection(
    bundle: ProjectEvidenceBundle,
) -> ProductEvidenceProjection:
    """Build the fixed diagnosis-visible projection used by the product UI."""

    return build_product_evidence_projection(bundle, visibility="diagnosis")


def resolve_product_evidence_references(
    projection: ProductEvidenceProjection,
    *,
    citation_ids: tuple[str, ...],
    counterevidence_ids: tuple[str, ...],
    visible_evidence_ids: tuple[str, ...],
) -> ProductEvidenceReferences:
    """Bind claim and turn references to one integrity-checked authorized view."""

    try:
        checked = ProductEvidenceProjection.model_validate(projection.model_dump(mode="python"))
    except (TypeError, ValueError):
        raise ProductError("evidence_projection_invalid", _PROJECTION_INVALID_MESSAGE) from None

    known_ids = {item.evidence_id for item in checked.items}
    supplied_ids = (*citation_ids, *counterevidence_ids, *visible_evidence_ids)
    if any(value not in known_ids for value in supplied_ids):
        raise ProductError("evidence_reference_invalid", _REFERENCE_INVALID_MESSAGE)

    sorted_citations = tuple(sorted(citation_ids))
    sorted_counterevidence = tuple(sorted(counterevidence_ids))
    sorted_visible = tuple(sorted(visible_evidence_ids))
    payload = {
        "projection_sha256": checked.projection_sha256,
        "citation_ids": list(sorted_citations),
        "counterevidence_ids": list(sorted_counterevidence),
        "visible_evidence_ids": list(sorted_visible),
    }
    try:
        return ProductEvidenceReferences(
            projection_sha256=checked.projection_sha256,
            citation_ids=sorted_citations,
            counterevidence_ids=sorted_counterevidence,
            visible_evidence_ids=sorted_visible,
            references_sha256=canonical_project_sha256(payload),
        )
    except (TypeError, ValueError):
        raise ProductError("evidence_reference_invalid", _REFERENCE_INVALID_MESSAGE) from None
