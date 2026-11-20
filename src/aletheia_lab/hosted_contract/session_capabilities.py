"""Draft producer models for review with the client; not a deployed contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Version = Literal["product-api/v1-draft"]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
ShortText = Annotated[str, Field(min_length=1, max_length=256)]
ErrorCode = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]
ExportFormat = Literal["json", "markdown", "pdf"]
PROBLEM_STATUS = {
    "session_expired": 401,
    "session_revoked": 401,
    "membership_revoked": 403,
    "permission_denied": 403,
    "csrf_invalid": 403,
    "idempotency_conflict": 409,
    "cursor_invalid": 400,
    "cursor_expired": 410,
    "revision_conflict": 412,
    "precondition_required": 428,
}
RFC3339_SHAPE = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Actor(_Contract):
    user_id: Identifier
    display_name: ShortText
    email: Annotated[str, Field(min_length=3, max_length=254)]
    email_verified: bool


class SessionInfo(_Contract):
    context_id: Identifier
    expires_at: datetime = Field(json_schema_extra={"pattern": RFC3339_SHAPE})

    @field_validator("expires_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class Session(_Contract):
    schema_version: Version
    actor: Actor
    session: SessionInfo
    csrf_token: Annotated[str, Field(min_length=1, max_length=512)]
    onboarding: Literal["ready", "needs_workspace"]
    last_workspace_id: Identifier | None
    can_create_workspace: bool = False


class Membership(_Contract):
    role: Literal["owner", "admin", "analyst", "reviewer", "viewer"]
    revision: int = Field(ge=0)


class Limits(_Contract):
    question_chars: int = Field(gt=0)
    upload_bundle_bytes: int = Field(gt=0)
    page_max: int = Field(gt=0)
    job_poll_min_ms: int = Field(gt=0)


class Capabilities(_Contract):
    schema_version: Version
    workspace_id: Identifier
    workspace_name: ShortText
    membership: Membership
    deployment_mode: Literal["hosted"]
    analysis_backends: list[ShortText] = Field(max_length=32)
    supported_source_types: list[ShortText] = Field(max_length=32)
    supported_export_formats: list[ExportFormat] = Field(max_length=32)
    allowed_actions: list[ShortText] = Field(max_length=128)
    features: dict[str, bool] = Field(max_length=128)
    limits: Limits

    def require_workspace(self, expected_workspace_id: str) -> None:
        """Reject an answer for another scope; this is not authorization."""
        if self.workspace_id != expected_workspace_id:
            raise ValueError("capabilities workspace does not match request")


class Problem(_Contract):
    """Draft safe error shape; the server must still map and redact errors."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"code": {"const": code}}, "required": ["code"]},
                    "then": {"properties": {"status": {"const": status}}},
                }
                for code, status in PROBLEM_STATUS.items()
            ]
        }
    )

    schema_version: Version
    type: Annotated[str, Field(min_length=1, max_length=256)]
    title: ShortText
    status: int = Field(ge=400, le=599)
    code: ErrorCode
    detail: Annotated[str, Field(min_length=1, max_length=1000)]
    request_id: Identifier

    @model_validator(mode="after")
    def _known_status_matches_code(self) -> Self:
        expected = PROBLEM_STATUS.get(self.code)
        if expected is not None and self.status != expected:
            raise ValueError("problem status does not match its known code")
        return self


def problem_consumer_schema() -> dict[str, object]:
    """Allow unknown extension members while preserving known-field checks."""
    schema = Problem.model_json_schema()
    schema["additionalProperties"] = True
    return schema
