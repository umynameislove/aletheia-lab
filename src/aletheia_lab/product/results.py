"""Immutable ProductView result persistence over the product lifecycle store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    ProductLifecycleStore,
    build_product_lifecycle_record,
)
from aletheia_lab.product.view import ProductView

PRODUCT_RESULT_ENVELOPE_SCHEMA_VERSION: Final[Literal["p6-result-envelope/v1"]] = (
    "p6-result-envelope/v1"
)
_RESULT_INVALID_MESSAGE: Final[str] = "The product result is not valid for this scope."
_RESULT_INTEGRITY_MESSAGE: Final[str] = "Stored product result failed its integrity check."


class ProductResultEnvelope(BaseModel):
    """Private stored envelope whose lifecycle record supplies the public result ID."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    schema_version: Literal["p6-result-envelope/v1"] = PRODUCT_RESULT_ENVELOPE_SCHEMA_VERSION
    view: dict[str, object]

    @field_validator("view")
    @classmethod
    def _view_is_independent_copy(cls, value: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(value, allow_nan=False)))


def persist_product_result(store_root: Path, view: ProductView) -> ProductView:
    """Persist one validated non-demo ProductView or accept an identical replay."""

    checked = ProductView.model_validate(view.model_dump(mode="python"))
    if checked.demo_only:
        raise ProductError("result_invalid", _RESULT_INVALID_MESSAGE)
    envelope = ProductResultEnvelope(view=checked.model_dump(mode="json"))
    record = build_product_lifecycle_record(
        record_kind="result",
        project_id=checked.project.id,
        snapshot_id=checked.snapshot.id,
        payload=envelope.model_dump(mode="json"),
    )
    persisted = _view_from_record(record)
    with ProductLifecycleStore(store_root) as lifecycle:
        lifecycle.put(record)
    return persisted


def load_product_result(store_root: Path, result_id: str) -> ProductView:
    """Load and fully revalidate one immutable ProductView result."""

    with ProductLifecycleStore(store_root) as lifecycle:
        record = lifecycle.get(result_id, expected_kind="result")
    try:
        return _view_from_record(record)
    except (TypeError, ValueError):
        raise ProductError("result_integrity_error", _RESULT_INTEGRITY_MESSAGE) from None


def _view_from_record(record: ProductLifecycleRecord) -> ProductView:
    envelope = ProductResultEnvelope.model_validate(record.payload())
    view = ProductView.model_validate_json(
        json.dumps(envelope.view, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
    if (
        record.record_kind != "result"
        or record.project_id != view.project.id
        or record.snapshot_id != view.snapshot.id
        or record.record_id != view.result.id
        or view.demo_only
        or view.visibility != "diagnosis"
    ):
        raise ValueError("stored product result does not match its immutable scope")
    return view


__all__ = [
    "PRODUCT_RESULT_ENVELOPE_SCHEMA_VERSION",
    "ProductResultEnvelope",
    "load_product_result",
    "persist_product_result",
]
