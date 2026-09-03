"""Exact observed-evidence census for the development claim corpus."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequestCensus,
    EvidenceCondition,
)
from aletheia_lab.evaluation.claim_evidence_semantics import ClaimEvidenceBinding
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

OBSERVED_EVIDENCE_CENSUS_SCHEMA_VERSION: Final = "claim-observed-evidence-census/v1"
EXPECTED_PRIMARY_CONTEXT_COUNT: Final = 45

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class ObservedEvidenceCensus(BaseModel):
    """One content-bound evidence context per primary family and condition."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    schema_version: Literal["claim-observed-evidence-census/v1"] = (
        OBSERVED_EVIDENCE_CENSUS_SCHEMA_VERSION
    )
    request_census_sha256: Sha256
    bindings: tuple[ClaimEvidenceBinding, ...] = Field(
        min_length=EXPECTED_PRIMARY_CONTEXT_COUNT,
        max_length=EXPECTED_PRIMARY_CONTEXT_COUNT,
    )
    provider_calls_executed: Literal[False] = False
    diagnosis_outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    census_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"census_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        keys = tuple(
            (item.family_id, item.family_sha256, item.evidence_condition) for item in self.bindings
        )
        if len(keys) != len(set(keys)):
            raise ValueError("observed evidence contains duplicate family-condition bindings")
        binding_hashes = tuple(item.binding_sha256 for item in self.bindings)
        if len(binding_hashes) != len(set(binding_hashes)):
            raise ValueError("observed evidence contains duplicate binding identities")
        if self.census_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("observed evidence census identity does not match content")
        return self


def _expected_primary_keys(
    census: ClaimCorpusRequestCensus,
) -> tuple[tuple[str, str, EvidenceCondition], ...]:
    seen: set[tuple[str, str, EvidenceCondition]] = set()
    result: list[tuple[str, str, EvidenceCondition]] = []
    for request in census.primary_requests:
        key = (request.family_id, request.family_sha256, request.evidence_condition)
        if key not in seen:
            seen.add(key)
            result.append(key)
    expected = tuple(result)
    if len(expected) != EXPECTED_PRIMARY_CONTEXT_COUNT:
        raise ClaimCorpusContractError("primary request census does not yield 45 contexts")
    return expected


def build_observed_evidence_census(
    request_census: ClaimCorpusRequestCensus,
    bindings: Sequence[ClaimEvidenceBinding],
) -> ObservedEvidenceCensus:
    """Build the exact primary evidence census in registered request order."""

    checked_census = ClaimCorpusRequestCensus.model_validate(
        request_census.model_dump(mode="python")
    )
    expected_keys = _expected_primary_keys(checked_census)
    checked_bindings = tuple(
        ClaimEvidenceBinding.model_validate(item.model_dump(mode="python")) for item in bindings
    )
    by_key = {
        (item.family_id, item.family_sha256, item.evidence_condition): item
        for item in checked_bindings
    }
    if len(by_key) != len(checked_bindings) or set(by_key) != set(expected_keys):
        raise ClaimCorpusContractError(
            "observed evidence must cover each primary family-condition exactly once"
        )
    ordered = tuple(by_key[key] for key in expected_keys)
    payload = {
        "schema_version": OBSERVED_EVIDENCE_CENSUS_SCHEMA_VERSION,
        "request_census_sha256": checked_census.census_sha256,
        "bindings": tuple(item.model_dump(mode="json") for item in ordered),
        "provider_calls_executed": False,
        "diagnosis_outputs_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ObservedEvidenceCensus(
        request_census_sha256=checked_census.census_sha256,
        bindings=ordered,
        provider_calls_executed=False,
        diagnosis_outputs_generated=False,
        claims_materialized=False,
        automatic_labels_generated=False,
        human_annotations_collected=False,
        main_or_sealed_outcomes_opened=False,
        census_sha256=canonical_execution_sha256(payload),
    )


def validate_observed_evidence_census(
    request_census: ClaimCorpusRequestCensus,
    observed: ObservedEvidenceCensus,
) -> ObservedEvidenceCensus:
    """Independently reconcile a census with the frozen primary requests."""

    checked = ObservedEvidenceCensus.model_validate(observed.model_dump(mode="python"))
    expected = build_observed_evidence_census(request_census, checked.bindings)
    if checked != expected:
        raise ClaimCorpusContractError("observed evidence is not bound to the request census")
    return checked


def load_observed_evidence_census(
    path: Path,
    request_census: ClaimCorpusRequestCensus,
) -> ObservedEvidenceCensus:
    """Load and independently verify a local observed-evidence census."""

    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("observed evidence path is not a regular file")
        observed = ObservedEvidenceCensus.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusContractError(
            "observed evidence census is unavailable or invalid"
        ) from exc
    return validate_observed_evidence_census(request_census, observed)


__all__ = [
    "EXPECTED_PRIMARY_CONTEXT_COUNT",
    "ObservedEvidenceCensus",
    "build_observed_evidence_census",
    "load_observed_evidence_census",
    "validate_observed_evidence_census",
]
