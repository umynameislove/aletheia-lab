"""Additive canonical project operation inputs; existing job envelopes are reused."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.project.identity import PROJECT_ID_PATTERN

from .job_page import JobOutput
from .session_capabilities import Identifier, Version

ResultId = Annotated[str, Field(pattern=r"^p6-result-[0-9a-f]{64}$")]


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Version
    selection_kind: Literal["claim", "node"]
    selection_id: Identifier
    question: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator("question")
    @classmethod
    def _checked_question(cls, value: str) -> str:
        return checked_product_question(value)


class FollowUpResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    project_id: Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
    result_id: ResultId
    parent_result_id: ResultId
    snapshot_id: Annotated[str, Field(pattern=r"^(?:p3-snapshot|upload-snapshot)-[0-9a-f]{64}$")]
    revision: int = Field(ge=0, le=9223372036854775807)

    @model_validator(mode="after")
    def _new_result(self) -> Self:
        if self.result_id == self.parent_result_id:
            raise ValueError("follow-up must reference a derived result")
        return self


class FollowUpJobOutput(JobOutput[FollowUpResult]):
    model_config = ConfigDict(hide_input_in_errors=True)

    operation: Literal["follow_up"]
