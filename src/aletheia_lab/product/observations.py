"""Offline observations for imported snapshots without an adverse regression."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.lifecycle import ProductLifecycleStore
from aletheia_lab.product.projection import build_product_graph
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import (
    COUNTERFACTUAL_NOT_AVAILABLE,
    ProductClaim,
    ProductConversation,
    ProductDenominators,
    ProductEvidence,
    ProductMetricChange,
    ProductMetricDefinition,
    ProductProject,
    ProductReproductionRef,
    ProductResult,
    ProductRuntime,
    ProductSnapshot,
    ProductTurn,
    ProductView,
)
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import ProjectMappingConfiguration
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import ProjectSnapshotComparison
from aletheia_lab.project.snapshots import ProjectSnapshot

_PENDING_RESULT_ID = "p6-result-" + "0" * 64
_INTEGRITY_MESSAGE = "Stored project observations failed their integrity check."


def _scoped_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{canonical_project_sha256(payload)}"


def _load_sources(
    store_root: Path,
    project_id: str,
    snapshot_id: str,
) -> tuple[
    ProjectSnapshot,
    ProjectSnapshot,
    ProjectMappingConfiguration,
    ProjectSnapshotComparison | None,
]:
    # Refresh owns reconciliation of the immutable lifecycle/bundle/snapshot binding.
    from aletheia_lab.product.refresh import _load_immutable_state

    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get_project_state_for_snapshot(project_id, snapshot_id)
    state, _, snapshot = _load_immutable_state(store_root, record)
    if any((state.event_id, state.evidence_bundle_id, state.lineage_graph_id)) or (
        state.adverse_metric_change_ids
    ):
        raise ProductError("store_integrity_error", _INTEGRITY_MESSAGE)
    if state.status == "confirmed":
        return snapshot, snapshot, state.mapping, None
    try:
        with ProjectStore(store_root) as store:
            if state.previous_snapshot_id is None or state.comparison_id is None:
                raise ValueError("missing comparison scope")
            before = store.load(state.previous_snapshot_id)
            comparison = store.load(state.comparison_id)
        if (
            not isinstance(before, ProjectSnapshot)
            or not isinstance(comparison, ProjectSnapshotComparison)
            or before.project_id != project_id
            or comparison.project_id != project_id
            or comparison.status != "changed"
            or comparison.before_snapshot_id != before.snapshot_id
            or comparison.after_snapshot_id != snapshot.snapshot_id
            or comparison.before_record_sha256 != before.record_sha256
            or comparison.after_record_sha256 != snapshot.record_sha256
        ):
            raise ValueError("comparison scope mismatch")
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error):
        raise ProductError("store_integrity_error", _INTEGRITY_MESSAGE) from None
    return before, snapshot, state.mapping, comparison


def _evidence(
    snapshot: ProjectSnapshot,
    *,
    role: str,
    source_id: str,
    source_sha256: str,
    kind: str,
    record_id: str,
    record_kind: Literal["snapshot", "snapshot_comparison"],
    title: str,
    text: str,
) -> ProductEvidence:
    return ProductEvidence(
        id=_scoped_id(
            "p6-evidence",
            {
                "project_id": snapshot.project_id,
                "snapshot_id": snapshot.snapshot_id,
                "role": role,
                "source_id": source_id,
                "source_sha256": source_sha256,
            },
        ),
        kind=kind,
        title=title,
        text=text,
        reproduction_ref=ProductReproductionRef(record_id=record_id, record_kind=record_kind),
        source_sha256=source_sha256,
        visibility="diagnosis",
        redacted=False,
    )


def _observation_evidence(
    before: ProjectSnapshot,
    snapshot: ProjectSnapshot,
    comparison: ProjectSnapshotComparison | None,
) -> tuple[tuple[ProductEvidence, ...], tuple[ProductMetricChange, ...], str]:
    after_evidence = _evidence(
        snapshot,
        role="after_snapshot",
        source_id=snapshot.snapshot_id,
        source_sha256=snapshot.state_sha256,
        kind="snapshot",
        record_id=snapshot.snapshot_id,
        record_kind="snapshot",
        title="Imported project snapshot",
        text="The authorized import produced this immutable mapped project snapshot.",
    )
    if comparison is None:
        return (after_evidence,), (), after_evidence.id

    from aletheia_lab.product.analysis import _product_metric_change

    baseline = _evidence(
        snapshot,
        role="before_snapshot",
        source_id=before.snapshot_id,
        source_sha256=before.state_sha256,
        kind="snapshot",
        record_id=before.snapshot_id,
        record_kind="snapshot",
        title="Previous project snapshot",
        text="This immutable snapshot is the before operand of the stored comparison.",
    )
    comparison_evidence = _evidence(
        snapshot,
        role="snapshot_comparison",
        source_id=comparison.comparison_id,
        source_sha256=comparison.comparison_sha256,
        kind="snapshot_comparison",
        record_id=comparison.comparison_id,
        record_kind="snapshot_comparison",
        title="Stored snapshot comparison",
        text="The stored comparison records a change without an adverse regression candidate.",
    )
    evidence = [after_evidence, baseline, comparison_evidence]
    changes = []
    for change in comparison.metric_changes:
        item = _evidence(
            snapshot,
            role="metric_change",
            source_id=change.metric_change_id,
            source_sha256=change.metric_change_id.removeprefix("p3-metric-change-"),
            kind="metric_observation",
            record_id=comparison.comparison_id,
            record_kind="snapshot_comparison",
            title="Observed metric change",
            text="The stored comparison contains this metric change; its cause is unverified.",
        )
        evidence.append(item)
        changes.append(
            ProductMetricChange.model_validate(
                _product_metric_change(
                    change,
                    evidence_id=item.id,
                    before_snapshot_id=before.snapshot_id,
                    after_snapshot_id=snapshot.snapshot_id,
                    adverse=False,
                )
            )
        )
    return tuple(sorted(evidence, key=lambda item: item.id)), tuple(changes), after_evidence.id


def analyze_product_observation(
    store_root: Path,
    project_id: str,
    snapshot_id: str,
    question: str,
) -> ProductView:
    """Persist a genuine snapshot/comparison observation with causal abstention."""

    checked_question = checked_product_question(question)
    before, snapshot, configuration, comparison = _load_sources(store_root, project_id, snapshot_id)
    evidence, changes, after_evidence_id = _observation_evidence(before, snapshot, comparison)
    identity = {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "question": checked_question,
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    turn_id = _scoped_id("p6-turn", identity)
    claim_id = _scoped_id("p6-claim", {**identity, "turn_id": turn_id})
    citation_ids = tuple(
        item.id
        for item in evidence
        if item.reproduction_ref.record_id
        in (
            snapshot.snapshot_id,
            None if comparison is None else comparison.comparison_id,
        )
    )
    missing = (
        "No prior snapshot comparison is available for this initial import."
        if comparison is None
        else "No adverse regression candidate was observed under the declared metric policy.",
        "Independent causal verification is not available.",
        COUNTERFACTUAL_NOT_AVAILABLE,
    )
    claim_text = (
        "The authorized import produced an immutable mapped project snapshot."
        if comparison is None
        else "The stored comparison records a project change without an adverse regression candidate."
    )
    runtime = ProductRuntime(
        provider="deterministic_mock", model="p6-deterministic-mock/v1", external_call=False
    )
    view = ProductView(
        demo_only=False,
        mode="project_audit",
        visibility="diagnosis",
        project=ProductProject(id=project_id, display_name="Imported project"),
        snapshot=ProductSnapshot(
            id=snapshot_id,
            baseline_id=before.snapshot_id,
            sha256=snapshot.state_sha256,
            metric_definitions=tuple(
                ProductMetricDefinition.model_validate(value.model_dump(mode="python"))
                for value in configuration.metric_definitions
            ),
            metric_changes=changes,
        ),
        runtime=runtime,
        result=ProductResult(
            id=_PENDING_RESULT_ID,
            status="complete",
            disposition="abstain",
            causal_status="unverified",
            summary=claim_text,
            denominators=ProductDenominators(
                independent_families=None, contexts=1, outputs=1, claims=1
            ),
            allowed_wording=claim_text,
            forbidden_wording="The visible project artifacts establish a verified cause.",
            caveat="Deterministic offline observation; no causal truth or model-quality result.",
        ),
        conversation=ProductConversation(
            id=_scoped_id(
                "p6-conversation", {"project_id": project_id, "snapshot_id": snapshot_id}
            ),
            turns=(
                ProductTurn(
                    id=turn_id,
                    snapshot_id=snapshot_id,
                    result_id=_PENDING_RESULT_ID,
                    runtime=runtime,
                    question=checked_question,
                    answer=f"{claim_text} Its cause remains unverified.",
                    abstained=True,
                    visible_evidence_ids=tuple(item.id for item in evidence),
                    missing_evidence=missing,
                ),
            ),
        ),
        claims=(
            ProductClaim(
                id=claim_id,
                turn_id=turn_id,
                text=claim_text,
                claim_type="evidence_statement",
                epistemic_state="observed",
                support="fully_supported",
                citation_ids=citation_ids,
                counterevidence_ids=(),
                missing_evidence=missing,
                max_strength="observation",
            ),
        ),
        evidence=evidence,
        graph=build_product_graph(
            snapshot_id=snapshot_id,
            evidence_ids=tuple(item.id for item in evidence),
            claim_citations={claim_id: citation_ids},
            disposition_id=_PENDING_RESULT_ID,
            result_scope_id=turn_id,
            observed_evidence_ids=(after_evidence_id,),
        ),
    )
    return persist_product_result(store_root, view)


__all__ = ["analyze_product_observation"]
