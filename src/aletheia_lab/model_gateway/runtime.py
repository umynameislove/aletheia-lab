"""Bounded provider-neutral runtime for deterministic adapter conformance."""

from __future__ import annotations

import json
import re
from typing import Literal, cast

from pydantic import ValidationError

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.execution_contracts import (
    AttemptIdentity,
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    TechnicalIssue,
)
from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    AttemptOutcome,
    AttemptRecord,
    AttemptTiming,
    CancellationProbe,
    Clock,
    GatewayContractError,
    GatewayExecutionResult,
    GatewayRequest,
    ParsedResponseArtifact,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    RuntimePolicyReference,
    TerminalStatus,
    UsageMetadata,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_STAGE = "model_gateway"
_OPAQUE_REFERENCE = re.compile(r"^ev-[0-9a-f]{64}$")


def prepare_gateway_request(
    *,
    manifest: EvaluationManifestReference,
    case: EvaluationCaseReference,
    model_policy: ModelPolicyReference,
    context: EvaluationContextPayload,
    prompt_text: str,
    response_schema: dict[str, object],
    runtime_policy: RuntimePolicyReference,
) -> GatewayRequest:
    """Bind exact outbound content to immutable K7-A identity before adapter access."""

    response_schema_json = canonical_project_json(response_schema)
    _validate_schema_shape(response_schema)
    try:
        prompt_sha256 = content_sha256(prompt_text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise GatewayContractError("gateway prompt contains invalid Unicode") from exc
    response_schema_sha256 = content_sha256(response_schema_json.encode("utf-8"))
    attempt = AttemptIdentity.build(
        manifest=manifest,
        case=case,
        model_policy=model_policy,
        context_sha256=context.context_sha256,
        prompt_sha256=prompt_sha256,
        response_schema_sha256=response_schema_sha256,
        attempt_ordinal=1,
    )
    try:
        return GatewayRequest(
            initial_attempt=attempt,
            context=context,
            prompt_text=prompt_text,
            response_schema_json=response_schema_json,
            runtime_policy=runtime_policy,
        )
    except ValidationError as exc:
        raise GatewayContractError("gateway request failed immutable binding checks") from exc


def execute_gateway_request(
    request: GatewayRequest,
    *,
    adapter: ProviderAdapter,
    clock: Clock,
    cancellation: CancellationProbe,
) -> GatewayExecutionResult:
    """Execute within explicit bounds without fallback or scientific interpretation."""

    checked = GatewayRequest.model_validate(request.model_dump(mode="python"))
    expected_binding = ProviderBinding.from_model_policy(checked.initial_attempt.model_policy)
    try:
        actual_binding = ProviderBinding.model_validate(adapter.binding.model_dump(mode="python"))
    except Exception:
        actual_binding = None
    if actual_binding != expected_binding:
        attempt = checked.initial_attempt
        issue = _issue(attempt, "adapter_binding_mismatch")
        timing = _zero_timing(clock)
        return _result(
            checked,
            "identity_rejected",
            (_record(attempt, "identity_mismatch", timing, issue=issue),),
            issue=issue,
        )

    records: list[AttemptRecord] = []
    for ordinal in range(1, checked.runtime_policy.max_attempts + 1):
        attempt = _retry_attempt(checked, ordinal)
        if cancellation.is_cancelled():
            issue = _issue(attempt, "cancelled_before_adapter")
            timing = _zero_timing(clock)
            records.append(_record(attempt, "cancelled", timing, issue=issue))
            return _result(checked, "cancelled", tuple(records), issue=issue)

        call = ProviderCall(
            request_identity_sha256=attempt.request_identity_sha256,
            attempt_id=attempt.attempt_id,
            attempt_identity_sha256=attempt.attempt_identity_sha256,
            attempt_ordinal=attempt.attempt_ordinal,
            context_sha256=attempt.context_sha256,
            prompt_sha256=attempt.prompt_sha256,
            response_schema_sha256=attempt.response_schema_sha256,
            context_json=checked.context.canonical_json(),
            prompt_text=checked.prompt_text,
            response_schema_json=checked.response_schema_json,
            runtime_policy=checked.runtime_policy,
        )
        started = clock.now_ns()
        try:
            returned = adapter.invoke(call)
            envelope = ProviderEnvelope.model_validate(returned.model_dump(mode="python"))
        except AdapterInvocationError as exc:
            ended = clock.now_ns()
            timing = AttemptTiming(
                started_ns=started,
                ended_ns=ended,
                latency_ns=ended - started,
            )
            if (
                not isinstance(exc.provider_attempt_ref, str)
                or _OPAQUE_REFERENCE.fullmatch(exc.provider_attempt_ref) is None
            ):
                issue = _issue(attempt, "invalid_provider_error_metadata")
                records.append(_record(attempt, "permanent_error", timing, issue=issue))
                return _result(checked, "provider_failed", tuple(records), issue=issue)
            status, outcome, issue_code = _error_disposition(exc, ordinal, checked)
            issue = _issue(attempt, issue_code)
            records.append(
                _record(
                    attempt,
                    outcome,
                    timing,
                    provider_attempt_ref=exc.provider_attempt_ref,
                    issue=issue,
                )
            )
            if status is None:
                continue
            return _result(checked, status, tuple(records), issue=issue)
        except Exception:
            ended = clock.now_ns()
            timing = AttemptTiming(
                started_ns=started,
                ended_ns=ended,
                latency_ns=ended - started,
            )
            issue = _issue(attempt, "untyped_provider_error")
            records.append(_record(attempt, "permanent_error", timing, issue=issue))
            return _result(checked, "provider_failed", tuple(records), issue=issue)

        ended = clock.now_ns()
        timing = AttemptTiming(started_ns=started, ended_ns=ended, latency_ns=ended - started)
        terminal = _validate_response_boundary(
            checked=checked,
            attempt=attempt,
            envelope=envelope,
            timing=timing,
            records=records,
            cancellation=cancellation,
        )
        if terminal is not None:
            return terminal

    raise AssertionError("bounded gateway loop ended without a terminal result")


def _validate_response_boundary(
    *,
    checked: GatewayRequest,
    attempt: AttemptIdentity,
    envelope: ProviderEnvelope,
    timing: AttemptTiming,
    records: list[AttemptRecord],
    cancellation: CancellationProbe,
) -> GatewayExecutionResult | None:
    expected_binding = ProviderBinding.from_model_policy(attempt.model_policy)
    if (
        envelope.request_identity_sha256 != attempt.request_identity_sha256
        or envelope.binding != expected_binding
    ):
        issue = _issue(attempt, "provider_response_identity_mismatch")
        records.append(
            _record(
                attempt,
                "identity_mismatch",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                issue=issue,
            )
        )
        return _result(checked, "identity_rejected", tuple(records), issue=issue)
    if timing.latency_ns > checked.runtime_policy.timeout_ns:
        exhausted = (
            attempt.attempt_ordinal == checked.runtime_policy.max_attempts
            and checked.runtime_policy.max_attempts > 1
        )
        issue = _issue(attempt, "retry_exhausted" if exhausted else "provider_timeout")
        records.append(
            _record(
                attempt,
                "timeout",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                issue=issue,
            )
        )
        if attempt.attempt_ordinal < checked.runtime_policy.max_attempts:
            return None
        status: TerminalStatus = (
            "timed_out" if checked.runtime_policy.max_attempts == 1 else "retry_exhausted"
        )
        return _result(checked, status, tuple(records), issue=issue)
    if cancellation.is_cancelled():
        issue = _issue(attempt, "cancelled_before_publication")
        records.append(
            _record(
                attempt,
                "cancelled",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                issue=issue,
            )
        )
        return _result(checked, "cancelled", tuple(records), issue=issue)

    raw = envelope.raw_response
    if raw.byte_count > checked.runtime_policy.max_response_bytes:
        issue = _issue(attempt, "oversized_response")
        records.append(
            _record(
                attempt,
                "oversized_response",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                response_mode=envelope.response_mode,
                usage=envelope.usage,
                issue=issue,
            )
        )
        return _result(
            checked,
            "oversized_response",
            tuple(records),
            raw_response=raw,
            issue=issue,
        )
    try:
        parsed = _parse_json_object(raw, checked.response_schema_json)
    except (UnicodeDecodeError, json.JSONDecodeError, GatewayContractError):
        issue = _issue(attempt, "response_parse_failure")
        records.append(
            _record(
                attempt,
                "parse_failure",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                response_mode=envelope.response_mode,
                usage=envelope.usage,
                issue=issue,
            )
        )
        return _result(
            checked,
            "parse_failed",
            tuple(records),
            raw_response=raw,
            issue=issue,
        )

    parsed_artifact = ParsedResponseArtifact.from_payload(parsed)
    if cancellation.is_cancelled():
        issue = _issue(attempt, "cancelled_before_publication")
        records.append(
            _record(
                attempt,
                "cancelled",
                timing,
                provider_attempt_ref=envelope.provider_attempt_ref,
                issue=issue,
            )
        )
        return _result(checked, "cancelled", tuple(records), issue=issue)
    records.append(
        _record(
            attempt,
            "response",
            timing,
            provider_attempt_ref=envelope.provider_attempt_ref,
            response_mode=envelope.response_mode,
            usage=envelope.usage,
        )
    )
    return _result(
        checked,
        "parsed",
        tuple(records),
        raw_response=raw,
        parsed_response=parsed_artifact,
    )


def _parse_json_object(
    raw: RawResponseArtifact,
    response_schema_json: str,
) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise GatewayContractError("provider response contains duplicate JSON keys")
            parsed[key] = value
        return parsed

    decoded = raw.content.decode("utf-8", errors="strict")

    def reject_constant(value: str) -> object:
        raise GatewayContractError(f"non-finite JSON constant rejected: {value}")

    payload = json.loads(
        decoded,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )
    if not isinstance(payload, dict):
        raise GatewayContractError("provider response must be a JSON object")
    checked_payload = cast(dict[str, object], payload)
    _validate_object_schema(checked_payload, response_schema_json)
    return checked_payload


def _validate_object_schema(payload: dict[str, object], schema_json: str) -> None:
    schema = json.loads(schema_json)
    if not isinstance(schema, dict):
        raise GatewayContractError("response schema must be a JSON object")
    _validate_schema_shape(cast(dict[str, object], schema))
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    if any(key not in payload for key in required):
        raise GatewayContractError("provider response is missing a required field")
    if schema.get("additionalProperties") is False and any(
        key not in properties for key in payload
    ):
        raise GatewayContractError("provider response contains an unknown field")
    expected_python_types: dict[str, type[object]] = {
        "array": list,
        "boolean": bool,
        "integer": int,
        "object": dict,
        "string": str,
    }
    for key, value in payload.items():
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict) or "type" not in property_schema:
            continue
        expected_name = property_schema["type"]
        if expected_name == "null":
            matches = value is None
        elif expected_name == "number":
            matches = isinstance(value, int | float) and not isinstance(value, bool)
        elif isinstance(expected_name, str) and expected_name in expected_python_types:
            expected_type = expected_python_types[expected_name]
            matches = isinstance(value, expected_type)
            if expected_name in {"integer", "number"} and isinstance(value, bool):
                matches = False
        else:
            raise GatewayContractError("response schema uses an unsupported field type")
        if not matches:
            raise GatewayContractError("provider response field does not match schema type")


def _validate_schema_shape(schema: dict[str, object]) -> None:
    supported_root_fields = {
        "additionalProperties",
        "properties",
        "required",
        "type",
    }
    if any(key not in supported_root_fields for key in schema):
        raise GatewayContractError("response schema uses unsupported root constraints")
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    if schema.get("type") != "object":
        raise GatewayContractError("response schema must describe one JSON object")
    if (
        not isinstance(required, list)
        or not all(isinstance(value, str) for value in required)
        or len(required) != len(set(required))
        or not isinstance(properties, dict)
        or not all(isinstance(key, str) and isinstance(value, dict) for key, value in properties.items())
    ):
        raise GatewayContractError("response schema has invalid object constraints")
    if any(value not in properties for value in required):
        raise GatewayContractError("response schema required fields lack property definitions")
    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise GatewayContractError("response schema additionalProperties must be boolean")
    supported_types = {"array", "boolean", "integer", "null", "number", "object", "string"}
    for property_schema in properties.values():
        if any(key != "type" for key in property_schema):
            raise GatewayContractError("response schema uses unsupported field constraints")
        property_type = property_schema.get("type")
        if not isinstance(property_type, str) or property_type not in supported_types:
            raise GatewayContractError("response schema property type is unsupported")


def _retry_attempt(request: GatewayRequest, ordinal: int) -> AttemptIdentity:
    initial = request.initial_attempt
    attempt = AttemptIdentity.build(
        manifest=initial.manifest,
        case=initial.case,
        model_policy=initial.model_policy,
        context_sha256=initial.context_sha256,
        prompt_sha256=initial.prompt_sha256,
        response_schema_sha256=initial.response_schema_sha256,
        attempt_ordinal=ordinal,
    )
    if attempt.request_identity_sha256 != initial.request_identity_sha256:
        raise GatewayContractError("retry changed immutable request identity")
    return attempt


def _error_disposition(
    error: AdapterInvocationError,
    ordinal: int,
    request: GatewayRequest,
) -> tuple[TerminalStatus | None, AttemptOutcome, str]:
    if error.code == "provider_cancelled":
        return "cancelled", "cancelled", "provider_cancelled"
    if error.code == "permanent_provider_error" or not error.retryable:
        return "provider_failed", "permanent_error", "permanent_provider_error"
    outcome: AttemptOutcome = (
        "timeout" if error.code == "provider_timeout" else "transient_error"
    )
    if ordinal < request.runtime_policy.max_attempts:
        return None, outcome, error.code
    if request.runtime_policy.max_attempts == 1 and error.code == "provider_timeout":
        return "timed_out", outcome, error.code
    return "retry_exhausted", outcome, "retry_exhausted"


def _issue(attempt: AttemptIdentity, code: str) -> TechnicalIssue:
    return TechnicalIssue.build(
        code=code,
        stage=_STAGE,
        severity="error",
        subject_reference_id=attempt.attempt_id,
        message="The provider-neutral gateway rejected a technical execution state.",
        authorization_ref=attempt.manifest.authorization_ref,
        provenance_sha256=attempt.case.provenance_sha256,
        visibility="public",
    )


def _zero_timing(clock: Clock) -> AttemptTiming:
    instant = clock.now_ns()
    return AttemptTiming(started_ns=instant, ended_ns=instant, latency_ns=0)


def _record(
    attempt: AttemptIdentity,
    outcome: AttemptOutcome,
    timing: AttemptTiming,
    *,
    provider_attempt_ref: str | None = None,
    response_mode: Literal["structured", "abstention"] | None = None,
    usage: UsageMetadata | None = None,
    issue: TechnicalIssue | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt=attempt,
        outcome=outcome,
        timing=timing,
        provider_attempt_ref=provider_attempt_ref,
        response_mode=response_mode,
        usage=usage,
        issue=issue,
    )


def _result(
    request: GatewayRequest,
    status: TerminalStatus,
    attempts: tuple[AttemptRecord, ...],
    *,
    raw_response: RawResponseArtifact | None = None,
    parsed_response: ParsedResponseArtifact | None = None,
    issue: TechnicalIssue | None = None,
) -> GatewayExecutionResult:
    return GatewayExecutionResult(
        request_identity_sha256=request.initial_attempt.request_identity_sha256,
        status=status,
        attempts=attempts,
        raw_response=raw_response,
        parsed_response=parsed_response,
        issue=issue,
    )
