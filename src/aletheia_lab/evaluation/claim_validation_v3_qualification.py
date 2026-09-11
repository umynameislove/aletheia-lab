"""V3 qualification planning and authority; no provider calls in this module."""

from __future__ import annotations

import importlib.metadata
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tiktoken

from aletheia_lab.diagnosis.variant_registry import build_variant_registry
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_live import (
    PreparedClaimCorpusRequest,
    _model_policy,
    _opaque,
    _request_authority,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    ProviderDiagnosisOutputV2,
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH, REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_validation_v2_extraction import load_v2_extraction_closeout
from aletheia_lab.evaluation.claim_validation_v2_qualification import _chat_tokens, _openai_policy
from aletheia_lab.evaluation.claim_validation_v3_design import (
    PREDECESSOR_CLOSEOUT,
    PREFIX,
    VERSION,
    accept_source,
    build_design,
    build_probes,
    build_v3_probes,
    implementation_bindings,
    legacy_source_payload,
    reduce_relations,
    render_source_payload,
    source_instance_id,
    source_payload,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_sha256 as digest,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import RuntimePolicyReference, prepare_gateway_request
from aletheia_lab.project.identity import canonical_project_json

PROTOCOL_PATH = "configs/evaluation/claim_support_validation_v3_protocol.json"
FAILURE_CLOSEOUT_PATH = "configs/evaluation/claim_support_validation_v3_qualification_failure.json"
FAILED_V3_PLAN_SHA256 = "a257d14d747d3d6b93dcfe2fa020c58a5bec1ef627aaf2edebfe8eb08f2ddc19"
FAILED_V3_RECEIPT_SHA256 = "7e45169d1167e72a0e76eb3011c3e4495c7981780db26f84e8518e29123beec2"
FAILED_V3_STORE_SHA256 = "e09d6ba632771d5b22d0fcdc840f898f96e096102593dec004361c088561920a"
FAILED_V3_PROTOCOL_SHA256 = "076bd07d241f17d9d36ae738effed97feb980137ddd74df44471165c0c3b7627"
FAILED_V3_SOURCE_COMMIT = "c472068e2287964f1970aacbd362a7f40776de42"
MODEL_SNAPSHOT = "gpt-4.1-2025-04-14"
FALSE_FLAGS = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "cohort_execution_authorized": False,
    "admitted_to_corpus": False,
}
AUTHORIZATION_FIELDS = {
    "schema_version",
    "plan_sha256",
    "source_commit_ref",
    "rehearsal_sha256",
    "destination_sha256",
    "operator_cost_ceiling_usd",
    "authorized_at",
    "registered_attempts",
    "credential_stored",
    "authorization_sha256",
    *FALSE_FLAGS,
}


def seal(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return {**payload, key: digest(payload)}


def read_document(path: Path, key: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required artifact must be a regular non-linked file")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or data.get(key) != digest(
        {k: v for k, v in data.items() if k != key}
    ):
        raise ValueError("artifact content hash mismatch")
    return data


def publish(path: Path, payload: dict[str, Any]) -> str:
    return publish_immutable_file(path, (canonical_project_json(payload) + "\n").encode())


def build_failure_closeout() -> dict[str, Any]:
    """Bind the terminal V3 result without reinterpreting it under V3.1."""
    return seal(
        {
            "schema_version": "claim-support-v3-qualification-failure-closeout/1",
            "status": "v3_qualification_closed_failed_source_representation",
            "source_commit_ref": FAILED_V3_SOURCE_COMMIT,
            "v3_protocol_sha256": FAILED_V3_PROTOCOL_SHA256,
            "plan_sha256": FAILED_V3_PLAN_SHA256,
            "receipt_sha256": FAILED_V3_RECEIPT_SHA256,
            "terminal_store_sha256": FAILED_V3_STORE_SHA256,
            "terminal_request_count": 33,
            "accepted_count": 19,
            "technical_failure_count": 0,
            "semantic_failure_count": 14,
            "source_accepted_count": 7,
            "source_semantic_failure_count": 14,
            "relation_accepted_count": 12,
            "relation_semantic_failure_count": 0,
            "failure_category_counts": {
                "claim_text_repeated_scope_prefix_omission": 12,
                "material_part_scope_prefix_omission": 14,
            },
            "measurement_values_correct_in_semantic_failures": 14,
            "synthetic_only": True,
            "provider_calls_executed": True,
            "rerun_forbidden": True,
            "cohort_planning_unlocked": False,
            **FALSE_FLAGS,
        },
        "closeout_sha256",
    )


def verify_failure_closeout(root: Path) -> dict[str, Any]:
    tracked = read_document(root / FAILURE_CLOSEOUT_PATH, "closeout_sha256")
    expected = build_failure_closeout()
    if tracked != expected:
        raise ValueError("V3 qualification failure closeout differs from terminal audit")
    return tracked


def _retired_reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects/sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _material_part_texts(observed: dict[str, Any]) -> list[str] | None:
    parts = observed.get("material_parts")
    if not isinstance(parts, list):
        return None
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            return None
        texts.append(part["text"])
    return texts


def _claim_scope_only_difference(
    observed: dict[str, Any], target: dict[str, Any]
) -> tuple[bool, bool] | None:
    if (
        observed.get("claim_type") != target["claim_type"]
        or observed.get("visible_evidence_ids") != target["visible_evidence_ids"]
    ):
        return None
    part_texts = _material_part_texts(observed)
    if part_texts is None:
        return None
    normalized_parts = [
        text if text.startswith(PREFIX) else PREFIX + text for text in part_texts
    ]
    if normalized_parts != [part["text"] for part in target["material_parts"]]:
        return None
    claim_text = observed.get("claim_text")
    if not isinstance(claim_text, str):
        return None
    segments = claim_text.split("; ")
    normalized_claim = "; ".join(
        segment if segment.startswith(PREFIX) else PREFIX + segment for segment in segments
    )
    if normalized_claim != target["claim_text"]:
        return None
    return (
        any(not text.startswith(PREFIX) for text in part_texts),
        any(not segment.startswith(PREFIX) for segment in segments),
    )


def _scope_only_difference(
    payload: dict[str, Any], expected: list[dict[str, Any]]
) -> tuple[bool, bool] | None:
    """Classify the observed V3 prose omission without accepting it."""
    try:
        ProviderDiagnosisOutputV2.model_validate_json(json.dumps(payload))
        actual = payload["result"]["atomic_claims"]
    except (KeyError, TypeError, ValueError):
        return None
    if len(actual) != len(expected):
        return None
    classifications = [
        _claim_scope_only_difference(observed, target)
        for observed, target in zip(actual, expected, strict=True)
    ]
    if any(classification is None for classification in classifications):
        return None
    return (
        any(classification[0] for classification in classifications if classification),
        any(classification[1] for classification in classifications if classification),
    )


def _relation_cell_key(item: dict[str, Any]) -> tuple[int, str]:
    return item["part"], item["evidence_id"]


def _relation_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("relations")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("retired V3 relation payload differs")
    return rows


def _retired_audit_inputs(
    root: Path, run: Path
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Path]:
    if run.is_symlink() or not run.is_dir():
        raise ValueError("retired V3 run must be a real directory")
    receipt = read_document(run / "receipt.json", "receipt_sha256")
    expected_core = {
        "receipt_sha256": FAILED_V3_RECEIPT_SHA256,
        "terminal_store_sha256": FAILED_V3_STORE_SHA256,
        "plan_sha256": FAILED_V3_PLAN_SHA256,
        "protocol_sha256": FAILED_V3_PROTOCOL_SHA256,
        "source_commit_ref": FAILED_V3_SOURCE_COMMIT,
        "terminal_request_count": 33,
        "parsed_count": 33,
        "accepted_count": 19,
        "technical_failure_count": 0,
        "semantic_failure_count": 14,
        "rerun_forbidden": True,
        "synthetic_only": True,
        "cohort_planning_unlocked": False,
    }
    if any(receipt.get(key) != value for key, value in expected_core.items()):
        raise ValueError("retired V3 receipt differs from registered failure")
    outcomes = receipt.get("outcomes")
    probes = {probe["probe_sha256"]: probe for probe in build_v3_probes(root)}
    if not isinstance(outcomes, list) or len(outcomes) != 33 or len(probes) != 33:
        raise ValueError("retired V3 outcome census differs")
    identities = {row.get("gateway_request_identity_sha256") for row in outcomes}
    store = run / "attempt-store"
    request_names = {path.name for path in (store / "requests").iterdir()}
    authority_names = {path.stem for path in (store / "authorities").glob("*.json")}
    if None in identities or request_names != identities or authority_names != identities:
        raise ValueError("retired V3 store membership differs")
    return outcomes, probes, store


def audit_failed_qualification(root: Path, run: Path) -> dict[str, Any]:
    """Replay the retired V3 failure classification without changing its verdict."""
    outcomes, probes, store = _retired_audit_inputs(root, run)
    source_accepted = 0
    relation_accepted = 0
    source_failed = 0
    part_omissions = 0
    claim_omissions = 0
    for row in outcomes:
        probe_id = row.get("probe_sha256")
        identity = row.get("gateway_request_identity_sha256")
        if not isinstance(probe_id, str) or not isinstance(identity, str):
            raise ValueError("retired V3 outcome identity differs")
        probe = probes.get(probe_id)
        if probe is None or row.get("gateway_status") != "parsed":
            raise ValueError("retired V3 outcome binding differs")
        reader = _retired_reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        payload = reader.terminal_parsed_payload(identity)
        if inventory.gateway_status != "parsed" or payload is None:
            raise ValueError("retired V3 terminal is not parsed")
        if probe["kind"] == "relation":
            accepted = sorted(_relation_rows(payload), key=_relation_cell_key) == sorted(
                _relation_rows(probe["expected"]), key=_relation_cell_key
            )
            relation_accepted += int(accepted)
        else:
            accepted = payload == legacy_source_payload(probe["expected"])
            source_accepted += int(accepted)
            if not accepted:
                classification = _scope_only_difference(payload, probe["expected"])
                if classification is None:
                    raise ValueError("retired V3 source failure is not scope-only")
                source_failed += 1
                part_omissions += int(classification[0])
                claim_omissions += int(classification[1])
        if row.get("accepted") is not accepted:
            raise ValueError("retired V3 receipt acceptance differs from terminal payload")
    if (source_accepted, source_failed, relation_accepted, part_omissions, claim_omissions) != (
        7,
        14,
        12,
        14,
        12,
    ):
        raise ValueError("retired V3 failure classification differs")
    return verify_failure_closeout(root)


def build_protocol(root: Path) -> dict[str, Any]:
    design = build_design(root)
    failure = verify_failure_closeout(root)
    return seal(
        {
            "schema_version": VERSION,
            "predecessor_closeout_sha256": PREDECESSOR_CLOSEOUT,
            "design_sha256": design["design_sha256"],
            "failed_qualification_closeout_sha256": failure["closeout_sha256"],
            "failed_qualification_receipt_sha256": failure["receipt_sha256"],
            "failed_qualification_terminal_store_sha256": failure["terminal_store_sha256"],
            "source_slot_count": design["source_slot_count"],
            "source_claim_instance_count": design["source_claim_instance_count"],
            "prospective_relation_request_count": design["relation_request_count"],
            "distinct_target_text_count": design["distinct_target_text_count"],
            "capacity_counts": design["capacity_assignment"]["counts"],
            "capacity_passed": design["capacity_assignment"]["passed"],
            "implementation_bindings": implementation_bindings(root),
            "model_snapshot": MODEL_SNAPSHOT,
            "maximum_output_tokens": 2048,
            "maximum_attempts": 2,
            "minimum_provider_interval_ms": 1000,
            "source_qualification_request_count": 21,
            "relation_qualification_request_count": 12,
            "sample_target": 200,
            "automatic_label_quota": 50,
            "maximum_claims_per_family_per_label": 5,
            "maximum_claims_per_output_per_label": 2,
            "global_canonical_text_uniqueness_required": True,
            "qualification_requires_all_parsed_and_exact": True,
            "source_provider_schema_version": "claim-source-measurement-output/1",
            "source_text_rendering": "deterministic_after_exact_reading_validation",
            "prior_qualification_rerun_forbidden": True,
            "cohort_success_not_guaranteed_by_qualification": True,
            "scientific_amendment_after_development_observations": True,
            "scientific_scope_review_required_before_cohort": True,
            "variant_superiority_claims_permitted": False,
            "free_form_diagnosis_generalization_permitted": False,
            "synthetic_only_qualification": True,
            "rate_basis": "frozen USD 2 input / 8 output per million, not a realized bill",
            **FALSE_FLAGS,
        },
        "protocol_sha256",
    )


def verify_protocol(root: Path) -> dict[str, Any]:
    tracked = read_document(root / PROTOCOL_PATH, "protocol_sha256")
    expected = build_protocol(root)
    if tracked != expected or not tracked["capacity_passed"]:
        raise ValueError("V3 protocol or source capacity differs from freeze")
    return tracked


def outbound(probe: dict[str, Any]) -> tuple[str, ModelVisibleEvidenceContext]:
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(probe["context"]))
    prompt = probe["prompt"]
    if probe["kind"] == "relation":
        prompt += "\nClaim and material parts: " + canonical_project_json(probe["claim"])
    return prompt, context


def source_roundtrip(root: Path, probe: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Exercise the real normalizer too; synthetic probes are never study entries."""
    if not accept_source(payload, probe["expected"]):
        return False
    rendered = render_source_payload(payload, probe["expected"])
    census = ClaimCorpusRequestCensus.model_validate_json((root / REQUEST_CENSUS_PATH).read_bytes())
    request = next(r for r in census.primary_requests if r.variant == probe["variant"])
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(probe["context"]))
    try:
        output = normalize_provider_output_v2(
            request,
            rendered,
            source_record_sha256=digest({"provider_payload": payload, "rendered": rendered}),
            visible_evidence_ids=[i.evidence_id for i in context.items],
        )
    except ValueError:
        return False
    return [c.claim_text for c in output.atomic_claims] == [
        c["claim_text"] for c in probe["expected"]
    ]


def build_plan(
    root: Path, predecessor: Path | None, *, source_commit: str | None = None
) -> dict[str, Any]:
    protocol = verify_protocol(root)
    closeout_sha = (
        load_v2_extraction_closeout(predecessor).closeout_sha256
        if predecessor is not None
        else PREDECESSOR_CLOSEOUT
    )
    if closeout_sha != PREDECESSOR_CLOSEOUT:
        raise ValueError("V3 requires the exact immutable V2 extraction closeout")
    if importlib.metadata.version("tiktoken") != "0.14.0":
        raise ValueError("qualification tokenizer differs from freeze")
    probes = build_probes(root)
    encoding = tiktoken.get_encoding("o200k_base")
    message_tokens = 0
    schema_tokens = 0
    for probe in probes:
        prompt, context = outbound(probe)
        message_tokens += _chat_tokens(
            encoding, prompt, canonical_project_json(context.model_payload())
        )
        schema = canonical_project_json(probe["schema"])
        schema_tokens += len(encoding.encode(schema))
    # Includes every maximum output on both attempts, schemas and wrapper allowance.
    input_ceiling = 2 * (message_tokens + schema_tokens + 512 * len(probes))
    cost_microusd = input_ceiling * 2 + len(probes) * 2048 * 2 * 8
    state = inspect_repository_state(root)
    return seal(
        {
            "schema_version": "claim-support-v3-qualification-plan/2",
            "source_commit_ref": source_commit or state.head_commit,
            "protocol_sha256": protocol["protocol_sha256"],
            "predecessor_closeout_sha256": closeout_sha,
            "probe_census_sha256": digest(probes),
            "request_count": len(probes),
            "source_request_count": 21,
            "relation_request_count": 12,
            "message_input_tokens": message_tokens,
            "wire_schema_tokens": schema_tokens,
            "estimated_upper_cost_usd": cost_microusd / 1_000_000,
            "cost_estimate_includes_two_attempts": True,
            "synthetic_only": True,
            "provider_calls_executed": False,
            **FALSE_FLAGS,
        },
        "plan_sha256",
    )


def rehearse(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    probes = build_probes(root)
    if digest(probes) != plan["probe_census_sha256"]:
        raise ValueError("qualification probe census mismatch")
    for probe in probes:
        context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(probe["context"]))
        if probe["kind"] == "source":
            valid = source_payload(probe["expected"])
            invalid = json.loads(json.dumps(valid))
            invalid["readings"][0]["value"] += "0"
            if not source_roundtrip(root, probe, valid) or accept_source(
                invalid, probe["expected"]
            ):
                raise ValueError("source rehearsal failed")
        else:
            reduce_relations(probe["expected"], 2, [i.evidence_id for i in context.items])
    # Same content in two scheduled cells must survive as different instances.
    a = source_instance_id(plan["protocol_sha256"], "1" * 64, "3" * 64, 1)
    b = source_instance_id(plan["protocol_sha256"], "2" * 64, "3" * 64, 1)
    if a == b:
        raise ValueError("source-instance collision")
    return seal(
        {
            "schema_version": "claim-support-v3-qualification-rehearsal/2",
            "plan_sha256": plan["plan_sha256"],
            "probe_count": len(probes),
            "same_content_distinct_request_identity": True,
            "source_and_relation_boundaries_exercised": True,
            "provider_calls_executed": False,
            **FALSE_FLAGS,
        },
        "rehearsal_sha256",
    )


def checked_run(root: Path, run: Path, predecessor: Path) -> Path:
    for path in (run, *run.parents):
        if path.is_symlink():
            raise ValueError("run destination contains a symlink")
    run = run.resolve()
    root = root.resolve()
    archive = predecessor.resolve().parent
    if any(run == p or run.is_relative_to(p) or p.is_relative_to(run) for p in (root, archive)):
        raise ValueError("qualification run overlaps repository or predecessor")
    allowed = {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
    if run.exists() and (
        not run.is_dir() or any(p.is_symlink() or p.name not in allowed for p in run.iterdir())
    ):
        raise ValueError("qualification directory contains unknown files or links")
    return run


def authorize(root: Path, run: Path, plan: dict[str, Any], ceiling: float) -> dict[str, Any]:
    state = inspect_repository_state(root)
    if plan != build_plan(root, None, source_commit=state.head_commit):
        raise ValueError("qualification plan differs from current frozen inputs")
    if not state.synchronized_main or state.head_commit != plan["source_commit_ref"]:
        raise ValueError("authorize requires clean synchronized main")
    if not math.isfinite(ceiling) or ceiling < plan["estimated_upper_cost_usd"]:
        raise ValueError("operator budget does not cover qualification")
    if run.exists() and any(run.iterdir()):
        raise ValueError("authorization requires a fresh empty destination")
    return seal(
        {
            "schema_version": "claim-support-v3-qualification-authorization/2",
            "plan_sha256": plan["plan_sha256"],
            "source_commit_ref": state.head_commit,
            "rehearsal_sha256": rehearse(plan, root)["rehearsal_sha256"],
            "destination_sha256": digest(run.as_posix()),
            "operator_cost_ceiling_usd": ceiling,
            "authorized_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "registered_attempts": 1,
            "credential_stored": False,
            **FALSE_FLAGS,
        },
        "authorization_sha256",
    )


def validate_authority(
    root: Path, run: Path, plan: dict[str, Any], auth: dict[str, Any], *, completed: bool = False
) -> None:
    state = inspect_repository_state(root)
    if auth.get("authorization_sha256") != digest(
        {k: v for k, v in auth.items() if k != "authorization_sha256"}
    ):
        raise ValueError("qualification authorization hash mismatch")
    if plan != build_plan(root, None, source_commit=plan["source_commit_ref"]):
        raise ValueError("qualification plan differs from frozen inputs")
    conditions = (
        set(auth) == AUTHORIZATION_FIELDS,
        auth.get("schema_version") == "claim-support-v3-qualification-authorization/2",
        auth.get("credential_stored") is False,
        auth.get("plan_sha256") == plan["plan_sha256"],
        auth.get("source_commit_ref") == plan["source_commit_ref"],
        auth.get("destination_sha256") == digest(run.as_posix()),
        auth.get("rehearsal_sha256") == rehearse(plan, root)["rehearsal_sha256"],
        type(auth.get("operator_cost_ceiling_usd")) in (float, int),
        type(auth.get("registered_attempts")) is int and auth.get("registered_attempts") == 1,
        all(auth.get(key) is value for key, value in FALSE_FLAGS.items()),
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
    probes = build_probes(root)
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
    result = []
    for probe in probes:
        prompt, context = outbound(probe)
        variant = registry.require(probe["variant"])
        identity = digest({"protocol": plan["protocol_sha256"], "probe": probe["probe_sha256"]})
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
            family_id=_opaque("synthetic-v3"),
            mechanism_id=_opaque("measurement-calibration"),
            dataset_id=_opaque("synthetic-v3"),
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
            retry_policy_ref=_opaque({"version": VERSION, "attempts": 2}),
            timeout_ns=int(policy.timeout_seconds * 1_000_000_000),
            max_attempts=2,
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
    if len({x.request.initial_attempt.request_identity_sha256 for x in result}) != 33:
        raise ValueError("duplicate qualification identities")
    return tuple(result)
