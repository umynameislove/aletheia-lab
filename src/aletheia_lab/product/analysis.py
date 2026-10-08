"""Deterministic offline ProductView construction from diagnosis-visible evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.evidence import (
    ProductEvidenceItem,
    ProductEvidenceProjection,
    resolve_product_evidence_references,
)
from aletheia_lab.product.evidence_store import load_diagnosis_evidence_projection
from aletheia_lab.product.lifecycle import ProductLifecycleStore
from aletheia_lab.product.mapping import adverse_metric_change_ids, load_stored_mapping
from aletheia_lab.product.projection import build_product_graph_projection
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.lineage import ProjectLineageGraph
from aletheia_lab.project.mapping import MetricObservation, ProjectMappingConfiguration
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import (
    EvidenceRole,
    ProjectMetricChange,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
)
from aletheia_lab.project.snapshots import ProjectSnapshot

_PENDING_RESULT_ID: Final[str] = "p6-result-" + "0" * 64
_SOURCE_INTEGRITY_MESSAGE: Final[str] = "Stored project evidence failed its integrity check."
_ROLE_PRESENTATION: Final[dict[EvidenceRole, tuple[str, str, str]]] = {
    "before_snapshot": (
        "snapshot",
        "Baseline project snapshot",
        "A baseline snapshot is bound to this regression evidence generation.",
    ),
    "after_snapshot": (
        "snapshot",
        "Observed project snapshot",
        "The analyzed project snapshot is bound to this regression evidence generation.",
    ),
    "snapshot_comparison": (
        "snapshot_comparison",
        "Stored snapshot comparison",
        "The stored comparison records a change between two immutable project snapshots.",
    ),
    "metric_change": (
        "metric_observation",
        "Observed metric change",
        "The stored evidence records an observed metric change without assigning a cause.",
    ),
    "regression_candidate": (
        "regression_candidate",
        "Regression candidate",
        "The stored event is a non-causal regression candidate with unverified causal status.",
    ),
}
_CITATION_ROLE_ORDER: Final[tuple[EvidenceRole, ...]] = (
    "snapshot_comparison",
    "metric_change",
    "regression_candidate",
)


def analyze_product_mock(
    store_root: Path,
    project_id: str,
    snapshot_id: str,
    question: str,
) -> ProductView:
    """Create and persist one deterministic result without network or hidden evidence."""

    checked_question = checked_product_question(question)
    projection = load_diagnosis_evidence_projection(
        store_root,
        project_id=project_id,
        snapshot_id=snapshot_id,
    )
    baseline_id = _singleton_source_id(projection, "before_snapshot")
    snapshot, comparison, configuration, lineage = _load_analysis_sources(
        store_root,
        project_id=project_id,
        snapshot_id=snapshot_id,
        baseline_id=baseline_id,
        comparison_id=_singleton_source_id(projection, "snapshot_comparison"),
        event_id=_singleton_source_id(projection, "regression_candidate"),
    )
    visible_ids = tuple(item.evidence_id for item in projection.items)
    citation_ids = _citation_ids(projection)
    references = resolve_product_evidence_references(
        projection,
        citation_ids=citation_ids,
        counterevidence_ids=(),
        visible_evidence_ids=visible_ids,
    )
    missing_evidence = _missing_evidence(projection)
    identity = {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "question": checked_question,
        "projection_sha256": projection.projection_sha256,
    }
    turn_id = _scoped_id("p6-turn", identity)
    claim_id = _scoped_id(
        "p6-claim",
        {**identity, "turn_id": turn_id, "citation_ids": list(references.citation_ids)},
    )
    conversation_id = _scoped_id(
        "p6-conversation",
        {"project_id": project_id, "snapshot_id": snapshot_id},
    )
    evidence = tuple(
        _product_evidence(item, comparison_id=comparison.comparison_id) for item in projection.items
    )
    metric_definitions, metric_changes = _product_metrics(
        projection,
        comparison,
        configuration,
    )
    runtime = {
        "provider": "deterministic_mock",
        "model": "p6-deterministic-mock/v1",
        "external_call": False,
    }
    payload = {
        "schema_version": "p6-product-view/v1",
        "demo_only": False,
        "mode": "project_audit",
        "visibility": "diagnosis",
        "project": {"id": project_id, "display_name": "Imported project"},
        "snapshot": {
            "id": snapshot_id,
            "baseline_id": baseline_id,
            "sha256": snapshot.state_sha256,
            "metric_definitions": list(metric_definitions),
            "metric_changes": list(metric_changes),
        },
        "runtime": runtime,
        "result": {
            "id": _PENDING_RESULT_ID,
            "status": "complete",
            "disposition": "abstain",
            "causal_status": "unverified",
            "summary": (
                "A stored project change is visible, but the authorized evidence does not "
                "establish one cause."
            ),
            "denominators": {
                "independent_families": None,
                "contexts": 1,
                "outputs": 1,
                "claims": 1,
            },
            "allowed_wording": "The stored evidence records an observed project change.",
            "forbidden_wording": "One visible project artifact caused the observed change.",
            "caveat": (
                "Deterministic offline project audit; no causal ground truth or model-quality "
                "result."
            ),
        },
        "conversation": {
            "id": conversation_id,
            "turns": [
                {
                    "id": turn_id,
                    "snapshot_id": snapshot_id,
                    "result_id": _PENDING_RESULT_ID,
                    "runtime": runtime,
                    "question": checked_question,
                    "answer": (
                        "The change is observed in diagnosis-visible evidence; its cause remains "
                        "unverified."
                    ),
                    "abstained": True,
                    "visible_evidence_ids": list(references.visible_evidence_ids),
                    "missing_evidence": list(missing_evidence),
                }
            ],
        },
        "claims": [
            {
                "id": claim_id,
                "turn_id": turn_id,
                "text": "The stored project evidence records an observed snapshot change.",
                "claim_type": "evidence_statement",
                "epistemic_state": "observed",
                "support": "fully_supported",
                "citation_ids": list(references.citation_ids),
                "counterevidence_ids": list(references.counterevidence_ids),
                "missing_evidence": list(missing_evidence),
                "max_strength": "observation",
            }
        ],
        "evidence": list(evidence),
        "graph": build_product_graph_projection(
            lineage,
            projection,
            snapshot_id=snapshot_id,
            claim_citations={claim_id: references.citation_ids},
            disposition_id=_PENDING_RESULT_ID,
            result_scope_id=turn_id,
        ).graph.model_dump(mode="json"),
    }
    view = ProductView.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
    return persist_product_result(store_root, view)


def _load_analysis_sources(
    store_root: Path,
    *,
    project_id: str,
    snapshot_id: str,
    baseline_id: str,
    comparison_id: str,
    event_id: str,
) -> tuple[
    ProjectSnapshot,
    ProjectSnapshotComparison,
    ProjectMappingConfiguration,
    ProjectLineageGraph,
]:
    try:
        with ProductLifecycleStore(store_root) as lifecycle:
            state = lifecycle.get_project_state_for_snapshot(project_id, snapshot_id)
        state_payload = state.payload()
        configuration = load_stored_mapping(state_payload.get("mapping_configuration"))
        lineage_id = state_payload.get("lineage_graph_id")
        with ProjectStore(store_root) as store:
            baseline = store.load(baseline_id)
            snapshot = store.load(snapshot_id)
            comparison = store.load(comparison_id)
            event = store.load(event_id)
            lineage = store.load(lineage_id) if isinstance(lineage_id, str) else None
        if (
            configuration is None
            or not isinstance(baseline, ProjectSnapshot)
            or baseline.project_id != project_id
            or not isinstance(snapshot, ProjectSnapshot)
            or snapshot.project_id != project_id
            or snapshot.mapping_configuration_sha256 != configuration.mapping_sha256
            or not isinstance(comparison, ProjectSnapshotComparison)
            or comparison.project_id != project_id
            or comparison.before_snapshot_id != baseline_id
            or comparison.after_snapshot_id != snapshot_id
            or not isinstance(event, ProjectRegressionEvent)
            or event.project_id != project_id
            or event.comparison_id != comparison_id
            or not isinstance(lineage, ProjectLineageGraph)
            or lineage.project_id != project_id
            or state_payload.get("comparison_id") != comparison_id
            or state_payload.get("event_id") != event_id
            or state_payload.get("lineage_graph_id") != lineage.graph_id
        ):
            raise ValueError("analysis sources do not reconcile")
        return snapshot, comparison, configuration, lineage
    except ProductError:
        raise
    except Exception:
        raise ProductError("store_integrity_error", _SOURCE_INTEGRITY_MESSAGE) from None


def _singleton_source_id(projection: ProductEvidenceProjection, role: EvidenceRole) -> str:
    matches = tuple(item.source_id for item in projection.items if item.role == role)
    if len(matches) != 1:
        raise ProductError("evidence_not_available", "Required evidence is not available.")
    return matches[0]


def _citation_ids(projection: ProductEvidenceProjection) -> tuple[str, ...]:
    by_role = {item.role: item.evidence_id for item in projection.items}
    selected = tuple(by_role[role] for role in _CITATION_ROLE_ORDER if role in by_role)
    if not selected:
        raise ProductError("evidence_not_available", "Required evidence is not available.")
    return selected


def _missing_evidence(projection: ProductEvidenceProjection) -> tuple[str, ...]:
    missing = tuple(
        f"Missing evidence category: {role.replace('_', ' ')}"
        for role in projection.missing_categories
    )
    omitted = tuple(
        f"Omitted evidence category: {role.replace('_', ' ')}"
        for role in projection.omitted_categories
    )
    return (
        *missing,
        *omitted,
        COUNTERFACTUAL_NOT_AVAILABLE,
    )


def _product_evidence(
    item: ProductEvidenceItem,
    *,
    comparison_id: str,
) -> dict[str, object]:
    kind, title, text = _ROLE_PRESENTATION[item.role]
    record_kind = {
        "before_snapshot": "snapshot",
        "after_snapshot": "snapshot",
        "snapshot_comparison": "snapshot_comparison",
        "metric_change": "snapshot_comparison",
        "regression_candidate": "regression_event",
    }[item.role]
    record_id = comparison_id if item.role == "metric_change" else item.source_id
    return {
        "id": item.evidence_id,
        "kind": kind,
        "title": title,
        "text": text,
        "reproduction_ref": {"record_id": record_id, "record_kind": record_kind},
        "source_sha256": item.source_sha256,
        "visibility": "diagnosis",
        "redacted": False,
    }


def _product_metrics(
    projection: ProductEvidenceProjection,
    comparison: ProjectSnapshotComparison,
    configuration: ProjectMappingConfiguration,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    evidence_by_change = {
        item.source_id: item.evidence_id
        for item in projection.items
        if item.role == "metric_change"
    }
    if set(evidence_by_change) != {change.metric_change_id for change in comparison.metric_changes}:
        raise ProductError("store_integrity_error", _SOURCE_INTEGRITY_MESSAGE)
    adverse_ids = set(adverse_metric_change_ids(comparison, configuration.metric_definitions))
    definitions = tuple(
        {
            "metric_name": value.metric_name,
            "direction": value.direction,
            "regression_threshold": _json_number(value.regression_threshold),
        }
        for value in configuration.metric_definitions
    )
    changes = tuple(
        _product_metric_change(
            value,
            evidence_id=evidence_by_change[value.metric_change_id],
            before_snapshot_id=comparison.before_snapshot_id,
            after_snapshot_id=comparison.after_snapshot_id,
            adverse=value.metric_change_id in adverse_ids,
        )
        for value in comparison.metric_changes
    )
    return definitions, changes


def _product_metric_change(
    change: ProjectMetricChange,
    *,
    evidence_id: str,
    before_snapshot_id: str,
    after_snapshot_id: str,
    adverse: bool,
) -> dict[str, object]:
    observation = change.after if change.after is not None else change.before
    if observation is None:
        raise ProductError("store_integrity_error", _SOURCE_INTEGRITY_MESSAGE)
    return {
        "evidence_id": evidence_id,
        "metric_name": observation.metric_name,
        "kind": change.kind,
        "before": _product_metric_observation(change.before, before_snapshot_id),
        "after": _product_metric_observation(change.after, after_snapshot_id),
        "delta": None if change.delta is None else _metric_delta(change),
        "adverse_status": (
            "not_applicable"
            if change.kind in {"added", "removed"}
            else "adverse"
            if adverse
            else "not_adverse"
        ),
    }


def _product_metric_observation(
    observation: MetricObservation | None,
    snapshot_id: str,
) -> dict[str, object] | None:
    if observation is None:
        return None
    return {
        "snapshot_id": snapshot_id,
        "run_id": observation.run_id,
        "step": observation.step,
        "value": _json_number(observation.metric_value),
    }


def _metric_delta(change: ProjectMetricChange) -> float:
    if change.before is None or change.after is None:
        raise ProductError("store_integrity_error", _SOURCE_INTEGRITY_MESSAGE)
    return float(Decimal(str(change.after.metric_value)) - Decimal(str(change.before.metric_value)))


def _json_number(value: float) -> float:
    return float(Decimal(str(value)))


def _scoped_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_project_sha256(payload)}"


__all__ = ["analyze_product_mock"]
