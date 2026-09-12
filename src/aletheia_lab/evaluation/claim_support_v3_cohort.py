"""Offline planning and one-use authority for the V3.1 source cohort.

The successful synthetic qualification unlocks planning only.  This module
freezes all 360 authentic source slots before any new provider call and keeps
the prospective 240-cell relation phase outside this authority.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import tiktoken

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_validation_v2_qualification import _chat_tokens
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_design,
    source_prompt,
    source_response_schema,
)
from aletheia_lab.evaluation.claim_validation_v3_execution import (
    verify as verify_qualification_execution,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    build_plan as build_qualification_plan,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    read_document,
    seal,
    verify_protocol,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    rehearse as rehearse_qualification,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.project.identity import canonical_project_json, content_sha256

SOURCE_REQUEST_COUNT: Final = 360
MODEL_REQUEST_COUNT: Final = 315
DETERMINISTIC_REQUEST_COUNT: Final = 45
SOURCE_CLAIM_INSTANCE_COUNT: Final = 720
PROSPECTIVE_RELATION_REQUEST_COUNT: Final = 240
MAXIMUM_OUTPUT_TOKENS: Final = 2048
MAXIMUM_PROVIDER_ATTEMPTS: Final = 2
MINIMUM_PROVIDER_INTERVAL_MS: Final = 1000
INPUT_USD_PER_MILLION: Final = 2.0
OUTPUT_USD_PER_MILLION: Final = 8.0
RESPONSE_FORMAT_OVERHEAD_ALLOWANCE: Final = 512
TOKENIZER_VERSION: Final = "0.14.0"
MODEL_SNAPSHOT: Final = "gpt-4.1-2025-04-14"
_B0_PROMPT: Final = (
    "Emit the exact deterministic source-measurement payload for this visible context."
)
_ALLOWED_RUN_ENTRIES: Final = frozenset(
    {"authorization.json", "lease.json", "attempt-store", "receipt.json"}
)
_IMPLEMENTATION_PATHS: Final = (
    "src/aletheia_lab/evaluation/claim_support_v3_cohort.py",
    "src/aletheia_lab/evaluation/claim_support_v3_cohort_execution.py",
    "scripts/claim_support_validation_v3_cohort.py",
)
_FALSE_OUTCOME_FLAGS: Final = {
    "automatic_labels_generated": False,
    "claims_materialized": False,
    "blind_packets_generated": False,
    "human_annotations_collected": False,
    "main_or_sealed_outcomes_opened": False,
    "admitted_to_corpus": False,
}
_AUTHORIZATION_FIELDS: Final = {
    "schema_version",
    "authorized_at",
    "source_commit_ref",
    "plan_sha256",
    "rehearsal_sha256",
    "qualification_receipt_sha256",
    "request_census_sha256",
    "request_projection_census_sha256",
    "source_request_count",
    "model_request_count",
    "deterministic_request_count",
    "prospective_relation_request_count",
    "estimated_upper_cost_usd",
    "operator_cost_ceiling_usd",
    "destination_sha256",
    "registered_attempts",
    "execution_phase",
    "source_cohort_execution_authorized",
    "relation_execution_authorized",
    "credential_stored",
    "source_measurements_collected",
    "provider_calls_executed",
    "authorization_ref",
    "authorization_sha256",
    *_FALSE_OUTCOME_FLAGS,
}


class ClaimSupportV3CohortError(ValueError):
    """Raised when source-cohort work differs from its prospective freeze."""


def _assert_exact_fields(payload: dict[str, Any], fields: set[str], label: str) -> None:
    if set(payload) != fields:
        raise ClaimSupportV3CohortError(f"{label} fields differ from contract")


def _implementation_bindings(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in _IMPLEMENTATION_PATHS:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ClaimSupportV3CohortError(
                "V3 source cohort implementation is incomplete or linked"
            )
        result[relative] = content_sha256(path.read_bytes())
    return result


def checked_cohort_run(
    root: Path,
    run: Path,
    qualification_run: Path,
    predecessor_closeout: Path,
) -> Path:
    """Require a private, non-linked destination disjoint from all inputs."""

    expanded = run.expanduser()
    for path in (expanded, *expanded.parents):
        if path.is_symlink():
            raise ClaimSupportV3CohortError("cohort destination contains a symlink")
    resolved = expanded.resolve()
    protected = (
        root.resolve(),
        qualification_run.resolve(),
        predecessor_closeout.resolve().parent,
    )
    if any(
        resolved == path or resolved.is_relative_to(path) or path.is_relative_to(resolved)
        for path in protected
    ):
        raise ClaimSupportV3CohortError("cohort destination overlaps repository or immutable input")
    if resolved.exists() and (
        not resolved.is_dir()
        or any(
            item.is_symlink() or item.name not in _ALLOWED_RUN_ENTRIES
            for item in resolved.iterdir()
        )
    ):
        raise ClaimSupportV3CohortError("cohort destination contains unknown files or links")
    return resolved


def load_verified_qualification(
    root: Path,
    predecessor_closeout: Path,
    qualification_run: Path,
) -> dict[str, Any]:
    """Independently replay the exact 33/33 V3.1 qualification pass."""

    run = qualification_run.resolve()
    if run.is_symlink() or not run.is_dir():
        raise ClaimSupportV3CohortError("qualification run is unavailable")
    try:
        authority = read_document(run / "authorization.json", "authorization_sha256")
        plan = build_qualification_plan(
            root,
            predecessor_closeout,
            source_commit=authority["source_commit_ref"],
        )
        receipt = verify_qualification_execution(root, run, plan, authority)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ClaimSupportV3CohortError(
            "qualification receipt did not replay from its immutable terminal store"
        ) from exc
    required = (
        receipt.get("schema_version") == "claim-support-v3-qualification-receipt/2",
        receipt.get("status") == "v3_1_qualification_passed",
        receipt.get("terminal_request_count") == 33,
        receipt.get("parsed_count") == 33,
        receipt.get("accepted_count") == 33,
        receipt.get("technical_failure_count") == 0,
        receipt.get("semantic_failure_count") == 0,
        receipt.get("semantic_issue_counts") == {},
        receipt.get("cohort_planning_unlocked") is True,
        receipt.get("synthetic_only") is True,
        receipt.get("rerun_forbidden") is True,
        receipt.get("admitted_to_corpus") is False,
    )
    if not all(required):
        raise ClaimSupportV3CohortError(
            "qualification result does not unlock the V3.1 source cohort"
        )
    return receipt


def _prompt_and_schema(freeze: Any, slot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    schedule = slot["source_schedule"]
    prompt = (
        _B0_PROMPT
        if schedule["execution_route"] == "deterministic_local"
        else "\n\n".join(
            (
                freeze.prompt_policies[schedule["variant"]].instruction_contract,
                source_prompt(slot["expected"], synthetic=False),
            )
        )
    )
    return prompt, source_response_schema(slot["expected"])


def source_request_projections(root: Path) -> tuple[dict[str, Any], ...]:
    """Build the exact provider-independent census and token accounting."""

    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ClaimSupportV3CohortError("cohort tokenizer differs from freeze")
    protocol = verify_protocol(root)
    design = build_design(root)
    if design["design_sha256"] != protocol["design_sha256"]:
        raise ClaimSupportV3CohortError("V3.1 design differs from tracked protocol")
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    encoding = tiktoken.get_encoding("o200k_base")
    rows: list[dict[str, Any]] = []
    for slot in design["slots"]:
        schedule = slot["source_schedule"]
        prompt, schema = _prompt_and_schema(freeze, slot)
        local = schedule["execution_route"] == "deterministic_local"
        request_sha = content_sha256(
            canonical_project_json(
                {
                    "protocol_sha256": protocol["protocol_sha256"],
                    "slot_sha256": slot["slot_sha256"],
                    "phase": "v3_1_source_cohort",
                }
            ).encode("utf-8")
        )
        context_json = canonical_project_json(slot["context"])
        schema_json = canonical_project_json(schema)
        payload = {
            "sequence": schedule["sequence"],
            "schedule_round": schedule["schedule_round"],
            "source_request_sha256": request_sha,
            "predecessor_request_sha256": schedule["v2_request_sha256"],
            "slot_sha256": slot["slot_sha256"],
            "family_id": schedule["family_id"],
            "evidence_condition": schedule["evidence_condition"],
            "variant": schedule["variant"],
            "execution_route": schedule["execution_route"],
            "visible_context_sha256": schedule["visible_context_sha256"],
            "prompt_sha256": content_sha256(prompt.encode("utf-8")),
            "response_schema_sha256": content_sha256(schema_json.encode("utf-8")),
            "exact_message_input_token_count": (
                0 if local else _chat_tokens(encoding, prompt, context_json)
            ),
            "exact_response_schema_token_count": (
                0 if local else len(encoding.encode(schema_json))
            ),
            "maximum_output_tokens": 0 if local else MAXIMUM_OUTPUT_TOKENS,
            "maximum_attempts": 1 if local else MAXIMUM_PROVIDER_ATTEMPTS,
        }
        rows.append(seal(payload, "projection_sha256"))
    result = tuple(rows)
    if (
        len(result) != SOURCE_REQUEST_COUNT
        or [row["sequence"] for row in result] != list(range(1, SOURCE_REQUEST_COUNT + 1))
        or len({row["source_request_sha256"] for row in result}) != SOURCE_REQUEST_COUNT
        or sum(row["execution_route"] == "model_gateway" for row in result) != MODEL_REQUEST_COUNT
        or sum(row["execution_route"] == "deterministic_local" for row in result)
        != DETERMINISTIC_REQUEST_COUNT
        or any(
            (row["variant"] == "B0") != (row["execution_route"] == "deterministic_local")
            for row in result
        )
    ):
        raise ClaimSupportV3CohortError("source request census differs from freeze")
    return result


def _validate_qualification_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict) or any(
        (
            receipt.get("status") != "v3_1_qualification_passed",
            receipt.get("terminal_request_count") != 33,
            receipt.get("parsed_count") != 33,
            receipt.get("accepted_count") != 33,
            receipt.get("technical_failure_count") != 0,
            receipt.get("semantic_failure_count") != 0,
            receipt.get("cohort_planning_unlocked") is not True,
            receipt.get("receipt_sha256") is None,
            receipt.get("terminal_store_sha256") is None,
        )
    ):
        raise ClaimSupportV3CohortError("qualification receipt is not an exact pass")


def build_cohort_plan(
    root: Path,
    qualification_receipt: dict[str, Any],
    *,
    source_commit_ref: str,
) -> dict[str, Any]:
    """Freeze all source requests, implementation hashes and upper cost."""

    _validate_qualification_receipt(qualification_receipt)
    protocol = verify_protocol(root)
    design = build_design(root)
    projections = source_request_projections(root)
    projection_hashes = [row["projection_sha256"] for row in projections]
    request_hashes = [row["source_request_sha256"] for row in projections]
    message_tokens = sum(row["exact_message_input_token_count"] for row in projections)
    schema_tokens = sum(row["exact_response_schema_token_count"] for row in projections)
    conservative_input = MAXIMUM_PROVIDER_ATTEMPTS * (
        message_tokens + schema_tokens + MODEL_REQUEST_COUNT * RESPONSE_FORMAT_OVERHEAD_ALLOWANCE
    )
    output_ceiling = MODEL_REQUEST_COUNT * MAXIMUM_OUTPUT_TOKENS * MAXIMUM_PROVIDER_ATTEMPTS
    estimated_cost = round(
        conservative_input * INPUT_USD_PER_MILLION / 1_000_000
        + output_ceiling * OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )
    payload = {
        "schema_version": "claim-support-v3-source-cohort-plan/1",
        "source_commit_ref": source_commit_ref,
        "protocol_sha256": protocol["protocol_sha256"],
        "design_sha256": design["design_sha256"],
        "implementation_bindings": _implementation_bindings(root),
        "qualification_authorization_sha256": qualification_receipt["authorization_sha256"],
        "qualification_receipt_sha256": qualification_receipt["receipt_sha256"],
        "qualification_terminal_store_sha256": qualification_receipt["terminal_store_sha256"],
        "qualification_source_commit_ref": qualification_receipt["source_commit_ref"],
        "qualification_request_count": 33,
        "qualification_accepted_count": 33,
        "source_request_count": SOURCE_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "source_claim_instance_count": SOURCE_CLAIM_INSTANCE_COUNT,
        "prospective_relation_request_count": PROSPECTIVE_RELATION_REQUEST_COUNT,
        "request_census_sha256": content_sha256(
            canonical_project_json(request_hashes).encode("utf-8")
        ),
        "request_projection_census_sha256": content_sha256(
            canonical_project_json(projection_hashes).encode("utf-8")
        ),
        "request_projection_sha256s": projection_hashes,
        "model": "gpt-4.1",
        "model_snapshot": MODEL_SNAPSHOT,
        "maximum_output_tokens_per_model_request": MAXIMUM_OUTPUT_TOKENS,
        "maximum_provider_attempts_per_request": MAXIMUM_PROVIDER_ATTEMPTS,
        "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
        "retry_initial_backoff_ms": 5000,
        "retry_backoff_multiplier": 2,
        "retry_backoff_ceiling_ms": 60000,
        "retry_after_ceiling_ms": 60000,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_encoding": "o200k_base",
        "exact_message_input_token_count": message_tokens,
        "exact_response_schema_token_count": schema_tokens,
        "provider_overhead_allowance_tokens_per_call": (RESPONSE_FORMAT_OVERHEAD_ALLOWANCE),
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output_ceiling,
        "provider_billed_input_tokens_known": False,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "estimated_upper_cost_usd": estimated_cost,
        "qualification_pass_receipt_bound": True,
        "all_source_slots_frozen_before_execution": True,
        "failures_preserved_without_adaptive_replacement": True,
        "relation_execution_authorized": False,
        "source_measurements_collected": False,
        "provider_calls_executed": False,
        **_FALSE_OUTCOME_FLAGS,
    }
    return seal(payload, "plan_sha256")


def rehearse_cohort(
    root: Path,
    plan: dict[str, Any],
    qualification_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild every source projection and exercise deterministic payloads."""

    expected = build_cohort_plan(
        root,
        qualification_receipt,
        source_commit_ref=plan["source_commit_ref"],
    )
    if plan != expected:
        raise ClaimSupportV3CohortError("source cohort rehearsal differs from plan")
    design = build_design(root)
    from aletheia_lab.evaluation.claim_validation_v3_design import (
        render_source_payload,
        source_payload,
    )

    for slot in design["slots"]:
        render_source_payload(source_payload(slot["expected"]), slot["expected"])
    payload = {
        "schema_version": "claim-support-v3-source-cohort-rehearsal/1",
        "status": "v3_1_source_cohort_rehearsal_passed",
        "plan_sha256": plan["plan_sha256"],
        "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
        "request_census_sha256": plan["request_census_sha256"],
        "request_projection_census_sha256": plan["request_projection_census_sha256"],
        "source_request_count": SOURCE_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "source_claim_instance_count": SOURCE_CLAIM_INSTANCE_COUNT,
        "prospective_relation_request_count": PROSPECTIVE_RELATION_REQUEST_COUNT,
        "exact_request_projections_rebuilt": True,
        "all_deterministic_payloads_roundtrip": True,
        "all_model_requests_share_budget": True,
        "provider_visible_prompts_exclude_evaluator_values": True,
        "relation_requests_not_constructed": True,
        "failures_preserved_without_adaptive_replacement": True,
        "authorization_created": False,
        "source_measurements_collected": False,
        "provider_calls_executed": False,
        **_FALSE_OUTCOME_FLAGS,
    }
    return seal(payload, "rehearsal_sha256")


def _destination_sha256(run: Path) -> str:
    return content_sha256(
        canonical_project_json({"private_v3_1_source_cohort_run_directory": run.as_posix()}).encode(
            "utf-8"
        )
    )


def build_authorization(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    *,
    repository_state: RepositoryExecutionState,
    operator_cost_ceiling_usd: float,
    authorized_at: str | None = None,
) -> dict[str, Any]:
    """Grant exactly one source-cohort attempt; never relation execution."""

    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan["source_commit_ref"]
        or rehearsal["plan_sha256"] != plan["plan_sha256"]
        or not math.isfinite(operator_cost_ceiling_usd)
        or operator_cost_ceiling_usd < plan["estimated_upper_cost_usd"]
        or (run.exists() and any(run.iterdir()))
    ):
        raise ClaimSupportV3CohortError(
            "authorization requires a fresh destination and clean synchronized main"
        )
    payload = {
        "schema_version": "claim-support-v3-source-cohort-authorization/1",
        "authorized_at": authorized_at
        or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_commit_ref": plan["source_commit_ref"],
        "plan_sha256": plan["plan_sha256"],
        "rehearsal_sha256": rehearsal["rehearsal_sha256"],
        "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
        "request_census_sha256": plan["request_census_sha256"],
        "request_projection_census_sha256": plan["request_projection_census_sha256"],
        "source_request_count": SOURCE_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "prospective_relation_request_count": PROSPECTIVE_RELATION_REQUEST_COUNT,
        "estimated_upper_cost_usd": plan["estimated_upper_cost_usd"],
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "destination_sha256": _destination_sha256(run),
        "registered_attempts": 1,
        "execution_phase": "v3_1_source_cohort",
        "source_cohort_execution_authorized": True,
        "relation_execution_authorized": False,
        "credential_stored": False,
        "source_measurements_collected": False,
        "provider_calls_executed": False,
        **_FALSE_OUTCOME_FLAGS,
    }
    digest = content_sha256(canonical_project_json(payload).encode("utf-8"))
    return {
        **payload,
        "authorization_ref": f"ev-{digest}",
        "authorization_sha256": digest,
    }


def validate_authorization(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    authorization: dict[str, Any],
    *,
    repository_state: RepositoryExecutionState,
    completed: bool = False,
) -> None:
    """Fail closed on a changed plan, destination, budget or repository."""

    _assert_exact_fields(authorization, _AUTHORIZATION_FIELDS, "source cohort authorization")
    digest = content_sha256(
        canonical_project_json(
            {
                key: value
                for key, value in authorization.items()
                if key not in {"authorization_ref", "authorization_sha256"}
            }
        ).encode("utf-8")
    )
    authorized_at = authorization.get("authorized_at")
    try:
        timestamp_valid = (
            isinstance(authorized_at, str)
            and datetime.fromisoformat(authorized_at.replace("Z", "+00:00")).tzinfo is not None
        )
    except ValueError:
        timestamp_valid = False
    source_commit_ref = authorization.get("source_commit_ref")
    registered_attempts = authorization.get("registered_attempts")
    operator_cost_ceiling = authorization.get("operator_cost_ceiling_usd")
    estimated_upper_cost = authorization.get("estimated_upper_cost_usd")
    required = (
        authorization.get("authorization_sha256") == digest,
        authorization.get("authorization_ref") == f"ev-{digest}",
        authorization.get("schema_version") == "claim-support-v3-source-cohort-authorization/1",
        timestamp_valid,
        isinstance(source_commit_ref, str),
        re.fullmatch(r"[0-9a-f]{40}", source_commit_ref or "") is not None,
        source_commit_ref == plan["source_commit_ref"],
        authorization.get("plan_sha256") == plan["plan_sha256"],
        authorization.get("rehearsal_sha256") == rehearsal["rehearsal_sha256"],
        authorization.get("qualification_receipt_sha256") == plan["qualification_receipt_sha256"],
        authorization.get("request_census_sha256") == plan["request_census_sha256"],
        authorization.get("request_projection_census_sha256")
        == plan["request_projection_census_sha256"],
        authorization.get("destination_sha256") == _destination_sha256(run),
        type(registered_attempts) is int,
        registered_attempts == 1,
        type(authorization.get("source_request_count")) is int,
        authorization.get("source_request_count") == SOURCE_REQUEST_COUNT,
        type(authorization.get("model_request_count")) is int,
        authorization.get("model_request_count") == MODEL_REQUEST_COUNT,
        type(authorization.get("deterministic_request_count")) is int,
        authorization.get("deterministic_request_count") == DETERMINISTIC_REQUEST_COUNT,
        type(authorization.get("prospective_relation_request_count")) is int,
        authorization.get("prospective_relation_request_count")
        == PROSPECTIVE_RELATION_REQUEST_COUNT,
        type(estimated_upper_cost) is float,
        estimated_upper_cost == plan["estimated_upper_cost_usd"],
        authorization.get("execution_phase") == "v3_1_source_cohort",
        authorization.get("source_cohort_execution_authorized") is True,
        authorization.get("relation_execution_authorized") is False,
        authorization.get("credential_stored") is False,
        authorization.get("source_measurements_collected") is False,
        authorization.get("provider_calls_executed") is False,
        type(operator_cost_ceiling) is float,
        isinstance(operator_cost_ceiling, float)
        and math.isfinite(operator_cost_ceiling)
        and operator_cost_ceiling >= plan["estimated_upper_cost_usd"],
        all(authorization.get(key) is value for key, value in _FALSE_OUTCOME_FLAGS.items()),
    )
    if not all(required):
        raise ClaimSupportV3CohortError("source cohort authorization differs")
    if not completed and (
        not repository_state.synchronized_main
        or repository_state.head_commit != authorization["source_commit_ref"]
    ):
        raise ClaimSupportV3CohortError(
            "live execution requires the authorized clean synchronized main"
        )


def build_preflight(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
    authorization: dict[str, Any] | None,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
) -> dict[str, Any]:
    """Return a secret-free live gate without granting any new authority."""

    blockers: list[str] = []
    if not repository_state.synchronized_main:
        blockers.append("repository_not_clean_synchronized_main")
    if not credential_present:
        blockers.append("credential_missing")
    if authorization is None:
        blockers.append("authorization_pending")
    else:
        validate_authorization(
            root,
            run,
            plan,
            rehearsal,
            authorization,
            repository_state=repository_state,
        )
    ordered = sorted(set(blockers))
    payload = {
        "schema_version": "claim-support-v3-source-cohort-preflight/1",
        "status": (
            "v3_1_source_cohort_live_ready" if not ordered else "v3_1_source_cohort_live_blocked"
        ),
        "plan_sha256": plan["plan_sha256"],
        "rehearsal_sha256": rehearsal["rehearsal_sha256"],
        "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
        "source_commit_ref": plan["source_commit_ref"],
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_present": credential_present,
        "source_request_count": SOURCE_REQUEST_COUNT,
        "model_request_count": MODEL_REQUEST_COUNT,
        "deterministic_request_count": DETERMINISTIC_REQUEST_COUNT,
        "source_claim_instance_count": SOURCE_CLAIM_INSTANCE_COUNT,
        "prospective_relation_request_count": PROSPECTIVE_RELATION_REQUEST_COUNT,
        "exact_message_input_token_count": plan["exact_message_input_token_count"],
        "exact_response_schema_token_count": plan["exact_response_schema_token_count"],
        "estimated_upper_cost_usd": plan["estimated_upper_cost_usd"],
        "operator_cost_ceiling_usd": (
            authorization["operator_cost_ceiling_usd"] if authorization else None
        ),
        "live_blockers": ordered,
        "source_measurements_collected": False,
        "provider_calls_executed": False,
        "relation_execution_authorized": False,
        **_FALSE_OUTCOME_FLAGS,
    }
    return seal(payload, "preflight_sha256")


def load_authorization(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("authorization is not a regular file")
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError("authorization is not an object")
        _assert_exact_fields(payload, _AUTHORIZATION_FIELDS, "source cohort authorization")
        digest = content_sha256(
            canonical_project_json(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"authorization_ref", "authorization_sha256"}
                }
            ).encode("utf-8")
        )
        if (
            payload.get("authorization_sha256") != digest
            or payload.get("authorization_ref") != f"ev-{digest}"
        ):
            raise ValueError("authorization hash mismatch")
        return payload
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise ClaimSupportV3CohortError("source cohort authorization is invalid") from exc


def qualification_rehearsal_is_stable(
    root: Path, predecessor_closeout: Path, qualification_run: Path
) -> bool:
    """Expose an audit assertion without opening any cohort outcome."""

    authority = read_document(qualification_run / "authorization.json", "authorization_sha256")
    plan = build_qualification_plan(
        root, predecessor_closeout, source_commit=authority["source_commit_ref"]
    )
    return rehearse_qualification(plan, root)["source_and_relation_boundaries_exercised"] is True


__all__ = [
    "ClaimSupportV3CohortError",
    "build_authorization",
    "build_cohort_plan",
    "build_preflight",
    "checked_cohort_run",
    "load_authorization",
    "load_verified_qualification",
    "qualification_rehearsal_is_stable",
    "rehearse_cohort",
    "source_request_projections",
    "validate_authorization",
]
