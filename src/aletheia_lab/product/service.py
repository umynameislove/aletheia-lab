"""Stable P6 facade over the existing project pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from aletheia_lab.product.analysis import analyze_product_mock
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.demo import demo_product_view
from aletheia_lab.product.follow_up import follow_up_product_result
from aletheia_lab.product.imports import confirm_product_import, preview_product_import
from aletheia_lab.product.lifecycle import ProductLifecycleStore
from aletheia_lab.product.refresh import refresh_product_project
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.results import load_product_result
from aletheia_lab.project.persistence import ProjectStore

_UNAVAILABLE_MESSAGE = "This product operation is not available yet."
_STORE_UNAVAILABLE_MESSAGE = "The product store could not be opened safely."


def _prepare_store_root(store_root: Path) -> Path | None:
    try:
        with ProjectStore(store_root) as store:
            prepared_root = store.root
        with ProductLifecycleStore(prepared_root):
            pass
    except Exception:
        return None
    return prepared_root


class ProductService:
    """Offline product boundary with persistent state rooted under ``store_root``."""

    def __init__(self, store_root: Path) -> None:
        prepared_root = _prepare_store_root(store_root)
        if prepared_root is None:
            raise ProductError("store_unavailable", _STORE_UNAVAILABLE_MESSAGE)
        self._store_root = prepared_root

    def demo_view(self) -> dict[str, object]:
        """Return the validated deterministic synthetic product view."""

        return demo_product_view()

    def preview_import(self, root: str) -> dict[str, object]:
        """Preview a read-only project import without confirming a project."""

        return preview_product_import(self._store_root, root)

    def confirm_import(self, preview_id: str, mapping: dict[str, object]) -> dict[str, object]:
        """Confirm a revalidated preview through the existing P3 pipeline."""

        return confirm_product_import(self._store_root, preview_id, mapping)

    def refresh(self, project_id: str) -> dict[str, object]:
        """Revalidate an existing project and preserve unchanged snapshot identity."""

        return refresh_product_project(self._store_root, project_id)

    def analyze_mock(
        self,
        project_id: str,
        snapshot_id: str,
        question: str,
    ) -> dict[str, object]:
        """Create and persist an immutable deterministic mock result."""

        return analyze_product_mock(
            self._store_root,
            project_id,
            snapshot_id,
            question,
        ).model_dump(mode="json")

    def follow_up(
        self,
        result_id: str,
        selection_kind: str,
        selection_id: str,
        question: str,
    ) -> dict[str, object]:
        """Create and persist a scoped immutable deterministic follow-up."""

        return follow_up_product_result(
            self._store_root,
            result_id,
            selection_kind,
            selection_id,
            question,
        ).model_dump(mode="json")

    def view(self, result_id: str) -> dict[str, object]:
        """Reload and revalidate an immutable diagnosis-visible product view."""

        return load_product_result(self._store_root, result_id).model_dump(mode="json")

    def export_report(self, result_id: str, format: str) -> bytes:
        """Render one authorized saved view without mutating persistent state."""

        return render_product_report(load_product_result(self._store_root, result_id), format)

    def delete_project(self, project_id: str) -> dict[str, object]:
        """Delete product-owned state when implemented."""

        self._unavailable()

    @staticmethod
    def _unavailable() -> NoReturn:
        raise ProductError("feature_unavailable", _UNAVAILABLE_MESSAGE)
