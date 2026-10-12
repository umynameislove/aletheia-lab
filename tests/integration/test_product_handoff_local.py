"""Selected backend semantics first; immutable view/export roundtrips second.

Only Git collection is synthetic. ProductService, secure file import, snapshot
construction, persistence, analysis, follow-up, reports and deletion run normally.
This is not an HTTP/dashboard test, a scientific rerun or real Git qualification.
"""

from __future__ import annotations

import importlib
import io
import json
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from aletheia_lab.evaluation.product_handoff_acceptance import check_product_handoff
from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.demo_flow import _demo_mapping
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import COUNTERFACTUAL_NOT_AVAILABLE, ProductView
from aletheia_lab.project.collectors import ProjectGitState
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import ProjectStore
from aletheia_lab.project.regression import ProjectEvidenceBundle, ProjectSnapshotComparison
from aletheia_lab.project.snapshots import ProjectSnapshot

pytestmark = pytest.mark.integration
_QUESTION = "What changed in the stored synthetic project evidence?"
_FOLLOW_UP = "What remains uncertain within this claim?"
_OBSERVATION = "The stored project evidence records an observed snapshot change."
_UNCERTAINTY = "The scoped follow-up does not establish a cause from the selected evidence."
_RESULT_WORDING = {
    False: {
        "summary": "A stored project change is visible, but the authorized evidence does not establish one cause.",
        "allowed_wording": "The stored evidence records an observed project change.",
        "forbidden_wording": "One visible project artifact caused the observed change.",
        "caveat": "Deterministic offline project audit; no causal ground truth or model-quality result.",
    },
    True: {
        "summary": "The scoped follow-up remains bounded to the parent's authorized evidence.",
        "allowed_wording": "The selected evidence remains available within the parent result.",
        "forbidden_wording": "The follow-up establishes a cause or introduces new evidence.",
        "caveat": "Deterministic offline follow-up; conversation history is not evidence.",
    },
}
_RUNTIME = {
    "provider": "deterministic_mock",
    "model": "p6-deterministic-mock/v1",
    "external_call": False,
}


def _synthetic_git(grant: Any) -> ProjectGitState:
    payload = {
        "schema_version": "project-git-state/v1",
        "collector_version": "project-git-state/1.0.0",
        "project_id": grant.project_id,
        "repository_state": "attached",
        "commit_sha": "a" * 40,
        "branch": "synthetic-test",
        "dirty": False,
        "changed_files": [],
        "git_version": "synthetic-no-command",
    }
    return ProjectGitState.model_validate_json(
        json.dumps(
            {
                **payload,
                "state_sha256": canonical_project_sha256(payload),
            }
        )
    )


def _source(root: Path) -> Path:
    root.mkdir()
    (root / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (root / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8"
    )
    (root / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    return root.resolve()


def _trusted_sources(store_root: Path, project_id: str, snapshot_id: str) -> dict[str, Any]:
    # Read the P3 operands before requesting a ProductView. Do not call product
    # analysis, graph/projection builders or metric classifiers to build the oracle.
    with ProjectStore(store_root) as store:
        snapshot = store.load(snapshot_id)
        comparisons = store.list_records(project_id, "snapshot_comparison")
        bundles = store.list_records(project_id, "evidence_bundle")
        assert len(comparisons) == len(bundles) == 1
        comparison = store.load(comparisons[0].record_id)
        bundle = store.load(bundles[0].record_id)
        assert isinstance(snapshot, ProjectSnapshot)
        assert isinstance(comparison, ProjectSnapshotComparison)
        assert isinstance(bundle, ProjectEvidenceBundle)
        before = store.load(comparison.before_snapshot_id)
        assert isinstance(before, ProjectSnapshot)
    assert comparison.after_snapshot_id == snapshot_id
    assert len(comparison.metric_changes) == 1
    change = comparison.metric_changes[0]
    assert change.before is not None and change.after is not None
    assert change.before.metric_value == 0.6 and change.after.metric_value == 0.8
    assert change.before.run_id == change.after.run_id == "candidate"
    assert all(item.visibility == "diagnosis" for item in bundle.items)
    assert {item.role for item in bundle.items} == {
        "before_snapshot",
        "after_snapshot",
        "snapshot_comparison",
        "metric_change",
        "regression_candidate",
    }
    by_role = {item.role: item for item in bundle.items}
    return {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "baseline_id": before.snapshot_id,
        "snapshot_sha256": snapshot.state_sha256,
        "comparison_id": comparison.comparison_id,
        "by_role": by_role,
    }


def _assert_source_semantics(
    candidate: dict[str, Any], expected: dict[str, Any], *, followed: bool = False
) -> None:
    view = ProductView.model_validate_json(json.dumps(candidate, allow_nan=False))
    assert view.demo_only is False
    assert view.mode == "project_audit" and view.visibility == "diagnosis"
    assert view.runtime.model_dump(mode="json") == _RUNTIME
    assert view.project.id == expected["project_id"]
    assert view.snapshot.id == expected["snapshot_id"]
    assert view.snapshot.baseline_id == expected["baseline_id"]
    assert view.snapshot.sha256 == expected["snapshot_sha256"]
    assert [item.model_dump(mode="json") for item in view.snapshot.metric_definitions] == [
        {
            "metric_name": "loss",
            "direction": "lower_is_better",
            "regression_threshold": 0.08,
        }
    ]
    roles = expected["by_role"]
    assert len(view.snapshot.metric_changes) == 1
    metric = view.snapshot.metric_changes[0]
    assert metric.model_dump(mode="json") == {
        "evidence_id": roles["metric_change"].evidence_id,
        "metric_name": "loss",
        "kind": "increased",
        "before": {
            "snapshot_id": expected["baseline_id"],
            "run_id": "candidate",
            "step": None,
            "value": 0.6,
        },
        "after": {
            "snapshot_id": expected["snapshot_id"],
            "run_id": "candidate",
            "step": None,
            "value": 0.8,
        },
        "delta": 0.2,
        "adverse_status": "adverse",
    }
    assert view.result.status == "complete" and view.result.disposition == "abstain"
    assert view.result.causal_status == "unverified"
    for field, wording in _RESULT_WORDING[followed].items():
        assert getattr(view.result, field) == wording
    assert view.result.denominators.model_dump() == {
        "independent_families": None,
        "contexts": 1,
        "outputs": 1,
        "claims": 2 if followed else 1,
    }
    assert view.claims[0].text == _OBSERVATION
    assert view.claims[0].claim_type == "evidence_statement"
    assert view.claims[0].epistemic_state == "observed"
    assert view.claims[0].support == "fully_supported"
    assert view.claims[0].max_strength == "observation"
    citations = tuple(
        sorted(
            roles[role].evidence_id
            for role in (
                "snapshot_comparison",
                "metric_change",
                "regression_candidate",
            )
        )
    )
    assert view.claims[0].citation_ids == citations
    assert not view.claims[0].counterevidence_ids
    assert view.conversation.turns[0].question == _QUESTION
    assert view.conversation.turns[0].answer == (
        "The change is observed in diagnosis-visible evidence; its cause remains unverified."
    )
    assert view.conversation.turns[0].visible_evidence_ids == tuple(
        sorted(item.evidence_id for item in roles.values())
    )
    assert len(view.conversation.turns) == (2 if followed else 1)
    for turn in view.conversation.turns:
        assert turn.abstained and turn.runtime.model_dump(mode="json") == _RUNTIME
        assert turn.missing_evidence == (COUNTERFACTUAL_NOT_AVAILABLE,)
    assert all(claim.missing_evidence == (COUNTERFACTUAL_NOT_AVAILABLE,) for claim in view.claims)
    if followed:
        assert view.conversation.turns[-1].question == _FOLLOW_UP
        assert view.conversation.turns[-1].answer == (
            "The selected evidence does not establish a cause; the scoped follow-up abstains."
        )
        assert view.claims[-1].text == _UNCERTAINTY
        assert view.claims[-1].claim_type == "uncertainty_statement"
        assert view.claims[-1].epistemic_state == "abstained"
        assert view.claims[-1].support == "not_estimated"
        assert view.claims[-1].max_strength == "abstain"
        assert view.claims[-1].citation_ids == citations
        assert view.conversation.turns[-1].visible_evidence_ids == citations
    assert {item.id for item in view.evidence} == {item.evidence_id for item in roles.values()}
    expected_kinds = {
        "before_snapshot": ("snapshot", "snapshot"),
        "after_snapshot": ("snapshot", "snapshot"),
        "snapshot_comparison": ("snapshot_comparison", "snapshot_comparison"),
        "metric_change": ("metric_observation", "snapshot_comparison"),
        "regression_candidate": ("regression_candidate", "regression_event"),
    }
    evidence_by_id = {item.id: item for item in view.evidence}
    for role, source in roles.items():
        item = evidence_by_id[source.evidence_id]
        assert (item.kind, item.reproduction_ref.record_kind) == expected_kinds[role]
        assert item.source_sha256 == source.source_sha256
        assert item.visibility == "diagnosis" and not item.redacted
        assert item.reproduction_ref.record_id == (
            expected["comparison_id"] if role == "metric_change" else source.source_id
        )
    nodes = {node.id: node for node in view.graph.nodes}
    assert {
        (nodes[edge.source].source_id, nodes[edge.target].source_id)
        for edge in view.graph.edges
        if edge.kind == "OBSERVED_IN"
    } == {
        (roles[role].evidence_id, expected["snapshot_id"])
        for role in ("after_snapshot", "snapshot_comparison", "metric_change")
    }


def test_backend_source_semantics_precede_saved_view_and_export_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def block_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("product handoff must remain offline")

    monkeypatch.setattr(socket, "socket", block_network)
    monkeypatch.setattr(socket, "create_connection", block_network)
    for name in ("aletheia_lab.product.imports", "aletheia_lab.product.refresh"):
        monkeypatch.setattr(importlib.import_module(name), "collect_git_state", _synthetic_git)
    source, store_root = _source(tmp_path / "source"), tmp_path / "store"
    service = ProductService(store_root)
    preview = service.preview_import(str(source))
    confirmed = service.confirm_import(str(preview["preview_id"]), _demo_mapping(preview))
    (source / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n", encoding="utf-8"
    )
    refreshed = service.refresh(str(confirmed["project_id"]))
    assert refreshed["status"] == "new_snapshot"
    expected = _trusted_sources(
        store_root, str(confirmed["project_id"]), str(refreshed["snapshot_id"])
    )
    parent = service.analyze_mock(expected["project_id"], expected["snapshot_id"], _QUESTION)
    _assert_source_semantics(parent, expected)
    parent_id, claim_id = parent["result"]["id"], parent["claims"][0]["id"]
    child = service.follow_up(parent_id, "claim", claim_id, _FOLLOW_UP)
    _assert_source_semantics(child, expected, followed=True)
    assert child["conversation"]["turns"][:-1] == parent["conversation"]["turns"]
    assert child["claims"][:-1] == parent["claims"]
    assert child["evidence"] == parent["evidence"]
    child_id = child["result"]["id"]

    # After the selected source oracle passes, this is a consistency reference,
    # not another independent observation channel or a full scientific oracle.
    accepted_parent, accepted_child = deepcopy(parent), deepcopy(child)
    reopened = ProductService(store_root)
    for reference in (accepted_parent, accepted_child):
        result_id = reference["result"]["id"]
        report = check_product_handoff(reference, reopened.view(result_id))
        assert report["projection_scope"] == "backend_result"
        check_product_handoff(reference, json.loads(reopened.export_report(result_id, "json")))
    markdown = reopened.export_report(child_id, "markdown").decode("utf-8")
    pdf = PdfReader(io.BytesIO(reopened.export_report(child_id, "pdf")), strict=True)
    pdf_text = "\n".join(page.extract_text(extraction_mode="layout") for page in pdf.pages)
    for rendered in (markdown.replace("`", ""), pdf_text):
        assert _OBSERVATION in rendered and _UNCERTAINTY in rendered
        assert "abstain" in rendered and COUNTERFACTUAL_NOT_AVAILABLE in rendered

    # A schema-valid, newly hashed result is still rejected by the independent
    # selected source oracle. Persistence identity does not certify semantics.
    for mutation in ("claim", "answer", "summary", "observed_edge", "reproduction"):
        altered = deepcopy(parent)
        causal_text = "The configuration caused the loss increase."
        if mutation == "claim":
            altered["claims"][0]["text"] = causal_text
        elif mutation == "answer":
            altered["conversation"]["turns"][0]["answer"] = causal_text
        elif mutation == "summary":
            altered["result"]["summary"] = causal_text
        elif mutation == "observed_edge":
            edge = next(edge for edge in altered["graph"]["edges"] if edge["kind"] == "OBSERVED_IN")
            altered["graph"]["edges"].remove(edge)
        else:
            altered["evidence"][0]["reproduction_ref"]["record_id"] = "foreign-comparison-001"
        rebound = persist_product_result(
            store_root, ProductView.model_validate_json(json.dumps(altered))
        )
        rebound_view = reopened.view(rebound.result.id)
        with pytest.raises(AssertionError):
            _assert_source_semantics(rebound_view, expected)
        with pytest.raises(ValueError):
            check_product_handoff(accepted_parent, rebound_view)

    (source / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.9\n", encoding="utf-8"
    )
    assert reopened.refresh(expected["project_id"])["status"] == "new_snapshot"
    check_product_handoff(accepted_parent, reopened.view(parent_id))
    check_product_handoff(accepted_child, json.loads(reopened.export_report(child_id, "json")))
    before_delete = {path.name: path.read_bytes() for path in source.iterdir()}
    assert reopened.delete_project(expected["project_id"])["status"] == "deleted"
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before_delete
    with pytest.raises(ProductError) as caught:
        reopened.view(child_id)
    assert caught.value.code == "record_not_found"
