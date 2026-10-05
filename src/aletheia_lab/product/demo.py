"""Packaged deterministic ProductView used by the offline product demo."""

from __future__ import annotations

from importlib.resources import files
from typing import Final, cast

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.view import ProductView

_FIXTURE_NAME: Final[str] = "synthetic_p6_view.json"
_FIXTURE_INVALID_MESSAGE: Final[str] = "The packaged product demo failed its integrity check."


def demo_product_view() -> dict[str, object]:
    """Return a fresh validated copy of the shared synthetic ProductView."""

    try:
        payload = files("aletheia_lab.product").joinpath(_FIXTURE_NAME).read_bytes()
        view = ProductView.model_validate_json(payload)
    except (OSError, TypeError, ValueError):
        raise ProductError("demo_integrity_error", _FIXTURE_INVALID_MESSAGE) from None
    return cast(dict[str, object], view.model_dump(mode="json"))


__all__ = ["demo_product_view"]
