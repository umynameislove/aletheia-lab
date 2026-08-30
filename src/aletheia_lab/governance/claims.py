"""Fail-closed reconciliation for evidence-bound public research claims.

The registry distinguishes implementation evidence from scientific evidence and
binds every current public statement to a unit, denominator, scope boundary and
falsifier.  It deliberately does not infer scientific admission from merge or
CI state.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ClaimState = Literal[
    "implemented",
    "available",
    "empty_primary_track",
    "assumption_limited",
    "negative_result",
    "positioning",
]
ClaimClass = Literal["engineering", "scientific", "positioning"]


class ClaimRegistryError(ValueError):
    """Raised when the public claim boundary cannot be reconciled."""


def _fail(message: str) -> NoReturn:
    raise ClaimRegistryError(message)


def _canonical_text(value: str, label: str) -> str:
    if value != value.strip() or value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must be trimmed Unicode NFC")
    if not value:
        raise ValueError(f"{label} must not be blank")
    return value


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MechanismDenominators(_StrictFrozenModel):
    inventory: int = Field(ge=0)
    admitted: int = Field(ge=0)
    assumption_limited: int = Field(ge=0)
    rejected: int = Field(ge=0)
    pending: int = Field(ge=0)
    diagnostic_ground_truth: int = Field(ge=0)

    @model_validator(mode="after")
    def _partition_is_complete(self) -> Self:
        if self.inventory != (
            self.admitted + self.assumption_limited + self.rejected + self.pending
        ):
            raise ValueError("mechanism status counts must partition the inventory")
        if self.diagnostic_ground_truth != self.admitted:
            raise ValueError("diagnostic ground truth must equal the admitted denominator")
        return self


class PublicClaim(_StrictFrozenModel):
    claim_id: str = Field(pattern=r"^C[0-9A-Z-]+$")
    claim_class: ClaimClass
    claim_level: str
    claim_text: str
    minimum_evidence: tuple[str, ...]
    unit_of_analysis: str
    denominator: str
    status: ClaimState
    scope_boundary: str
    falsifier_or_downgrade: str
    allowed_wording: str
    forbidden_wording: tuple[str, ...]
    primary_artifacts: tuple[str, ...]
    decision_owner: str

    @field_validator(
        "claim_level",
        "claim_text",
        "unit_of_analysis",
        "denominator",
        "scope_boundary",
        "falsifier_or_downgrade",
        "allowed_wording",
        "decision_owner",
    )
    @classmethod
    def _text_is_canonical(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "claim text")
        return _canonical_text(value, str(field_name))

    @field_validator("minimum_evidence", "forbidden_wording", "primary_artifacts")
    @classmethod
    def _sequences_are_canonical_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("claim lists must be non-empty and unique")
        for value in values:
            _canonical_text(value, "claim list value")
        return values

    @field_validator("primary_artifacts")
    @classmethod
    def _artifact_paths_are_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("primary artifact paths must be repository relative")
        return values

    @model_validator(mode="after")
    def _scientific_claim_needs_scientific_evidence(self) -> Self:
        if self.claim_class == "scientific":
            engineering_only = {"merge", "merged", "ci", "test pass", "implementation"}
            normalized = {item.casefold() for item in self.minimum_evidence}
            if normalized and normalized <= engineering_only:
                raise ValueError("scientific claims cannot rely only on engineering evidence")
        if self.status == "empty_primary_track" and self.denominator != "0 admitted mechanisms":
            raise ValueError("empty-primary claims must bind the zero admitted denominator")
        return self


class ForbiddenPublicPattern(_StrictFrozenModel):
    rule_id: str = Field(pattern=r"^FW-[0-9]{2}$")
    pattern: str
    rationale: str

    @field_validator("pattern", "rationale")
    @classmethod
    def _canonical_rule_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "rule")
        return _canonical_text(value, str(field_name))

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        re.compile(value, re.IGNORECASE)
        return value


class PublicClaimRegistry(_StrictFrozenModel):
    schema_version: Literal["public-claim-registry/v1"]
    frozen_on: Literal["2026-08-30"]
    mechanism_denominators: MechanismDenominators
    terminal_store_sha256: Sha256
    claims: tuple[PublicClaim, ...]
    public_surfaces: tuple[str, ...]
    forbidden_public_patterns: tuple[ForbiddenPublicPattern, ...]
    registry_sha256: Sha256

    @field_validator("public_surfaces")
    @classmethod
    def _surfaces_are_unique_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("public surfaces must be non-empty and unique")
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("public surfaces must be repository relative")
        return values

    @field_validator("claims")
    @classmethod
    def _claims_are_sorted(cls, values: tuple[PublicClaim, ...]) -> tuple[PublicClaim, ...]:
        claim_ids = tuple(claim.claim_id for claim in values)
        if claim_ids != tuple(sorted(claim_ids)):
            raise ValueError("claims must be sorted by claim_id")
        return values

    @model_validator(mode="after")
    def _identity_and_census_are_derived(self) -> Self:
        claim_ids = [claim.claim_id for claim in self.claims]
        if not claim_ids or len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be non-empty and unique")
        rule_ids = [rule.rule_id for rule in self.forbidden_public_patterns]
        if not rule_ids or len(rule_ids) != len(set(rule_ids)):
            raise ValueError("forbidden-wording rule IDs must be non-empty and unique")
        payload = self.model_dump(exclude={"registry_sha256"})
        if self.registry_sha256 != canonical_sha256(payload):
            raise ValueError("registry_sha256 is not derived from the canonical registry")
        return self


class ForbiddenWordingFinding(_StrictFrozenModel):
    rule_id: str
    relative_path: str
    line_number: int = Field(ge=1)
    matched_text: str


class ClaimRegistryAudit(_StrictFrozenModel):
    schema_version: Literal["public-claim-registry-audit/v1"] = (
        "public-claim-registry-audit/v1"
    )
    registry_sha256: Sha256
    claim_count: int = Field(gt=0)
    current_scientific_claim_count: int = Field(ge=0)
    public_surface_count: int = Field(gt=0)
    forbidden_wording_findings: tuple[ForbiddenWordingFinding, ...]
    denominator_reconciled: bool
    artifacts_resolved: bool
    status: Literal["pass", "blocked"]


def load_public_claim_registry(path: Path) -> PublicClaimRegistry:
    """Load and validate a claim registry without coercing malformed fields."""

    try:
        return PublicClaimRegistry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ClaimRegistryError("public claim registry is unavailable or invalid") from exc


def _load_json(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimRegistryError(f"{label} is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        _fail(f"{label} must be a JSON object")
    return payload


def _denominators_from_filter(payload: Mapping[str, object]) -> MechanismDenominators:
    def _list(name: str) -> Sequence[object]:
        value = payload.get(name)
        if not isinstance(value, list):
            _fail(f"mechanism filter field {name!r} must be a list")
        return value

    inventory = _list("mechanism_inventory")
    admitted = _list("primary_causal_diagnosis_track")
    assumption_limited = _list("assumption_limited_abstention_track")
    rejected = _list("instrument_rejection_track")
    return MechanismDenominators(
        inventory=len(inventory),
        admitted=len(admitted),
        assumption_limited=len(assumption_limited),
        rejected=len(rejected),
        pending=0,
        diagnostic_ground_truth=len(admitted),
    )


def _rejected_p2r_mechanism_count(payload: Mapping[str, object]) -> int:
    mechanisms = payload.get("mechanisms")
    if not isinstance(mechanisms, list):
        _fail("P2R publication summary mechanisms must be a list")
    rejected = 0
    for mechanism in mechanisms:
        if not isinstance(mechanism, dict):
            _fail("P2R publication summary mechanism entries must be objects")
        if mechanism.get("disposition") == "rejected" and mechanism.get("admitted") is False:
            rejected += 1
    return rejected


def _scan_forbidden_wording(
    root: Path,
    registry: PublicClaimRegistry,
) -> tuple[ForbiddenWordingFinding, ...]:
    findings: list[ForbiddenWordingFinding] = []
    for relative_path in registry.public_surfaces:
        path = root / relative_path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ClaimRegistryError(f"public claim surface is unavailable: {relative_path}") from exc
        for rule in registry.forbidden_public_patterns:
            compiled = re.compile(rule.pattern, re.IGNORECASE)
            for line_number, line in enumerate(lines, start=1):
                match = compiled.search(line)
                if match:
                    findings.append(
                        ForbiddenWordingFinding(
                            rule_id=rule.rule_id,
                            relative_path=relative_path,
                            line_number=line_number,
                            matched_text=match.group(0),
                        )
                    )
    return tuple(findings)


def audit_public_claim_registry(
    *,
    root: Path,
    registry: PublicClaimRegistry,
    mechanism_filter_path: Path,
    p2r_summary_path: Path,
) -> ClaimRegistryAudit:
    """Reconcile registry, terminal denominators, artifacts and public wording."""

    mechanism_filter = _load_json(mechanism_filter_path, "P4/P5 mechanism filter")
    p2r_summary = _load_json(p2r_summary_path, "P2R publication summary")
    observed_denominators = _denominators_from_filter(mechanism_filter)
    denominator_reconciled = observed_denominators == registry.mechanism_denominators
    if p2r_summary.get("terminal_store_sha256") != registry.terminal_store_sha256:
        denominator_reconciled = False
    if p2r_summary.get("n_admitted") != registry.mechanism_denominators.admitted:
        denominator_reconciled = False
    if p2r_summary.get("n_mechanisms") != _rejected_p2r_mechanism_count(p2r_summary):
        denominator_reconciled = False
    if _rejected_p2r_mechanism_count(p2r_summary) != registry.mechanism_denominators.rejected:
        denominator_reconciled = False

    current_states: set[ClaimState] = {
        "implemented",
        "available",
        "empty_primary_track",
        "assumption_limited",
        "negative_result",
        "positioning",
    }
    artifacts_resolved = all(
        (root / artifact).is_file()
        for claim in registry.claims
        if claim.status in current_states
        for artifact in claim.primary_artifacts
    )
    findings = _scan_forbidden_wording(root, registry)
    status: Literal["pass", "blocked"] = (
        "pass" if denominator_reconciled and artifacts_resolved and not findings else "blocked"
    )
    return ClaimRegistryAudit(
        registry_sha256=registry.registry_sha256,
        claim_count=len(registry.claims),
        current_scientific_claim_count=sum(
            claim.claim_class == "scientific" for claim in registry.claims
        ),
        public_surface_count=len(registry.public_surfaces),
        forbidden_wording_findings=findings,
        denominator_reconciled=denominator_reconciled,
        artifacts_resolved=artifacts_resolved,
        status=status,
    )
