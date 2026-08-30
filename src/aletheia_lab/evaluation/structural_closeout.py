"""Offline structural closeout over sealed evaluation terminal inventories."""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.attempt_store import (
    AttemptStoreError,
    ImmutableAttemptStore,
    TerminalExecutionInventory,
)
from aletheia_lab.evaluation.execution_contracts import (
    AttemptIdentity,
    EvaluationManifestReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway.contracts import ResponseMode, TerminalStatus
from aletheia_lab.project.identity import SHA256_PATTERN, SNAPSHOT_ID_PATTERN

AUTHORIZATION_CHECK_SCHEMA_VERSION: Final[
    Literal["evaluation-structural-authorization/v1"]
] = "evaluation-structural-authorization/v1"
REQUEST_EXPECTATION_SCHEMA_VERSION: Final[
    Literal["evaluation-structural-request-expectation/v1"]
] = "evaluation-structural-request-expectation/v1"
STRUCTURAL_PLAN_SCHEMA_VERSION: Final[
    Literal["evaluation-structural-closeout-plan/v1"]
] = "evaluation-structural-closeout-plan/v1"
STRUCTURAL_RECEIPT_SCHEMA_VERSION: Final[
    Literal["evaluation-structural-closeout-receipt/v1"]
] = "evaluation-structural-closeout-receipt/v1"

_OPAQUE_PATTERN: Final[str] = r"^ev-[0-9a-f]{64}$"
_CODE_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
OpaqueReference = Annotated[str, Field(pattern=_OPAQUE_PATTERN)]
TechnicalCode = Annotated[str, Field(pattern=_CODE_PATTERN)]
SnapshotId = Annotated[str, Field(pattern=SNAPSHOT_ID_PATTERN)]

StructuralState = Literal[
    "complete_uninterpreted",
    "incomplete",
    "invalid_provenance",
    "duplicate_or_replay",
    "technical_failure",
    "not_authorized",
]
RequestReconciliation = Literal["reconciled", "missing", "mismatch"]
FindingCode = Literal[
    "authorization_not_current",
    "authority_manifest_hash_mismatch",
    "authority_manifest_reference_mismatch",
    "authority_reference_mismatch",
    "attempt_count_mismatch",
    "case_content_mismatch",
    "case_reference_mismatch",
    "context_mismatch",
    "evidence_mismatch",
    "expected_manifest_hash_mismatch",
    "expected_manifest_reference_mismatch",
    "expected_snapshot_mismatch",
    "family_mismatch",
    "missing_terminal",
    "model_mismatch",
    "model_policy_mismatch",
    "model_version_mismatch",
    "prompt_mismatch",
    "provider_mismatch",
    "resource_policy_mismatch",
    "response_schema_mismatch",
    "retry_policy_mismatch",
    "store_technical_failure",
    "unexpected_terminal_cross_manifest",
    "unexpected_terminal_same_manifest",
    "variant_mismatch",
    "visibility_projection_mismatch",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class StructuralAuthorizationCheck(_StrictFrozenModel):
    """Explicit current authorization evidence supplied by the manifest owner."""

    schema_version: Literal["evaluation-structural-authorization/v1"] = (
        AUTHORIZATION_CHECK_SCHEMA_VERSION
    )
    verification_ref: OpaqueReference
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    authorization_ref: OpaqueReference
    authorization_valid: bool


class StructuralRequestExpectation(_StrictFrozenModel):
    """One fully explicit technical request expected by the closeout manifest."""

    schema_version: Literal["evaluation-structural-request-expectation/v1"] = (
        REQUEST_EXPECTATION_SCHEMA_VERSION
    )
    expectation_id: OpaqueReference
    expectation_sha256: Sha256
    request_identity_sha256: Sha256
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    case_reference_id: OpaqueReference
    case_content_sha256: Sha256
    family_id: OpaqueReference
    variant_content_sha256: Sha256
    snapshot_id: SnapshotId
    evidence_content_sha256: Sha256
    visibility_projection_sha256: Sha256
    context_sha256: Sha256
    prompt_sha256: Sha256
    response_schema_sha256: Sha256
    model_policy_sha256: Sha256
    provider_ref: OpaqueReference
    model_ref: OpaqueReference
    model_version_ref: OpaqueReference
    resource_policy_ref: OpaqueReference
    retry_policy_ref: OpaqueReference
    expected_attempt_count: int = Field(gt=0)

    def identity_payload(self) -> dict[str, object]:
        payload = self.model_dump(
            mode="json", exclude={"expectation_id", "expectation_sha256"}
        )
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        expected = canonical_execution_sha256(self.identity_payload())
        if self.expectation_sha256 != expected or self.expectation_id != f"ev-{expected}":
            raise ValueError("structural request expectation identity does not reconcile")
        return self

    @classmethod
    def build(
        cls,
        *,
        attempt: AttemptIdentity,
        retry_policy_ref: str,
        expected_attempt_count: int,
    ) -> Self:
        checked = AttemptIdentity.model_validate(attempt.model_dump(mode="python"))
        fields: dict[str, object] = {
            "schema_version": REQUEST_EXPECTATION_SCHEMA_VERSION,
            "request_identity_sha256": checked.request_identity_sha256,
            "manifest_reference_id": checked.manifest.reference_id,
            "manifest_content_sha256": checked.manifest.manifest_content_sha256,
            "case_reference_id": checked.case.reference_id,
            "case_content_sha256": checked.case.case_content_sha256,
            "family_id": checked.case.family_id,
            "variant_content_sha256": checked.case.variant_content_sha256,
            "snapshot_id": checked.case.snapshot_id,
            "evidence_content_sha256": checked.case.evidence_content_sha256,
            "visibility_projection_sha256": checked.case.visibility_projection_sha256,
            "context_sha256": checked.context_sha256,
            "prompt_sha256": checked.prompt_sha256,
            "response_schema_sha256": checked.response_schema_sha256,
            "model_policy_sha256": checked.model_policy.policy_content_sha256,
            "provider_ref": checked.model_policy.provider_ref,
            "model_ref": checked.model_policy.model_ref,
            "model_version_ref": checked.model_policy.model_version_ref,
            "resource_policy_ref": checked.model_policy.resource_policy_ref,
            "retry_policy_ref": retry_policy_ref,
            "expected_attempt_count": expected_attempt_count,
        }
        expectation_sha = canonical_execution_sha256(fields)
        return cls.model_validate(
            {
                "expectation_id": f"ev-{expectation_sha}",
                "expectation_sha256": expectation_sha,
                **fields,
            }
        )


class StructuralCloseoutPlan(_StrictFrozenModel):
    """Authorized structural expectations; contains no scientific decision policy."""

    schema_version: Literal["evaluation-structural-closeout-plan/v1"] = (
        STRUCTURAL_PLAN_SCHEMA_VERSION
    )
    plan_id: OpaqueReference
    plan_sha256: Sha256
    manifest: EvaluationManifestReference
    authorization_check: StructuralAuthorizationCheck
    requests: tuple[StructuralRequestExpectation, ...]

    def identity_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", exclude={"plan_id", "plan_sha256"})
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_and_membership_reconcile(self) -> Self:
        request_hashes = tuple(item.request_identity_sha256 for item in self.requests)
        if not request_hashes:
            raise ValueError("structural closeout plan requires expected requests")
        if request_hashes != tuple(sorted(request_hashes)):
            raise ValueError("structural closeout requests must be canonically sorted")
        if len(request_hashes) != len(set(request_hashes)):
            raise ValueError("structural closeout requests must be unique")
        expected = canonical_execution_sha256(self.identity_payload())
        if self.plan_sha256 != expected or self.plan_id != f"ev-{expected}":
            raise ValueError("structural closeout plan identity does not reconcile")
        return self

    @classmethod
    def build(
        cls,
        *,
        manifest: EvaluationManifestReference,
        authorization_check: StructuralAuthorizationCheck,
        requests: tuple[StructuralRequestExpectation, ...],
    ) -> Self:
        checked_manifest = EvaluationManifestReference.model_validate(
            manifest.model_dump(mode="python")
        )
        checked_authorization = StructuralAuthorizationCheck.model_validate(
            authorization_check.model_dump(mode="python")
        )
        checked_requests = tuple(
            sorted(
                (
                    StructuralRequestExpectation.model_validate(
                        item.model_dump(mode="python")
                    )
                    for item in requests
                ),
                key=lambda item: item.request_identity_sha256,
            )
        )
        fields: dict[str, object] = {
            "schema_version": STRUCTURAL_PLAN_SCHEMA_VERSION,
            "manifest": checked_manifest.model_dump(mode="json"),
            "authorization_check": checked_authorization.model_dump(mode="json"),
            "requests": [item.model_dump(mode="json") for item in checked_requests],
        }
        plan_sha = canonical_execution_sha256(fields)
        return cls(
            plan_id=f"ev-{plan_sha}",
            plan_sha256=plan_sha,
            manifest=checked_manifest,
            authorization_check=checked_authorization,
            requests=checked_requests,
        )


class StructuralFinding(_StrictFrozenModel):
    """Stable structural finding without scientific interpretation."""

    code: FindingCode
    request_identity_sha256: Sha256 | None
    technical_code: TechnicalCode | None


class StructuralRequestReceipt(_StrictFrozenModel):
    """Per-request reconciliation inventory retained in the closeout receipt."""

    request_identity_sha256: Sha256
    reconciliation: RequestReconciliation
    expected_attempt_count: int = Field(gt=0)
    observed_attempt_count: int | None = Field(default=None, ge=0)
    terminal_inventory_sha256: Sha256 | None
    gateway_status: TerminalStatus | None
    response_mode: ResponseMode | None
    raw_response_sha256: Sha256 | None
    parsed_response_sha256: Sha256 | None
    issue_sha256: Sha256 | None

    @model_validator(mode="after")
    def _presence_reconciles(self) -> Self:
        observed = (
            self.observed_attempt_count,
            self.terminal_inventory_sha256,
            self.gateway_status,
        )
        if self.reconciliation == "missing" and any(value is not None for value in observed):
            raise ValueError("missing structural request cannot contain terminal inventory")
        if self.reconciliation == "missing" and self.response_mode is not None:
            raise ValueError("missing structural request cannot contain response mode")
        if self.reconciliation != "missing" and any(value is None for value in observed):
            raise ValueError("observed structural request requires terminal inventory")
        if (self.parsed_response_sha256 is None) == (self.issue_sha256 is None) and (
            self.reconciliation != "missing"
        ):
            raise ValueError("observed structural request must retain exactly one outcome")
        return self


class StructuralCloseoutReceipt(_StrictFrozenModel):
    """Deterministic technical closeout with no metrics or scientific disposition."""

    schema_version: Literal["evaluation-structural-closeout-receipt/v1"] = (
        STRUCTURAL_RECEIPT_SCHEMA_VERSION
    )
    receipt_id: OpaqueReference
    receipt_sha256: Sha256
    structural_state: StructuralState
    plan_id: OpaqueReference
    plan_sha256: Sha256
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    snapshot_id: SnapshotId
    authorization_ref: OpaqueReference
    authorization_verification_ref: OpaqueReference
    store_sha256: Sha256 | None
    expected_request_count: int = Field(gt=0)
    observed_terminal_count: int = Field(ge=0)
    reconciled_request_count: int = Field(ge=0)
    expected_case_count: int = Field(gt=0)
    observed_case_count: int = Field(ge=0)
    expected_variant_count: int = Field(gt=0)
    observed_variant_count: int = Field(ge=0)
    expected_attempt_count: int = Field(gt=0)
    observed_attempt_count: int = Field(ge=0)
    requests: tuple[StructuralRequestReceipt, ...]
    findings: tuple[StructuralFinding, ...]

    def identity_payload(self) -> dict[str, object]:
        payload = self.model_dump(
            mode="json", exclude={"receipt_id", "receipt_sha256"}
        )
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_and_counts_reconcile(self) -> Self:
        request_hashes = tuple(item.request_identity_sha256 for item in self.requests)
        if request_hashes != tuple(sorted(request_hashes)) or len(request_hashes) != len(
            set(request_hashes)
        ):
            raise ValueError("structural receipt requests must be unique and sorted")
        finding_keys = tuple(_finding_key(item) for item in self.findings)
        if finding_keys != tuple(sorted(finding_keys)) or len(finding_keys) != len(
            set(finding_keys)
        ):
            raise ValueError("structural receipt findings must be unique and sorted")
        if self.expected_request_count != len(self.requests):
            raise ValueError("expected request count is not derived")
        if self.reconciled_request_count != sum(
            item.reconciliation == "reconciled" for item in self.requests
        ):
            raise ValueError("reconciled request count is not derived")
        expected = canonical_execution_sha256(self.identity_payload())
        if self.receipt_sha256 != expected or self.receipt_id != f"ev-{expected}":
            raise ValueError("structural closeout receipt identity does not reconcile")
        return self


def reduce_structural_closeout(
    plan: StructuralCloseoutPlan,
    *,
    store: ImmutableAttemptStore,
) -> StructuralCloseoutReceipt:
    """Reduce sealed inventories into one deterministic structural-only receipt."""

    checked_plan = StructuralCloseoutPlan.model_validate(plan.model_dump(mode="python"))
    findings: list[StructuralFinding] = []
    _check_authorization(checked_plan, findings)
    _check_expected_provenance(checked_plan, findings)
    try:
        store_sha = store.store_sha256()
        inventories = store.terminal_inventories()
    except (AttemptStoreError, OSError) as exc:
        technical_code = exc.code if isinstance(exc, AttemptStoreError) else "io_error"
        findings.append(
            StructuralFinding(
                code="store_technical_failure",
                request_identity_sha256=None,
                technical_code=technical_code,
            )
        )
        store_sha = None
        inventories = ()
    expected_by_request = {
        item.request_identity_sha256: item for item in checked_plan.requests
    }
    observed_by_request = {
        item.request_identity_sha256: item for item in inventories
    }
    for inventory in inventories:
        if inventory.request_identity_sha256 in expected_by_request:
            continue
        code: FindingCode = (
            "unexpected_terminal_cross_manifest"
            if (
                inventory.manifest_reference_id != checked_plan.manifest.reference_id
                or inventory.manifest_content_sha256
                != checked_plan.manifest.manifest_content_sha256
                or inventory.snapshot_id != checked_plan.manifest.snapshot_id
            )
            else "unexpected_terminal_same_manifest"
        )
        findings.append(
            StructuralFinding(
                code=code,
                request_identity_sha256=inventory.request_identity_sha256,
                technical_code=None,
            )
        )
    request_receipts: list[StructuralRequestReceipt] = []
    for expectation in checked_plan.requests:
        observed = observed_by_request.get(expectation.request_identity_sha256)
        if observed is None:
            findings.append(
                StructuralFinding(
                    code="missing_terminal",
                    request_identity_sha256=expectation.request_identity_sha256,
                    technical_code=None,
                )
            )
            request_receipts.append(
                StructuralRequestReceipt(
                    request_identity_sha256=expectation.request_identity_sha256,
                    reconciliation="missing",
                    expected_attempt_count=expectation.expected_attempt_count,
                    observed_attempt_count=None,
                    terminal_inventory_sha256=None,
                    gateway_status=None,
                    response_mode=None,
                    raw_response_sha256=None,
                    parsed_response_sha256=None,
                    issue_sha256=None,
                )
            )
            continue
        mismatch = _compare_inventory(expectation, observed, findings)
        observed_attempt_count = len(observed.attempt_record_sha256)
        if observed_attempt_count != expectation.expected_attempt_count:
            findings.append(
                StructuralFinding(
                    code="attempt_count_mismatch",
                    request_identity_sha256=expectation.request_identity_sha256,
                    technical_code=None,
                )
            )
            mismatch = True
        request_receipts.append(
            StructuralRequestReceipt(
                request_identity_sha256=expectation.request_identity_sha256,
                reconciliation="mismatch" if mismatch else "reconciled",
                expected_attempt_count=expectation.expected_attempt_count,
                observed_attempt_count=observed_attempt_count,
                terminal_inventory_sha256=observed.inventory_sha256,
                gateway_status=observed.gateway_status,
                response_mode=observed.attempt_response_modes[-1],
                raw_response_sha256=observed.raw_response_sha256,
                parsed_response_sha256=observed.parsed_response_sha256,
                issue_sha256=observed.issue_sha256,
            )
        )
    sorted_findings = tuple(sorted(set(findings), key=_finding_key))
    sorted_receipts = tuple(
        sorted(request_receipts, key=lambda item: item.request_identity_sha256)
    )
    state = _structural_state(sorted_findings)
    expected_cases = {item.case_reference_id for item in checked_plan.requests}
    expected_variants = {item.variant_content_sha256 for item in checked_plan.requests}
    observed_cases = {item.case_reference_id for item in inventories}
    observed_variants = {item.variant_content_sha256 for item in inventories}
    fields: dict[str, object] = {
        "schema_version": STRUCTURAL_RECEIPT_SCHEMA_VERSION,
        "structural_state": state,
        "plan_id": checked_plan.plan_id,
        "plan_sha256": checked_plan.plan_sha256,
        "manifest_reference_id": checked_plan.manifest.reference_id,
        "manifest_content_sha256": checked_plan.manifest.manifest_content_sha256,
        "snapshot_id": checked_plan.manifest.snapshot_id,
        "authorization_ref": checked_plan.manifest.authorization_ref,
        "authorization_verification_ref": (
            checked_plan.authorization_check.verification_ref
        ),
        "store_sha256": store_sha,
        "expected_request_count": len(checked_plan.requests),
        "observed_terminal_count": len(inventories),
        "reconciled_request_count": sum(
            item.reconciliation == "reconciled" for item in sorted_receipts
        ),
        "expected_case_count": len(expected_cases),
        "observed_case_count": len(observed_cases),
        "expected_variant_count": len(expected_variants),
        "observed_variant_count": len(observed_variants),
        "expected_attempt_count": sum(
            item.expected_attempt_count for item in checked_plan.requests
        ),
        "observed_attempt_count": sum(
            len(item.attempt_record_sha256) for item in inventories
        ),
        "requests": tuple(item.model_dump(mode="json") for item in sorted_receipts),
        "findings": tuple(item.model_dump(mode="json") for item in sorted_findings),
    }
    assert_no_scientific_closeout_fields(fields)
    receipt_sha = canonical_execution_sha256(fields)
    return StructuralCloseoutReceipt.model_validate(
        {
            "receipt_id": f"ev-{receipt_sha}",
            "receipt_sha256": receipt_sha,
            **fields,
        }
    )


def _check_authorization(
    plan: StructuralCloseoutPlan,
    findings: list[StructuralFinding],
) -> None:
    check = plan.authorization_check
    comparisons: tuple[tuple[bool, FindingCode], ...] = (
        (check.authorization_valid, "authorization_not_current"),
        (
            check.manifest_reference_id == plan.manifest.reference_id,
            "authority_manifest_reference_mismatch",
        ),
        (
            check.manifest_content_sha256 == plan.manifest.manifest_content_sha256,
            "authority_manifest_hash_mismatch",
        ),
        (
            check.authorization_ref == plan.manifest.authorization_ref,
            "authority_reference_mismatch",
        ),
    )
    for matches, code in comparisons:
        if not matches:
            findings.append(
                StructuralFinding(
                    code=code,
                    request_identity_sha256=None,
                    technical_code=None,
                )
            )


def _check_expected_provenance(
    plan: StructuralCloseoutPlan,
    findings: list[StructuralFinding],
) -> None:
    for expectation in plan.requests:
        comparisons: tuple[tuple[bool, FindingCode], ...] = (
            (
                expectation.manifest_reference_id == plan.manifest.reference_id,
                "expected_manifest_reference_mismatch",
            ),
            (
                expectation.manifest_content_sha256
                == plan.manifest.manifest_content_sha256,
                "expected_manifest_hash_mismatch",
            ),
            (
                expectation.snapshot_id == plan.manifest.snapshot_id,
                "expected_snapshot_mismatch",
            ),
        )
        for matches, code in comparisons:
            if not matches:
                findings.append(
                    StructuralFinding(
                        code=code,
                        request_identity_sha256=expectation.request_identity_sha256,
                        technical_code=None,
                    )
                )


def _compare_inventory(
    expectation: StructuralRequestExpectation,
    inventory: TerminalExecutionInventory,
    findings: list[StructuralFinding],
) -> bool:
    comparisons: tuple[tuple[bool, FindingCode], ...] = (
        (
            inventory.manifest_reference_id == expectation.manifest_reference_id,
            "expected_manifest_reference_mismatch",
        ),
        (
            inventory.manifest_content_sha256 == expectation.manifest_content_sha256,
            "expected_manifest_hash_mismatch",
        ),
        (inventory.case_reference_id == expectation.case_reference_id, "case_reference_mismatch"),
        (inventory.case_content_sha256 == expectation.case_content_sha256, "case_content_mismatch"),
        (inventory.family_id == expectation.family_id, "family_mismatch"),
        (inventory.variant_content_sha256 == expectation.variant_content_sha256, "variant_mismatch"),
        (inventory.snapshot_id == expectation.snapshot_id, "expected_snapshot_mismatch"),
        (inventory.evidence_content_sha256 == expectation.evidence_content_sha256, "evidence_mismatch"),
        (
            inventory.visibility_projection_sha256
            == expectation.visibility_projection_sha256,
            "visibility_projection_mismatch",
        ),
        (inventory.context_sha256 == expectation.context_sha256, "context_mismatch"),
        (inventory.prompt_sha256 == expectation.prompt_sha256, "prompt_mismatch"),
        (
            inventory.response_schema_sha256 == expectation.response_schema_sha256,
            "response_schema_mismatch",
        ),
        (
            inventory.model_policy_sha256 == expectation.model_policy_sha256,
            "model_policy_mismatch",
        ),
        (inventory.provider_ref == expectation.provider_ref, "provider_mismatch"),
        (inventory.model_ref == expectation.model_ref, "model_mismatch"),
        (
            inventory.model_version_ref == expectation.model_version_ref,
            "model_version_mismatch",
        ),
        (
            inventory.resource_policy_ref == expectation.resource_policy_ref,
            "resource_policy_mismatch",
        ),
        (
            inventory.retry_policy_ref == expectation.retry_policy_ref,
            "retry_policy_mismatch",
        ),
    )
    mismatch = False
    for matches, code in comparisons:
        if not matches:
            mismatch = True
            findings.append(
                StructuralFinding(
                    code=code,
                    request_identity_sha256=expectation.request_identity_sha256,
                    technical_code=None,
                )
            )
    return mismatch


def _finding_key(finding: StructuralFinding) -> tuple[str, str, str]:
    return (
        finding.code,
        finding.request_identity_sha256 or "",
        finding.technical_code or "",
    )


def _structural_state(findings: tuple[StructuralFinding, ...]) -> StructuralState:
    codes = {item.code for item in findings}
    if "store_technical_failure" in codes:
        return "technical_failure"
    if codes & {
        "authorization_not_current",
        "authority_manifest_hash_mismatch",
        "authority_manifest_reference_mismatch",
        "authority_reference_mismatch",
    }:
        return "not_authorized"
    if codes & {
        "case_content_mismatch",
        "case_reference_mismatch",
        "context_mismatch",
        "evidence_mismatch",
        "expected_manifest_hash_mismatch",
        "expected_manifest_reference_mismatch",
        "expected_snapshot_mismatch",
        "family_mismatch",
        "model_mismatch",
        "model_policy_mismatch",
        "model_version_mismatch",
        "prompt_mismatch",
        "provider_mismatch",
        "resource_policy_mismatch",
        "response_schema_mismatch",
        "retry_policy_mismatch",
        "unexpected_terminal_cross_manifest",
        "variant_mismatch",
        "visibility_projection_mismatch",
    }:
        return "invalid_provenance"
    if "unexpected_terminal_same_manifest" in codes:
        return "duplicate_or_replay"
    if codes & {"attempt_count_mismatch", "missing_terminal"}:
        return "incomplete"
    return "complete_uninterpreted"


def assert_no_scientific_closeout_fields(payload: object) -> None:
    """Fail if an exported payload introduces forbidden scientific defaults."""

    forbidden = re.compile(
        r"(?:threshold|denominator|primary_outcome|scientific_(?:pass|fail)|"
        r"admitted|superior|generalizable)",
        flags=re.IGNORECASE,
    )
    serialized = str(payload)
    if forbidden.search(serialized) is not None:
        raise ValueError("structural closeout contains forbidden scientific interpretation")
