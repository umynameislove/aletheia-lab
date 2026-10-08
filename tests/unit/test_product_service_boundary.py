from __future__ import annotations

import inspect
import socket
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

import pytest

from aletheia_lab.product import ProductError, ProductService


def test_product_service_exposes_exact_contract_method_parameters() -> None:
    expected = {
        "demo_view": ("self",),
        "preview_import": ("self", "root"),
        "confirm_import": ("self", "preview_id", "mapping"),
        "refresh": ("self", "project_id"),
        "analyze_mock": ("self", "project_id", "snapshot_id", "question"),
        "follow_up": (
            "self",
            "result_id",
            "selection_kind",
            "selection_id",
            "question",
        ),
        "view": ("self", "result_id"),
        "export_report": ("self", "result_id", "format"),
        "delete_project": ("self", "project_id"),
    }

    for method_name, parameters in expected.items():
        method = getattr(ProductService, method_name)
        assert tuple(inspect.signature(method).parameters) == parameters

    public_methods = {
        name
        for name, method in inspect.getmembers(ProductService, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == set(expected)
    assert tuple(inspect.signature(ProductService.__init__).parameters) == ("self", "store_root")


def test_product_service_contract_annotations_are_exact() -> None:
    assert get_type_hints(ProductService.__init__) == {
        "store_root": Path,
        "return": type(None),
    }
    assert get_type_hints(ProductService.demo_view) == {"return": dict[str, object]}
    assert get_type_hints(ProductService.preview_import) == {
        "root": str,
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.confirm_import) == {
        "preview_id": str,
        "mapping": dict[str, object],
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.refresh) == {
        "project_id": str,
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.analyze_mock) == {
        "project_id": str,
        "snapshot_id": str,
        "question": str,
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.follow_up) == {
        "result_id": str,
        "selection_kind": str,
        "selection_id": str,
        "question": str,
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.view) == {
        "result_id": str,
        "return": dict[str, object],
    }
    assert get_type_hints(ProductService.export_report) == {
        "result_id": str,
        "format": str,
        "return": bytes,
    }
    assert get_type_hints(ProductService.delete_project) == {
        "project_id": str,
        "return": dict[str, object],
    }


def test_constructor_prepares_only_the_store_and_can_reopen(tmp_path: Path) -> None:
    store_root = tmp_path / "product-store"

    first = ProductService(store_root)
    second = ProductService(store_root)

    assert first._store_root == store_root.resolve()
    assert second._store_root == store_root.resolve()
    assert sorted(path.relative_to(store_root).as_posix() for path in store_root.rglob("*")) == [
        "objects",
        "objects/sha256",
        "project-store.sqlite3",
    ]


def test_constructor_does_not_touch_a_source_or_connect_to_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    marker = source_root / "marker.txt"
    marker.write_bytes(b"source-must-remain-read-only\n")
    before = (marker.read_bytes(), marker.stat().st_mtime_ns)

    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("constructor attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)

    ProductService(tmp_path / "store")

    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


def test_constructor_maps_private_store_failure_to_safe_product_error(tmp_path: Path) -> None:
    invalid_root = tmp_path / "private-source-name"
    invalid_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ProductError) as captured:
        ProductService(invalid_root)

    error = captured.value
    assert error.code == "store_unavailable"
    assert error.safe_message == "The product store could not be opened safely."
    assert str(invalid_root) not in str(error)
    assert "private-source-name" not in str(error)
    assert error.__context__ is None
    assert error.__cause__ is None
    assert "private-source-name" not in repr(error.__dict__)


@pytest.mark.parametrize(
    "unsafe_message",
    [
        r"Could not read C:\private\project\secret.txt",
        "Could not read /private/project/secret.txt",
        "Authorization bearer value was rejected",
        "Imported text\nmust not become an error",
    ],
)
def test_product_error_rejects_unsafe_public_messages(unsafe_message: str) -> None:
    with pytest.raises(ValueError):
        ProductError("invalid_request", unsafe_message)


def test_unimplemented_methods_fail_explicitly_instead_of_returning_fake_success(
    tmp_path: Path,
) -> None:
    service = ProductService(tmp_path / "store")
    calls: tuple[Callable[[], object], ...] = (
        lambda: service.export_report("result", "json"),
        lambda: service.delete_project("project"),
    )

    for call in calls:
        with pytest.raises(ProductError) as captured:
            call()
        assert captured.value.code == "feature_unavailable"
        assert captured.value.safe_message == "This product operation is not available yet."
