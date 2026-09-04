"""Read-only reserve decision and terminal reconciliation for claim-corpus execution.

This boundary closes the registered request census without normalizing provider
outputs, extracting claims, assigning support labels, or creating human-rating
packets.  It never imports a provider adapter or reads credentials.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation._attempt_store.integrity import (
    AttemptStoreIntegrityVerifier,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionAuthorization,
    ClaimCorpusExecutionPlan,
    ScheduledClaimCorpusRequest,
    build_execution_plan,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusExecutionLease,
    ClaimCorpusLiveReceipt,
    ClaimCorpusRequestAuthority,
)
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import ClaimEvidenceBinding
from aletheia_lab.evaluation.execution_contracts import (
    TechnicalIssue,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway.contracts import AttemptOutcome, TerminalStatus
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    canonical_project_json,
    content_sha256,
)

RESERVE_DECISION_SCHEMA_VERSION: Final = "claim-corpus-reserve-decision/v1"
RECONCILIATION_SCHEMA_VERSION: Final = "claim-corpus-request-reconciliation/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Mechanism = Literal["data_drift", "preprocessing_mismatch", "label_noise"]
EvidenceCondition = Literal["full", "missing_key", "noisy"]
ExecutionRoute = Literal["deterministic_local", "model_gateway"]
Dimension = Literal["mechanism", "evidence_condition", "variant", "route"]


class _AuditRow(TypedDict):
    request_sha256: str
    request_identity_sha256: str
    authority_sha256: str
    inventory_sha256: str
    mechanism: Mechanism
    evidence_condition: EvidenceCondition
    variant: str
    route: ExecutionRoute
    status: TerminalStatus
    attempt_count: int
    issue_code: str | None
    issue_stage: str | None


@dataclass(frozen=True)
class _AuditedRequest:
    row: _AuditRow
    shard_sha256: str
    attempt_outcomes: tuple[AttemptOutcome, ...]
    provider_attempt_count: int
    failure_receipt_count: int


class ClaimCorpusReconciliationError(ValueError):
    """Raised when the private execution artifacts do not reconcile exactly."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class NamedCount(_StrictFrozenModel):
    """Canonical aggregate count with no response text or rater-visible metadata."""

    name: str = Field(min_length=1, max_length=128)
    count: int = Field(ge=0)


class ReconciliationSlice(_StrictFrozenModel):
    """Outcome coverage for one prespecified schedule dimension."""

    dimension: Dimension
    value: str = Field(min_length=1, max_length=128)
    expected_count: int = Field(gt=0)
    terminal_count: int = Field(gt=0)
    parsed_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    attempt_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _counts_reconcile(self) -> Self:
        if (
            self.expected_count != self.terminal_count
            or self.parsed_count + self.technical_failure_count != self.terminal_count
            or self.attempt_count < self.terminal_count
        ):
            raise ValueError("reconciliation slice counts do not reconcile")
        return self


class ClaimCorpusReserveDecisionReceipt(_StrictFrozenModel):
    """Prospective reserve rule applied without consulting response content."""

    schema_version: Literal["claim-corpus-reserve-decision/v1"] = (
        RESERVE_DECISION_SCHEMA_VERSION
    )
    status: Literal["claim_corpus_no_reserve_activation_required"]
    authorization_sha256: Sha256
    execution_plan_sha256: Sha256
    live_receipt_sha256: Sha256
    terminal_store_sha256: Sha256
    reserve_activation_rule: Literal["pre_execution_technical_ineligibility_only"]
    primary_request_count: Literal[360]
    primary_requests_started: Literal[360]
    primary_terminal_count: Literal[360]
    pre_execution_ineligible_family_count: Literal[0]
    provider_failure_count: int = Field(ge=0, le=315)
    provider_failures_eligible_for_reserve: Literal[False] = False
    activated_reserve_family_count: Literal[0] = 0
    replacement_request_count: Literal[0] = 0
    reserve_requests_executed: Literal[0] = 0
    outcome_driven_activation_forbidden: Literal[True] = True
    reserve_activation_performed: Literal[False] = False
    outputs_normalized: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("reserve-decision receipt identity does not match content")
        return self


class ClaimCorpusRequestReconciliationReceipt(_StrictFrozenModel):
    """Independent aggregate proof that every authorized request closed exactly once."""

    schema_version: Literal["claim-corpus-request-reconciliation/v1"] = (
        RECONCILIATION_SCHEMA_VERSION
    )
    status: Literal["claim_corpus_request_reconciliation_passed"]
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    authorization_sha256: Sha256
    execution_plan_sha256: Sha256
    observed_evidence_census_sha256: Sha256
    live_receipt_sha256: Sha256
    execution_lease_sha256: Sha256
    terminal_store_sha256: Sha256
    reserve_decision_sha256: Sha256
    expected_request_count: Literal[360]
    authority_count: Literal[360]
    request_shard_count: Literal[360]
    terminal_request_count: Literal[360]
    parsed_terminal_count: int = Field(ge=0, le=360)
    technical_failure_terminal_count: int = Field(ge=0, le=360)
    provider_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    provider_attempt_count: int = Field(ge=0, le=630)
    technical_attempt_count: int = Field(ge=360, le=675)
    missing_request_count: Literal[0] = 0
    duplicate_request_count: Literal[0] = 0
    unknown_request_count: Literal[0] = 0
    partial_request_count: Literal[0] = 0
    store_failure_receipt_count: Literal[0] = 0
    first_technical_failure_ordinal: int | None = Field(default=None, ge=1, le=360)
    last_technical_failure_ordinal: int | None = Field(default=None, ge=1, le=360)
    longest_consecutive_technical_failure_run: int = Field(ge=0, le=360)
    gateway_status_counts: tuple[NamedCount, ...]
    attempt_outcome_counts: tuple[NamedCount, ...]
    issue_code_counts: tuple[NamedCount, ...]
    issue_stage_counts: tuple[NamedCount, ...]
    slices: tuple[ReconciliationSlice, ...]
    request_inventory_sha256: Sha256
    store_integrity_verified: Literal[True] = True
    exact_request_coverage_verified: Literal[True] = True
    ready_for_output_normalization: Literal[True] = True
    outputs_normalized: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _counts_and_identity_reconcile(self) -> Self:
        if (
            self.parsed_terminal_count + self.technical_failure_terminal_count != 360
            or sum(item.count for item in self.gateway_status_counts) != 360
            or sum(item.count for item in self.attempt_outcome_counts)
            != self.technical_attempt_count
        ):
            raise ValueError("request reconciliation aggregate counts do not reconcile")
        for items in (
            self.gateway_status_counts,
            self.attempt_outcome_counts,
            self.issue_code_counts,
            self.issue_stage_counts,
        ):
            names = tuple(item.name for item in items)
            if names != tuple(sorted(set(names))):
                raise ValueError("reconciliation aggregate names must be canonical and unique")
        slice_keys = tuple((item.dimension, item.value) for item in self.slices)
        if slice_keys != tuple(sorted(set(slice_keys))):
            raise ValueError("reconciliation slices must be canonical and unique")
        failure_ordinals = (
            self.first_technical_failure_ordinal,
            self.last_technical_failure_ordinal,
        )
        if self.technical_failure_terminal_count == 0:
            if failure_ordinals != (None, None) or self.longest_consecutive_technical_failure_run:
                raise ValueError("failure-run metadata exists without failures")
        elif (
            any(value is None for value in failure_ordinals)
            or self.longest_consecutive_technical_failure_run == 0
        ):
            raise ValueError("technical failures require complete schedule metadata")
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("reconciliation receipt identity does not match content")
        return self


def _regular_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ClaimCorpusReconciliationError(f"{label} is unavailable") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise ClaimCorpusReconciliationError(f"{label} must be a real directory")
    return resolved


def _load_census(root: Path) -> ClaimCorpusRequestCensus:
    try:
        return ClaimCorpusRequestCensus.model_validate_json(
            (root / REQUEST_CENSUS_PATH).read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusReconciliationError("request census is unavailable or invalid") from exc


def _canonical_counts(counter: Counter[str]) -> tuple[NamedCount, ...]:
    return tuple(NamedCount(name=name, count=counter[name]) for name in sorted(counter))


def _longest_failure_run(statuses: tuple[TerminalStatus, ...]) -> int:
    longest = 0
    current = 0
    for status in statuses:
        if status == "parsed":
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _read_issue(
    verifier: AttemptStoreIntegrityVerifier,
    identity: str,
) -> TechnicalIssue | None:
    entries, _ = verifier._load_chain(identity)  # independent read-only verifier
    outcome = next((item for item in entries if item.state == "parsed_or_failed"), None)
    if outcome is None:
        raise ClaimCorpusReconciliationError("terminal shard has no outcome entry")
    if outcome.issue_object_sha256 is None:
        return None
    try:
        return TechnicalIssue.model_validate_json(
            verifier._read_object(outcome.issue_object_sha256)
        )
    except ValidationError as exc:
        raise ClaimCorpusReconciliationError("terminal technical issue is invalid") from exc


def _verify_authority_bytes(path: Path) -> ClaimCorpusRequestAuthority:
    try:
        payload = path.read_bytes()
        authority = ClaimCorpusRequestAuthority.model_validate_json(payload)
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusReconciliationError("request authority is invalid") from exc
    expected = (
        canonical_project_json(authority.model_dump(mode="json")) + "\n"
    ).encode("utf-8")
    if payload != expected:
        raise ClaimCorpusReconciliationError("request authority serialization is not canonical")
    return authority


def _verify_top_level_store(store_root: Path) -> tuple[Path, Path]:
    root = _regular_directory(store_root, label="attempt store")
    members = {path.name: path for path in root.iterdir()}
    if set(members) != {"authorities", "requests"}:
        raise ClaimCorpusReconciliationError("attempt store root membership differs")
    return (
        _regular_directory(members["authorities"], label="authority store"),
        _regular_directory(members["requests"], label="request-shard store"),
    )


def _validate_authority_chain(
    *,
    store_root: Path,
    plan: ClaimCorpusExecutionPlan,
    authorization: ClaimCorpusExecutionAuthorization,
    lease: ClaimCorpusExecutionLease,
    live_receipt: ClaimCorpusLiveReceipt,
    evidence_census: ObservedEvidenceCensus,
) -> None:
    mismatches = (
        authorization.execution_plan_sha256 != plan.plan_sha256,
        authorization.source_commit_ref != live_receipt.source_commit_ref,
        authorization.authorization_sha256 != live_receipt.authorization_sha256,
        authorization.observed_evidence_census_sha256 != evidence_census.census_sha256,
        live_receipt.execution_plan_sha256 != plan.plan_sha256,
        live_receipt.observed_evidence_census_sha256 != evidence_census.census_sha256,
        lease.authorization_sha256 != authorization.authorization_sha256,
        lease.execution_plan_sha256 != plan.plan_sha256,
        lease.source_commit_ref != authorization.source_commit_ref,
        lease.store_location_sha256
        != content_sha256(str(store_root.resolve()).encode("utf-8")),
    )
    if any(mismatches):
        raise ClaimCorpusReconciliationError("execution authority chain does not reconcile")


def _load_authorities(
    authority_paths: tuple[Path, ...],
    scheduled_request_hashes: set[str],
) -> tuple[
    dict[str, tuple[str, ClaimCorpusRequestAuthority]],
    list[tuple[str, str]],
]:
    by_request: dict[str, tuple[str, ClaimCorpusRequestAuthority]] = {}
    hashes: list[tuple[str, str]] = []
    for path in authority_paths:
        authority = _verify_authority_bytes(path)
        if authority.request_sha256 in by_request:
            raise ClaimCorpusReconciliationError("duplicate frozen request authority")
        by_request[authority.request_sha256] = (path.stem, authority)
        hashes.append((path.stem, content_sha256(path.read_bytes())))
    if set(by_request) != scheduled_request_hashes:
        raise ClaimCorpusReconciliationError(
            "authority request census differs from execution plan"
        )
    return by_request, hashes


def _audit_request(
    *,
    scheduled: ScheduledClaimCorpusRequest,
    request: ClaimCorpusRequest,
    binding: ClaimEvidenceBinding,
    identity: str,
    authority: ClaimCorpusRequestAuthority,
    request_root: Path,
) -> _AuditedRequest:
    authority_mismatches = (
        authority.variant_binding.variant_id != scheduled.variant,
        authority.variant_binding.variant_content_sha256
        != scheduled.variant_content_sha256,
        authority.variant_binding.model_policy_sha256 != scheduled.model_policy_sha256,
        authority.variant_binding.prompt_policy_sha256 != scheduled.prompt_policy_sha256,
        authority.observed_evidence_binding_sha256 != binding.binding_sha256,
        authority.variant_binding.context_sha256 != binding.visible_context.context_sha256,
    )
    if any(authority_mismatches):
        raise ClaimCorpusReconciliationError(
            "request authority differs from frozen schedule"
        )

    shard = request_root / identity
    verifier = AttemptStoreIntegrityVerifier(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )
    shard_sha = verifier.store_sha256()
    terminal_root = shard / "terminal"
    failure_root = shard / "failures"
    terminals = tuple(path for path in terminal_root.iterdir() if not path.name.endswith(".stage"))
    failures = tuple(path for path in failure_root.iterdir() if not path.name.endswith(".stage"))
    if len(terminals) != 1 or terminals[0].stem != identity:
        raise ClaimCorpusReconciliationError("request shard lacks its exact terminal record")

    inventory = verifier._terminal_inventory(identity)
    inventory_mismatches = (
        inventory.request_identity_sha256 != identity,
        inventory.case_content_sha256 != scheduled.request_sha256,
        inventory.variant_content_sha256 != scheduled.variant_content_sha256,
        inventory.context_sha256 != binding.visible_context.context_sha256,
        inventory.evidence_content_sha256 != binding.visible_context.context_sha256,
        inventory.visibility_projection_sha256 != binding.visible_context.context_sha256,
        len(inventory.attempt_outcomes) > scheduled.maximum_attempts,
    )
    if any(inventory_mismatches):
        raise ClaimCorpusReconciliationError("terminal inventory differs from frozen request")
    if scheduled.route == "deterministic_local" and (
        inventory.gateway_status != "parsed" or inventory.attempt_outcomes != ("response",)
    ):
        raise ClaimCorpusReconciliationError("deterministic reference did not close parsed")

    issue = _read_issue(verifier, identity)
    if (inventory.gateway_status == "parsed") != (issue is None):
        raise ClaimCorpusReconciliationError("technical issue presence differs from status")
    row: _AuditRow = {
        "request_sha256": scheduled.request_sha256,
        "request_identity_sha256": identity,
        "authority_sha256": authority.authority_sha256,
        "inventory_sha256": inventory.inventory_sha256,
        "mechanism": request.mechanism,
        "evidence_condition": request.evidence_condition,
        "variant": scheduled.variant,
        "route": scheduled.route,
        "status": inventory.gateway_status,
        "attempt_count": len(inventory.attempt_outcomes),
        "issue_code": issue.code if issue is not None else None,
        "issue_stage": issue.stage if issue is not None else None,
    }
    return _AuditedRequest(
        row=row,
        shard_sha256=shard_sha,
        attempt_outcomes=inventory.attempt_outcomes,
        provider_attempt_count=(
            len(inventory.attempt_outcomes) if scheduled.route == "model_gateway" else 0
        ),
        failure_receipt_count=len(failures),
    )


def _build_slices(rows: list[_AuditRow]) -> tuple[ReconciliationSlice, ...]:
    grouped: dict[tuple[Dimension, str], list[_AuditRow]] = {}
    dimensions: tuple[tuple[Dimension, Callable[[_AuditRow], str]], ...] = (
        ("mechanism", lambda item: item["mechanism"]),
        ("evidence_condition", lambda item: item["evidence_condition"]),
        ("variant", lambda item: item["variant"]),
        ("route", lambda item: item["route"]),
    )
    for row in rows:
        for dimension, value_of in dimensions:
            key = (dimension, value_of(row))
            grouped.setdefault(key, []).append(row)
    return tuple(
        ReconciliationSlice(
            dimension=dimension,
            value=value,
            expected_count=len(items),
            terminal_count=len(items),
            parsed_count=sum(item["status"] == "parsed" for item in items),
            technical_failure_count=sum(item["status"] != "parsed" for item in items),
            attempt_count=sum(item["attempt_count"] for item in items),
        )
        for (dimension, value), items in sorted(grouped.items())
    )


def reconcile_claim_corpus_execution(
    root: Path,
    *,
    store_root: Path,
    authorization: ClaimCorpusExecutionAuthorization,
    lease: ClaimCorpusExecutionLease,
    live_receipt: ClaimCorpusLiveReceipt,
    evidence_census: ObservedEvidenceCensus,
) -> tuple[ClaimCorpusReserveDecisionReceipt, ClaimCorpusRequestReconciliationReceipt]:
    """Apply CSV-19/20 read-only gates without generating downstream outcomes."""

    checked_root = root.resolve()
    plan: ClaimCorpusExecutionPlan = build_execution_plan(checked_root)
    census = _load_census(checked_root)
    checked_authorization = ClaimCorpusExecutionAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    checked_lease = ClaimCorpusExecutionLease.model_validate(lease.model_dump(mode="python"))
    checked_live = ClaimCorpusLiveReceipt.model_validate(live_receipt.model_dump(mode="python"))
    checked_evidence = ObservedEvidenceCensus.model_validate(
        evidence_census.model_dump(mode="python")
    )
    _validate_authority_chain(
        store_root=store_root,
        plan=plan,
        authorization=checked_authorization,
        lease=checked_lease,
        live_receipt=checked_live,
        evidence_census=checked_evidence,
    )

    authority_root, request_root = _verify_top_level_store(store_root)
    authority_paths = tuple(sorted(authority_root.iterdir(), key=lambda item: item.name))
    shard_paths = tuple(sorted(request_root.iterdir(), key=lambda item: item.name))
    if len(authority_paths) != 360 or len(shard_paths) != 360:
        raise ClaimCorpusReconciliationError(
            "attempt store does not contain 360 authorities and shards"
        )
    if any(
        path.is_symlink() or not path.is_file() or path.suffix != ".json"
        for path in authority_paths
    ) or any(path.is_symlink() or not path.is_dir() for path in shard_paths):
        raise ClaimCorpusReconciliationError("attempt store membership type is invalid")
    authority_identities = {path.stem for path in authority_paths}
    shard_identities = {path.name for path in shard_paths}
    if len(authority_identities) != 360 or authority_identities != shard_identities:
        raise ClaimCorpusReconciliationError("authority and shard identities differ")

    scheduled_by_sha = {item.request_sha256: item for item in plan.requests}
    census_by_sha = {item.request_sha256: item for item in census.primary_requests}
    evidence_by_key = {
        (item.family_id, item.evidence_condition): item for item in checked_evidence.bindings
    }
    authority_by_request, authority_hashes = _load_authorities(
        authority_paths,
        set(scheduled_by_sha),
    )

    rows: list[_AuditRow] = []
    inventory_payload: list[object] = []
    shard_hashes: list[tuple[str, str]] = []
    gateway_statuses: Counter[str] = Counter()
    attempt_outcomes: Counter[str] = Counter()
    issue_codes: Counter[str] = Counter()
    issue_stages: Counter[str] = Counter()
    provider_attempt_count = 0
    store_failure_receipt_count = 0
    ordered_statuses: list[TerminalStatus] = []

    for scheduled in plan.requests:
        request = census_by_sha[scheduled.request_sha256]
        identity, authority = authority_by_request[scheduled.request_sha256]
        binding = evidence_by_key[(request.family_id, request.evidence_condition)]
        audited = _audit_request(
            scheduled=scheduled,
            request=request,
            binding=binding,
            identity=identity,
            authority=authority,
            request_root=request_root,
        )
        row = audited.row
        shard_hashes.append((identity, audited.shard_sha256))
        store_failure_receipt_count += audited.failure_receipt_count
        issue_codes.update((row["issue_code"],) if row["issue_code"] is not None else ())
        issue_stages.update((row["issue_stage"],) if row["issue_stage"] is not None else ())
        ordered_statuses.append(row["status"])
        gateway_statuses[row["status"]] += 1
        attempt_outcomes.update(audited.attempt_outcomes)
        provider_attempt_count += audited.provider_attempt_count
        rows.append(row)
        inventory_payload.append(row)

    computed_store_sha = canonical_execution_sha256(
        {
            "schema_version": "claim-corpus-sharded-attempt-store/v1",
            "authorities": tuple(sorted(authority_hashes)),
            "shards": tuple(sorted(shard_hashes)),
        }
    )
    if computed_store_sha != checked_live.terminal_store_sha256:
        raise ClaimCorpusReconciliationError("independent store hash differs from live receipt")
    if (
        dict(sorted(gateway_statuses.items())) != checked_live.gateway_status_counts
        or provider_attempt_count != checked_live.provider_attempt_count
        or sum(attempt_outcomes.values()) != checked_live.technical_attempt_count
        or store_failure_receipt_count != 0
    ):
        raise ClaimCorpusReconciliationError("computed terminal census differs from live receipt")

    provider_failures = gateway_statuses["provider_failed"]
    reserve_payload: dict[str, object] = {
        "schema_version": RESERVE_DECISION_SCHEMA_VERSION,
        "status": "claim_corpus_no_reserve_activation_required",
        "authorization_sha256": checked_authorization.authorization_sha256,
        "execution_plan_sha256": plan.plan_sha256,
        "live_receipt_sha256": checked_live.receipt_sha256,
        "terminal_store_sha256": computed_store_sha,
        "reserve_activation_rule": "pre_execution_technical_ineligibility_only",
        "primary_request_count": 360,
        "primary_requests_started": 360,
        "primary_terminal_count": 360,
        "pre_execution_ineligible_family_count": 0,
        "provider_failure_count": provider_failures,
        "provider_failures_eligible_for_reserve": False,
        "activated_reserve_family_count": 0,
        "replacement_request_count": 0,
        "reserve_requests_executed": 0,
        "outcome_driven_activation_forbidden": True,
        "reserve_activation_performed": False,
        "outputs_normalized": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    reserve_receipt = ClaimCorpusReserveDecisionReceipt.model_validate(
        {
            **reserve_payload,
            "receipt_sha256": canonical_execution_sha256(reserve_payload),
        }
    )

    statuses = tuple(ordered_statuses)
    failure_ordinals = tuple(
        index for index, status in enumerate(statuses, start=1) if status != "parsed"
    )
    reconcile_payload: dict[str, object] = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "status": "claim_corpus_request_reconciliation_passed",
        "source_commit_ref": checked_authorization.source_commit_ref,
        "authorization_sha256": checked_authorization.authorization_sha256,
        "execution_plan_sha256": plan.plan_sha256,
        "observed_evidence_census_sha256": checked_evidence.census_sha256,
        "live_receipt_sha256": checked_live.receipt_sha256,
        "execution_lease_sha256": checked_lease.lease_sha256,
        "terminal_store_sha256": computed_store_sha,
        "reserve_decision_sha256": reserve_receipt.receipt_sha256,
        "expected_request_count": 360,
        "authority_count": 360,
        "request_shard_count": 360,
        "terminal_request_count": 360,
        "parsed_terminal_count": gateway_statuses["parsed"],
        "technical_failure_terminal_count": 360 - gateway_statuses["parsed"],
        "provider_request_count": 315,
        "deterministic_request_count": 45,
        "provider_attempt_count": provider_attempt_count,
        "technical_attempt_count": sum(attempt_outcomes.values()),
        "missing_request_count": 0,
        "duplicate_request_count": 0,
        "unknown_request_count": 0,
        "partial_request_count": 0,
        "store_failure_receipt_count": 0,
        "first_technical_failure_ordinal": failure_ordinals[0] if failure_ordinals else None,
        "last_technical_failure_ordinal": failure_ordinals[-1] if failure_ordinals else None,
        "longest_consecutive_technical_failure_run": _longest_failure_run(statuses),
        "gateway_status_counts": tuple(
            item.model_dump(mode="json") for item in _canonical_counts(gateway_statuses)
        ),
        "attempt_outcome_counts": tuple(
            item.model_dump(mode="json") for item in _canonical_counts(attempt_outcomes)
        ),
        "issue_code_counts": tuple(
            item.model_dump(mode="json") for item in _canonical_counts(issue_codes)
        ),
        "issue_stage_counts": tuple(
            item.model_dump(mode="json") for item in _canonical_counts(issue_stages)
        ),
        "slices": tuple(item.model_dump(mode="json") for item in _build_slices(rows)),
        "request_inventory_sha256": canonical_execution_sha256(inventory_payload),
        "store_integrity_verified": True,
        "exact_request_coverage_verified": True,
        "ready_for_output_normalization": True,
        "outputs_normalized": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    reconciliation_receipt = ClaimCorpusRequestReconciliationReceipt.model_validate(
        {
            **reconcile_payload,
            "receipt_sha256": canonical_execution_sha256(reconcile_payload),
        }
    )
    return reserve_receipt, reconciliation_receipt


__all__ = [
    "ClaimCorpusReconciliationError",
    "ClaimCorpusRequestReconciliationReceipt",
    "ClaimCorpusReserveDecisionReceipt",
    "NamedCount",
    "ReconciliationSlice",
    "reconcile_claim_corpus_execution",
]
