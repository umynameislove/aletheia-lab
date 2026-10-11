"""Visible-only post-generation checking, not a new causal or model oracle.

The runtime uses the intersection/reachability resolver. Evaluation retains
the independently enumerated world reference. No hidden labels enter this API.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Literal

from aletheia_lab.evaluation.compositional_lineage import (
    ARTIFACTS,
    CHECK_COSTS,
    TRUSTED_KINDS,
    Check,
    Decision,
    checked_context,
    reachable_statuses,
)

GuardMode = Literal["reject_only", "proof_guarded"]


@dataclass(frozen=True)
class GuardResult:
    decision: Decision | None
    state: str
    origin: str
    canonicalized: bool
    reason: str


def canonicalize_decision(proposal: Decision | None) -> Decision | None:
    """Interpret an explicitly requested check; never infer its correctness.

    This is an action-contract intervention, NOT merely JSON/format repair.
    No basis, endpoint, citation or committed status is changed.
    """
    if proposal is None:
        return None
    value = Decision.model_validate(proposal.model_dump(warnings=False))
    if (
        value.decision == "abstain"
        and value.basis == "underdetermined"
        and value.next_check in CHECK_COSTS
    ):
        return Decision.model_validate({**value.model_dump(), "decision": "check_evidence"})
    return value


def _admitted(payload: dict[str, Any]) -> list[dict[str, Any]]:
    context = checked_context(payload)
    return sorted(
        (
            record.model_dump()
            for record in context.records
            if record.kind in TRUSTED_KINDS
            and (record.request, record.attempt) == (context.request, context.attempt)
        ),
        key=lambda record: record["id"],
    )


def _subset(payload: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    return {**payload, "records": records}


def _proof_statuses(payload: dict[str, Any], ids: list[str]) -> list[str] | None:
    admitted = {record["id"]: record for record in _admitted(payload)}
    if len(ids) != len(set(ids)) or not set(ids) <= admitted.keys():
        return None
    return reachable_statuses(_subset(payload, [admitted[key] for key in ids]))


def _certificate(payload: dict[str, Any], statuses: list[str]) -> list[str]:
    """Deterministic inclusion-minimal support, not minimum-cardinality."""
    records = _admitted(payload)
    if reachable_statuses(_subset(payload, records)) != statuses:
        raise ValueError("visible records do not support the requested certificate")
    for record in list(records):
        candidate = [item for item in records if item["id"] != record["id"]]
        if reachable_statuses(_subset(payload, candidate)) == statuses:
            records = candidate
    return [record["id"] for record in records]


def _endpoint_pairs(payload: dict[str, Any]) -> set[tuple[str, str]]:
    # Remove duplicate constraints and nonconstraints before adding probe facts.
    # Ten binary constraint keys give at most 20 distinct records, so two probes
    # remain within the original 24-record grammar even for a full input.
    unique = {
        (record["kind"], record["subject"], record["value"]): record
        for record in _admitted(payload)
    }
    records = list(unique.values())
    unused = [
        f"r{index:02d}" for index in range(100) if f"r{index:02d}" not in {r["id"] for r in records}
    ]
    pairs = set()
    for requested, loaded in product(ARTIFACTS, repeat=2):
        probes = [
            {
                "id": unused[index],
                "kind": kind,
                "request": payload["request"],
                "attempt": payload["attempt"],
                "subject": payload["request"] if index == 0 else payload["attempt"],
                "value": value,
            }
            for index, (kind, value) in enumerate(
                (("requested_endpoint", requested), ("loaded_endpoint", loaded))
            )
        ]
        if reachable_statuses(_subset(payload, records + probes)):
            pairs.add((requested, loaded))
    return pairs


def _minimum_checks(payload: dict[str, Any]) -> list[Check]:
    pairs = _endpoint_pairs(payload)
    guaranteed: list[Check] = []
    checks: tuple[Check, ...] = ("requested_endpoint", "loaded_endpoint", "both_endpoints")
    for check in checks:
        groups: dict[tuple[str, ...], set[bool]] = {}
        for pair in pairs:
            observed = (
                pair
                if check == "both_endpoints"
                else (pair[0 if check == "requested_endpoint" else 1],)
            )
            groups.setdefault(observed, set()).add(pair[0] != pair[1])
        if groups and all(len(statuses) == 1 for statuses in groups.values()):
            guaranteed.append(check)
    if not guaranteed:
        raise ValueError("ambiguous context has no feasible resolving endpoint measurement")
    minimum = min(CHECK_COSTS[check] for check in guaranteed)
    return sorted(check for check in guaranteed if CHECK_COSTS[check] == minimum)


def _accepted(payload: dict[str, Any], decision: Decision, statuses: list[str], state: str) -> bool:
    proof = _proof_statuses(payload, decision.cited_records)
    if decision.decision in {"binding_fault", "no_binding_fault"}:
        return (
            statuses == [decision.decision]
            and proof == statuses
            and decision.basis == "entailed"
            and decision.next_check == "none"
        )
    if state == "conflict":
        return (
            decision.decision == "flag_conflict"
            and decision.basis == "inconsistent"
            and decision.next_check == "reconcile_records"
            and proof == []
        )
    if state != "ambiguous" or decision.basis != "underdetermined" or proof is None:
        return False
    return (decision.decision == "abstain" and decision.next_check == "none") or (
        decision.decision == "check_evidence" and decision.next_check in _minimum_checks(payload)
    )


def guard_decision(
    payload: dict[str, Any], proposal: Decision | None, *, mode: GuardMode = "proof_guarded"
) -> GuardResult:
    """Keep warranted proposals; never replace an identified wrong status.

    Proof reconstruction and ambiguous/conflict fallbacks are explicitly tool
    generated. Both modes share the same action canonicalization. Unavailable
    proposals remain unavailable, not successful synthetic safe answers.
    """
    if mode not in {"reject_only", "proof_guarded"}:
        raise ValueError("unknown guard mode")
    checked_context(payload)
    statuses = reachable_statuses(payload)
    state = "conflict" if not statuses else "identified" if len(statuses) == 1 else "ambiguous"
    decision = canonicalize_decision(proposal)
    canonicalized = proposal != decision
    if decision is None:
        return GuardResult(None, state, "invalid_proposal", False, "source_unavailable")
    if _accepted(payload, decision, statuses, state):
        return GuardResult(decision, state, "model", canonicalized, "accepted")
    if mode == "proof_guarded":
        if state == "identified" and decision.decision == statuses[0]:
            if decision.basis == "entailed" and decision.next_check == "none":
                answer = Decision.model_validate(
                    {**decision.model_dump(), "cited_records": _certificate(payload, statuses)}
                )
                return GuardResult(
                    answer, state, "proof_reconstructed", canonicalized, "visible_certificate_added"
                )
        elif state == "ambiguous":
            answer = Decision(
                decision="check_evidence",
                basis="underdetermined",
                next_check=_minimum_checks(payload)[0],
                cited_records=[],
            )
            return GuardResult(
                answer, state, "resolver_query", canonicalized, "proposal_not_warranted"
            )
        elif state == "conflict":
            answer = Decision(
                decision="flag_conflict",
                basis="inconsistent",
                next_check="reconcile_records",
                cited_records=_certificate(payload, []),
            )
            return GuardResult(
                answer, state, "resolver_conflict", canonicalized, "proposal_not_warranted"
            )
    refusal = Decision(decision="abstain", basis="none", next_check="none", cited_records=[])
    return GuardResult(refusal, state, "rejected", canonicalized, "proposal_not_warranted")
