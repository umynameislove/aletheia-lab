"""Versioned CSR-03R output-budget amendment and failed-run evidence binding."""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation._attempt_store.integrity import AttemptStoreIntegrityVerifier
from aletheia_lab.evaluation.claim_corpus_reconciliation import _read_issue
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256

AMENDMENT_PATH: Final = (
    "configs/evaluation/claim_support_recovery_output_budget_amendment.json"
)
AMENDED_MAX_OUTPUT_TOKENS: Final = 2048
PRIOR_MAX_OUTPUT_TOKENS: Final = 600

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

# CSR-03R is retired, not overwritten by the CSR-03S transport amendment.
CSR03R_FAILURE: Final = {
    "failed_compatibility_authorization_sha256": "a547b7362ddbd978a87d723345a5d1b41e6be5d843ae8d73ec36ccb27190cbab",
    "failed_compatibility_receipt_sha256": "9161d0a992aea2feb29c200e8466c6059c0d593c04ca78e410e70fdaaa79f7f9",
    "failed_compatibility_store_sha256": "5bc30a3c1eb6e16719fab95a80dd03528456b2211911f5061b54cbddef5e32e7",
    "failed_compatibility_rehearsal_sha256": "2b9d93a1b03de2fe1ba848cb394bcc33d1dbc64d0b42b351e271e14e1d9ccb3e",
    "failed_compatibility_source_commit_ref": "bdc794f02b29515a02a0ce28609ac53e7d478bd2",
    "predecessor_protocol_sha256": "71f0a9b311f2d93f1d6b48399b2509cc8c4f5ac5b5c95251b07bbc04a3164e0e",
    "prior_maximum_output_tokens": 2048,
}


class RecoveryOutputBudgetAmendment(BaseModel):
    """Outcome-blind technical amendment justified by the retired probe failure."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    schema_version: Literal["claim-corpus-recovery-output-budget-amendment/v1"]
    status: Literal["recovery_output_budget_amendment_frozen"]
    amendment_id: Literal["claim-support-recovery-csr-03r"]
    predecessor_protocol_sha256: Sha256
    failed_compatibility_authorization_sha256: Sha256
    failed_compatibility_receipt_sha256: Sha256
    failed_compatibility_store_sha256: Sha256
    failed_compatibility_rehearsal_sha256: Sha256
    failed_compatibility_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    failed_probe_count: Literal[3]
    failed_probe_issue_code: Literal["provider_output_truncated"]
    failure_inference: Literal[
        "all_synthetic_probes_reached_the_registered_output_cap_before_a_complete_structured_response"
    ]
    exact_truncated_output_content_known: Literal[False]
    prior_maximum_output_tokens: Literal[600]
    amended_maximum_output_tokens: Literal[2048]
    budget_multiplier: float
    budget_rationale: Literal[
        "bounded_technical_headroom_after_three_of_three_synthetic_probes_hit_the_prior_cap"
    ]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    scheduled_model_backed_variants: tuple[
        Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"], ...
    ]
    output_budget_uniform_across_model_variants: Literal[True]
    request_census_unchanged: Literal[True]
    observed_evidence_unchanged: Literal[True]
    prompt_semantics_unchanged: Literal[True]
    response_schema_unchanged: Literal[True]
    variant_semantics_unchanged: Literal[True]
    model_and_snapshot_unchanged: Literal[True]
    selection_policy_unchanged: Literal[True]
    failed_attempt_retired: Literal[True]
    failed_attempt_preserved: Literal[True]
    new_authorization_required: Literal[True]
    compatibility_must_pass_before_diagnosis: Literal[True]
    provider_calls_executed: Literal[False]
    claims_materialized: Literal[False]
    automatic_labels_generated: Literal[False]
    blind_packets_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    amendment_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"amendment_sha256"})

    @property
    def recovery_protocol_sha256(self) -> str:
        return canonical_execution_sha256(
            {
                "schema_version": "claim-corpus-recovery-protocol/v2",
                "predecessor_protocol_sha256": self.predecessor_protocol_sha256,
                "output_budget_amendment_sha256": self.amendment_sha256,
            }
        )

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.budget_multiplier != AMENDED_MAX_OUTPUT_TOKENS / PRIOR_MAX_OUTPUT_TOKENS:
            raise ValueError("output-budget multiplier differs")
        if self.scheduled_model_backed_variants != (
            "A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"
        ):
            raise ValueError("model-backed variant census differs")
        if self.amendment_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("output-budget amendment identity differs")
        return self


def load_recovery_output_budget_amendment(root: Path) -> RecoveryOutputBudgetAmendment:
    return RecoveryOutputBudgetAmendment.model_validate_json(
        (root / AMENDMENT_PATH).read_bytes()
    )


def audit_retired_compatibility_run(root: Path, run_dir: Path) -> dict[str, object]:
    """Verify the immutable CSR-03 failure without rebuilding or rerunning it."""

    amendment = load_recovery_output_budget_amendment(root)
    return _audit_retired_run(run_dir, amendment.model_dump(mode="json"))


def audit_retired_structured_output_run(root: Path, run_dir: Path) -> dict[str, object]:
    """Independently verify the second failure using pinned identities, never current requests."""
    load_recovery_output_budget_amendment(root)
    return _audit_retired_run(run_dir, dict(CSR03R_FAILURE))


def _audit_retired_run(run_dir: Path, expected: dict[str, object]) -> dict[str, object]:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("retired compatibility run must be a real directory")
    expected_members = {
        "compatibility-authorization.json",
        "compatibility-lease.json",
        "compatibility-receipt.json",
        "compatibility-store",
    }
    if {item.name for item in run_dir.iterdir()} != expected_members:
        raise ValueError("retired compatibility run membership differs")
    if any(item.is_symlink() for item in run_dir.iterdir()):
        raise ValueError("retired compatibility run contains a symbolic link")

    authorization = _json_object(run_dir / "compatibility-authorization.json")
    authorization_sha = authorization.get("authorization_sha256")
    authorization_identity = dict(authorization)
    authorization_identity.pop("authorization_sha256", None)
    if (
        authorization_sha != canonical_execution_sha256(authorization_identity)
        or authorization_sha != expected["failed_compatibility_authorization_sha256"]
        or authorization.get("maximum_output_tokens_per_model_request")
        != expected["prior_maximum_output_tokens"]
        or authorization.get("protocol_sha256") != expected["predecessor_protocol_sha256"]
        or authorization.get("rehearsal_sha256")
        != expected["failed_compatibility_rehearsal_sha256"]
        or authorization.get("source_commit_ref")
        != expected["failed_compatibility_source_commit_ref"]
        or authorization.get("phase") != "compatibility"
    ):
        raise ValueError("retired compatibility authorization differs")

    lease = _json_object(run_dir / "compatibility-lease.json")
    if lease != {
        "schema_version": "claim-corpus-recovery-lease/v1",
        "authorization_sha256": authorization_sha,
        "destination_sha256": authorization.get("destination_sha256"),
        "phase": "compatibility",
        "registered_attempts": 1,
    }:
        raise ValueError("retired compatibility lease differs")

    store = run_dir / "compatibility-store"
    store_sha, status_counts, issue_counts, attempts = _audit_retired_store(store)
    if store_sha != expected["failed_compatibility_store_sha256"]:
        raise ValueError("retired compatibility store differs")

    receipt = _json_object(run_dir / "compatibility-receipt.json")
    receipt_sha = receipt.get("receipt_sha256")
    receipt_identity = dict(receipt)
    receipt_identity.pop("receipt_sha256", None)
    if (
        receipt_sha != canonical_execution_sha256(receipt_identity)
        or receipt_sha != expected["failed_compatibility_receipt_sha256"]
        or receipt.get("authorization_sha256") != authorization_sha
        or receipt.get("terminal_store_sha256") != store_sha
        or receipt.get("gateway_status_counts") != dict(sorted(status_counts.items()))
        or receipt.get("provider_issue_code_counts") != dict(sorted(issue_counts.items()))
        or receipt.get("technical_attempt_count") != attempts
        or receipt.get("status") != "recovery_compatibility_failed"
        or receipt.get("rerun_forbidden") is not True
        or receipt.get("synthetic_only") is not True
        or receipt.get("claims_materialized") is not False
        or receipt.get("main_or_sealed_outcomes_opened") is not False
    ):
        raise ValueError("retired compatibility receipt differs")

    payload: dict[str, object] = {
        "schema_version": "claim-corpus-retired-compatibility-audit/v1",
        "status": "retired_compatibility_failure_verified",
        "authorization_sha256": authorization_sha,
        "receipt_sha256": receipt_sha,
        "terminal_store_sha256": store_sha,
        "prior_maximum_output_tokens": expected["prior_maximum_output_tokens"],
        "terminal_request_count": sum(status_counts.values()),
        "gateway_status_counts": dict(sorted(status_counts.items())),
        "provider_issue_code_counts": dict(sorted(issue_counts.items())),
        "exact_truncated_output_content_known": False,
        "provider_calls_executed": False,
        "retired_run_modified": False,
        "rerun_performed": False,
    }
    return {**payload, "audit_sha256": canonical_execution_sha256(payload)}


def _audit_retired_store(
    store: Path,
) -> tuple[str, Counter[str], Counter[str], int]:
    if store.is_symlink() or not store.is_dir():
        raise ValueError("retired compatibility store is invalid")
    if {item.name for item in store.iterdir()} != {"authorities", "requests"}:
        raise ValueError("retired compatibility store membership differs")
    authorities = store / "authorities"
    requests = store / "requests"
    if any(path.is_symlink() or not path.is_dir() for path in (authorities, requests)):
        raise ValueError("retired compatibility store bucket is invalid")
    authority_paths = sorted(authorities.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in authority_paths):
        raise ValueError("retired compatibility authority is linked or invalid")
    shards = sorted(requests.iterdir())
    if len(shards) != 3 or {path.name for path in authority_paths} != {
        f"{shard.name}.json" for shard in shards
    }:
        raise ValueError("retired compatibility request census differs")
    statuses: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    attempts = 0
    shard_hashes: list[tuple[str, str]] = []
    for shard in shards:
        if shard.is_symlink() or not shard.is_dir():
            raise ValueError("retired compatibility shard is invalid")
        verifier = AttemptStoreIntegrityVerifier(
            root=shard,
            object_root=shard / "objects" / "sha256",
            request_root=shard / "requests",
            terminal_root=shard / "terminal",
            failure_root=shard / "failures",
        )
        shard_hashes.append((shard.name, verifier.store_sha256()))
        inventory = verifier._terminal_inventory(shard.name)
        statuses[inventory.gateway_status] += 1
        attempts += len(inventory.attempt_outcomes)
        issue = _read_issue(verifier, shard.name)
        if issue is None:
            raise ValueError("retired compatibility failure has no issue record")
        issues[issue.code] += 1
    identity = canonical_execution_sha256(
        {
            "schema_version": "claim-corpus-sharded-attempt-store/v1",
            "authorities": tuple(
                (path.stem, content_sha256(path.read_bytes())) for path in authority_paths
            ),
            "shards": tuple(shard_hashes),
        }
    )
    return identity, statuses, issues, attempts


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value
