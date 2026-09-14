"""Read-only V3.2 relation audit and fixed, actual-label sample selection.

Frame intent is provenance, never a label or a selection preference. This
module does not authorize provider calls or admit historical source outputs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.claim_sample_selection import select_balanced_validation_sample
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import (
    build_plan,
    build_relation_tasks,
    read_document,
    seal,
)
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort_execution import _reader, verify
from aletheia_lab.evaluation.claim_support_v3_2_role import ESTIMAND
from aletheia_lab.evaluation.claim_validation_v3_design import reduce_relations
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
from aletheia_lab.evaluation.instrument_validation import (
    LABEL_ORDER,
    ClaimSupportValidationProtocol,
    ClaimType,
    SupportLabel,
    load_validation_protocol,
)

VALIDATION_PATH = "configs/evaluation/claim_support_validation_protocol.json"
VERSION = "claim-support-v3.2-relation-closeout/1"


@dataclass(frozen=True)
class Candidate:
    task: dict[str, Any]
    automatic_label: SupportLabel
    gateway_request_identity_sha256: str
    payload_sha256: str

    @property
    def claim_id(self) -> str:
        return f"claim-{self.task['source_instance_sha256']}"

    @property
    def output_id(self) -> str:
        # Two source instances in one slot remain one output dependence unit.
        return f"output-{self.task['source_slot_sha256']}"

    @property
    def case_family_id(self) -> str:
        return f"family-{digest(self.task['family_id'])}"

    @property
    def claim_text(self) -> str:
        return str(self.task["claim"]["claim_text"])

    @property
    def claim_type(self) -> ClaimType:
        return cast(ClaimType, self.task["claim"]["claim_type"])

    @property
    def evidence_condition(self) -> str:
        return str(self.task["evidence_condition"])

    @property
    def variant(self) -> str:
        return str(self.task["source_variant"])

    @property
    def source_partition(self) -> str:
        return "development"

    @property
    def source_record_sha256(self) -> str:
        return digest(
            {
                "gateway_request_identity_sha256": self.gateway_request_identity_sha256,
                "payload_sha256": self.payload_sha256,
            }
        )

    @property
    def entry_sha256(self) -> str:
        return digest(self.record())

    def record(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "automatic_label": self.automatic_label,
            "gateway_request_identity_sha256": self.gateway_request_identity_sha256,
            "payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True)
class OnboardingPolicy:
    protocol_sha256: str
    sample_target: int = 20
    automatic_label_quota: int = 5
    maximum_claims_per_family_per_label: int = 5
    maximum_claims_per_output_per_label: int = 2
    source_partition: str = "development"


def _rank(protocol_sha256: str, label: str, value: object) -> str:
    # Exactly the existing balanced-label-round-robin-hash/v1 rank.
    return digest({"protocol_sha256": protocol_sha256, "label": label, "value": value})


def _fail(message: str) -> None:
    raise ValueError(message)


def select_samples(
    candidates: tuple[Candidate, ...], protocol: ClaimSupportValidationProtocol
) -> tuple[tuple[Candidate, ...], tuple[Candidate, ...]]:
    main = select_balanced_validation_sample(
        candidates, protocol, labels=LABEL_ORDER, rank=_rank, fail=_fail
    )
    texts = {item.claim_text for item in main}
    remaining = tuple(item for item in candidates if item.claim_text not in texts)
    onboarding = select_balanced_validation_sample(
        remaining,
        OnboardingPolicy(protocol.protocol_sha256),
        labels=LABEL_ORDER,
        rank=_rank,
        fail=_fail,
    )
    return main, onboarding


def sample_census(candidates: tuple[Candidate, ...]) -> list[dict[str, Any]]:
    rows = []
    for label in LABEL_ORDER:
        members = [item for item in candidates if item.automatic_label == label]
        families = Counter(item.case_family_id for item in members)
        outputs = Counter(item.output_id for item in members)
        rows.append(
            {
                "automatic_label": label,
                "claim_count": len(members),
                "canonical_text_count": len({item.claim_text for item in members}),
                "family_count": len(families),
                "output_count": len(outputs),
                "maximum_claims_per_family": max(families.values(), default=0),
                "maximum_claims_per_output": max(outputs.values(), default=0),
                "evidence_condition_counts": dict(
                    Counter(item.evidence_condition for item in members)
                ),
                "source_variant_counts": dict(Counter(item.variant for item in members)),
            }
        )
    return rows


def matrix_diagnostics(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Explain structural failure without repairing cells or judging their truth."""
    parts = len(task["claim"]["material_parts"])
    evidence_ids = [item["evidence_id"] for item in task["framed_context"]["items"]]
    expected = {(part, evidence) for part in range(1, parts + 1) for evidence in evidence_ids}
    cells = payload.get("relations", [])
    if not isinstance(cells, list):
        cells = []
    actual = Counter(
        (cell["part"], cell["evidence_id"])
        for cell in cells
        if isinstance(cell, dict)
        and type(cell.get("part")) is int
        and isinstance(cell.get("evidence_id"), str)
    )
    return {
        "expected_cell_count": len(expected),
        "received_cell_count": len(cells),
        "unique_cell_count": len(actual),
        "duplicate_cell_count": sum(count - 1 for count in actual.values()),
        "missing_cells": [list(key) for key in sorted(expected - actual.keys())],
        "foreign_cells": [list(key) for key in sorted(actual.keys() - expected)],
        "repair_performed": False,
        "rerun_performed": False,
    }


def interpret_terminal(
    task: dict[str, Any], outcome: dict[str, Any], payload: dict[str, Any] | None
) -> tuple[Candidate | None, dict[str, Any]]:
    if outcome["task_sha256"] != task["task_sha256"]:
        raise ValueError("terminal task binding mismatch")
    ids = [item["evidence_id"] for item in task["framed_context"]["items"]]
    audit = dict(outcome)
    audit.update(frame=task["frame"], family_id=task["family_id"])
    if outcome["gateway_status"] != "parsed":
        if outcome["structurally_accepted"]:
            raise ValueError("unparsed terminal cannot be accepted")
        return None, audit
    if payload is None:
        raise ValueError("parsed terminal has no parsed payload")
    try:
        label = reduce_relations(payload, len(task["claim"]["material_parts"]), ids)
    except (KeyError, TypeError, ValueError):
        if outcome["structurally_accepted"]:
            raise ValueError("accepted terminal fails frozen reducer") from None
        audit["matrix_diagnostics"] = matrix_diagnostics(task, payload)
        return None, audit
    if not outcome["structurally_accepted"]:
        raise ValueError("rejected terminal unexpectedly passes frozen reducer")
    if label not in LABEL_ORDER:
        raise ValueError("frozen reducer returned an unknown label")
    candidate = Candidate(
        task,
        label,
        outcome["gateway_request_identity_sha256"],
        digest(payload),
    )
    audit.update(automatic_label=label, entry_sha256=candidate.entry_sha256)
    return candidate, audit


def build_closeout(
    root: Path, run: Path, qualification_run: Path
) -> tuple[dict[str, Any], tuple[Candidate, ...], tuple[Candidate, ...], tuple[Candidate, ...]]:
    """Replay all authority/store bindings before reading actual relation judgments."""
    root = root.resolve()
    run = run.resolve()
    qualification_run = qualification_run.resolve()
    auth = read_document(run / "authorization.json", "authorization_sha256")
    plan = build_plan(root, qualification_run, source_commit=auth["source_commit_ref"])
    receipt = verify(root, run, qualification_run, plan, auth)
    protocol = load_validation_protocol(root / VALIDATION_PATH)
    candidates: list[Candidate] = []
    audits = []
    for task, outcome in zip(build_relation_tasks(root), receipt["outcomes"], strict=True):
        identity = outcome["gateway_request_identity_sha256"]
        payload = _reader(run / "attempt-store", identity).terminal_parsed_payload(identity)
        candidate, audit = interpret_terminal(task, outcome, payload)
        if candidate is not None:
            candidates.append(candidate)
        audits.append(audit)
    pool = tuple(candidates)
    main: tuple[Candidate, ...] = ()
    onboarding: tuple[Candidate, ...] = ()
    blocker = None
    try:
        main, onboarding = select_samples(pool, protocol)
    except ValueError as exc:
        blocker = str(exc)
    return (
        seal(
            {
                "schema_version": VERSION,
                "status": "v3_2_fixed_samples_feasible"
                if blocker is None
                else "v3_2_fixed_samples_blocked",
                "cohort_receipt_sha256": receipt["receipt_sha256"],
                "cohort_terminal_store_sha256": receipt["terminal_store_sha256"],
                "cohort_authorization_sha256": auth["authorization_sha256"],
                "cohort_protocol_sha256": plan["protocol_sha256"],
                "cohort_source_commit_ref": auth["source_commit_ref"],
                "validation_protocol_sha256": protocol.protocol_sha256,
                "estimand": ESTIMAND,
                "terminal_request_count": receipt["terminal_request_count"],
                "provider_attempt_count": receipt["provider_attempt_count"],
                "technical_failure_count": receipt["technical_failure_count"],
                "structural_failure_count": receipt["semantic_failure_count"],
                "automatic_label_count": len(pool),
                "pool_strata": sample_census(pool),
                "validation_strata": sample_census(main),
                "onboarding_strata": sample_census(onboarding),
                "selected_entry_sha256": [item.entry_sha256 for item in main],
                "onboarding_entry_sha256": [item.entry_sha256 for item in onboarding],
                "sampling_algorithm": protocol.sampling_algorithm,
                "selection_blocker": blocker,
                "outcomes": audits,
                "failures_preserved_in_denominator": True,
                "frame_intent_used_as_label": False,
                "provider_calls_executed": False,
                "automatic_labels_generated": True,
                "sample_materialized": False,
                "blind_packets_generated": False,
                "human_annotations_collected": False,
                "main_or_sealed_outcomes_opened": False,
                "historical_outputs_admitted": False,
                "natural_error_prevalence_claim_authorized": False,
                "variant_superiority_claim_authorized": False,
            },
            "closeout_sha256",
        ),
        pool,
        main,
        onboarding,
    )
