"""Workspace additions to the existing producer contract; consumer review pending."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Generic, Literal, Self, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from aletheia_lab.product.view import (
    ProductClaim,
    ProductEvidence,
    ProductGraph,
    ProductProject,
    ProductResult,
    ProductRuntime,
    ProductView,
    ProductViewV2,
)

from .job_page import Page
from .session_capabilities import Identifier, Membership, ShortText, Version

Role = Literal["owner", "admin", "analyst", "reviewer", "viewer"]
GrantAction = Literal["read", "write", "review", "export", "delete"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class CreateWorkspace(_Model):
    name: ShortText

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("a workspace name is required")
        return value.strip()


class ChangeMember(_Model):
    role: Role | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _change(self) -> Self:
        if self.role is None and self.active is None:
            raise ValueError("a membership change is required")
        return self


class TransferOwner(_Model):
    user_id: Identifier


class CreateInvitation(_Model):
    user_id: Identifier
    role: Literal["admin", "analyst", "reviewer", "viewer"]
    expires_in_seconds: int = Field(default=86400, ge=60, le=604800)


class AcceptInvitation(_Model):
    token: SecretStr = Field(min_length=43, max_length=128)


class ChangeGrant(_Model):
    actions: list[GrantAction] = Field(max_length=5)

    @field_validator("actions")
    @classmethod
    def _unique(cls, value: list[GrantAction]) -> list[GrantAction]:
        if len(set(value)) != len(value):
            raise ValueError("grant actions must be unique")
        if value and "read" not in value:
            raise ValueError("object actions require read access")
        return value


class Empty(_Model):
    pass


class Workspace(_Model):
    schema_version: Version
    workspace_id: Identifier
    workspace_name: ShortText
    membership: Membership


class WorkspacePage(_Model):
    schema_version: Version
    items: list[Workspace] = Field(max_length=200)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    total: None = None
    total_kind: Literal["unknown"] = "unknown"


class Member(_Model):
    schema_version: Version
    workspace_id: Identifier
    user_id: Identifier
    role: Role
    active: bool
    revision: int = Field(ge=0)
    display_name: ShortText | None = None


class MemberPage(Page[Member]):
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    total: int | None = Field(default=None, ge=0)
    total_kind: Literal["exact", "estimated", "unknown"] = "unknown"

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if any(item.workspace_id != self.workspace_id for item in self.items):
            raise ValueError("member scope mismatch")
        return self


class Invitation(_Model):
    schema_version: Version
    workspace_id: Identifier
    invitation_id: Identifier
    user_id: Identifier
    role: Literal["admin", "analyst", "reviewer", "viewer"]
    expires_at: datetime
    token: str = Field(min_length=43, max_length=128, repr=False)

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("invitation expiry must include a timezone")
        return value


class PendingInvitation(_Model):
    schema_version: Version
    workspace_id: Identifier
    invitation_id: Identifier
    user_id: Identifier
    display_name: ShortText
    role: Literal["admin", "analyst", "reviewer", "viewer"]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("invitation expiry must include a timezone")
        return value


class InvitationPage(Page[PendingInvitation]):
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    total: int | None = Field(default=None, ge=0)
    total_kind: Literal["exact", "estimated", "unknown"] = "unknown"

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if any(item.workspace_id != self.workspace_id for item in self.items):
            raise ValueError("invitation scope mismatch")
        return self


class LookupInvitationTargets(_Model):
    email: str = Field(min_length=3, max_length=254)
    limit: int = Field(default=20, ge=1, le=20)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        if "@" not in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise ValueError("an exact email is required")
        return value


class InvitationTarget(_Model):
    user_id: Identifier
    display_name: ShortText


class InvitationTargetPage(Page[InvitationTarget]):
    items: list[InvitationTarget] = Field(max_length=20)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    total: int | None = Field(default=None, ge=0)
    total_kind: Literal["exact", "estimated", "unknown"] = "unknown"


class RevokedInvitation(_Model):
    schema_version: Version
    workspace_id: Identifier
    invitation_id: Identifier
    revoked: Literal[True]


class SourceGrant(_Model):
    schema_version: Version
    workspace_id: Identifier
    source_id: Identifier
    user_id: Identifier
    actions: list[GrantAction] = Field(max_length=5)
    revision: int = Field(ge=0)


CanonicalKind = Literal["project_bundle", "snapshot", "snapshot_comparison", "regression_event",
                        "evidence_bundle", "lineage_graph", "project_state", "result", "turn", "deletion",
                        "upload_snapshot", "upload_result"]


class ProjectSummary(_Model):
    project_id: Identifier
    revision: int = Field(ge=0)


class ProjectPage(Page[ProjectSummary]):
    pass


class RecordSummary(_Model):
    record_id: Identifier
    project_id: Identifier
    record_type: CanonicalKind
    schema_version: ShortText
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: Identifier | None
    parent_result_id: Identifier | None


class RecordPage(Page[RecordSummary]):
    project_id: Identifier | None = None

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.project_id is not None and any(item.project_id != self.project_id for item in self.items):
            raise ValueError("record page project scope mismatch")
        return self


class CanonicalRecord(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    record_id: Identifier
    record_kind: CanonicalKind
    data: dict[str, object]


_ProductViewT = TypeVar('_ProductViewT', ProductView, ProductViewV2)


class _ResultViewBase(_Model, Generic[_ProductViewT]):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: Identifier
    view: _ProductViewT

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.view.result.id != self.result_id or self.view.project.id != self.project_id:
            raise ValueError("result view parent mismatch")
        return self


class ResultView(_ResultViewBase[ProductView]):
    pass


class ResultViewV2(_ResultViewBase[ProductViewV2]):
    pass


class ResultHeader(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: Identifier
    snapshot_id: Identifier
    view_schema_version: Literal['p6-product-view/v1', 'p6-product-view/v2']
    project: ProductProject
    runtime: ProductRuntime
    result: ProductResult
    conversation_id: Identifier
    turn_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)

    @model_validator(mode='after')
    def _scope(self) -> Self:
        if self.project.id != self.project_id or self.result.id != self.result_id:
            raise ValueError('result header parent mismatch')
        return self


class ClaimPage(Page[ProductClaim]):
    project_id: Identifier
    result_id: Identifier
    snapshot_id: Identifier


class EvidencePage(Page[ProductEvidence]):
    project_id: Identifier
    result_id: Identifier
    snapshot_id: Identifier


class EvidenceView(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: Identifier
    evidence_id: Identifier
    claim_id: Identifier | None = None
    relation: Literal["citation", "counterevidence"] | None = None
    evidence: ProductEvidence

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.evidence.id != self.evidence_id or (self.claim_id is None) != (self.relation is None):
            raise ValueError("evidence parent or relation mismatch")
        return self


class GraphView(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: Identifier
    graph: ProductGraph
    truncated: bool = False
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _continuation(self) -> Self:
        if self.next_cursor is not None and not self.truncated:
            raise ValueError("a graph continuation requires truthful truncation")
        return self


class ProjectGrant(ChangeGrant):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    user_id: Identifier
    revision: int = Field(ge=0)


class PutAnnotation(_Model):
    body: str = Field(min_length=1, max_length=4000)
    status: Literal["comment", "approved", "rejected"] = "comment"

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a review body is required")
        return value


class Annotation(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: Identifier
    annotation_id: Identifier
    revision: int = Field(ge=1)
    author_id: Identifier
    body: str = Field(min_length=1, max_length=4000)
    status: Literal["comment", "approved", "rejected"]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("annotation time requires a timezone")
        return value


class AnnotationPage(Page[Annotation]):
    project_id: Identifier
    result_id: Identifier
    annotation_id: Identifier

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if any((item.workspace_id, item.project_id, item.result_id, item.annotation_id) !=
               (self.workspace_id, self.project_id, self.result_id, self.annotation_id) for item in self.items):
            raise ValueError("annotation history parent mismatch")
        return self


class ExportRequest(_Model):
    format: Literal["json", "markdown", "pdf"]


class ExportDescriptor(_Model):
    model_config = ConfigDict(json_schema_extra={
        "allOf": [{
            "if": {"properties": {"export_id": {"pattern": r"_[0-9a-f]{32}$"}}, "required": ["export_id"]},
            "then": {"properties": {"expires_at": {"type": "string", "format": "date-time"}}, "required": ["expires_at"]},
            "else": {"properties": {"expires_at": {"type": "null"}}},
        }],
    })

    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    result_id: str = Field(pattern=r"^p6-result-[0-9a-f]{64}$")
    export_id: Identifier = Field(pattern=r"^ex_(?:json|markdown|pdf)_p6-result-[0-9a-f]{64}(?:_[0-9a-f]{32})?$")
    format: Literal["json", "markdown", "pdf"]
    byte_size: int = Field(ge=0, le=33554432)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    download_url: str = Field(min_length=1, max_length=1024)
    # None identifies a legacy descriptor with unknown expiry, never an infinite TTL.
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _scope(self) -> Self:
        legacy = f"ex_{self.format}_{self.result_id}"
        durable = re.fullmatch(re.escape(legacy) + r"_[0-9a-f]{32}", self.export_id) is not None
        if (self.export_id != legacy and not durable) or self.download_url != (
                f"/v1/workspaces/{self.workspace_id}/exports/{self.export_id}/download"):
            raise ValueError("export descriptor scope mismatch")
        if durable != (self.expires_at is not None):
            raise ValueError("durable exports require an expiry; legacy descriptors have unknown expiry")
        return self


class DeletedProject(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: Identifier
    revision: int = Field(ge=1)
    tombstone_id: str = Field(pattern=r"^p6-deletion-[0-9a-f]{64}$")
    status: Literal["tombstoned"]
    retention: Literal["retained"]
