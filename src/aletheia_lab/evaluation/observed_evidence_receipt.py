"""Token and cost accounting for one measured observed-evidence census."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import tiktoken
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusContractError
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.observed_evidence import (
    ObservedEvidenceMaterializationError,
    load_observed_evidence_inputs,
    validate_condition_semantics,
)
from aletheia_lab.project.identity import SHA256_PATTERN

OBSERVED_EVIDENCE_RECEIPT_SCHEMA_VERSION: Final = "claim-observed-evidence-receipt/v1"
TOKENIZER_ENCODING: Final = "o200k_base"
TOKENIZER_VERSION: Final = "0.14.0"
INPUT_USD_PER_MILLION: Final = 2.0
OUTPUT_USD_PER_MILLION: Final = 8.0
MAXIMUM_VISIBLE_CONTEXT_TOKENS: Final = 12_000
MODEL_REQUEST_COUNT: Final = 315
RELATION_REQUEST_CEILING: Final = 1_800
GENERATION_OUTPUT_TOKEN_CEILING: Final = 189_000
RELATION_OUTPUT_TOKEN_CEILING: Final = 1_080_000

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _usd(tokens: int, rate: float) -> float:
    return round(tokens * rate / 1_000_000, 6)


def _expected_costs(input_tokens: int) -> tuple[float, float, float, float, float]:
    input_cost = _usd(input_tokens, INPUT_USD_PER_MILLION)
    generation_cost = _usd(GENERATION_OUTPUT_TOKEN_CEILING, OUTPUT_USD_PER_MILLION)
    relation_input = _usd(
        RELATION_REQUEST_CEILING * MAXIMUM_VISIBLE_CONTEXT_TOKENS,
        INPUT_USD_PER_MILLION,
    )
    relation_output = _usd(RELATION_OUTPUT_TOKEN_CEILING, OUTPUT_USD_PER_MILLION)
    total = round(input_cost + generation_cost + relation_input + relation_output, 6)
    return input_cost, generation_cost, relation_input, relation_output, total


def _validate_source_paths(paths_by_projection: dict[str, tuple[str, ...]]) -> None:
    if len(paths_by_projection) != 15:
        raise ValueError("source provenance must cover all 15 measured projections")
    for projection_sha, paths in paths_by_projection.items():
        valid_sha = len(projection_sha) == 64 and all(
            character in "0123456789abcdef" for character in projection_sha
        )
        if not valid_sha:
            raise ValueError("source projection identity is not SHA-256")
        if len(paths) != 3 or len(paths) != len(set(paths)) or any(not path for path in paths):
            raise ValueError("each projection must bind three unique source artifacts")


class ObservedEvidenceReceipt(_StrictFrozenModel):
    """Public-safe proof of coverage, budgets, token accounting, and zero outcomes."""

    schema_version: Literal["claim-observed-evidence-receipt/v1"] = (
        OBSERVED_EVIDENCE_RECEIPT_SCHEMA_VERSION
    )
    status: Literal["claim_observed_evidence_census_complete_zero_outcome"]
    census_sha256: Sha256
    request_census_sha256: Sha256
    context_count: Literal[45]
    primary_family_count: Literal[15]
    condition_count_per_family: Literal[3]
    maximum_item_count_observed: int = Field(ge=1, le=32)
    maximum_context_utf8_bytes_observed: int = Field(ge=1, le=12_000)
    source_artifact_paths_by_projection_sha256: dict[str, tuple[str, ...]]
    tokenizer: Literal["o200k_base"] = TOKENIZER_ENCODING
    tokenizer_package: Literal["tiktoken"] = "tiktoken"
    tokenizer_package_version: Literal["0.14.0"] = TOKENIZER_VERSION
    token_accounting_scope: Literal[
        "two_message_chat_template_plus_canonical_visible_context"
    ]
    diagnosis_input_token_count_exact: int = Field(gt=0)
    diagnosis_input_token_count_by_variant: dict[str, int]
    diagnosis_model_request_count: Literal[315]
    pricing_basis: Literal["openai_gpt_4_1_standard_rates_observed_2026_09_03"]
    pricing_source_url: Literal["https://developers.openai.com/api/docs/models/gpt-4.1"]
    input_usd_per_million_tokens: float = Field(ge=2.0, le=2.0)
    output_usd_per_million_tokens: float = Field(ge=8.0, le=8.0)
    diagnosis_output_token_ceiling: Literal[189000]
    relation_request_ceiling: Literal[1800]
    relation_input_token_ceiling_per_request: Literal[12000]
    relation_output_token_ceiling: Literal[1080000]
    diagnosis_input_cost_estimate_usd: float = Field(ge=0.0)
    diagnosis_output_cost_ceiling_usd: float = Field(ge=0.0)
    relation_input_cost_ceiling_usd: float = Field(ge=0.0)
    relation_output_cost_ceiling_usd: float = Field(ge=0.0)
    one_attempt_total_cost_ceiling_usd: float = Field(ge=0.0)
    provider_billed_input_tokens_available: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    diagnosis_outputs_generated: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _identity_and_accounting_reconcile(self) -> Self:
        expected_variants = {"A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"}
        if set(self.diagnosis_input_token_count_by_variant) != expected_variants:
            raise ValueError("token accounting must cover the seven model-backed variants")
        if sum(self.diagnosis_input_token_count_by_variant.values()) != (
            self.diagnosis_input_token_count_exact
        ):
            raise ValueError("per-variant input tokens do not sum to the exact total")
        _validate_source_paths(self.source_artifact_paths_by_projection_sha256)
        observed_costs = (
            self.diagnosis_input_cost_estimate_usd,
            self.diagnosis_output_cost_ceiling_usd,
            self.relation_input_cost_ceiling_usd,
            self.relation_output_cost_ceiling_usd,
            self.one_attempt_total_cost_ceiling_usd,
        )
        if observed_costs != _expected_costs(self.diagnosis_input_token_count_exact):
            raise ValueError("API cost estimates do not reconcile with frozen rates and ceilings")
        if self.receipt_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("observed-evidence receipt identity does not match content")
        return self


def _chat_input_tokens(encoding: tiktoken.Encoding, system_text: str, user_text: str) -> int:
    # Pinned two-message accounting contract: three tokens per message and
    # three reply-priming tokens, including encoded role and content strings.
    total = 3
    for role, content in (("system", system_text), ("user", user_text)):
        total += 3 + len(encoding.encode(role)) + len(encoding.encode(content))
    return total


def build_observed_evidence_receipt(
    root: Path,
    census: ObservedEvidenceCensus,
) -> ObservedEvidenceReceipt:
    """Count exact frozen inputs and derive a conservative no-retry cost ceiling."""

    checked_root = root.resolve()
    inventory, requests, freeze = load_observed_evidence_inputs(checked_root)
    checked = validate_condition_semantics(census)
    if checked.request_census_sha256 != requests.census_sha256:
        raise ObservedEvidenceMaterializationError("evidence census belongs to another request census")
    if getattr(tiktoken, "__version__", None) != TOKENIZER_VERSION:
        raise ObservedEvidenceMaterializationError("tiktoken version differs from the frozen contract")
    encoding = tiktoken.get_encoding(TOKENIZER_ENCODING)
    bindings = {
        (item.family_id, item.evidence_condition): item for item in checked.bindings
    }
    variants = {item.variant_id: item for item in freeze.variants}
    by_variant: dict[str, int] = {}
    for request in requests.primary_requests:
        if request.variant == "B0":
            continue
        variant = variants[request.variant]
        prompt = freeze.prompt_policies[variant.prompt_policy_ref].instruction_contract
        binding = bindings[(request.family_id, request.evidence_condition)]
        user_content = canonical_execution_json(binding.model_payload())
        by_variant[request.variant] = by_variant.get(request.variant, 0) + _chat_input_tokens(
            encoding, prompt, user_content
        )
    exact_tokens = sum(by_variant.values())
    model_requests = sum(1 for item in requests.primary_requests if item.variant != "B0")
    if model_requests != MODEL_REQUEST_COUNT:
        raise ObservedEvidenceMaterializationError("model request count differs from freeze")
    context_bytes = [
        len(canonical_execution_json(item.model_payload()).encode("utf-8"))
        for item in checked.bindings
    ]
    item_counts = [len(item.visible_context.items) for item in checked.bindings]
    source_paths_by_projection: dict[str, tuple[str, ...]] = {}
    for family in inventory.families:
        if family.role == "primary":
            projection_sha = bindings[(family.family_id, "full")].source_projection_sha256
            source_paths_by_projection[projection_sha] = (
                family.source_artifact.path,
                "data/processed/telco_customer_churn.csv",
                "configs/project.yaml",
            )
    input_cost, generation_cost, relation_input, relation_output, total = _expected_costs(
        exact_tokens
    )
    payload: dict[str, object] = {
        "schema_version": OBSERVED_EVIDENCE_RECEIPT_SCHEMA_VERSION,
        "status": "claim_observed_evidence_census_complete_zero_outcome",
        "census_sha256": checked.census_sha256,
        "request_census_sha256": checked.request_census_sha256,
        "context_count": 45,
        "primary_family_count": 15,
        "condition_count_per_family": 3,
        "maximum_item_count_observed": max(item_counts),
        "maximum_context_utf8_bytes_observed": max(context_bytes),
        "source_artifact_paths_by_projection_sha256": dict(
            sorted(source_paths_by_projection.items())
        ),
        "tokenizer": TOKENIZER_ENCODING,
        "tokenizer_package": "tiktoken",
        "tokenizer_package_version": TOKENIZER_VERSION,
        "token_accounting_scope": "two_message_chat_template_plus_canonical_visible_context",
        "diagnosis_input_token_count_exact": exact_tokens,
        "diagnosis_input_token_count_by_variant": dict(sorted(by_variant.items())),
        "diagnosis_model_request_count": MODEL_REQUEST_COUNT,
        "pricing_basis": "openai_gpt_4_1_standard_rates_observed_2026_09_03",
        "pricing_source_url": "https://developers.openai.com/api/docs/models/gpt-4.1",
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "diagnosis_output_token_ceiling": GENERATION_OUTPUT_TOKEN_CEILING,
        "relation_request_ceiling": RELATION_REQUEST_CEILING,
        "relation_input_token_ceiling_per_request": MAXIMUM_VISIBLE_CONTEXT_TOKENS,
        "relation_output_token_ceiling": RELATION_OUTPUT_TOKEN_CEILING,
        "diagnosis_input_cost_estimate_usd": input_cost,
        "diagnosis_output_cost_ceiling_usd": generation_cost,
        "relation_input_cost_ceiling_usd": relation_input,
        "relation_output_cost_ceiling_usd": relation_output,
        "one_attempt_total_cost_ceiling_usd": total,
        "provider_billed_input_tokens_available": False,
        "provider_calls_executed": False,
        "diagnosis_outputs_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ObservedEvidenceReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def validate_observed_evidence_receipt(
    census: ObservedEvidenceCensus,
    receipt: ObservedEvidenceReceipt,
) -> ObservedEvidenceReceipt:
    """Bind a receipt to one census without requiring provider access."""

    checked_census = validate_condition_semantics(census)
    checked_receipt = ObservedEvidenceReceipt.model_validate(receipt.model_dump(mode="python"))
    if (
        checked_receipt.census_sha256 != checked_census.census_sha256
        or checked_receipt.request_census_sha256 != checked_census.request_census_sha256
    ):
        raise ClaimCorpusContractError("observed-evidence receipt belongs to another census")
    return checked_receipt


def canonical_json_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


__all__ = [
    "ObservedEvidenceReceipt",
    "build_observed_evidence_receipt",
    "canonical_json_bytes",
    "validate_observed_evidence_receipt",
]
