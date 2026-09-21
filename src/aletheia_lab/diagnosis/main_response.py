"""Variant-aware provider response boundary for the registered main study."""

from __future__ import annotations

import json
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAIN_RESPONSE_SCHEMA_VERSION: Final = "diagnosis-main-provider-output/1"

MainVariant = Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
ClaimType = Literal[
    "cause_assertion",
    "evidence_statement",
    "uncertainty_statement",
    "recommended_action",
    "other",
]

_CITATIONS_FORBIDDEN: Final = frozenset(("A1", "B1", "B2"))
_CITATIONS_REQUIRED: Final = frozenset(("A2", "A3", "CodeGraph", "FULL"))
_CITATION_REQUIRED_CLAIMS: Final = frozenset(("cause_assertion", "evidence_statement"))


class DiagnosisMainResponseError(ValueError):
    """Raised when a provider response violates the frozen arm contract."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class MainMaterialPart(_StrictFrozenModel):
    part_id: str
    text: str

    @field_validator("part_id", "text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("material parts must be non-blank and trimmed")
        return value


class MainAtomicClaim(_StrictFrozenModel):
    claim_local_id: str = Field(pattern=r"^claim-[1-5]$")
    claim_type: ClaimType
    claim_text: str = Field(min_length=1, max_length=2048)
    material_parts: tuple[MainMaterialPart, ...] = Field(min_length=1, max_length=8)
    visible_evidence_ids: tuple[str, ...] = Field(max_length=32)

    @field_validator("claim_text")
    @classmethod
    def _claim_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim text must not be blank")
        return value

    @field_validator("visible_evidence_ids")
    @classmethod
    def _evidence_ids_are_unique_and_non_blank(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not item.strip() or item != item.strip() for item in value
        ):
            raise ValueError("visible evidence IDs must be unique, non-blank and trimmed")
        return value


class DiagnosisMainProviderOutput(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-provider-output/1"]
    output_status: Literal["completed", "abstained"]
    atomic_claims: tuple[MainAtomicClaim, ...] = Field(max_length=5)
    abstention_reason: str | None

    @model_validator(mode="after")
    def _output_shape_reconciles(self) -> Self:
        identifiers = tuple(item.claim_local_id for item in self.atomic_claims)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("atomic claim IDs must be unique")
        if self.output_status == "abstained":
            if self.abstention_reason is None or not self.abstention_reason.strip():
                raise ValueError("abstention requires a non-blank reason")
            if any(item.claim_type == "cause_assertion" for item in self.atomic_claims):
                raise ValueError("abstention cannot retain a cause assertion")
        elif self.abstention_reason is not None:
            raise ValueError("completed output cannot contain an abstention reason")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosisMainResponseError(f"response contains duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_main_provider_output(
    raw_text: str,
    *,
    variant: MainVariant,
    visible_evidence_ids: set[str],
) -> DiagnosisMainProviderOutput:
    """Validate syntax, visible-ID containment and variant-specific citations."""

    try:
        parsed = json.loads(raw_text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise DiagnosisMainResponseError("response is not one valid JSON object") from exc
    if not isinstance(parsed, dict):
        raise DiagnosisMainResponseError("response root must be a JSON object")
    output = DiagnosisMainProviderOutput.model_validate_json(raw_text)
    cited = {
        evidence_id
        for claim in output.atomic_claims
        for evidence_id in claim.visible_evidence_ids
    }
    unknown = cited - visible_evidence_ids
    if unknown:
        raise DiagnosisMainResponseError(
            "response cites IDs outside the visible projection: " + ",".join(sorted(unknown))
        )
    if variant in _CITATIONS_FORBIDDEN and cited:
        raise DiagnosisMainResponseError(
            f"variant {variant} gained citations forbidden by its ablation arm"
        )
    if variant in _CITATIONS_REQUIRED:
        missing = tuple(
            claim.claim_local_id
            for claim in output.atomic_claims
            if claim.claim_type in _CITATION_REQUIRED_CLAIMS
            and not claim.visible_evidence_ids
        )
        if missing:
            raise DiagnosisMainResponseError(
                f"variant {variant} omitted required citations: {','.join(missing)}"
            )
    return output
