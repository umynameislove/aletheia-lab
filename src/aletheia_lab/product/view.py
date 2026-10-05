"""Strict immutable models for the shared P6 product-view contract."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import SHA256_PATTERN

PRODUCT_VIEW_SCHEMA_VERSION: Final[Literal["p6-product-view/v1"]] = "p6-product-view/v1"

ProductVisibility: TypeAlias = Literal["diagnosis"]
ProductMode: TypeAlias = Literal["project_audit"]
ResultDisposition: TypeAlias = Literal[
    "supported",
    "mixed",
    "abstain",
    "not_estimated",
    "technical_failure",
    "redacted",
]
ClaimSupport: TypeAlias = Literal[
    "fully_supported",
    "partially_supported",
    "unsupported",
    "not_estimated",
    "technical_failure",
    "redacted",
]

_ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_EDGE_KIND_PATTERN: Final[str] = r"^[A-Z][A-Z0-9_]{0,63}$"
_FORBIDDEN_EDGE_KINDS: Final[frozenset[str]] = frozenset({"CAUSES", "RELATED_TO"})


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class ProductProject(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=160)


class ProductSnapshot(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    baseline_id: str = Field(pattern=_ID_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)


class ProductRuntime(_StrictFrozenModel):
    provider: Literal["deterministic_mock"]
    model: str = Field(min_length=1, max_length=160)
    external_call: Literal[False]


class ProductDenominators(_StrictFrozenModel):
    independent_families: None
    contexts: int = Field(ge=0)
    outputs: int = Field(ge=0)
    claims: int = Field(ge=0)


class ProductResult(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    status: Literal["complete", "technical_failure"]
    disposition: ResultDisposition
    causal_status: Literal["unverified"]
    summary: str = Field(min_length=1, max_length=1000)
    denominators: ProductDenominators
    allowed_wording: str = Field(min_length=1, max_length=1000)
    forbidden_wording: str = Field(min_length=1, max_length=1000)
    caveat: str = Field(min_length=1, max_length=1000)


class ProductTurn(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    snapshot_id: str = Field(pattern=_ID_PATTERN)
    result_id: str = Field(pattern=_ID_PATTERN)
    runtime: ProductRuntime
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)
    abstained: bool
    visible_evidence_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...]

    @field_validator("visible_evidence_ids")
    @classmethod
    def _valid_visible_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(values, label="turn-visible evidence")


class ProductConversation(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    turns: tuple[ProductTurn, ...]

    @field_validator("turns")
    @classmethod
    def _unique_turns(cls, values: tuple[ProductTurn, ...]) -> tuple[ProductTurn, ...]:
        _require_unique((item.id for item in values), label="conversation turn IDs")
        return values


class ProductClaim(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    turn_id: str = Field(pattern=_ID_PATTERN)
    text: str = Field(min_length=1, max_length=4000)
    epistemic_state: Literal["observed", "hypothesis", "abstained"]
    support: ClaimSupport
    citation_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    max_strength: Literal["observation", "bounded_hypothesis", "abstain"]

    @field_validator("citation_ids")
    @classmethod
    def _valid_citation_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(values, label="claim citation IDs")

    @field_validator("counterevidence_ids")
    @classmethod
    def _valid_counterevidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_ids(values, label="claim counterevidence IDs")

    @model_validator(mode="after")
    def _evidence_roles_are_disjoint(self) -> Self:
        if set(self.citation_ids) & set(self.counterevidence_ids):
            raise ValueError("claim citation and counterevidence IDs must be disjoint")
        return self


class ProductEvidence(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=4000)
    relative_path: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    visibility: ProductVisibility
    redacted: Literal[False]


class ProductGraphNode(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    kind: str = Field(min_length=1, max_length=64)
    source_id: str = Field(pattern=_ID_PATTERN)
    visibility: ProductVisibility


class ProductGraphEdge(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    kind: str = Field(pattern=_EDGE_KIND_PATTERN)
    source: str = Field(pattern=_ID_PATTERN)
    target: str = Field(pattern=_ID_PATTERN)
    visibility: ProductVisibility

    @field_validator("kind")
    @classmethod
    def _noncausal_kind(cls, value: str) -> str:
        if value in _FORBIDDEN_EDGE_KINDS:
            raise ValueError("causal or generic related graph edges are forbidden")
        return value


class ProductGraph(_StrictFrozenModel):
    nodes: tuple[ProductGraphNode, ...]
    edges: tuple[ProductGraphEdge, ...]

    @model_validator(mode="after")
    def _graph_reconciles(self) -> Self:
        node_ids = {item.id for item in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("product graph node IDs must be unique")
        _require_unique((item.id for item in self.edges), label="product graph edge IDs")
        if any(edge.source not in node_ids or edge.target not in node_ids for edge in self.edges):
            raise ValueError("product graph contains a dangling edge")
        return self


class ProductView(_StrictFrozenModel):
    """One exact, diagnosis-visible, self-contained product projection."""

    schema_version: Literal["p6-product-view/v1"] = PRODUCT_VIEW_SCHEMA_VERSION
    demo_only: bool
    mode: ProductMode
    visibility: ProductVisibility
    project: ProductProject
    snapshot: ProductSnapshot
    runtime: ProductRuntime
    result: ProductResult
    conversation: ProductConversation
    claims: tuple[ProductClaim, ...]
    evidence: tuple[ProductEvidence, ...]
    graph: ProductGraph

    @model_validator(mode="after")
    def _references_reconcile(self) -> Self:
        evidence_ids = {item.id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("product evidence IDs must be unique")
        turn_ids = {item.id for item in self.conversation.turns}
        claim_ids = {item.id for item in self.claims}
        if len(claim_ids) != len(self.claims):
            raise ValueError("product claim IDs must be unique")
        if any(turn.snapshot_id != self.snapshot.id for turn in self.conversation.turns):
            raise ValueError("product turn belongs to a different snapshot")
        if not self.conversation.turns or self.conversation.turns[-1].result_id != self.result.id:
            raise ValueError("latest product turn does not belong to the current result")
        if self.conversation.turns[-1].runtime != self.runtime:
            raise ValueError("latest product turn runtime does not match the current result")
        if any(
            not set(turn.visible_evidence_ids) <= evidence_ids for turn in self.conversation.turns
        ):
            raise ValueError("turn-visible evidence is absent from the product view")
        if any(claim.turn_id not in turn_ids for claim in self.claims):
            raise ValueError("product claim references an unknown turn")
        if any(
            not (set(claim.citation_ids) | set(claim.counterevidence_ids)) <= evidence_ids
            for claim in self.claims
        ):
            raise ValueError("product claim references unavailable evidence")
        self._graph_sources_reconcile(evidence_ids, claim_ids)
        if self.result.denominators.claims != len(self.claims):
            raise ValueError("product claim denominator does not match the projection")
        return self

    def _graph_sources_reconcile(self, evidence_ids: set[str], claim_ids: set[str]) -> None:
        sources_by_kind = {
            "Snapshot": {self.snapshot.id},
            "EvidenceItem": evidence_ids,
            "AtomicClaim": claim_ids,
            "Disposition": {self.result.id},
        }
        for node in self.graph.nodes:
            allowed = sources_by_kind.get(node.kind)
            if allowed is None or node.source_id not in allowed:
                raise ValueError("product graph node source is unavailable in this view")


def _require_unique(values: Iterable[str], *, label: str) -> None:
    materialized: tuple[str, ...] = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _unique_ids(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if any(re.fullmatch(_ID_PATTERN, value) is None for value in values):
        raise ValueError(f"{label} contains an invalid identifier")
    _require_unique(values, label=label)
    return values


__all__ = ["PRODUCT_VIEW_SCHEMA_VERSION", "ProductView"]
