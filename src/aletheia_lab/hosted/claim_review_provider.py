"""A bounded provider observation of the local historical claim check."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Literal, cast
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from aletheia_lab.hosted_contract.claim_review import AssertedVerdict, ClaimReviewFinding
from aletheia_lab.hosted_contract.claim_review import (
    ClaimReviewProviderResult as ClaimReviewProviderResult,
)
from aletheia_lab.hosted_contract.imports import Sha256
from aletheia_lab.product.view import AnyProductView
from aletheia_lab.project.identity import canonical_project_sha256

from .claim_review import evaluate_claim_assertion
from .historical_state_evidence import HistoricalState
from .upload_snapshot import AnyUploadSnapshot, HistoricalStateUploadSnapshot

CLAIM_PROVIDER_MODEL = "gpt-6-luna"
CLAIM_PROVIDER_ENDPOINT = "https://api.openai.com/v1/responses"
CLAIM_PROVIDER_MAX_INPUT_TOKENS = 2048
CLAIM_PROVIDER_MAX_OUTPUT_TOKENS = 128
# Standard $0.125/1M input (cache-write ceiling) and $0.50/1M output.
CLAIM_PROVIDER_HOLD_MICROUSD = 320
CLAIM_PROVIDER_TIMEOUT_SECONDS = 20
_MAX_BODY_BYTES = 1536
_MAX_RESPONSE_BYTES = 32768
_REQUEST_ID = re.compile(r"req_[A-Za-z0-9_-]{1,124}")

_INSTRUCTIONS = (
    "Check the submitted assertion on this single case. Each state's fields "
    "are opaque tags. The observed state is allowed exactly when each tag "
    "equals its counterpart in the allowed state. The assertion is supported "
    "if it names the resulting allowed or denied verdict. Return an "
    "approve_in_scope decision for a supported assertion, otherwise revise."
)
_Decision = Literal["approve_in_scope", "revise"]


class _RestrictedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class _TaggedState(_RestrictedInput):
    namespace: Sha256
    generation: Sha256
    state_sha256: Sha256


class _ProviderProjection(_RestrictedInput):
    asserted_verdict: AssertedVerdict
    observed_state: _TaggedState
    allowed_state: _TaggedState


class _DecisionBody(_RestrictedInput):
    decision: _Decision


class _ResponseModel(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)


class _Usage(_ResponseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class _OutputText(_ResponseModel):
    type: Literal["output_text"]
    text: str = Field(min_length=1, max_length=256)


class _AssistantMessage(_ResponseModel):
    type: Literal["message"]
    status: Literal["completed"]
    role: Literal["assistant"]
    content: list[_OutputText] = Field(min_length=1, max_length=1)


class _ProviderCompletion(_ResponseModel):
    id: Annotated[str, Field(pattern=r"^resp_[A-Za-z0-9_-]{1,123}$")]
    object: Literal["response"]
    status: Literal["completed"]
    model: Literal["gpt-6-luna"]
    service_tier: Literal["default"]
    output: list[_AssistantMessage] = Field(min_length=1, max_length=1)


@dataclass(frozen=True, repr=False)
class ClaimReviewProviderInput:
    body: bytes
    request_sha256: str
    finding: ClaimReviewFinding


def _tag_state(state: HistoricalState, snapshot_id: str) -> _TaggedState:
    return _TaggedState.model_validate({
        field: canonical_project_sha256({
            "domain": "claim-review-provider-tags/v1",
            "snapshot": snapshot_id,
            "field": field,
            "value": value,
        })
        for field, value in state.model_dump(mode="json").items()
    })


def _request_body(projection: _ProviderProjection) -> bytes:
    return json.dumps({
        "model": CLAIM_PROVIDER_MODEL,
        "instructions": _INSTRUCTIONS,
        "input": projection.model_dump_json(),
        "reasoning": {"effort": "none"},
        "service_tier": "default",
        "store": False,
        "stream": False,
        "truncation": "disabled",
        "max_output_tokens": CLAIM_PROVIDER_MAX_OUTPUT_TOKENS,
        "tools": [],
        "tool_choice": "none",
        "text": {"format": {
            "type": "json_schema", "name": "claim_assertion_check", "strict": True,
            "schema": _DecisionBody.model_json_schema(),
        }},
    }, separators=(",", ":"), sort_keys=True).encode("ascii")


def prepare_claim_review_provider(
    view: AnyProductView,
    claim_id: str,
    asserted_verdict: AssertedVerdict,
    snapshot: AnyUploadSnapshot | None,
) -> ClaimReviewProviderInput | None:
    """Project only a verified, fully defined claim; the caller decides external authorization."""
    finding = evaluate_claim_assertion(view, claim_id, asserted_verdict, snapshot)
    if not isinstance(snapshot, HistoricalStateUploadSnapshot) or finding.decision not in {"approve_in_scope", "revise"}:
        return None
    evidence = snapshot.historical_state
    observed, contract = evidence.receipt.observation, evidence.contract
    if observed is None or contract is None:
        return None
    body = _request_body(_ProviderProjection(
        asserted_verdict=asserted_verdict,
        observed_state=_tag_state(observed, snapshot.snapshot_id),
        allowed_state=_tag_state(contract.allowed_state, snapshot.snapshot_id),
    ))
    if len(body) > _MAX_BODY_BYTES:
        return None
    return ClaimReviewProviderInput(body, sha256(body).hexdigest(), finding)


def _approved(prepared: ClaimReviewProviderInput, approved_request_sha256: str | None) -> bool:
    if len(prepared.body) > _MAX_BODY_BYTES or prepared.finding.decision not in {"approve_in_scope", "revise"}:
        return False
    if approved_request_sha256 != prepared.request_sha256 or sha256(prepared.body).hexdigest() != approved_request_sha256:
        return False
    try:
        body = json.loads(prepared.body)
        projection = _ProviderProjection.model_validate_json(body["input"])
    except (ValueError, KeyError, TypeError, RecursionError):
        return False
    return prepared.body == _request_body(projection)


def _valid_attempt_id(attempt_id: str) -> bool:
    try:
        parsed = UUID(attempt_id)
    except ValueError:
        return False
    return parsed.version == 4 and attempt_id in {str(parsed), parsed.hex}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate provider JSON field")
    return result


def _body_dict(raw: bytes) -> dict[str, object] | None:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _read_usage(raw: object) -> _Usage | None:
    try:
        usage = _Usage.model_validate(raw)
    except ValidationError:
        return None
    if (usage.total_tokens != usage.input_tokens + usage.output_tokens
            or usage.input_tokens > CLAIM_PROVIDER_MAX_INPUT_TOKENS
            or usage.output_tokens > CLAIM_PROVIDER_MAX_OUTPUT_TOKENS):
        return None
    return usage


def _read_decision(body: dict[str, object]) -> _Decision | None:
    try:
        completion = _ProviderCompletion.model_validate(body)
        choice = _body_dict(completion.output[0].content[0].text.encode("utf-8"))
        return _DecisionBody.model_validate(choice).decision
    except (ValueError, UnicodeError):
        return None


def _without_usage(status: Literal["unavailable", "unresolved"], request_id: str | None = None) -> ClaimReviewProviderResult:
    return ClaimReviewProviderResult(status=status, provider_request_id=request_id,
                                      input_tokens=None, output_tokens=None,
                                      provider_decision=None, agrees_with_host=None)


def _parse_result(raw: bytes, request_id: str | None, finding: ClaimReviewFinding) -> ClaimReviewProviderResult:
    parsed = _body_dict(raw)
    if parsed is None or (parsed.get("object"), parsed.get("model"), parsed.get("service_tier")) != (
        "response", CLAIM_PROVIDER_MODEL, "default",
    ):
        # Usage outside the approved model and pricing tier cannot settle this attempt.
        return _without_usage("unresolved", request_id)
    usage = _read_usage(parsed.get("usage"))
    if usage is None:
        return _without_usage("unresolved", request_id)

    decision = _read_decision(parsed) if request_id is not None and usage.output_tokens > 0 else None
    return ClaimReviewProviderResult(
        status="completed" if decision is not None else "unresolved",
        provider_request_id=request_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        provider_decision=decision,
        agrees_with_host=(decision == finding.decision) if decision is not None else None,
    )


async def _read_bounded_body(response: httpx2.Response) -> bytes | None:
    if response.status_code != 200 or response.headers.get("content-encoding", "identity").lower() != "identity":
        return None
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return None
    result = bytearray()
    async for chunk in response.aiter_bytes():
        if len(result) + len(chunk) > _MAX_RESPONSE_BYTES:
            return None
        result.extend(chunk)
    return bytes(result)


async def dispatch_claim_review_provider(
    prepared: ClaimReviewProviderInput,
    *,
    approved_request_sha256: str | None,
    api_key: SecretStr | None,
    attempt_id: str,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> ClaimReviewProviderResult:
    """Make one HTTP attempt after the caller records intent and validates its approved plan."""
    if not api_key or not _valid_attempt_id(attempt_id) or not _approved(prepared, approved_request_sha256):
        return _without_usage("unavailable")
    key = api_key.get_secret_value()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", key):
        return _without_usage("unavailable")
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "X-Client-Request-Id": attempt_id,
    }
    request_id = None
    try:
        async with (
            asyncio.timeout(CLAIM_PROVIDER_TIMEOUT_SECONDS),
            httpx2.AsyncClient(
                transport=transport if transport is not None else httpx2.AsyncHTTPTransport(retries=0, trust_env=False),
                timeout=CLAIM_PROVIDER_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False,
            ) as client,
            client.stream("POST", CLAIM_PROVIDER_ENDPOINT, headers=headers, content=prepared.body) as response,
        ):
            raw_id = response.headers.get("x-request-id", "")
            request_id = raw_id if _REQUEST_ID.fullmatch(raw_id) else None
            raw = await _read_bounded_body(response)
    except (httpx2.HTTPError, TimeoutError):
        return _without_usage("unresolved", request_id)
    return (_parse_result(raw, request_id, prepared.finding) if raw is not None
            else _without_usage("unresolved", request_id))
