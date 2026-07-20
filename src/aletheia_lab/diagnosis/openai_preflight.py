"""Frozen OpenAI configuration and no-network preflight for the matched pilot."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis.adapters import (
    OPENAI_INPUT_PRICE_PER_MILLION,
    OPENAI_MODEL_SNAPSHOT,
    OPENAI_MODEL_VERSION,
    OPENAI_OUTPUT_PRICE_PER_MILLION,
    OPENAI_SDK_VERSION,
    openai_output_json_schema,
)
from aletheia_lab.diagnosis.pilot import (
    DEFAULT_SETTINGS,
    build_matched_requests,
    validate_matched_requests,
    validate_source_binding,
)
from aletheia_lab.diagnosis.schema import (
    DiagnosisRequest,
    GenerationSettings,
    PilotVariant,
    ProviderIdentity,
)
from aletheia_lab.evidence.schema import canonical_json, project_diagnosis_evidence, sha256_text
from aletheia_lab.evidence.store import load_bundle_store

CONFIG_SCHEMA_VERSION: Final[Literal["openai-pilot-config/1"]] = (
    "openai-pilot-config/1"
)
PREFLIGHT_SCHEMA_VERSION: Final[Literal["openai-pilot-preflight/1"]] = (
    "openai-pilot-preflight/1"
)
MODEL_SNAPSHOT: Final[str] = OPENAI_MODEL_SNAPSHOT
MODEL_VERSION: Final[str] = OPENAI_MODEL_VERSION
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OpenAICapabilities(_StrictFrozenModel):
    structured_output: Literal[True]
    tools: Literal[False]
    web_search: Literal[False]
    retrieval: Literal[False]


class OpenAIPricing(_StrictFrozenModel):
    input: Literal[2.0]
    output: Literal[8.0]


class OpenAIExecutionPolicy(_StrictFrozenModel):
    preflight_only: Literal[True]
    smoke_request_count: Literal[8]
    full_request_count: Literal[30]
    explicit_human_confirmation_required: Literal[True]


class OpenAIPilotConfig(_StrictFrozenModel):
    schema_version: Literal["openai-pilot-config/1"]
    decision_status: Literal["frozen_before_external_results"]
    provider: Literal["openai"]
    api: Literal["chat_completions"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    sdk_version: Literal["2.46.0"]
    settings: GenerationSettings
    capabilities: OpenAICapabilities
    pricing_usd_per_million_tokens: OpenAIPricing
    execution: OpenAIExecutionPolicy

    @model_validator(mode="after")
    def _frozen_settings(self) -> Self:
        if self.settings != DEFAULT_SETTINGS:
            raise ValueError("settings differ from the frozen matched-pilot contract")
        if self.sdk_version != OPENAI_SDK_VERSION:
            raise ValueError("SDK version differs from the adapter lock")
        if (
            self.pricing_usd_per_million_tokens.input != OPENAI_INPUT_PRICE_PER_MILLION
            or self.pricing_usd_per_million_tokens.output != OPENAI_OUTPUT_PRICE_PER_MILLION
        ):
            raise ValueError("pricing differs from the adapter lock")
        return self

    @property
    def provider_identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider=self.provider,
            model=self.model_snapshot,
            version=MODEL_VERSION,
        )


class OpenAICostProjection(_StrictFrozenModel):
    """Conservative token/cost projection for a fixed request-attempt set."""

    request_attempt_count: int = Field(ge=1)
    estimated_input_tokens: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    estimated_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)


class OpenAICostEstimates(_StrictFrozenModel):
    """Separate smoke/full budgets for one attempt and the retry ceiling."""

    smoke_one_attempt: OpenAICostProjection
    smoke_retry_ceiling: OpenAICostProjection
    full_one_attempt: OpenAICostProjection
    full_retry_ceiling: OpenAICostProjection


class OpenAIPreflightReport(_StrictFrozenModel):
    schema_version: Literal["openai-pilot-preflight/1"]
    passed: bool
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    outbound_payload_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_identity: ProviderIdentity
    settings: GenerationSettings
    context_count: int
    matched_pair_count: int
    request_count: int
    smoke_request_ids: tuple[str, ...]
    estimated_input_tokens: int
    reserved_output_tokens: int
    estimated_max_cost_usd: float
    # Added compatibly: legacy preflights omit this field and retain their original digest.
    cost_estimates: OpenAICostEstimates | None = None
    checks: dict[str, bool]

    @model_validator(mode="after")
    def _derived_pass_and_census(self) -> Self:
        if self.passed != all(self.checks.values()):
            raise ValueError("preflight PASS must be derived from all checks")
        if (self.context_count, self.matched_pair_count, self.request_count) != (15, 15, 30):
            raise ValueError("preflight must preserve the 15-context/30-request census")
        if len(self.smoke_request_ids) != 8 or len(set(self.smoke_request_ids)) != 8:
            raise ValueError("smoke plan must contain exactly eight unique requests")
        if self.cost_estimates is not None:
            costs = self.cost_estimates
            attempts = self.settings.max_attempts
            if (
                costs.smoke_one_attempt.request_attempt_count != 8
                or costs.full_one_attempt.request_attempt_count != 30
            ):
                raise ValueError("one-attempt cost projections must preserve the 8/30 census")
            for one, ceiling in (
                (costs.smoke_one_attempt, costs.smoke_retry_ceiling),
                (costs.full_one_attempt, costs.full_retry_ceiling),
            ):
                if (
                    ceiling.request_attempt_count != one.request_attempt_count * attempts
                    or ceiling.estimated_input_tokens
                    != one.estimated_input_tokens * attempts
                    or ceiling.reserved_output_tokens != one.reserved_output_tokens * attempts
                    or abs(
                        ceiling.estimated_cost_usd
                        - one.estimated_cost_usd * attempts
                    )
                    > 1e-12
                ):
                    raise ValueError("retry-ceiling cost projection is not derived")
            full = costs.full_one_attempt
            if (
                self.estimated_input_tokens != full.estimated_input_tokens
                or self.reserved_output_tokens != full.reserved_output_tokens
                or abs(self.estimated_max_cost_usd - full.estimated_cost_usd) > 1e-12
            ):
                raise ValueError("legacy full-run cost fields differ from the explicit budget")
        return self


def _cost_projection(
    payloads: tuple[dict[str, object], ...],
    config: OpenAIPilotConfig,
    *,
    attempts: int,
) -> OpenAICostProjection:
    estimated_input = sum(max(1, len(canonical_json(payload)) // 4) for payload in payloads)
    reserved_output = len(payloads) * config.settings.max_output_tokens
    one_attempt_cost = (
        estimated_input * config.pricing_usd_per_million_tokens.input
        + reserved_output * config.pricing_usd_per_million_tokens.output
    ) / 1_000_000
    return OpenAICostProjection(
        request_attempt_count=len(payloads) * attempts,
        estimated_input_tokens=estimated_input * attempts,
        reserved_output_tokens=reserved_output * attempts,
        estimated_cost_usd=one_attempt_cost * attempts,
    )


def load_openai_pilot_config(path: str | Path) -> OpenAIPilotConfig:
    payload = yaml.safe_load(Path(path).read_text("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OpenAI pilot config must be a YAML mapping")
    return OpenAIPilotConfig.model_validate(payload)


def openai_outbound_payload(
    request: DiagnosisRequest, config: OpenAIPilotConfig
) -> dict[str, object]:
    """Build the exact secret-free payload; no client and no network are involved."""

    if request.provider_identity != config.provider_identity:
        raise ValueError("request provider identity differs from the frozen OpenAI config")
    if request.settings != config.settings:
        raise ValueError("request settings differ from the frozen OpenAI config")
    return {
        "model": config.model_snapshot,
        "messages": list(request.provider_messages()),
        "response_format": openai_output_json_schema(),
        "temperature": request.settings.temperature,
        "top_p": request.settings.top_p,
        "seed": request.settings.seed,
        "max_tokens": request.settings.max_output_tokens,
        "timeout": request.settings.timeout_seconds,
    }


def _contains_forbidden_outbound_material(payloads: tuple[dict[str, object], ...]) -> bool:
    serialized = canonical_json(payloads).casefold()
    forbidden = (
        "api_key",
        "authorization",
        "bearer ",
        "ground_truth",
        "case_family_id",
        "evidence_bundle_id",
        "evidence_condition",
        "expected_diagnosis_behavior",
        "missing_key",
        "distractor",
    )
    return any(marker in serialized for marker in forbidden) or re.search(
        r"\bsk-[a-z0-9_-]{12,}\b", serialized
    ) is not None


def build_openai_preflight(
    evidence_store_dir: str | Path,
    config: OpenAIPilotConfig,
) -> OpenAIPreflightReport:
    """Recompute a complete 30-request plan without constructing an API client."""

    store = load_bundle_store(evidence_store_dir)
    if len(store.bundles) != 15:
        raise ValueError("OpenAI preflight requires the canonical 15-bundle evidence store")
    views = tuple(project_diagnosis_evidence(bundle) for bundle in store.bundles)
    requests = build_matched_requests(
        views,
        provider_identity=config.provider_identity,
        settings=config.settings,
    )
    validate_matched_requests(requests)
    validate_source_binding(requests, views)
    outbound = tuple(openai_outbound_payload(request, config) for request in requests)

    request_by_key = {
        (request.diagnosis_view.diagnosis_context_id, request.variant): request
        for request in requests
    }
    family_ids = sorted({bundle.case_family_id for bundle in store.bundles})[:2]
    smoke_ids: list[str] = []
    for family_id in family_ids:
        for condition in ("full", "missing_key"):
            bundle = next(
                item
                for item in store.bundles
                if item.case_family_id == family_id and item.evidence_condition == condition
            )
            context_id = project_diagnosis_evidence(bundle).diagnosis_context_id
            for variant in (PilotVariant.B1_PLAIN, PilotVariant.A3_EVIDENCE_CONTRACT):
                smoke_ids.append(request_by_key[(context_id, variant)].request_id)

    outbound_by_request_id = {
        request.request_id: payload for request, payload in zip(requests, outbound, strict=True)
    }
    smoke_outbound = tuple(outbound_by_request_id[request_id] for request_id in smoke_ids)
    smoke_one = _cost_projection(smoke_outbound, config, attempts=1)
    full_one = _cost_projection(outbound, config, attempts=1)
    cost_estimates = OpenAICostEstimates(
        smoke_one_attempt=smoke_one,
        smoke_retry_ceiling=_cost_projection(
            smoke_outbound, config, attempts=config.settings.max_attempts
        ),
        full_one_attempt=full_one,
        full_retry_ceiling=_cost_projection(
            outbound, config, attempts=config.settings.max_attempts
        ),
    )
    checks = {
        "exact_model_snapshot_locked": config.model_snapshot == MODEL_SNAPSHOT,
        "sdk_version_locked": config.sdk_version == OPENAI_SDK_VERSION,
        "no_tools_web_or_retrieval": not (
            config.capabilities.tools
            or config.capabilities.web_search
            or config.capabilities.retrieval
        ),
        "structured_output_shared": all(
            payload["response_format"] == openai_output_json_schema() for payload in outbound
        ),
        "fifteen_matched_pairs": len(requests) == 30 and len(views) == 15,
        "outbound_contains_no_secret_or_evaluator_metadata": not _contains_forbidden_outbound_material(
            outbound
        ),
        "preflight_only_no_send_authorized": config.execution.preflight_only,
    }
    config_payload = config.model_dump(mode="json")
    return OpenAIPreflightReport(
        schema_version=PREFLIGHT_SCHEMA_VERSION,
        passed=all(checks.values()),
        source_evidence_store_sha256=store.manifest.store_sha256,
        config_sha256=sha256_text(canonical_json(config_payload)),
        request_set_sha256=sha256_text(
            canonical_json([request.model_dump(mode="json") for request in requests])
        ),
        outbound_payload_set_sha256=sha256_text(canonical_json(outbound)),
        provider_identity=config.provider_identity,
        settings=config.settings,
        context_count=15,
        matched_pair_count=15,
        request_count=30,
        smoke_request_ids=tuple(smoke_ids),
        estimated_input_tokens=full_one.estimated_input_tokens,
        reserved_output_tokens=full_one.reserved_output_tokens,
        estimated_max_cost_usd=full_one.estimated_cost_usd,
        cost_estimates=cost_estimates,
        checks=checks,
    )


def write_openai_preflight(report: OpenAIPreflightReport, output_path: str | Path) -> None:
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"refusing to replace an existing preflight report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False
    ) + "\n"
    with output.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def openai_preflight_sha256(report: OpenAIPreflightReport) -> str:
    """Return the canonical confirmation digest for one validated preflight."""

    # Excluding None preserves the original confirmation digest of legacy reports
    # created before the additive cost-estimates block existed.
    return sha256_text(canonical_json(report.model_dump(mode="json", exclude_none=True)))


def preflight_matches_recomputed(
    persisted: OpenAIPreflightReport,
    recomputed: OpenAIPreflightReport,
) -> bool:
    """Compare a preflight to the current plan while accepting genuine legacy reports."""

    if persisted.cost_estimates is None:
        recomputed = recomputed.model_copy(update={"cost_estimates": None})
    return persisted == recomputed


def load_openai_preflight(path: str | Path) -> OpenAIPreflightReport:
    """Strictly load a persisted preflight report without trusting its filename."""

    raw = Path(path).read_text("utf-8")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"preflight contains duplicate JSON key: {key}")
            result[key] = value
        return result

    json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    return OpenAIPreflightReport.model_validate_json(raw)
