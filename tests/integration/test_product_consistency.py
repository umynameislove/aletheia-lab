"""Adversarial lifecycle checks over owned, provider-free projects."""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest
from test_product_delete import _analyze, _confirm, _mapping, _project

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.lifecycle import ProductLifecycleStore
from aletheia_lab.product.questions import checked_product_question
from aletheia_lab.project import ProjectStore

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline product test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def _record_count(root: Path) -> int:
    with sqlite3.connect(root / "project-store.sqlite3") as connection:
        return connection.execute("SELECT count(*) FROM product_records").fetchone()[0]


def test_delete_removes_all_project_previews_and_their_leases(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    root = tmp_path / "store"
    service = ProductService(root)
    confirmed = _confirm(service, source)
    (source / "config.json").write_text('{"seed":43}\n', encoding="utf-8")
    extra = service.preview_import(str(source))
    assert service.delete_project(str(confirmed["project_id"]))["status"] == "deleted"
    with sqlite3.connect(root / "project-store.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM product_records WHERE record_id = ?", (extra["preview_id"],)
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT 1 FROM product_preview_leases").fetchone() is None
        rows = connection.execute("SELECT payload_json FROM product_records").fetchall()
        assert not any(str(source) in row[0] for row in rows)
    assert source.is_dir()
    with pytest.raises(ProductError, match="not available"):
        ProductService(root).preview_import(str(source))


def test_second_import_is_rejected_without_poisoning_existing_project(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    root = tmp_path / "store"
    service = ProductService(root)
    confirmed = _confirm(service, source)
    (source / "config.json").write_text('{"seed":43}\n', encoding="utf-8")
    preview = service.preview_import(str(source))
    with pytest.raises(ProductError) as rejected:
        service.confirm_import(str(preview["preview_id"]), _mapping(preview))
    assert rejected.value.code == "project_already_imported"
    assert service.refresh(str(confirmed["project_id"]))["status"] == "new_snapshot"
    assert service.delete_project(str(confirmed["project_id"]))["status"] == "deleted"


def test_stale_refresh_cannot_overwrite_a_newer_completed_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _project(tmp_path / "source")
    root = tmp_path / "store"
    service = ProductService(root)
    project_id = str(_confirm(service, source)["project_id"])
    original = ProjectStore.persist
    nested: list[dict[str, object]] = []

    def metric(value: str) -> None:
        (source / "metrics.csv").write_text(
            f"run,name,value\nbaseline,loss,0.5\ncandidate,loss,{value}\n", encoding="utf-8"
        )

    def interleaved(store: ProjectStore, models: object, **kwargs: object) -> object:
        if not nested:
            nested.append({})
            metric("0.9")
            nested[0] = ProductService(root).refresh(project_id)
        return original(store, models, **kwargs)

    metric("0.8")
    monkeypatch.setattr(ProjectStore, "persist", interleaved)
    with pytest.raises(ProductError) as rejected:
        service.refresh(project_id)
    assert rejected.value.code == "refresh_stale"
    with ProductLifecycleStore(root) as lifecycle:
        current = lifecycle.get_project_state(project_id)
    assert current.payload()["snapshot_id"] == nested[0]["snapshot_id"]
    with ProjectStore(root) as store:
        assert len(store.list_records(project_id, "snapshot_comparison")) == 1
        store.verify_integrity()


@pytest.mark.parametrize(
    "question",
    [
        "Does token = SYNTHETIC_PRIVATE_VALUE_123456789 affect this result?",
        'What is in "/Users/synthetic-private/project"?',
        "Use sk-SYNTHETIC_PRIVATE_VALUE_123456789 to explain this.",
        "Use Bearer SYNTHETIC_PRIVATE_VALUE_123456789 to explain this.",
        r"Does C:\private\source affect this result?",
        "Explain `/Users/synthetic-private/project`",
        "Explain [/Users/synthetic-private/project]",
        'Does {"token": "SYNTHETIC_PRIVATE_VALUE_123456789"} affect this result?',
        "Counterfactual comparison: not_available",
        "Explain \ud800",
    ],
)
def test_sensitive_questions_never_reach_saved_views_or_exports(
    tmp_path: Path, question: str
) -> None:
    source = _project(tmp_path / "source")
    root = tmp_path / "store"
    service = ProductService(root)
    refreshed, view = _analyze(service, source)
    count = _record_count(root)
    with pytest.raises(ProductError) as rejected:
        service.analyze_mock(str(refreshed["project_id"]), str(refreshed["snapshot_id"]), question)
    assert rejected.value.code == "invalid_question"
    result_id = view["result"]["id"]
    with pytest.raises(ProductError) as followup:
        service.follow_up(result_id, "claim", view["claims"][0]["id"], question)
    assert followup.value.code == "invalid_question"
    assert _record_count(root) == count
    reopened = ProductService(root)
    assert reopened.view(result_id) == view
    for format in ("json", "markdown", "pdf"):
        assert b"SYNTHETIC_PRIVATE_VALUE" not in reopened.export_report(result_id, format)


def test_ordinary_token_discussion_remains_an_allowed_question() -> None:
    assert checked_product_question("What does the token count mean?") == (
        "What does the token count mean?"
    )
