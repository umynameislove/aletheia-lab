"""Selected semantic acceptance checks for the existing synthetic product view.

This is not a ResultEnvelope implementation, a DTO adapter, a general privacy
filter, or evidence that a service/UI/export consumer has actually run. Callers
provide the trusted synthetic reference and the candidate canonical view.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_research_acceptance import count

_FIELDS = {
    "schema_version",
    "demo_only",
    "mode",
    "visibility",
    "project",
    "snapshot",
    "runtime",
    "result",
    "conversation",
    "claims",
    "evidence",
    "graph",
}
_FORBIDDEN = {
    "raw_hex",
    "raw_loader_buffers",
    "target_buffers",
    "ground_truth",
    "evaluator_only",
    "reference_verdict",
    "private_key",
    "api_key",
    "code_bindings",
}


def _hidden(value: Any) -> None:
    if isinstance(value, dict):
        if set(value) & _FORBIDDEN:
            raise ValueError("private field in synthetic projection")
        for item in value.values():
            _hidden(item)
    elif isinstance(value, list):
        for item in value:
            _hidden(item)


def _indexed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = item["id"]
        if not isinstance(identifier, str) or not identifier or identifier in values:
            raise ValueError("missing or duplicate scoped identity")
        values[identifier] = item
    return values


def _scope(view: dict[str, Any]) -> None:
    if (
        set(view) != _FIELDS
        or view["schema_version"] != "p6-product-view/v1"
        or view["demo_only"] is not True
        or view["mode"] != "project_audit"
        or view["visibility"] != "diagnosis"
        or view["runtime"]["provider"] != "deterministic_mock"
        or view["runtime"]["external_call"] is not False
        or view["result"]["causal_status"] != "unverified"
    ):
        raise ValueError("synthetic project-audit scope changed")
    denominators = view["result"]["denominators"]
    if set(denominators) != {"independent_families", "contexts", "outputs", "claims"}:
        raise ValueError("denominator dimensions changed")
    if denominators["independent_families"] is not None:
        raise ValueError("project-audit independent family count is not estimated")
    for name in ("contexts", "outputs", "claims"):
        count(denominators[name])
    if denominators["claims"] != len(view["claims"]):
        raise ValueError("claim denominator differs from visible claim census")


def _citations(view: dict[str, Any]) -> None:
    claims = _indexed(view["claims"])
    evidence = _indexed(view["evidence"])
    turns = _indexed(view["conversation"]["turns"])
    for item in evidence.values():
        if item["visibility"] not in {"public", "diagnosis"}:
            raise ValueError("evidence is outside the authorized view")
        path = item["relative_path"]
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).drive
            or ".." in PurePosixPath(path.replace("\\", "/")).parts
        ):
            raise ValueError("evidence path is not an authorized relative reference")
    for turn in turns.values():
        if turn["snapshot_id"] != view["snapshot"]["id"] or not set(
            turn["visible_evidence_ids"]
        ) <= set(evidence):
            raise ValueError("turn is outside its snapshot/evidence scope")
    for claim in claims.values():
        if claim["turn_id"] not in turns:
            raise ValueError("claim has no authorized turn")
        references = set(claim["citation_ids"]) | set(claim["counterevidence_ids"])
        if not references <= set(turns[claim["turn_id"]]["visible_evidence_ids"]):
            raise ValueError("claim citation is dangling or outside its turn")
        if claim["max_strength"] != "observation":
            raise ValueError("synthetic observation promoted to causal evidence")


def _graph(view: dict[str, Any]) -> None:
    nodes = _indexed(view["graph"]["nodes"])
    edges = _indexed(view["graph"]["edges"])
    sources = {
        "Snapshot": {view["snapshot"]["id"], view["snapshot"]["baseline_id"]},
        "EvidenceItem": set(_indexed(view["evidence"])),
        "AtomicClaim": set(_indexed(view["claims"])),
        "Disposition": {view["result"]["id"]},
    }
    for node in nodes.values():
        if node["visibility"] not in {"public", "diagnosis"} or node[
            "source_id"
        ] not in sources.get(node["kind"], set()):
            raise ValueError("graph node lacks a visible typed source")
    for edge in edges.values():
        if (
            edge["visibility"] not in {"public", "diagnosis"}
            or edge["source"] not in nodes
            or edge["target"] not in nodes
            or edge["kind"] not in {"OBSERVED_IN", "CITES", "ASSIGNED_DISPOSITION"}
        ):
            raise ValueError("graph edge is dangling, hidden or outside the synthetic relations")
        source, target = nodes[edge["source"]], nodes[edge["target"]]
        pair = (source["kind"], target["kind"])
        expected = {
            "OBSERVED_IN": ("EvidenceItem", "Snapshot"),
            "CITES": ("AtomicClaim", "EvidenceItem"),
            "ASSIGNED_DISPOSITION": ("AtomicClaim", "Disposition"),
        }
        if pair != expected[edge["kind"]]:
            raise ValueError("graph relation has incompatible endpoint types")
        if edge["kind"] == "CITES":
            claim = _indexed(view["claims"])[source["source_id"]]
            if target["source_id"] not in claim["citation_ids"]:
                raise ValueError("graph citation differs from claim citation")


def check_synthetic_product_view(
    reference: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Compare canonical JSON projections, not screenshots or arbitrary report formats.

    The reference must itself pass the selected synthetic invariants. An unchanged
    hash/count fingerprint alone cannot authorize a changed disposition or scope.
    Real consumers must provide their canonical view in a later integration run.
    """
    for view in (reference, candidate):
        _hidden(view)
        _scope(view)
        _citations(view)
        _graph(view)
    identity = document_digest(reference)
    if document_digest(candidate) != identity:
        raise ValueError("canonical projection differs from its trusted synthetic reference")
    return {
        "status": "synthetic_canonical_projection_acceptance_pass",
        "reference_sha256": identity,
        "candidate_sha256": identity,
        "independent_families": None,
        "independent_families_status": "not_estimated",
        "provider_calls": 0,
        "product_consumer_integration": "not_executed",
        "scope": "selected synthetic invariants, not a general DTO/privacy validator",
    }
