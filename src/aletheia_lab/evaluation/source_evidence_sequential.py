"""Bounded sequential acquisition on caller-pinned development endpoint evidence.

Exactness concerns declared cost under complete, truthful, successful endpoint
returns, not arbitrary failure recovery, physical IO, or semantic tool discovery.
The legacy one-attempt planner and historical replay remain unchanged.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.compositional_lineage import CHECK_COSTS
from aletheia_lab.evaluation.source_evidence_acquisition import (
    QuerySource,
    _document,
    _record,
    acquire,
    run_query,
)
from aletheia_lab.evaluation.source_evidence_admission import SourceDocument, resolve_documents
from aletheia_lab.evaluation.source_evidence_headroom import STORES, audit_development_sources
from aletheia_lab.project.identity import content_sha256

CATALOGUE = ("requested_endpoint", "loaded_endpoint", "both_endpoints")
_ROLES = {
    "requested_endpoint": {"requested_endpoint"},
    "loaded_endpoint": {"loaded_endpoint"},
    "both_endpoints": {"requested_endpoint", "loaded_endpoint"},
}
_VIEWS = ("full", "requested_endpoint", "loaded_endpoint", "neither")


def choose_query_plan(
    documents: list[SourceDocument], *, scope: str, budget: int, available: tuple[str, ...]
) -> tuple[str, ...]:
    """Minimum-cost guaranteed-resolution sequence in the fixed endpoint model.

    Only visible admitted role presence and caller-supplied capability metadata
    are inspected. Ties use fewer calls then lexical order; equal-cost alternatives
    remain cost-optimal. Empty means resolved/non-admissible or no feasible plan,
    not proof that no fault exists. At most two non-repeated reads are relevant.
    """
    if type(budget) is not int or not 0 <= budget <= 2:
        raise ValueError("invalid endpoint query budget")
    if len(set(available)) != len(available) or not set(available) <= _ROLES.keys():
        raise ValueError("invalid query catalogue")
    result = resolve_documents(documents, scope=scope)
    if result["state"] != "ambiguous":
        return ()
    known = {fact["kind"] for fact in result["facts"]}
    missing = _ROLES["both_endpoints"] - known
    feasible = [
        sequence
        for length in (1, 2)
        for sequence in permutations(available, length)
        if sum(CHECK_COSTS[action] for action in sequence) <= budget
        and missing <= set().union(*(_ROLES[action] for action in sequence))
    ]
    return min(
        feasible,
        key=lambda sequence: (
            sum(CHECK_COSTS[action] for action in sequence),
            len(sequence),
            sequence,
        ),
        default=(),
    )


def _validate_proposal(actions: tuple[str, ...] | None, budget: int) -> None:
    if actions is not None and (
        not isinstance(actions, tuple)
        or len(actions) > 2
        or any(action not in _ROLES for action in actions)
        or len(set(actions)) != len(actions)
        or sum(CHECK_COSTS[action] for action in actions) > budget
    ):
        raise ValueError("invalid or overbudget query sequence")


def _next_query(
    documents: list[SourceDocument],
    *,
    scope: str,
    budget: int,
    available: tuple[str, ...],
    actions: tuple[str, ...] | None,
    used: set[str],
) -> tuple[str | None, str]:
    if actions is not None:
        if len(used) == len(actions):
            return None, "proposal_exhausted"
        return actions[len(used)], ""
    plan = choose_query_plan(
        documents,
        scope=scope,
        budget=budget,
        available=tuple(action for action in available if action not in used),
    )
    return (plan[0], "") if plan else (None, "no_resolving_plan")


def run_query_sequence(
    initial: list[SourceDocument],
    *,
    source: QuerySource,
    budget: int = 2,
    available: tuple[str, ...] = CATALOGUE,
    actions: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Execute/replan from observed facts only, with one shared conservative stop rule.

    None selects the exact baseline; a supplied fixed sequence uses the same
    executor. Failed/unavailable attempts are charged, never negative facts.
    Stop on errors, invalid/conflicting evidence or no new facts; no retry or
    optimal-recovery claim. Paths, snapshots and authority stay caller-bound.
    """
    if len(initial) > 10:
        raise ValueError("initial evidence leaves no room for acquired documents")
    # Validate origin/capability/catalogue/budget even when no read will occur.
    run_query(initial, source=source, action=None, budget=budget, available=available)
    _validate_proposal(actions, budget)
    documents = list(initial)
    before = resolve_documents(documents, scope=source.scope)
    current = before
    ledgers: list[dict[str, Any]] = []
    used: set[str] = set()
    spent = 0
    stop = "budget_exhausted"
    while True:
        if current["state"] != "ambiguous":
            stop = current["state"]
            break
        if spent == budget:
            break
        action, reason = _next_query(
            documents,
            scope=source.scope,
            budget=budget - spent,
            available=available,
            actions=actions,
            used=used,
        )
        if action is None:
            stop = reason
            break
        observed, ledger = acquire(source, action, available=available)
        spent += ledger["cost_units"]
        used.add(action)
        old_facts = {(fact["kind"], fact["digest"]) for fact in current["facts"]}
        if observed is not None:
            documents.append(observed)
        current = resolve_documents(documents, scope=source.scope)
        ledgers.append({**ledger, "post_query_state": current["state"]})
        if ledger["status"] != "observed":
            stop = "query_failed"
            break
        if current["state"] != "ambiguous":
            stop = current["state"]
            break
        new_facts = {(fact["kind"], fact["digest"]) for fact in current["facts"]}
        if new_facts == old_facts:
            stop = "no_information_gain"
            break
    resolved = before["state"] == "ambiguous" and current["state"] == "identified"
    return {
        "before_state": before["state"],
        "before_compatible": before["compatible"],
        "after_state": current["state"],
        "after_compatible": current["compatible"],
        "newly_resolved": resolved,
        "cost_units": spent,
        "remaining_budget_units": budget - spent,
        "source_bytes_read": sum(ledger["source_bytes_read"] for ledger in ledgers),
        "stop_reason": stop,
        "resolution_gain_per_cost_unit": int(resolved) / spent if spent else 0.0,
        "ledgers": ledgers,
    }


def replay_sequential_acquisition(
    *, root: Path, memory_root: Path, available: tuple[str, ...] = CATALOGUE, budget: int = 2
) -> dict[str, Any]:
    """Opt-in native replay; authored views are dependent, not naturally missing logs."""
    # Validate settings before auditing/reading any source.
    if type(budget) is not int or not 0 <= budget <= 2:
        raise ValueError("invalid endpoint query budget")
    if len(set(available)) != len(available) or not set(available) <= _ROLES.keys():
        raise ValueError("invalid query catalogue")
    audit = audit_development_sources(root=root, memory_root=memory_root)
    rows: list[dict[str, Any]] = []
    for entry in audit["inventory"][1:]:
        filename = "load-trace.json" if entry["store"] == STORES[1] else "scores.json"
        identity = audit["source_sha256_after"][entry["store"]][filename]
        for record_row in entry["rows"]:
            source = QuerySource(
                memory_root,
                entry["store"],
                record_row["field_pointer"],
                identity,
                f"runtime-slot-{len(rows) // len(_VIEWS)}",
            )
            record, _, _ = _record(source)
            for view in _VIEWS:
                initial = [_document(record, source=source, view=view)]
                result = run_query_sequence(
                    initial, source=source, budget=budget, available=available
                )
                rows.append(
                    {
                        "scope": source.scope,
                        "authored_view": view,
                        "initial_input_sha256": content_sha256(initial[0].raw),
                        "reference_status": record_row["reference_status"],
                        "sequential_planner": result,
                        "post_query_status_correct": result["after_compatible"]
                        == [record_row["reference_status"]],
                    }
                )
    after = audit_development_sources(root=root, memory_root=memory_root)
    if after["source_sha256_after"] != audit["source_sha256_after"]:
        raise ValueError("retained source frame mutated during acquisition replay")
    results = [row["sequential_planner"] for row in rows]
    ledgers = [ledger for result in results for ledger in result["ledgers"]]
    return {
        "schema_version": "source-evidence-sequential-replay/v1",
        "status": "authored_visibility_sequential_replay_complete",
        "native_runtime_record_count": audit["native_runtime_event_count"],
        "authored_view_count": len(rows),
        "source_cluster_count": audit["source_cluster_count"],
        "producer_family_count": audit["producer_family_count"],
        "available_queries": list(available),
        "budget_units_per_view": budget,
        "initial_identified_count": sum(
            result["before_state"] == "identified" for result in results
        ),
        "newly_resolved_count": sum(result["newly_resolved"] for result in results),
        "correct_post_query_count": sum(row["post_query_status_correct"] for row in rows),
        "post_query_count_meaning": "recovered full-source status; unresolved is not a wrong commitment",
        "source_query_count": len(ledgers),
        "observed_query_count": sum(ledger["status"] == "observed" for ledger in ledgers),
        "query_cost_units": sum(result["cost_units"] for result in results),
        "query_cost_meaning": "declared endpoint costs 1/1/2; not physical IO or USD",
        "source_bytes_read": sum(result["source_bytes_read"] for result in results),
        "source_bytes_meaning": "query reads only, including failed reads; excludes initial views/audits",
        "stop_reason_counts": {
            reason: sum(result["stop_reason"] == reason for result in results)
            for reason in sorted({result["stop_reason"] for result in results})
        },
        "sources_unchanged": True,
        "source_sha256_before": audit["source_sha256_after"],
        "source_sha256_after": after["source_sha256_after"],
        "executed_module_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in ("source_evidence_sequential.py", "source_evidence_acquisition.py")
        },
        "naturally_missing_evidence_measured": False,
        "llm_planner_measured": False,
        "provider_calls": 0,
        "rows": rows,
    }
