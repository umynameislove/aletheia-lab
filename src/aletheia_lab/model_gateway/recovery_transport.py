"""CSR-03S wire-schema projection; never a relaxation of local acceptance."""

import json
from typing import cast

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.schema import validate_response_schema
from aletheia_lab.project.identity import canonical_project_json, normalize_text

TRANSPORT_CONTRACT: dict[str, object] = {
    "schema_version": "claim-recovery-transport/v1",
    "provider_schema_projection": "remove_string_patterns_only",
    "local_schema_validation": "original_full_schema_required",
    "local_text_validation": "trimmed_control_free_nfc_before_canonicalization",
    "token_parameter": "max_tokens",
    "maximum_output_tokens": 2048,
    "sdk_retries": 0,
    "failed_response_content_persisted": False,
    "failure_diagnostics": "allowlisted_metadata_only/v1",
}
TRANSPORT_SHA256 = canonical_execution_sha256(TRANSPORT_CONTRACT)


def provider_wire_schema(schema_json: str) -> dict[str, object]:
    """Remove only regex decoding constraints, retaining types, unions and citations.

    The provider grammar is intentionally a superset. The gateway still uses
    the ORIGINAL schema before publishing parsed output. No response is repaired.
    """
    schema = json.loads(schema_json)
    if not isinstance(schema, dict):
        raise ValueError("recovery schema must be an object")
    validate_response_schema(schema)
    if schema.get("properties", {}).get("schema_version", {}).get("const") != (
        "diagnosis-provider-output/2"
    ):
        raise ValueError("wire projection requires the registered recovery schema")
    projected = cast(dict[str, object], _project(schema))
    validate_response_schema(projected)
    return projected


def _project(value: object) -> object:
    if isinstance(value, list):
        return [_project(item) for item in value]
    if isinstance(value, dict):
        return {key: _project(item) for key, item in value.items() if key != "pattern"}
    return value


def wire_schema_json(schema_json: str) -> str:
    return canonical_project_json(provider_wire_schema(schema_json))


def validate_recovery_text(content: str) -> None:
    """Reject noncanonical text before JSON canonicalization could silently normalize it."""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("recovery output must be an object")
    _check_text(payload)


def _check_text(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _check_text(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"claim_text", "abstention_reason", "text"}:
                if not isinstance(item, str):
                    raise ValueError("recovery text must be a string")
                normalize_text(
                    item, label="recovery text", max_length=1024 if key == "text" else 2048
                )
            else:
                _check_text(item)
