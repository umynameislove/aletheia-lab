"""Offline product facade for project audit workflows."""

from typing import TYPE_CHECKING

from aletheia_lab.product.boundary import ProductError

if TYPE_CHECKING:
    from aletheia_lab.product.service import ProductService

__all__ = ["ProductError", "ProductService"]


def __getattr__(name: str) -> object:
    # A report worker needs the models and renderer without the local store.
    if name == "ProductService":
        from aletheia_lab.product.service import ProductService

        globals()[name] = ProductService
        return ProductService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
