"""Executed read-only acquisition replay on exposed development loader records.

Masking a retained endpoint is an authored visibility intervention, not a claim
that native logs were incomplete. The planner uses only the initial view and
query catalogue. File paths, historical pointers and authority are caller-bound,
never model-selected. This module cannot call providers, fit or load models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.compositional_lineage import CHECK_COSTS
from aletheia_lab.evaluation.source_evidence_admission import (
    PRODUCER,
    SourceDocument,
    checked_json,
    resolve_documents,
)
from aletheia_lab.evaluation.source_evidence_headroom import STORES, audit_development_sources
from aletheia_lab.project.identity import content_sha256

_ENDPOINTS = {
    "requested_endpoint": ("declared_artifact_sha256",),
    "loaded_endpoint": ("actual_loaded_sha256",),
    "both_endpoints": ("declared_artifact_sha256", "actual_loaded_sha256"),
}
_VIEWS = ("full", "requested_endpoint", "loaded_endpoint", "neither")


@dataclass(frozen=True)
class QuerySource:
    """Trusted caller capability for a single historical record, not arbitrary IO."""

    memory_root: Path
    store: str
    pointer: str
    container_sha256: str
    scope: str

    def path(self) -> Path:
        # Only runtime event containers. Legacy receipt aggregates are excluded.
        if self.store == STORES[1]:
            valid = self.pointer in {f"/events/{index}" for index in range(6)}
            filename = "load-trace.json"
        elif self.store == STORES[2]:
            valid = self.pointer in {
                f"/doses/{dose}/events/{index}" for dose in (1, 5, 10, 25, 50) for index in range(6)
            }
            filename = "scores.json"
        else:
            raise ValueError("query source is outside the runtime allowlist")
        if not valid or not self.scope:
            raise ValueError("query pointer or scope is invalid")
        path = self.memory_root / self.store / filename
        if any(item.is_symlink() for item in (path, *path.absolute().parents)):
            raise ValueError("query source traverses a symlink")
        return path


def _decode_record(source: QuerySource, raw: bytes) -> tuple[dict[str, Any], str, int]:
    identity = content_sha256(raw)
    if identity != source.container_sha256:
        raise ValueError("query source identity changed")
    value: Any = checked_json(raw)
    for part in source.pointer.split("/")[1:]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    if not isinstance(value, dict):
        raise ValueError("query record is not an object")
    return value, identity, len(raw)


def _record(source: QuerySource) -> tuple[dict[str, Any], str, int]:
    return _decode_record(source, source.path().read_bytes())


def _origin(source: QuerySource) -> str:
    # Opaque caller-bound snapshot/record capability, not a model-selected path.
    identity = content_sha256(
        json.dumps(
            [source.store, source.pointer, source.container_sha256, source.scope],
            separators=(",", ":"),
        ).encode()
    )
    return "/source-" + identity


def _document(record: dict[str, Any], *, source: QuerySource, view: str) -> SourceDocument:
    fields = _ENDPOINTS.get(view, ()) if view != "full" else _ENDPOINTS["both_endpoints"]
    # Report prose, control name, metrics and recipe never enter either arm.
    value = {field: record[field] for field in fields if field in record}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return SourceDocument(raw, "loader-event/v1", PRODUCER, source.scope, _origin(source))


def choose_query(
    initial: list[SourceDocument], *, scope: str, budget: int, available: tuple[str, ...]
) -> str | None:
    """Exact minimum guaranteed measurement for the fixed binary endpoint model.

    Availability is capability metadata, not a peek at returned evidence. Query
    success is not assumed in the execution result. This one-step planner cannot
    reconcile a conflict or cure an unsupported source schema.
    """
    if type(budget) is not int or not 0 <= budget <= 2:
        raise ValueError("invalid endpoint query budget")
    if len(set(available)) != len(available) or not set(available) <= CHECK_COSTS.keys():
        raise ValueError("invalid query catalogue")
    result = resolve_documents(initial, scope=scope)
    if result["state"] != "ambiguous":
        return None
    # A more expensive both-endpoint query is a valid fallback when the needed
    # single endpoint capability is unavailable. Visible guaranteed minima alone
    # would incorrectly exclude that option.
    guaranteed = set(result["minimum_guaranteed_checks"]) | {"both_endpoints"}
    feasible = [key for key in available if key in guaranteed and CHECK_COSTS[key] <= budget]
    return min(feasible, key=lambda key: (CHECK_COSTS[key], key)) if feasible else None


def acquire(
    source: QuerySource, action: str, *, available: tuple[str, ...]
) -> tuple[SourceDocument | None, dict[str, Any]]:
    """Actually read pinned source bytes; failures are retained, never negative facts."""
    if action not in _ENDPOINTS:
        raise ValueError("unknown endpoint query")
    ledger: dict[str, Any] = {
        "action": action,
        "cost_units": CHECK_COSTS[action],
        "status": "unavailable",
        "source_read_executed": False,
        "source_bytes_read": 0,
        "returned_fact_pointers": [],
    }
    if action not in available:
        return None, ledger
    try:
        raw = source.path().read_bytes()
        ledger.update(source_read_executed=True, source_bytes_read=len(raw))
        record, identity, byte_count = _decode_record(source, raw)
        document = _document(record, source=source, view=action)
        result = resolve_documents([document], scope=source.scope)
        ledger.update(
            source_read_executed=True,
            source_bytes_read=byte_count,
            container_sha256=identity,
            returned_document_sha256=content_sha256(document.raw),
            returned_fact_pointers=[
                source.pointer + fact["pointer"].removeprefix(_origin(source))
                for fact in result["facts"]
            ],
            status="observed" if result["state"] != "admission_unresolved" else "invalid_evidence",
        )
        return document, ledger
    except (OSError, ValueError, UnicodeError, KeyError, TypeError, IndexError) as exc:
        return None, {**ledger, "status": "source_error", "error_type": type(exc).__name__}


def run_query(
    initial: list[SourceDocument],
    *,
    source: QuerySource,
    action: str | None,
    budget: int = 2,
    available: tuple[str, ...] = tuple(_ENDPOINTS),
) -> dict[str, Any]:
    """Matched one-attempt query execution; no retries or hidden policy repair."""
    if type(budget) is not int or not 0 <= budget <= 2:
        raise ValueError("invalid endpoint query budget")
    if len(set(available)) != len(available) or not set(available) <= CHECK_COSTS.keys():
        raise ValueError("invalid query catalogue")
    source.path()  # Validate the caller capability even for no-query execution.
    if not initial or any(
        document.scope != source.scope
        or document.producer != PRODUCER
        or document.schema != "loader-event/v1"
        or document.pointer != _origin(source)
        for document in initial
    ):
        raise ValueError("initial evidence and query capability have different origins")
    before = resolve_documents(initial, scope=source.scope)
    ledger: dict[str, Any] | None = None
    observed = None
    if action is not None:
        if action not in _ENDPOINTS or CHECK_COSTS[action] > budget:
            raise ValueError("query is invalid or exceeds budget")
        observed, ledger = acquire(source, action, available=available)
    after = resolve_documents(initial + ([observed] if observed else []), scope=source.scope)
    cost = ledger["cost_units"] if ledger else 0
    # Missing/changed source is not evidence that the pre-query commitment is
    # false. Retain failure separately and do not manufacture observed efficacy.
    resolved = before["state"] == "ambiguous" and after["state"] == "identified"
    return {
        "before_state": before["state"],
        "before_compatible": before["compatible"],
        "after_state": after["state"],
        "after_compatible": after["compatible"],
        "newly_resolved": resolved,
        "cost_units": cost,
        "resolution_gain_per_cost_unit": int(resolved) / cost if cost else 0.0,
        "ledger": ledger,
    }


def _replay_row(source: QuerySource, gold: str, view: str) -> dict[str, Any]:
    record, _, _ = _record(source)
    initial = [_document(record, source=source, view=view)]
    available = tuple(_ENDPOINTS)
    action = choose_query(initial, scope=source.scope, budget=2, available=available)
    no_query = run_query(initial, source=source, action=None)
    planned = run_query(initial, source=source, action=action)
    return {
        "scope": source.scope,
        "source_binding": {
            "store": source.store,
            "pointer": source.pointer,
            "container_sha256": source.container_sha256,
            "producer": PRODUCER,
            "parser_contract": "loader-event/v1",
        },
        "authored_view": view,
        "initial_input_sha256": content_sha256(initial[0].raw),
        "reference_status": gold,
        "no_query": no_query,
        "deterministic_planner": planned,
        "post_query_status_correct": planned["after_compatible"] == [gold],
    }


def replay_acquisition(*, root: Path, memory_root: Path) -> dict[str, Any]:
    """End-to-end native reads, with authored masks reported as dependent views."""
    audit = audit_development_sources(root=root, memory_root=memory_root)
    rows: list[dict[str, Any]] = []
    bindings = []
    for entry in audit["inventory"][1:]:
        filename = "load-trace.json" if entry["store"] == STORES[1] else "scores.json"
        path = memory_root / entry["store"] / filename
        identity = audit["source_sha256_after"][entry["store"]][filename]
        if content_sha256(path.read_bytes()) != identity:
            raise ValueError("audited snapshot changed before acquisition")
        bindings.append((path, identity))
        for row in entry["rows"]:
            source = QuerySource(
                memory_root,
                entry["store"],
                row["field_pointer"],
                identity,
                f"runtime-slot-{len(rows) // len(_VIEWS)}",
            )
            for view in _VIEWS:
                rows.append(_replay_row(source, row["reference_status"], view))
    if any(content_sha256(path.read_bytes()) != identity for path, identity in bindings):
        raise ValueError("source mutated during acquisition replay")
    after = audit_development_sources(root=root, memory_root=memory_root)
    if after["source_sha256_after"] != audit["source_sha256_after"]:
        raise ValueError("retained source frame mutated during acquisition replay")
    planned = [row["deterministic_planner"] for row in rows]
    return {
        "schema_version": "source-evidence-acquisition-replay/v1",
        "status": "authored_visibility_acquisition_replay_complete",
        "native_runtime_record_count": audit["native_runtime_event_count"],
        "authored_view_count": len(rows),
        "source_cluster_count": audit["source_cluster_count"],
        "producer_family_count": audit["producer_family_count"],
        "initial_identified_count": sum(row["before_state"] == "identified" for row in planned),
        "newly_resolved_count": sum(row["newly_resolved"] for row in planned),
        "correct_post_query_count": sum(row["post_query_status_correct"] for row in rows),
        "source_query_count": sum(row["ledger"] is not None for row in planned),
        "observed_query_count": sum(
            row["ledger"] is not None and row["ledger"]["status"] == "observed" for row in planned
        ),
        "query_cost_units": sum(row["cost_units"] for row in planned),
        "query_cost_meaning": "declared endpoint costs 1/1/2; not physical IO or USD",
        "successful_query_source_bytes_read": sum(
            row["ledger"]["source_bytes_read"] for row in planned if row["ledger"] is not None
        ),
        "native_container_read_amplification": True,
        "sources_unchanged": True,
        "source_sha256_before": audit["source_sha256_after"],
        "source_sha256_after": after["source_sha256_after"],
        "executed_module_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in (
                "source_evidence_acquisition.py",
                "source_evidence_admission.py",
                "source_evidence_headroom.py",
                "compositional_lineage.py",
            )
        },
        "naturally_missing_evidence_measured": False,
        "llm_planner_measured": False,
        "provider_calls": 0,
        "semantic_extraction_headroom_demonstrated": audit["semantic_llm_trial_ready"],
        "validation_preparation": {
            "status": "design_only_independent_source_unbound",
            "execution_authorized": False,
            "retained_comparator": "deterministic producer adapter within existing scope",
            "llm_variant_selected": False,
            "independent_source_admitted": False,
            "blockers": [
                "no demonstrated semantic extraction headroom",
                "no independent producer/schema reference frame admitted",
                "no observed LLM extraction or acquisition comparison",
            ],
        },
        "rows": rows,
    }
