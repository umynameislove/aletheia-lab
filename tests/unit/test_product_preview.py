from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import ProductLifecycleStore


def _source_state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_preview_is_safe_persistent_idempotent_and_does_not_build_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("Ordinary project notes.\n", encoding="utf-8")
    (source_root / "config.json").write_text(
        json.dumps({"owner": "person@example.com"}),
        encoding="utf-8",
    )
    (source_root / "ignored.bin").write_bytes(b"not admitted")
    before = _source_state(source_root)

    def reject_bundle(*args: object, **kwargs: object) -> None:
        raise AssertionError("preview attempted to construct a ProjectBundle")

    monkeypatch.setattr("aletheia_lab.project.importer.build_project_bundle", reject_bundle)
    store_root = tmp_path / "store"
    service = ProductService(store_root)

    first = service.preview_import(str(source_root.resolve()))
    second = ProductService(store_root).preview_import(str(source_root.resolve()))

    assert first == second
    assert set(first) == {
        "preview_id",
        "included_count",
        "excluded_count",
        "redacted_count",
        "withheld_count",
        "blockers",
        "warnings",
        "outbound_categories",
        "mapping_candidates",
    }
    assert str(first["preview_id"]).startswith("p6-preview-")
    assert first["included_count"] == 1
    assert first["excluded_count"] == 1
    assert first["redacted_count"] == 1
    assert first["withheld_count"] == 0
    assert first["blockers"] == []
    warnings = cast(list[dict[str, object]], first["warnings"])
    assert [warning["code"] for warning in warnings] == ["pii_redacted"]
    assert first["outbound_categories"] == ["config", "other"]
    candidates = cast(dict[str, list[dict[str, object]]], first["mapping_candidates"])
    assert candidates["targets"] == []
    assert candidates["metric_sources"] == []
    assert len(candidates["configs"]) == 1
    assert candidates["runs"] == []
    assert str(source_root.resolve()) not in json.dumps(first)
    assert _source_state(source_root) == before

    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM product_records").fetchone()[0] == 1

    with ProductLifecycleStore(store_root) as lifecycle:
        staged = lifecycle.get(str(first["preview_id"]), expected_kind="preview")
    staged_payload = staged.payload()
    assert staged_payload["status"] == "active"
    assert isinstance(staged_payload["source_state_sha256"], str)
    assert len(staged_payload["source_state_sha256"]) == 64


def test_same_bytes_with_replaced_file_identity_require_a_new_preview(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_file = source_root / "README.md"
    source_file.write_bytes(b"stable bytes\n")
    service = ProductService(tmp_path / "store")

    first = service.preview_import(str(source_root.resolve()))
    replacement = tmp_path / "replacement.md"
    replacement.write_bytes(source_file.read_bytes())
    replacement.replace(source_file)
    second = service.preview_import(str(source_root.resolve()))

    assert first["preview_id"] != second["preview_id"]
    assert {key: value for key, value in first.items() if key != "preview_id"} == {
        key: value for key, value in second.items() if key != "preview_id"
    }


def test_blocked_preview_returns_safe_blockers_without_persisting_a_project(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "empty-source"
    source_root.mkdir()
    store_root = tmp_path / "store"

    preview = ProductService(store_root).preview_import(str(source_root.resolve()))

    blockers = cast(list[dict[str, object]], preview["blockers"])
    blocker_codes = {item["code"] for item in blockers}
    assert blocker_codes == {"atomic_import_aborted", "empty_project"}
    with sqlite3.connect(store_root / "project-store.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


def test_invalid_root_is_a_safe_product_error(tmp_path: Path) -> None:
    private_root = tmp_path / "private-project-name"
    private_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ProductError) as captured:
        ProductService(tmp_path / "store").preview_import(str(private_root.resolve()))

    assert captured.value.code == "import_root_not_directory"
    assert "private-project-name" not in captured.value.safe_message
    assert str(private_root) not in captured.value.safe_message
    assert captured.value.__context__ is None


def test_unexpected_preview_failure_does_not_retain_raw_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    private_detail = str(source_root / "private-input.txt")

    def fail_preview(*args: object, **kwargs: object) -> None:
        raise OSError(f"could not read {private_detail}")

    monkeypatch.setattr("aletheia_lab.product.imports.inspect_local_project", fail_preview)

    with pytest.raises(ProductError) as captured:
        ProductService(tmp_path / "store").preview_import(str(source_root.resolve()))

    assert captured.value.code == "preview_failed"
    assert private_detail not in captured.value.safe_message
    assert captured.value.__context__ is None
