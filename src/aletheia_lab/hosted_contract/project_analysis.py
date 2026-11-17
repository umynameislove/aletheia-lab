"""Project-scoped analysis inputs use the existing local-evidence backend."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aletheia_lab.product.questions import checked_product_question

from .session_capabilities import Version


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Version
    snapshot_id: Annotated[str, Field(pattern=r"^(?:p3-snapshot|upload-snapshot)-[0-9a-f]{64}$")]
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    backend: Literal["local_evidence"] = "local_evidence"

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        return checked_product_question(value)
