"""Strict immutable models for the shared P6 product-view contract."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Final, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import SHA256_PATTERN

PRODUCT_VIEW_SCHEMA_VERSION: Final[Literal["p6-product-view/v1"]] = "p6-product-view/v1"
COUNTERFACTUAL_NOT_AVAILABLE: Final[str] = "Counterfactual comparison: not_available"

ProductVisibility: TypeAlias = Literal["diagnosis"]
ProductMode: TypeAlias = Literal["project_audit"]
ResultDisposition: TypeAlias = Literal[
    "supported",
    "mixed",
    "abstain",
    "not_estimated",
    "redacted",
]
ClaimType: TypeAlias = Literal[
    "evidence_statement",
    "cause_assertion",
    "uncertainty_statement",
    "recommended_action",
    "other",
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
_METRIC_NAME_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$"


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


class ProductMetricDefinition(_StrictFrozenModel):
    metric_name: str = Field(pattern=_METRIC_NAME_PATTERN)
    direction: Literal["higher_is_better", "lower_is_better"]
    regression_threshold: float = Field(ge=0, allow_inf_nan=False)


class ProductMetricObservation(_StrictFrozenModel):
    snapshot_id: str = Field(pattern=_ID_PATTERN)
    run_id: str = Field(pattern=_METRIC_NAME_PATTERN)
    step: int | None = Field(default=None, ge=0)
    value: float = Field(allow_inf_nan=False)


class ProductMetricChange(_StrictFrozenModel):
    evidence_id: str = Field(pattern=_ID_PATTERN)
    metric_name: str = Field(pattern=_METRIC_NAME_PATTERN)
    kind: Literal["added", "removed", "increased", "decreased"]
    before: ProductMetricObservation | None
    after: ProductMetricObservation | None
    delta: float | None = Field(allow_inf_nan=False)
    adverse_status: Literal["adverse", "not_adverse", "not_applicable"]

    @model_validator(mode="after")
    def _change_reconciles(self) -> Self:
        _validate_metric_change_shape(self)
        _validate_metric_change_pair(self)
        _validate_metric_adverse_status(self)
        return self


def _validate_metric_change_shape(change: ProductMetricChange) -> None:
    if change.kind == "added":
        valid = change.before is None and change.after is not None and change.delta is None
        message = "added product metric requires only an after observation"
    elif change.kind == "removed":
        valid = change.before is not None and change.after is None and change.delta is None
        message = "removed product metric requires only a before observation"
    else:
        valid = change.before is not None and change.after is not None and change.delta is not None
        message = "changed product metric requires before, after and delta"
    if not valid:
        raise ValueError(message)


def _validate_metric_change_pair(change: ProductMetricChange) -> None:
    if change.before is None or change.after is None or change.delta is None:
        return
    if change.before.snapshot_id == change.after.snapshot_id:
        raise ValueError("product metric observations must belong to different snapshots")
    if change.before.run_id != change.after.run_id or change.before.step != change.after.step:
        raise ValueError("product metric observations must share run and step identity")
    expected = Decimal(str(change.after.value)) - Decimal(str(change.before.value))
    if expected == 0:
        raise ValueError("changed product metric delta must be non-zero")
    if Decimal(str(change.delta)) != expected:
        raise ValueError("product metric delta does not match observations")
    if (change.kind == "increased") != (expected > 0):
        raise ValueError("product metric kind does not match its delta")


def _validate_metric_adverse_status(change: ProductMetricChange) -> None:
    not_comparable = change.kind in {"added", "removed"}
    if not_comparable != (change.adverse_status == "not_applicable"):
        raise ValueError("product metric adverse status does not match its kind")


class ProductSnapshot(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    baseline_id: str = Field(pattern=_ID_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)
    metric_definitions: tuple[ProductMetricDefinition, ...]
    metric_changes: tuple[ProductMetricChange, ...]

    @field_validator("metric_definitions")
    @classmethod
    def _canonical_definitions(
        cls, values: tuple[ProductMetricDefinition, ...]
    ) -> tuple[ProductMetricDefinition, ...]:
        names = tuple(value.metric_name for value in values)
        if len(names) != len(set(names)):
            raise ValueError("product metric definitions must have unique names")
        return tuple(sorted(values, key=lambda value: value.metric_name))

    @field_validator("metric_changes")
    @classmethod
    def _canonical_changes(
        cls, values: tuple[ProductMetricChange, ...]
    ) -> tuple[ProductMetricChange, ...]:
        ids = tuple(value.evidence_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("product metric changes must have unique evidence IDs")
        identities = tuple(_metric_change_identity(value) for value in values)
        if len(identities) != len(set(identities)):
            raise ValueError(
                "product metric changes must have unique run, metric and step identity"
            )
        return tuple(sorted(values, key=lambda value: value.evidence_id))

    @model_validator(mode="after")
    def _metrics_reconcile(self) -> Self:
        definitions = {value.metric_name for value in self.metric_definitions}
        if any(value.metric_name not in definitions for value in self.metric_changes):
            raise ValueError("product metric change does not have a definition")
        for change in self.metric_changes:
            if change.before is not None and change.before.snapshot_id != self.baseline_id:
                raise ValueError("product metric before observation has the wrong snapshot")
            if change.after is not None and change.after.snapshot_id != self.id:
                raise ValueError("product metric after observation has the wrong snapshot")
        return self


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
    disposition: ResultDisposition | None
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

    @field_validator("missing_evidence")
    @classmethod
    def _counterfactual_marker_is_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_counterfactual_marker(values)


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
    claim_type: ClaimType
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

    @field_validator("missing_evidence")
    @classmethod
    def _counterfactual_marker_is_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_counterfactual_marker(values)

    @model_validator(mode="after")
    def _evidence_roles_are_disjoint(self) -> Self:
        if set(self.citation_ids) & set(self.counterevidence_ids):
            raise ValueError("claim citation and counterevidence IDs must be disjoint")
        return self


class ProductReproductionRef(_StrictFrozenModel):
    record_id: str = Field(pattern=_ID_PATTERN)
    record_kind: Literal["snapshot", "snapshot_comparison", "regression_event"]


class ProductEvidence(_StrictFrozenModel):
    id: str = Field(pattern=_ID_PATTERN)
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=4000)
    reproduction_ref: ProductReproductionRef
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    visibility: ProductVisibility
    redacted: Literal[False]

    @model_validator(mode="after")
    def _reproduction_ref_reconciles(self) -> Self:
        expected = {
            "snapshot": "snapshot",
            "snapshot_comparison": "snapshot_comparison",
            "metric_observation": "snapshot_comparison",
            "regression_candidate": "regression_event",
        }.get(self.kind)
        if expected is not None and self.reproduction_ref.record_kind != expected:
            raise ValueError("product evidence reproduction record kind does not match evidence")
        return self


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
        evidence_ids = self._evidence_ids()
        turn_ids = self._turn_ids(evidence_ids)
        claim_ids = self._claim_ids(turn_ids, evidence_ids)
        self._graph_sources_reconcile(evidence_ids, claim_ids)
        if _contains_counterfactual_marker_outside_missing_evidence(self.model_dump(mode="python")):
            raise ValueError("counterfactual marker may appear only in missing evidence")
        if self.result.denominators.claims != len(self.claims):
            raise ValueError("product claim denominator does not match the projection")
        self._result_status_reconciles()
        return self

    def _evidence_ids(self) -> set[str]:
        evidence_by_id = {item.id: item for item in self.evidence}
        evidence_ids = set(evidence_by_id)
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("product evidence IDs must be unique")
        if any(change.evidence_id not in evidence_ids for change in self.snapshot.metric_changes):
            raise ValueError("product metric change references unavailable evidence")
        if any(
            evidence_by_id[change.evidence_id].kind != "metric_observation"
            for change in self.snapshot.metric_changes
        ):
            raise ValueError("product metric change references non-metric evidence")
        metric_change_evidence_ids = {change.evidence_id for change in self.snapshot.metric_changes}
        metric_evidence_ids = {
            item.id for item in self.evidence if item.kind == "metric_observation"
        }
        if metric_change_evidence_ids != metric_evidence_ids:
            raise ValueError(
                "product metric changes and evidence must have exact one-to-one parity"
            )
        return evidence_ids

    def _turn_ids(self, evidence_ids: set[str]) -> set[str]:
        turn_ids = {item.id for item in self.conversation.turns}
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
        return turn_ids

    def _claim_ids(self, turn_ids: set[str], evidence_ids: set[str]) -> set[str]:
        claim_ids = {item.id for item in self.claims}
        if len(claim_ids) != len(self.claims):
            raise ValueError("product claim IDs must be unique")
        if any(claim.turn_id not in turn_ids for claim in self.claims):
            raise ValueError("product claim references an unknown turn")
        if any(
            not (set(claim.citation_ids) | set(claim.counterevidence_ids)) <= evidence_ids
            for claim in self.claims
        ):
            raise ValueError("product claim references unavailable evidence")
        return claim_ids

    def _result_status_reconciles(self) -> None:
        if self.result.status == "complete":
            if self.result.disposition is None:
                raise ValueError("complete product result requires a disposition")
            return
        if self.result.disposition is not None or self.claims:
            raise ValueError("technical failure must not contain claims or a disposition")
        if not self.conversation.turns[-1].abstained:
            raise ValueError("technical failure turn must abstain")
        if COUNTERFACTUAL_NOT_AVAILABLE in self.conversation.turns[-1].missing_evidence:
            raise ValueError("technical failure must not assert counterfactual availability")
        if any(node.kind not in {"Snapshot", "EvidenceItem"} for node in self.graph.nodes):
            raise ValueError("technical failure graph contains a conclusion node")
        if any(edge.kind != "OBSERVED_IN" for edge in self.graph.edges):
            raise ValueError("technical failure graph contains a conclusion edge")

    def _graph_sources_reconcile(self, evidence_ids: set[str], claim_ids: set[str]) -> None:
        sources_by_kind = {
            "Snapshot": {self.snapshot.id},
            "EvidenceItem": evidence_ids,
            "AtomicClaim": claim_ids,
            "Disposition": ({self.result.id} if self.result.status == "complete" else set()),
        }
        nodes_by_source: dict[tuple[str, str], str] = {}
        for kind, expected_sources in sources_by_kind.items():
            nodes = tuple(node for node in self.graph.nodes if node.kind == kind)
            actual_sources = {node.source_id for node in nodes}
            if len(nodes) != len(expected_sources) or actual_sources != expected_sources:
                raise ValueError("product graph does not contain the exact required node sources")
            nodes_by_source.update(((node.kind, node.source_id), node.id) for node in nodes)
        if len(nodes_by_source) != len(self.graph.nodes):
            raise ValueError("product graph node source is unavailable in this view")
        self._graph_edges_reconcile(nodes_by_source)

    def _graph_edges_reconcile(self, nodes_by_source: dict[tuple[str, str], str]) -> None:
        allowed_kinds = {"OBSERVED_IN", "CITES", "ASSIGNED_DISPOSITION"}
        if any(edge.kind not in allowed_kinds for edge in self.graph.edges):
            raise ValueError("product graph contains an unsupported relationship")
        semantic_edges = tuple((edge.kind, edge.source, edge.target) for edge in self.graph.edges)
        if len(semantic_edges) != len(set(semantic_edges)):
            raise ValueError("product graph relationships must be semantically unique")

        snapshot_node_id = nodes_by_source[("Snapshot", self.snapshot.id)]
        evidence_node_ids = {
            node_id for (kind, _), node_id in nodes_by_source.items() if kind == "EvidenceItem"
        }
        observed = tuple(edge for edge in self.graph.edges if edge.kind == "OBSERVED_IN")
        if any(
            edge.source not in evidence_node_ids or edge.target != snapshot_node_id
            for edge in observed
        ):
            raise ValueError("product graph observation edge has invalid endpoint roles")

        expected_citations = {
            (
                nodes_by_source[("AtomicClaim", claim.id)],
                nodes_by_source[("EvidenceItem", evidence_id)],
            )
            for claim in self.claims
            for evidence_id in claim.citation_ids
        }
        actual_citations = {
            (edge.source, edge.target) for edge in self.graph.edges if edge.kind == "CITES"
        }
        if actual_citations != expected_citations:
            raise ValueError("product graph citation edges do not match its claims")

        expected_dispositions: set[tuple[str, str]] = set()
        if self.result.status == "complete":
            disposition_node_id = nodes_by_source[("Disposition", self.result.id)]
            expected_dispositions = {
                (nodes_by_source[("AtomicClaim", claim.id)], disposition_node_id)
                for claim in self.claims
            }
        actual_dispositions = {
            (edge.source, edge.target)
            for edge in self.graph.edges
            if edge.kind == "ASSIGNED_DISPOSITION"
        }
        if actual_dispositions != expected_dispositions:
            raise ValueError("product graph disposition edges do not match its claims")


def _metric_change_identity(change: ProductMetricChange) -> tuple[str, str, int | None]:
    observation = change.before if change.before is not None else change.after
    if observation is None:
        raise ValueError("product metric change requires an observation identity")
    return observation.run_id, change.metric_name, observation.step


def _require_unique(values: Iterable[str], *, label: str) -> None:
    materialized: tuple[str, ...] = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _unique_ids(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if any(re.fullmatch(_ID_PATTERN, value) is None for value in values):
        raise ValueError(f"{label} contains an invalid identifier")
    _require_unique(values, label=label)
    return values


def _bounded_counterfactual_marker(values: tuple[str, ...]) -> tuple[str, ...]:
    count = values.count(COUNTERFACTUAL_NOT_AVAILABLE)
    if count > 1:
        raise ValueError("counterfactual not-available marker may appear at most once")
    if count == 1 and values[-1] != COUNTERFACTUAL_NOT_AVAILABLE:
        raise ValueError("counterfactual not-available marker must be last")
    return values


def _contains_counterfactual_marker_outside_missing_evidence(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key != "missing_evidence"
            and _contains_counterfactual_marker_outside_missing_evidence(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_counterfactual_marker_outside_missing_evidence(item) for item in value)
    return value == COUNTERFACTUAL_NOT_AVAILABLE


__all__ = [
    "COUNTERFACTUAL_NOT_AVAILABLE",
    "PRODUCT_VIEW_SCHEMA_VERSION",
    "ProductView",
]
