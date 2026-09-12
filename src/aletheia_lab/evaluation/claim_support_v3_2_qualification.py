"""Prospective V3.2 relation qualification planning and authority boundary.

Only the unchanged relation instrument is provider-backed. Source claims are
constructed deterministically under the separately frozen V3.2 source-role
protocol. This module performs no provider calls and grants no relation-cohort,
corpus, label, or human-review authority.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tiktoken

from aletheia_lab.diagnosis.variant_registry import build_variant_registry
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_live import (
    PreparedClaimCorpusRequest,
    _model_policy,
    _opaque,
    _request_authority,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_support_v3_2_role import (
    PROTOCOL_PATH as SOURCE_ROLE_PROTOCOL_PATH,
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
    build_probes,
    reduce_relations,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    publish,
    read_document,
    seal,
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

VERSION = "claim-support-validation-v3.2-relation-qualification/1"
PROTOCOL_PATH = "configs/evaluation/claim_support_validation_v3_2_qualification_protocol.json"
MODEL_SNAPSHOT = "gpt-4.1-2025-04-14"
TOKENIZER_VERSION = "0.14.0"
MAXIMUM_OUTPUT_TOKENS = 2048
MAXIMUM_ATTEMPTS = 2
MINIMUM_PROVIDER_INTERVAL_MS = 1000
REQUEST_COUNT = 12
SOURCE_REQUEST_COUNT = 0
RELATION_REQUEST_COUNT = 12

PROTECTED_FALSE_FLAGS = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "admitted_to_corpus": False,
    "relation_execution_authorized": False,
}

AUTHORIZATION_FIELDS = {
    "schema_version",
    "plan_sha256",
    "protocol_sha256",
    "source_commit_ref",
    "rehearsal_sha256",
    "destination_sha256",
    "operator_cost_ceiling_usd",
    "authorized_at",
    "registered_attempts",
    "credential_stored",
    "authorization_scope",
    "qualification_execution_authorized",
    "relation_planning_unlocked",
    "authorization_sha256",
    *PROTECTED_FALSE_FLAGS,
}


def _implementation_bindings(root: Path) -> dict[str, str]:
    paths = list((root / "src/aletheia_lab/model_gateway").rglob("*.py"))
    paths += list((root / "src/aletheia_lab/evaluation/_attempt_store").rglob("*.py"))
    paths += [
        root / path
        for path in (
            "scripts/claim_support_validation_v3_2_qualification.py",
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
        )
    ]
    return {
        path.relative_to(root).as_posix(): content_sha256(path.read_bytes())
        for path in sorted(set(paths))
    }


def build_relation_probes(root: Path) -> list[dict[str, Any]]:
    """Rebind the prespecified relation probes to fresh V3.2 identities."""

    probes: list[dict[str, Any]] = []
    for predecessor in build_probes(root):
        if predecessor["kind"] != "relation":
            continue
        body = {key: value for key, value in predecessor.items() if key != "probe_sha256"}
        probes.append({**body, "probe_sha256": digest({"version": VERSION, **body})})
    frame_counts = Counter(probe["condition"] for probe in probes)
    if (
        len(probes) != REQUEST_COUNT
        or len({probe["probe_sha256"] for probe in probes}) != REQUEST_COUNT
        or frame_counts != dict.fromkeys(FRAMES, 3)
        or any(probe["kind"] != "relation" or probe["variant"] != "FULL" for probe in probes)
    ):
        raise ValueError("V3.2 relation qualification probe census differs")
    return probes


def outbound(probe: dict[str, Any]) -> tuple[str, ModelVisibleEvidenceContext]:
    """Return only model-visible relation inputs; expected matrices remain local."""

    if probe.get("kind") != "relation":
        raise ValueError("source probe cannot enter V3.2 relation qualification")
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(probe["context"]))
    prompt = probe["prompt"] + "\nClaim and material parts: " + canonical_project_json(
        probe["claim"]
    )
    return prompt, context


def relation_semantic_issue(
    probe: dict[str, Any], payload: dict[str, Any] | None
) -> str | None:
    if payload is None:
        return None
    _, context = outbound(probe)
    parts = len(probe["claim"]["material_parts"])
    evidence_ids = [item.evidence_id for item in context.items]
    try:
        reduce_relations(payload, parts, evidence_ids)
    except (KeyError, TypeError, ValueError):
        return "relation_matrix_invalid"

    def key(row: dict[str, Any]) -> tuple[int, str]:
        return row["part"], row["evidence_id"]

    try:
        observed = sorted(payload["relations"], key=key)
        expected = sorted(probe["expected"]["relations"], key=key)
    except (KeyError, TypeError, ValueError):
        return "relation_matrix_invalid"
    return None if observed == expected else "relation_expected_matrix_mismatch"


def build_protocol(root: Path) -> dict[str, Any]:
    source_role = verify_source_role_protocol(root)
    probes = build_relation_probes(root)
    return seal(
        {
            "schema_version": VERSION,
            "status": "v3_2_relation_qualification_boundary_frozen",
            "source_role_protocol_sha256": source_role["protocol_sha256"],
            "source_measurement_role": source_role["source_measurement_role"],
            "deterministic_source_census_sha256": source_role[
                "deterministic_source_census_sha256"
            ],
            "source_claim_instance_count": source_role["source_claim_instance_count"],
            "prospective_relation_request_count": source_role[
                "prospective_relation_request_count"
            ],
            "relation_assignment_census_sha256": source_role[
                "relation_assignment_census_sha256"
            ],
            "relation_probe_census_sha256": digest(probes),
            "qualification_request_count": len(probes),
            "source_qualification_provider_request_count": SOURCE_REQUEST_COUNT,
            "relation_qualification_provider_request_count": RELATION_REQUEST_COUNT,
            "relation_frame_probe_counts": dict(
                sorted(Counter(probe["condition"] for probe in probes).items())
            ),
            "prior_relation_probe_census_reused_without_adaptation": True,
            "prior_relation_probe_outcomes_used_for_selection": False,
            "fresh_request_identities_required": True,
            "expected_relation_matrices_evaluator_only": True,
            "synthetic_only": True,
            "model_snapshot": MODEL_SNAPSHOT,
            "maximum_output_tokens": MAXIMUM_OUTPUT_TOKENS,
            "maximum_attempts": MAXIMUM_ATTEMPTS,
            "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
            "qualification_requires_all_parsed_and_exact": True,
            "qualification_success_authorizes_planning_only": True,
            "implementation_bindings": _implementation_bindings(root),
            "next_authorized_action": (
                "authorize_v3_2_relation_qualification_from_clean_synchronized_main"
            ),
            "provider_calls_executed": False,
            "qualification_execution_authorized": False,
            "relation_planning_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "protocol_sha256",
    )


def verify_protocol(root: Path) -> dict[str, Any]:
    tracked = read_document(root / PROTOCOL_PATH, "protocol_sha256")
    expected = build_protocol(root)
    if tracked != expected:
        raise ValueError("tracked V3.2 relation qualification protocol differs")
    return tracked


def build_plan(root: Path, *, source_commit: str | None = None) -> dict[str, Any]:
    protocol = verify_protocol(root)
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ValueError("qualification tokenizer differs from freeze")
    probes = build_relation_probes(root)
    encoding = tiktoken.get_encoding("o200k_base")
    message_tokens = 0
    schema_tokens = 0
    for probe in probes:
        prompt, context = outbound(probe)
        message_tokens += _chat_tokens(
            encoding, prompt, canonical_project_json(context.model_payload())
        )
        schema_tokens += len(encoding.encode(canonical_project_json(probe["schema"])))
    input_ceiling = MAXIMUM_ATTEMPTS * (
        message_tokens + schema_tokens + 512 * len(probes)
    )
    output_ceiling = len(probes) * MAXIMUM_OUTPUT_TOKENS * MAXIMUM_ATTEMPTS
    cost_microusd = input_ceiling * 2 + output_ceiling * 8
    state = inspect_repository_state(root)
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-qualification-plan/1",
            "source_commit_ref": source_commit or state.head_commit,
            "protocol_sha256": protocol["protocol_sha256"],
            "probe_census_sha256": digest(probes),
            "request_count": len(probes),
            "source_request_count": SOURCE_REQUEST_COUNT,
            "relation_request_count": RELATION_REQUEST_COUNT,
            "exact_message_input_token_count": message_tokens,
            "exact_response_schema_token_count": schema_tokens,
            "conservative_input_token_ceiling": input_ceiling,
            "output_token_ceiling": output_ceiling,
            "estimated_upper_cost_usd": cost_microusd / 1_000_000,
            "cost_estimate_includes_two_attempts": True,
            "synthetic_only": True,
            "provider_calls_executed": False,
            "qualification_execution_authorized": False,
            "relation_planning_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "plan_sha256",
    )


def rehearse(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    probes = build_relation_probes(root)
    if digest(probes) != plan["probe_census_sha256"]:
        raise ValueError("qualification probe census mismatch")
    labels: Counter[str] = Counter()
    for probe in probes:
        prompt, context = outbound(probe)
        expected = probe["expected"]
        label = reduce_relations(
            expected,
            len(probe["claim"]["material_parts"]),
            [item.evidence_id for item in context.items],
        )
        labels[label] += 1
        if relation_semantic_issue(probe, expected) is not None:
            raise ValueError("expected relation matrix fails its own qualification boundary")
        if "Expected answer:" in prompt or canonical_project_json(expected) in prompt:
            raise ValueError("evaluator-only relation answer entered provider prompt")
        changed = json.loads(json.dumps(expected))
        changed["relations"][0]["relation"] = (
            "neutral" if changed["relations"][0]["relation"] != "neutral" else "supports"
        )
        if relation_semantic_issue(probe, changed) is None:
            raise ValueError("relation semantic mutation escaped qualification boundary")
    if labels != dict.fromkeys(LABELS, 3):
        raise ValueError("relation qualification labels differ from frame design")
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-qualification-rehearsal/1",
            "plan_sha256": plan["plan_sha256"],
            "probe_count": len(probes),
            "relation_frame_count": len(FRAMES),
            "negative_semantic_mutations_rejected": len(probes),
            "expected_matrices_excluded_from_provider_messages": True,
            "provider_calls_executed": False,
            "qualification_execution_authorized": False,
            "relation_planning_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "rehearsal_sha256",
    )


def checked_run(root: Path, run: Path) -> Path:
    for path in (run, *run.parents):
        if path.is_symlink():
            raise ValueError("qualification destination contains a symlink")
    run = run.resolve()
    root = root.resolve()
    if run == root or run.is_relative_to(root) or root.is_relative_to(run):
        raise ValueError("qualification run overlaps repository")
    allowed = {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
    if run.exists() and (
        not run.is_dir() or any(path.is_symlink() or path.name not in allowed for path in run.iterdir())
    ):
        raise ValueError("qualification directory contains unknown files or links")
    return run


def authorize(root: Path, run: Path, plan: dict[str, Any], ceiling: float) -> dict[str, Any]:
    state = inspect_repository_state(root)
    if plan != build_plan(root, source_commit=state.head_commit):
        raise ValueError("qualification plan differs from current frozen inputs")
    if not state.synchronized_main or state.head_commit != plan["source_commit_ref"]:
        raise ValueError("authorize requires clean synchronized main")
    if (
        type(ceiling) not in (float, int)
        or not math.isfinite(ceiling)
        or ceiling < plan["estimated_upper_cost_usd"]
    ):
        raise ValueError("operator budget does not cover qualification")
    if run.exists() and any(run.iterdir()):
        raise ValueError("authorization requires a fresh empty destination")
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-qualification-authorization/1",
            "plan_sha256": plan["plan_sha256"],
            "protocol_sha256": plan["protocol_sha256"],
            "source_commit_ref": state.head_commit,
            "rehearsal_sha256": rehearse(plan, root)["rehearsal_sha256"],
            "destination_sha256": digest(run.as_posix()),
            "operator_cost_ceiling_usd": ceiling,
            "authorized_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "registered_attempts": 1,
            "credential_stored": False,
            "authorization_scope": "v3_2_relation_qualification_only",
            "qualification_execution_authorized": True,
            "relation_planning_unlocked": False,
            **PROTECTED_FALSE_FLAGS,
        },
        "authorization_sha256",
    )


def validate_authority(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
    *,
    completed: bool = False,
) -> None:
    state = inspect_repository_state(root)
    if auth.get("authorization_sha256") != digest(
        {key: value for key, value in auth.items() if key != "authorization_sha256"}
    ):
        raise ValueError("qualification authorization hash mismatch")
    if plan != build_plan(root, source_commit=plan["source_commit_ref"]):
        raise ValueError("qualification plan differs from frozen inputs")
    conditions = (
        set(auth) == AUTHORIZATION_FIELDS,
        auth.get("schema_version")
        == "claim-support-v3.2-relation-qualification-authorization/1",
        auth.get("credential_stored") is False,
        auth.get("authorization_scope") == "v3_2_relation_qualification_only",
        auth.get("qualification_execution_authorized") is True,
        auth.get("relation_planning_unlocked") is False,
        auth.get("plan_sha256") == plan["plan_sha256"],
        auth.get("protocol_sha256") == plan["protocol_sha256"],
        auth.get("source_commit_ref") == plan["source_commit_ref"],
        auth.get("destination_sha256") == digest(run.as_posix()),
        auth.get("rehearsal_sha256") == rehearse(plan, root)["rehearsal_sha256"],
        type(auth.get("operator_cost_ceiling_usd")) in (float, int),
        type(auth.get("registered_attempts")) is int,
        auth.get("registered_attempts") == 1,
        all(auth.get(key) is value for key, value in PROTECTED_FALSE_FLAGS.items()),
    )
    if not all(conditions):
        raise ValueError("qualification authority differs from plan or destination")
    ceiling = auth["operator_cost_ceiling_usd"]
    if not math.isfinite(ceiling) or ceiling < plan["estimated_upper_cost_usd"]:
        raise ValueError("qualification budget is invalid")
    if not completed and (
        not state.synchronized_main or state.head_commit != auth["source_commit_ref"]
    ):
        raise ValueError("live execution requires the authorized clean synchronized main")


def prepare_requests(
    root: Path, plan: dict[str, Any], auth: dict[str, Any]
) -> tuple[PreparedClaimCorpusRequest, ...]:
    probes = build_relation_probes(root)
    if digest(probes) != plan["probe_census_sha256"]:
        raise ValueError("request census mismatch")
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    registry = build_variant_registry(freeze)
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
    for probe in probes:
        prompt, context = outbound(probe)
        variant = registry.require(probe["variant"])
        identity = digest(
            {
                "version": VERSION,
                "protocol": plan["protocol_sha256"],
                "probe": probe["probe_sha256"],
            }
        )
        authority = _request_authority(
            registry=registry,
            request_sha256=identity,
            variant=probe["variant"],
            context=context,
            observed_binding_sha256=probe["probe_sha256"],
        )
        case = EvaluationCaseReference.build(
            manifest=manifest,
            case_id=_opaque(identity),
            family_id=_opaque("synthetic-v3-2"),
            mechanism_id=_opaque("relation-calibration"),
            dataset_id=_opaque("synthetic-v3-2"),
            variant_id=_opaque(probe["variant"]),
            variant_content_sha256=variant.variant_content_sha256,
            case_content_sha256=identity,
            evidence_bundle_id=f"p3-evidence-bundle-{context.context_sha256}",
            evidence_content_sha256=context.context_sha256,
            lineage_graph_id=f"p3-lineage-graph-{identity}",
            lineage_sha256=identity,
            visibility_projection_sha256=context.context_sha256,
            provenance_sha256=authority.authority_sha256,
            visibility="diagnosis",
        )
        model = _model_policy(
            manifest=manifest,
            route="model_gateway",
            variant_content_sha256=variant.variant_content_sha256,
            prompt_policy_sha256=digest(prompt),
            response_schema=probe["schema"],
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
            response_schema=probe["schema"],
            runtime_policy=runtime,
        )
        result.append(PreparedClaimCorpusRequest(identity, "model_gateway", authority, gateway))
    identities = {
        item.request.initial_attempt.request_identity_sha256 for item in result
    }
    if len(result) != REQUEST_COUNT or len(identities) != REQUEST_COUNT:
        raise ValueError("duplicate or incomplete qualification request identities")
    return tuple(result)


__all__ = [
    "AUTHORIZATION_FIELDS",
    "MAXIMUM_ATTEMPTS",
    "MAXIMUM_OUTPUT_TOKENS",
    "MINIMUM_PROVIDER_INTERVAL_MS",
    "MODEL_SNAPSHOT",
    "PROTECTED_FALSE_FLAGS",
    "PROTOCOL_PATH",
    "RELATION_REQUEST_COUNT",
    "REQUEST_COUNT",
    "SOURCE_REQUEST_COUNT",
    "TOKENIZER_VERSION",
    "VERSION",
    "authorize",
    "build_plan",
    "build_protocol",
    "build_relation_probes",
    "checked_run",
    "outbound",
    "prepare_requests",
    "publish",
    "read_document",
    "rehearse",
    "relation_semantic_issue",
    "seal",
    "validate_authority",
    "verify_protocol",
]
