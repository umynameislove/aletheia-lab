"""Post-hoc output diagnostics, not explanations of a model's internal reasoning."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation import proof_aware_lineage_transfer as pilot
from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    TRUSTED_KINDS,
    Decision,
    assess_decision,
    baseline_decision,
    checked_context,
    visible_reference,
)
from aletheia_lab.evaluation.compositional_lineage_guard import (
    canonicalize_decision,
    guard_decision,
)
from aletheia_lab.evaluation.compositional_lineage_guard_replay import _tree
from aletheia_lab.evaluation.compositional_lineage_pilot import _parse
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as sha256
from aletheia_lab.evaluation.proof_aware_lineage_transfer_cases import validate_cases
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import _read


def _category(reference: dict[str, Any], decision: Decision | None, score: dict[str, Any]) -> str:
    if decision is None:
        return "unavailable"
    state = reference["state"]
    if score["action_success"]:
        return {
            "identified": "valid_resolution",
            "ambiguous": "valid_query",
            "conflict": "valid_conflict",
        }[state]
    committed = score["commitment"]
    if state == "identified":
        if not committed:
            return "missed_identified_resolution"
        if score["unwarranted_commitment"]:
            return "wrong_endpoint_status"
        return "correct_status_invalid_certificate_or_contract"
    if state == "ambiguous":
        return _ambiguous_category(decision, score)
    if committed:
        return "commitment_on_conflicting_evidence"
    if decision.decision == "flag_conflict":
        return "conflict_flag_invalid_certificate_or_contract"
    return "missed_conflict"


def _ambiguous_category(decision: Decision, score: dict[str, Any]) -> str:
    if score["commitment"]:
        return "commitment_on_ambiguous_evidence"
    if decision.decision == "flag_conflict":
        return "false_conflict"
    if score["bounded_nonanswer"]:
        return "safe_simple_abstention"
    return "invalid_ambiguous_action_contract"


def _citation_flags(
    payload: dict[str, Any], decision: Decision, score: dict[str, Any]
) -> list[str]:
    context = checked_context(payload)
    known = {record.id: record for record in context.records}
    ids, flags = decision.cited_records, []
    if len(ids) != len(set(ids)):
        flags.append("duplicate_citation")
    if set(ids) - known.keys():
        flags.append("unknown_citation")
    if any(
        known[key].kind not in TRUSTED_KINDS
        or (known[key].request, known[key].attempt) != (context.request, context.attempt)
        for key in ids
        if key in known
    ):
        flags.append("untrusted_or_off_scope_citation")
    if not score["citation_proof_valid"]:
        flags.append("citation_not_valid_certificate")
    return flags


def _action_flags(
    reference: dict[str, Any], decision: Decision, score: dict[str, Any]
) -> list[str]:
    flags = []
    if score["unwarranted_commitment"]:
        flags.append("endpoint_not_entailed_by_full_context")
    if score["commitment"] and (decision.basis != "entailed" or decision.next_check != "none"):
        flags.append("commitment_basis_or_action_mismatch")
    if decision.decision == "flag_conflict" and (
        decision.basis != "inconsistent" or decision.next_check != "reconcile_records"
    ):
        flags.append("conflict_basis_or_action_mismatch")
    if decision.decision in {"check_evidence", "abstain"}:
        if reference["state"] == "ambiguous" and decision.basis != "underdetermined":
            flags.append("nonanswer_basis_mismatch")
        if (
            decision.decision == "check_evidence"
            and decision.next_check not in reference["minimum_guaranteed_checks"]
        ):
            flags.append("check_not_minimum_guaranteed")
        if decision.decision == "abstain" and decision.next_check != "none":
            flags.append("abstention_with_action")
    return flags


def diagnose_view(payload: dict[str, Any], decision: Decision | None) -> dict[str, Any]:
    """Separate an exclusive output category from overlapping defect flags.

    A wrong endpoint is an observed logical error, not a proven failure of a
    particular hidden reasoning step. Citation and action defects can co-occur.
    Safe abstention is reported separately from useful-query success.
    """
    reference = visible_reference(payload)
    score = assess_decision(payload, decision)
    flags = []
    if decision is not None:
        flags = _citation_flags(payload, decision, score) + _action_flags(
            reference, decision, score
        )
    guarded = guard_decision(payload, decision)
    guarded_score = assess_decision(payload, guarded.decision)
    return {
        "state": reference["state"],
        "category": _category(reference, decision, score),
        "defect_flags": sorted(flags),
        "raw_action_success": score["action_success"],
        "raw_canonicalized": canonicalize_decision(decision) != decision,
        "guard_origin": guarded.origin,
        "guard_action_success": guarded_score["action_success"],
        "guard_category": _category(reference, guarded.decision, guarded_score),
    }


def _comparison(left: Decision | None, right: Decision | None) -> str:
    if left is None or right is None:
        return "unavailable_pair"
    if left == right:
        return "exact_same"
    if (left.decision, left.basis, left.next_check) != (
        right.decision,
        right.basis,
        right.next_check,
    ):
        return "status_basis_or_action_differs"
    if sorted(left.cited_records) == sorted(right.cited_records):
        return "citation_order_only"
    return "citation_content_differs"


def diagnose(
    cases: list[dict[str, Any]], proposals: dict[tuple[str, str], Decision | None]
) -> dict[str, Any]:
    validate_cases(cases)
    expected = {(case["case_id"], arm) for case in cases for arm in POLICIES}
    if proposals.keys() != expected:
        raise ValueError("diagnostics require the complete planned frame including missing slots")
    indexed = {case["case_id"]: case for case in cases}
    rows = {
        (case["case_id"], arm): diagnose_view(case["context"], proposals[(case["case_id"], arm)])
        for case in cases
        for arm in POLICIES
    }
    arms = {}
    for arm in POLICIES:
        selected = [rows[(case["case_id"], arm)] for case in cases]
        pairs = []
        comparisons: dict[str, Counter[str]] = {"raw": Counter(), "proof_guarded": Counter()}
        for case in cases[::2]:
            ids = [f"{case['motif']}:{representation}" for representation in ("records", "table")]
            decisions = [proposals[(key, arm)] for key in ids]
            guarded = [
                guard_decision(indexed[key]["context"], value)
                for key, value in zip(ids, decisions, strict=True)
            ]
            raw_kind = _comparison(*decisions)
            guard_kind = _comparison(*(value.decision for value in guarded))
            comparisons["raw"][raw_kind] += 1
            comparisons["proof_guarded"][guard_kind] += 1
            paired = [rows[(key, arm)] for key in ids]
            pairs.append(
                {
                    "motif": case["motif"],
                    "state": paired[0]["state"],
                    "raw_categories": [value["category"] for value in paired],
                    "guard_categories": [value["guard_category"] for value in paired],
                    "raw_comparison": raw_kind,
                    "guard_comparison": guard_kind,
                    "raw_action_discordant": paired[0]["raw_action_success"]
                    != paired[1]["raw_action_success"],
                    "guard_action_discordant": paired[0]["guard_action_success"]
                    != paired[1]["guard_action_success"],
                    "guard_joint_success": all(value["guard_action_success"] for value in paired),
                }
            )
        arms[arm] = {
            "planned_views": len(selected),
            "raw_action_success": sum(value["raw_action_success"] for value in selected),
            "guard_action_success": sum(value["guard_action_success"] for value in selected),
            "guard_success_origins": dict(
                sorted(
                    Counter(
                        value["guard_origin"] for value in selected if value["guard_action_success"]
                    ).items()
                )
            ),
            "raw_category_counts": dict(
                sorted(Counter(value["category"] for value in selected).items())
            ),
            "overlapping_defect_flag_counts": dict(
                sorted(
                    Counter(flag for value in selected for flag in value["defect_flags"]).items()
                )
            ),
            "by_state": {
                state: dict(
                    sorted(
                        Counter(
                            value["category"] for value in selected if value["state"] == state
                        ).items()
                    )
                )
                for state in ("identified", "ambiguous", "conflict")
            },
            "guard_unsuccessful_category_counts": dict(
                sorted(
                    Counter(
                        value["guard_category"]
                        for value in selected
                        if not value["guard_action_success"]
                    ).items()
                )
            ),
            "format_comparison_counts": {
                stage: dict(sorted(counts.items())) for stage, counts in comparisons.items()
            },
            "paired_motif_diagnostics": pairs,
        }
    return {
        "schema_version": "proof-aware-lineage-output-diagnostics/v1",
        "scope": "post-hoc observed-output diagnostics; not a model thought-process or format causal-effect inference",
        "statistical_unit": "24 authored motifs, two dependent representations per arm",
        "arms": arms,
        "secondary_baseline_case_sensitivity": _symptom_case_sensitivity(cases),
        "provider_calls": 0,
        "frozen_outcomes_mutated": False,
    }


def _symptom_case_sensitivity(cases: list[dict[str, Any]]) -> dict[str, Any]:
    original: Counter[str] = Counter()
    lowercased: Counter[str] = Counter()
    unchanged = 0
    for case in cases:
        context = case["context"]
        transformed = {
            **context,
            "records": [
                {**record, "value": record["value"].lower()}
                if record["kind"] == "performance_report"
                else dict(record)
                for record in context["records"]
            ],
        }
        original[baseline_decision(context, baseline="symptom_only").decision] += 1
        lowercased[baseline_decision(transformed, baseline="symptom_only").decision] += 1
        unchanged += visible_reference(context) == visible_reference(transformed)
    return {
        "scope": "post-hoc case-only counterfactual; not a replacement of the frozen baseline",
        "original_decision_counts": dict(sorted(original.items())),
        "lowercase_report_decision_counts": dict(sorted(lowercased.items())),
        "unchanged_visible_semantics_count": unchanged,
        "view_count": len(cases),
    }


def replay_diagnostics(*, root: Path, memory_root: Path, directory: Path) -> dict[str, Any]:
    before = _tree(directory)
    receipt = pilot.verify(root=root, memory_root=memory_root, directory=directory)
    cases = _read(directory / "cases.json")
    records = pilot._records(directory, cases)
    proposals = {}
    for request in pilot.request_frame(cases):
        record = records.get(request["request_id"])
        proposals[(request["case_id"], request["policy"])] = (
            _parse(DevelopmentCall.model_validate(record["call"])) if record else None
        )
    report = diagnose(cases, proposals)
    if (
        pilot.verify(root=root, memory_root=memory_root, directory=directory) != receipt
        or _tree(directory) != before
    ):
        raise ValueError("source pilot changed during diagnostic replay")
    return {
        **report,
        "verification": "pass",
        "source_receipt_sha256": sha256(
            {key: value for key, value in receipt.items() if key != "verification"}
        ),
        "source_tree_sha256": sha256(before),
        "source_analysis_sha256": receipt["analysis_sha256"],
    }
