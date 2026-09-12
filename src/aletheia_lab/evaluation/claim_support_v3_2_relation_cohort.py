"""Prospective V3.2 relation-cohort planning and authority boundary.

The source measurements are deterministic evaluator projections.  The model is
used only for the already-qualified part-by-evidence relation instrument.  This
module builds and audits the frozen 240-request cohort but performs no provider
calls and grants no corpus, label, sample, or human-review authority.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tiktoken

from aletheia_lab.diagnosis.variant_registry import VariantId, build_variant_registry
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_live import (
    PreparedClaimCorpusRequest,
    _model_policy,
    _opaque,
    _request_authority,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    MAXIMUM_ATTEMPTS,
    MAXIMUM_OUTPUT_TOKENS,
    MINIMUM_PROVIDER_INTERVAL_MS,
    MODEL_SNAPSHOT,
    TOKENIZER_VERSION,
    publish,
    read_document,
    seal,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    PROTOCOL_PATH as QUALIFICATION_PROTOCOL_PATH,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    build_plan as build_qualification_plan,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    checked_run as checked_qualification_run,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    verify_protocol as verify_qualification_protocol,
)
from aletheia_lab.evaluation.claim_support_v3_2_qualification_execution import (
    verify as verify_qualification_execution,
)
from aletheia_lab.evaluation.claim_support_v3_2_role import (
    PROTOCOL_PATH as SOURCE_ROLE_PROTOCOL_PATH,
)
from aletheia_lab.evaluation.claim_support_v3_2_role import (
    build_deterministic_source_census,
)
from aletheia_lab.evaluation.claim_support_v3_2_role import (
    verify_protocol as verify_source_role_protocol,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    _chat_tokens,
    _openai_policy,
)
from aletheia_lab.evaluation.claim_validation_v3_design import (
    FRAMES,
    LABELS,
    RELATION_PROMPT,
    build_design,
    reduce_relations,
    relation_schema,
    structural_relations,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_sha256 as digest,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import RuntimePolicyReference, prepare_gateway_request
from aletheia_lab.project.identity import canonical_project_json, content_sha256

VERSION = "claim-support-validation-v3.2-relation-cohort/1"
PROTOCOL_PATH = "configs/evaluation/claim_support_validation_v3_2_relation_cohort_protocol.json"
REQUEST_COUNT = 240
SOURCE_REQUEST_COUNT = 0
RELATION_REQUEST_COUNT = 240
INSTRUMENT_VARIANT: VariantId = "FULL"

PROTECTED_FALSE_FLAGS = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "sample_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "admitted_to_corpus": False,
    "relation_outcomes_interpreted": False,
}

AUTHORIZATION_FIELDS = {
    "schema_version",
    "plan_sha256",
    "protocol_sha256",
    "source_commit_ref",
    "qualification_authorization_sha256",
    "qualification_receipt_sha256",
    "qualification_terminal_store_sha256",
    "rehearsal_sha256",
    "destination_sha256",
    "operator_cost_ceiling_usd",
    "authorized_at",
    "registered_attempts",
    "credential_stored",
    "authorization_scope",
    "relation_execution_authorized",
    "relation_closeout_unlocked",
    "authorization_sha256",
    *PROTECTED_FALSE_FLAGS,
}


def _implementation_bindings(root: Path) -> dict[str, str]:
    paths = list((root / "src/aletheia_lab/model_gateway").rglob("*.py"))
    paths += list((root / "src/aletheia_lab/evaluation/_attempt_store").rglob("*.py"))
    paths += [
        root / path
        for path in (
            "scripts/claim_support_validation_v3_2_relation_cohort.py",
            "src/aletheia_lab/evaluation/claim_support_v3_2_relation_cohort.py",
            "src/aletheia_lab/evaluation/claim_support_v3_2_relation_cohort_execution.py",
            "src/aletheia_lab/evaluation/claim_support_v3_2_qualification.py",
            "src/aletheia_lab/evaluation/claim_support_v3_2_qualification_execution.py",
            "src/aletheia_lab/evaluation/claim_support_v3_2_role.py",
            "src/aletheia_lab/evaluation/claim_validation_v3_design.py",
            "src/aletheia_lab/evaluation/claim_validation_v3_qualification.py",
            "src/aletheia_lab/evaluation/claim_validation_v2_qualification.py",
            "src/aletheia_lab/evaluation/claim_corpus_live.py",
            "src/aletheia_lab/evaluation/claim_corpus_live_store.py",
            "src/aletheia_lab/evaluation/claim_corpus_terminal_reader.py",
            "src/aletheia_lab/evaluation/execution_contracts.py",
            "src/aletheia_lab/evaluation/variant_fairness.py",
            "src/aletheia_lab/filesystem.py",
            "src/aletheia_lab/project/identity.py",
            "src/aletheia_lab/diagnosis/variant_registry.py",
            FAIRNESS_PATH,
            SOURCE_ROLE_PROTOCOL_PATH,
            QUALIFICATION_PROTOCOL_PATH,
        )
    ]
    return {
        path.relative_to(root).as_posix(): content_sha256(path.read_bytes())
        for path in sorted(set(paths))
    }


def _task_body(
    assignment_ordinal: int,
    assignment: dict[str, Any],
    source: dict[str, Any],
    context: ModelVisibleEvidenceContext,
) -> dict[str, Any]:
    claim = source["claim"]
    evidence_ids = [item.evidence_id for item in context.items]
    return {
        "schema_version": VERSION,
        "assignment_ordinal": assignment_ordinal,
        "source_instance_sha256": source["source_instance_sha256"],
        "source_slot_sha256": source["source_slot_sha256"],
        "predecessor_slot_sha256": source["predecessor_slot_sha256"],
        "source_binding_sha256": source["source_binding_sha256"],
        "claim_ordinal": source["claim_ordinal"],
        "family_id": source["family_id"],
        "mechanism": source["mechanism"],
        "evidence_condition": source["evidence_condition"],
        "source_variant": source["variant"],
        "frame": assignment["frame"],
        "claim": claim,
        "framed_context": context.model_dump(mode="json"),
        "response_schema": relation_schema(len(claim["material_parts"]), evidence_ids),
    }


def _schedule_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hash-order within frames, then interleave one frame per four-request block."""

    by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_frame[task["frame"]].append(task)
    for frame in FRAMES:
        by_frame[frame].sort(
            key=lambda row: digest(
                {"version": VERSION, "schedule": "balanced-v1", "task": row["task_sha256"]}
            )
        )
        if len(by_frame[frame]) != 60:
            raise ValueError("relation task frame capacity differs from freeze")
    scheduled: list[dict[str, Any]] = []
    for block in range(60):
        rotation = block % len(FRAMES)
        frame_order = FRAMES[rotation:] + FRAMES[:rotation]
        for frame in frame_order:
            row = by_frame[frame][block]
            body = {
                **{key: value for key, value in row.items() if key != "task_sha256"},
                "execution_ordinal": len(scheduled) + 1,
            }
            scheduled.append({**body, "task_sha256": digest(body)})
    return scheduled


def build_relation_tasks(root: Path) -> list[dict[str, Any]]:
    """Project the prespecified assignment set onto deterministic V3.2 sources."""

    design = build_design(root)
    sources = build_deterministic_source_census(root)
    source_by_key = {
        (row["predecessor_slot_sha256"], row["claim_ordinal"]): row for row in sources
    }
    slot_by_sha = {slot["slot_sha256"]: slot for slot in design["slots"]}
    tasks: list[dict[str, Any]] = []
    for ordinal, assignment in enumerate(design["relation_census"], 1):
        key = assignment["slot"], assignment["ordinal"]
        source = source_by_key.get(key)
        slot = slot_by_sha.get(assignment["slot"])
        if source is None or slot is None:
            raise ValueError("relation assignment lacks its frozen deterministic source")
        if (
            assignment["family_id"] != source["family_id"]
            or assignment["text_sha256"] != digest(source["claim"]["claim_text"])
        ):
            raise ValueError("relation assignment differs from source identity")
        context = ModelVisibleEvidenceContext.model_validate_json(
            json.dumps(slot["frames"][assignment["frame"]])
        )
        body = _task_body(ordinal, assignment, source, context)
        tasks.append({**body, "task_sha256": digest(body)})
    scheduled = _schedule_tasks(tasks)
    if (
        len(scheduled) != REQUEST_COUNT
        or len({row["task_sha256"] for row in scheduled}) != REQUEST_COUNT
        or len({row["source_instance_sha256"] for row in scheduled}) != REQUEST_COUNT
        or len({row["claim"]["claim_text"] for row in scheduled}) != REQUEST_COUNT
    ):
        raise ValueError("relation task census is incomplete or contains collisions")
    return scheduled


def outbound(task: dict[str, Any]) -> tuple[str, ModelVisibleEvidenceContext]:
    """Return the entire provider-visible surface; all design strata stay local."""

    body = {key: value for key, value in task.items() if key != "task_sha256"}
    if task.get("task_sha256") != digest(body):
        raise ValueError("relation task hash mismatch")
    context = ModelVisibleEvidenceContext.model_validate_json(
        json.dumps(task["framed_context"])
    )
    prompt = RELATION_PROMPT + "\nClaim and material parts: " + canonical_project_json(
        task["claim"]
    )
    return prompt, context


def relation_semantic_issue(
    task: dict[str, Any], payload: dict[str, Any] | None
) -> str | None:
    """Reject malformed matrices while preserving substantive judgments as outcomes."""

    if payload is None:
        return None
    _, context = outbound(task)
    try:
        reduce_relations(
            payload,
            len(task["claim"]["material_parts"]),
            [item.evidence_id for item in context.items],
        )
    except (KeyError, TypeError, ValueError):
        return "relation_matrix_invalid"
    return None


def _task_audit(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    frames = Counter(task["frame"] for task in tasks)
    labels: Counter[str] = Counter()
    for task in tasks:
        context = ModelVisibleEvidenceContext.model_validate_json(
            json.dumps(task["framed_context"])
        )
        labels[
            reduce_relations(
                structural_relations(task["claim"], context),
                len(task["claim"]["material_parts"]),
                [item.evidence_id for item in context.items],
            )
        ] += 1
    blocks_balanced = all(
        Counter(task["frame"] for task in tasks[index : index + 4])
        == dict.fromkeys(FRAMES, 1)
        for index in range(0, len(tasks), 4)
    )
    if (
        frames != dict.fromkeys(FRAMES, 60)
        or labels != dict.fromkeys(LABELS, 60)
        or not blocks_balanced
    ):
        raise ValueError("relation task schedule differs from frozen structural capacity")
    return {
        "relation_frame_counts": dict(sorted(frames.items())),
        "evaluator_structural_capacity_counts": dict(sorted(labels.items())),
        "four_request_blocks_frame_balanced": True,
    }


def build_protocol(root: Path) -> dict[str, Any]:
    source = verify_source_role_protocol(root)
    qualification = verify_qualification_protocol(root)
    tasks = build_relation_tasks(root)
    audit = _task_audit(tasks)
    return seal(
        {
            "schema_version": VERSION,
            "status": "v3_2_relation_cohort_boundary_frozen",
            "source_role_protocol_sha256": source["protocol_sha256"],
            "deterministic_source_census_sha256": source[
                "deterministic_source_census_sha256"
            ],
            "source_claim_instance_count": source["source_claim_instance_count"],
            "relation_assignment_census_sha256": source[
                "relation_assignment_census_sha256"
            ],
            "qualification_protocol_sha256": qualification["protocol_sha256"],
            "qualification_requires_passed_12_of_12_receipt": True,
            "qualification_outcomes_may_not_adapt_cohort": True,
            "relation_task_set_sha256": digest(
                sorted(task["task_sha256"] for task in tasks)
            ),
            "relation_task_schedule_sha256": digest(
                [task["task_sha256"] for task in tasks]
            ),
            "source_provider_request_count": SOURCE_REQUEST_COUNT,
            "relation_provider_request_count": RELATION_REQUEST_COUNT,
            "relation_instrument_variant": INSTRUMENT_VARIANT,
            "source_variant_is_provenance_only": True,
            "variant_superiority_claims_permitted": False,
            "model_snapshot": MODEL_SNAPSHOT,
            "maximum_output_tokens": MAXIMUM_OUTPUT_TOKENS,
            "maximum_attempts": MAXIMUM_ATTEMPTS,
            "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
            "substantive_relation_judgments_are_measurements": True,
            "cohort_semantic_gate_is_structural_only": True,
            "evaluator_structural_matrices_excluded_from_provider_messages": True,
            "technical_and_structural_failures_preserved_in_denominator": True,
            "selective_rerun_permitted": False,
            "implementation_bindings": _implementation_bindings(root),
            **audit,
            "next_authorized_action": (
                "authorize_v3_2_relation_cohort_from_clean_synchronized_main"
            ),
            "provider_calls_executed": False,
            "relation_execution_authorized": False,
            "relation_closeout_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "protocol_sha256",
    )


def verify_protocol(root: Path) -> dict[str, Any]:
    tracked = read_document(root / PROTOCOL_PATH, "protocol_sha256")
    expected = build_protocol(root)
    if tracked != expected:
        raise ValueError("tracked V3.2 relation cohort protocol differs")
    return tracked


def qualification_evidence(root: Path, qualification_run: Path) -> dict[str, Any]:
    """Independently replay the exact passed qualification used as this gate."""

    run = checked_qualification_run(root, qualification_run)
    auth = read_document(run / "authorization.json", "authorization_sha256")
    plan = build_qualification_plan(root, source_commit=auth["source_commit_ref"])
    receipt = verify_qualification_execution(root, run, plan, auth)
    if (
        receipt.get("status") != "v3_2_relation_qualification_passed"
        or receipt.get("terminal_request_count") != 12
        or receipt.get("parsed_count") != 12
        or receipt.get("accepted_count") != 12
        or receipt.get("technical_failure_count") != 0
        or receipt.get("semantic_failure_count") != 0
        or receipt.get("relation_planning_unlocked") is not True
        or receipt.get("rerun_forbidden") is not True
    ):
        raise ValueError("V3.2 relation qualification did not pass exactly 12 of 12")
    return {
        "qualification_authorization_sha256": auth["authorization_sha256"],
        "qualification_receipt_sha256": receipt["receipt_sha256"],
        "qualification_terminal_store_sha256": receipt["terminal_store_sha256"],
        "qualification_source_commit_ref": receipt["source_commit_ref"],
    }


def build_plan(
    root: Path,
    qualification_run: Path,
    *,
    source_commit: str | None = None,
) -> dict[str, Any]:
    protocol = verify_protocol(root)
    qualification = qualification_evidence(root, qualification_run)
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ValueError("relation cohort tokenizer differs from freeze")
    tasks = build_relation_tasks(root)
    encoding = tiktoken.get_encoding("o200k_base")
    message_tokens = 0
    schema_tokens = 0
    for task in tasks:
        prompt, context = outbound(task)
        message_tokens += _chat_tokens(
            encoding, prompt, canonical_project_json(context.model_payload())
        )
        schema_tokens += len(
            encoding.encode(canonical_project_json(task["response_schema"]))
        )
    input_ceiling = MAXIMUM_ATTEMPTS * (
        message_tokens + schema_tokens + 512 * len(tasks)
    )
    output_ceiling = len(tasks) * MAXIMUM_OUTPUT_TOKENS * MAXIMUM_ATTEMPTS
    cost_microusd = input_ceiling * 2 + output_ceiling * 8
    state = inspect_repository_state(root)
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-cohort-plan/1",
            "source_commit_ref": source_commit or state.head_commit,
            "protocol_sha256": protocol["protocol_sha256"],
            **qualification,
            "relation_task_set_sha256": protocol["relation_task_set_sha256"],
            "relation_task_schedule_sha256": protocol[
                "relation_task_schedule_sha256"
            ],
            "request_count": len(tasks),
            "source_request_count": SOURCE_REQUEST_COUNT,
            "relation_request_count": RELATION_REQUEST_COUNT,
            "exact_message_input_token_count": message_tokens,
            "exact_response_schema_token_count": schema_tokens,
            "conservative_input_token_ceiling": input_ceiling,
            "output_token_ceiling": output_ceiling,
            "estimated_upper_cost_usd": cost_microusd / 1_000_000,
            "cost_estimate_includes_two_attempts": True,
            "provider_calls_executed": False,
            "relation_execution_authorized": False,
            "relation_closeout_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "plan_sha256",
    )


def rehearse(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    tasks = build_relation_tasks(root)
    if (
        digest(sorted(task["task_sha256"] for task in tasks))
        != plan["relation_task_set_sha256"]
        or digest([task["task_sha256"] for task in tasks])
        != plan["relation_task_schedule_sha256"]
    ):
        raise ValueError("relation cohort task census differs from plan")
    audit = _task_audit(tasks)
    structural_mutations_rejected = 0
    substantive_mutations_preserved = 0
    for task in tasks:
        prompt, context = outbound(task)
        expected = structural_relations(task["claim"], context)
        if canonical_project_json(expected) in prompt:
            raise ValueError("evaluator-only relation matrix entered provider prompt")
        missing = json.loads(json.dumps(expected))
        missing["relations"].pop()
        if relation_semantic_issue(task, missing) != "relation_matrix_invalid":
            raise ValueError("missing relation cell escaped structural boundary")
        structural_mutations_rejected += 1
        changed = json.loads(json.dumps(expected))
        changed["relations"][0]["relation"] = (
            "neutral"
            if changed["relations"][0]["relation"] != "neutral"
            else "supports"
        )
        if relation_semantic_issue(task, changed) is not None:
            raise ValueError("substantive relation judgment was incorrectly rejected")
        substantive_mutations_preserved += 1
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-cohort-rehearsal/1",
            "plan_sha256": plan["plan_sha256"],
            "task_count": len(tasks),
            **audit,
            "structural_negative_mutations_rejected": structural_mutations_rejected,
            "substantive_judgment_mutations_preserved": substantive_mutations_preserved,
            "evaluator_matrices_excluded_from_provider_messages": True,
            "provider_calls_executed": False,
            "relation_execution_authorized": False,
            "relation_closeout_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "rehearsal_sha256",
    )


def checked_run(root: Path, run: Path, qualification_run: Path) -> Path:
    for path in (run, *run.parents):
        if path.is_symlink():
            raise ValueError("relation cohort destination contains a symlink")
    run = run.resolve()
    root = root.resolve()
    qualification_run = qualification_run.resolve()
    if (
        run in (root, qualification_run)
        or run.is_relative_to(root)
        or root.is_relative_to(run)
        or run.is_relative_to(qualification_run)
        or qualification_run.is_relative_to(run)
    ):
        raise ValueError("relation cohort run overlaps a protected source or repository")
    allowed = {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
    if run.exists() and (
        not run.is_dir()
        or any(path.is_symlink() or path.name not in allowed for path in run.iterdir())
    ):
        raise ValueError("relation cohort directory contains unknown files or links")
    return run


def authorize(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    ceiling: float,
) -> dict[str, Any]:
    state = inspect_repository_state(root)
    if plan != build_plan(root, qualification_run, source_commit=state.head_commit):
        raise ValueError("relation cohort plan differs from current frozen inputs")
    if not state.synchronized_main or state.head_commit != plan["source_commit_ref"]:
        raise ValueError("authorize requires clean synchronized main")
    if (
        type(ceiling) not in (float, int)
        or not math.isfinite(ceiling)
        or ceiling < plan["estimated_upper_cost_usd"]
    ):
        raise ValueError("operator budget does not cover relation cohort")
    if run.exists() and any(run.iterdir()):
        raise ValueError("authorization requires a fresh empty destination")
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-cohort-authorization/1",
            "plan_sha256": plan["plan_sha256"],
            "protocol_sha256": plan["protocol_sha256"],
            "source_commit_ref": state.head_commit,
            "qualification_authorization_sha256": plan[
                "qualification_authorization_sha256"
            ],
            "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
            "qualification_terminal_store_sha256": plan[
                "qualification_terminal_store_sha256"
            ],
            "rehearsal_sha256": rehearse(plan, root)["rehearsal_sha256"],
            "destination_sha256": digest(run.as_posix()),
            "operator_cost_ceiling_usd": ceiling,
            "authorized_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "registered_attempts": 1,
            "credential_stored": False,
            "authorization_scope": "v3_2_relation_cohort_only",
            "relation_execution_authorized": True,
            "relation_closeout_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "authorization_sha256",
    )


def validate_authority(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
    *,
    completed: bool = False,
) -> None:
    state = inspect_repository_state(root)
    if auth.get("authorization_sha256") != digest(
        {key: value for key, value in auth.items() if key != "authorization_sha256"}
    ):
        raise ValueError("relation cohort authorization hash mismatch")
    if plan != build_plan(root, qualification_run, source_commit=plan["source_commit_ref"]):
        raise ValueError("relation cohort plan differs from frozen inputs")
    qualification_keys = (
        "qualification_authorization_sha256",
        "qualification_receipt_sha256",
        "qualification_terminal_store_sha256",
    )
    conditions = (
        set(auth) == AUTHORIZATION_FIELDS,
        auth.get("schema_version")
        == "claim-support-v3.2-relation-cohort-authorization/1",
        auth.get("credential_stored") is False,
        auth.get("authorization_scope") == "v3_2_relation_cohort_only",
        auth.get("relation_execution_authorized") is True,
        auth.get("relation_closeout_unlocked") is False,
        auth.get("plan_sha256") == plan["plan_sha256"],
        auth.get("protocol_sha256") == plan["protocol_sha256"],
        auth.get("source_commit_ref") == plan["source_commit_ref"],
        auth.get("destination_sha256") == digest(run.as_posix()),
        auth.get("rehearsal_sha256") == rehearse(plan, root)["rehearsal_sha256"],
        all(auth.get(key) == plan[key] for key in qualification_keys),
        type(auth.get("operator_cost_ceiling_usd")) in (float, int),
        type(auth.get("registered_attempts")) is int,
        auth.get("registered_attempts") == 1,
        all(auth.get(key) is value for key, value in PROTECTED_FALSE_FLAGS.items()),
    )
    if not all(conditions):
        raise ValueError("relation cohort authority differs from plan or destination")
    ceiling = auth["operator_cost_ceiling_usd"]
    if not math.isfinite(ceiling) or ceiling < plan["estimated_upper_cost_usd"]:
        raise ValueError("relation cohort budget is invalid")
    if not completed and (
        not state.synchronized_main or state.head_commit != auth["source_commit_ref"]
    ):
        raise ValueError("live execution requires the authorized clean synchronized main")


def prepare_requests(
    root: Path, plan: dict[str, Any], auth: dict[str, Any]
) -> tuple[PreparedClaimCorpusRequest, ...]:
    tasks = build_relation_tasks(root)
    if (
        digest(sorted(task["task_sha256"] for task in tasks))
        != plan["relation_task_set_sha256"]
        or digest([task["task_sha256"] for task in tasks])
        != plan["relation_task_schedule_sha256"]
    ):
        raise ValueError("relation cohort request census mismatch")
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
    variant = registry.require(INSTRUMENT_VARIANT)
    policy = _openai_policy(root)
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{plan['protocol_sha256']}",
        snapshot_id=f"p3-snapshot-{plan['plan_sha256']}",
        manifest_content_sha256=plan["plan_sha256"],
        source_commit_ref=plan["source_commit_ref"],
        authorization_state="authorized",
        authorization_ref=f"ev-{auth['authorization_sha256']}",
        provenance_sha256=plan["protocol_sha256"],
        created_at=auth["authorized_at"],
        frozen_at=auth["authorized_at"],
        visibility="diagnosis",
    )
    result: list[PreparedClaimCorpusRequest] = []
    for task in tasks:
        prompt, context = outbound(task)
        identity = digest(
            {
                "version": VERSION,
                "protocol": plan["protocol_sha256"],
                "task": task["task_sha256"],
            }
        )
        authority = _request_authority(
            registry=registry,
            request_sha256=identity,
            variant=INSTRUMENT_VARIANT,
            context=context,
            observed_binding_sha256=task["task_sha256"],
        )
        case = EvaluationCaseReference.build(
            manifest=manifest,
            case_id=_opaque(identity),
            family_id=_opaque(task["family_id"]),
            mechanism_id=_opaque("v3-2-relation-measurement"),
            dataset_id=_opaque("v3-2-deterministic-source-cohort"),
            variant_id=_opaque(INSTRUMENT_VARIANT),
            variant_content_sha256=variant.variant_content_sha256,
            case_content_sha256=identity,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{task['source_instance_sha256']}",
            lineage_sha256=task["source_instance_sha256"],
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        model = _model_policy(
            manifest=manifest,
            route="model_gateway",
            variant_content_sha256=variant.variant_content_sha256,
            prompt_policy_sha256=digest(prompt),
            response_schema=task["response_schema"],
            openai_policy=policy,
        )
        runtime = RuntimePolicyReference.build(
            manifest=manifest,
            model_policy=model,
            retry_policy_ref=_opaque({"version": VERSION, "attempts": MAXIMUM_ATTEMPTS}),
            timeout_ns=int(policy.timeout_seconds * 1_000_000_000),
            max_attempts=MAXIMUM_ATTEMPTS,
            max_response_bytes=32768,
            provenance_sha256=identity,
        )
        gateway = prepare_gateway_request(
            manifest=manifest,
            case=case,
            model_policy=model,
            context=context,
            prompt_text=prompt,
            response_schema=task["response_schema"],
            runtime_policy=runtime,
        )
        result.append(PreparedClaimCorpusRequest(identity, "model_gateway", authority, gateway))
    identities = {
        item.request.initial_attempt.request_identity_sha256 for item in result
    }
    if len(result) != REQUEST_COUNT or len(identities) != REQUEST_COUNT:
        raise ValueError("duplicate or incomplete relation cohort request identities")
    return tuple(result)


__all__ = [
    "AUTHORIZATION_FIELDS",
    "INSTRUMENT_VARIANT",
    "PROTECTED_FALSE_FLAGS",
    "PROTOCOL_PATH",
    "RELATION_REQUEST_COUNT",
    "REQUEST_COUNT",
    "SOURCE_REQUEST_COUNT",
    "VERSION",
    "authorize",
    "build_plan",
    "build_protocol",
    "build_relation_tasks",
    "checked_run",
    "outbound",
    "prepare_requests",
    "publish",
    "qualification_evidence",
    "read_document",
    "rehearse",
    "relation_semantic_issue",
    "seal",
    "validate_authority",
    "verify_protocol",
]
