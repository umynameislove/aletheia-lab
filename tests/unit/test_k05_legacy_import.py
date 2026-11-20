"""Legacy source is read only; canonical identities and full histories survive export."""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from aletheia_lab.hosted.legacy_import import (
    ImportTarget,
    LegacyImportConflict,
    LegacyImportError,
    LegacySource,
    model_metadata,
    record_parents,
    validate_project_models,
)
from aletheia_lab.product import ProductService
from aletheia_lab.product.demo_flow import _demo_mapping


def legacy_journey(tmp_path, *, extra_artifact=None):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(("git", "init", "-q", str(source)), check=True, capture_output=True)
    (source / "dataset.csv").write_text("id,target\n1,0\n2,1\n", encoding="utf-8")
    (source / "metrics.csv").write_text("run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n", encoding="utf-8")
    (source / "config.json").write_text('{"seed":42}\n', encoding="utf-8")
    if extra_artifact is not None:
        (source / "extra.json").write_bytes(extra_artifact)
    root = tmp_path / "legacy-copy"
    service = ProductService(root)
    preview = service.preview_import(str(source.resolve()))
    initial = service.confirm_import(preview["preview_id"], _demo_mapping(preview))
    (source / "metrics.csv").write_text("run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.8\n", encoding="utf-8")
    refreshed = service.refresh(initial["project_id"])
    view = service.analyze_mock(refreshed["project_id"], refreshed["snapshot_id"], "Giải thích bằng chứng đã lưu?")
    followed = service.follow_up(view["result"]["id"], "claim", view["claims"][0]["id"], "Điều gì còn chưa chắc chắn?")
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return root, refreshed["project_id"], view, followed


def _files(root):
    return {file.relative_to(root).as_posix(): (hashlib.sha256(file.read_bytes()).hexdigest(), file.stat().st_mtime_ns)
            for file in root.rglob("*") if file.is_file()}


def test_readonly_export_preserves_id_history_turn_evidence_and_current_pointer(tmp_path):
    root, project_id, view, followed = legacy_journey(tmp_path)
    before = _files(root)
    with LegacySource(root) as source:
        assert source.project_ids() == (project_id,)
        first = source.export_project(project_id)
        assert first.semantic_export() == source.export_project(project_id).semantic_export()
        kinds = [model_metadata(model)[0] for model in first.models]
        assert kinds.count("snapshot") == 2
        assert kinds.count("result") == 2
        assert kinds.count("project_state") == 2
        assert kinds.count("evidence_bundle") == 1
        result_ids = {model_metadata(model)[1] for model in first.models if model_metadata(model)[0] == "result"}
        assert result_ids == {view["result"]["id"], followed["result"]["id"]}
        assert len(first.current_pointers) == 1 and len(first.confirmations) == 1
        assert first.preview_leases and first.artifacts
        graph = next(model for model in first.models if model_metadata(model)[0] == "lineage_graph")
        relations = {relation for relation, _ in record_parents(graph)}
        assert {"lineage_snapshot_comparison", "lineage_regression_candidate"} <= relations
    assert _files(root) == before
    assert ProductService(root).view(view["result"]["id"]) == view
    assert ProductService(root).view(followed["result"]["id"]) == followed


def test_no_implicit_workspace_or_preview_mapping(tmp_path):
    root = tmp_path / "legacy-copy"
    service = ProductService(root)
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("public synthetic content\n", encoding="utf-8")
    preview = service.preview_import(str(source_root.resolve()))
    with pytest.raises(LegacyImportError, match="explicit project mapping"):
        LegacySource(root)
    with LegacySource(root, preview_projects={preview["preview_id"]: "p3-project-" + "1" * 64}) as source:
        assert source.project_ids() == ("p3-project-" + "1" * 64,)
    with pytest.raises(LegacyImportError):
        ImportTarget("p3-project-" + "1" * 64, "", "owner")
    with pytest.raises(LegacyImportError):
        ImportTarget("made-up", "workspace", "owner")


def test_export_includes_only_explicitly_mapped_unconfirmed_previews_without_writes(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    service = ProductService(root)
    selected = service.preview_import(str((tmp_path / "source").resolve()))
    (tmp_path / "source" / "metrics.csv").write_text(
        "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.9\n", encoding="utf-8")
    foreign = service.preview_import(str((tmp_path / "source").resolve()))
    assert selected["preview_id"] != foreign["preview_id"]
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _files(root)
    with LegacySource(root, preview_projects={selected["preview_id"]: project_id,
            foreign["preview_id"]: "p3-project-" + "f" * 64}) as source:
        exported = source.export_project(project_id)
        preview_ids = {model_metadata(model)[1] for model in exported.models
                       if model_metadata(model)[0] == "preview"}
        assert selected["preview_id"] in preview_ids and foreign["preview_id"] not in preview_ids
        assert len(preview_ids) == 2
    assert _files(root) == before


def test_migration_checksum_tampering_is_rejected_without_source_writes(tmp_path):
    root, _, _, _ = legacy_journey(tmp_path)
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        conn.execute("UPDATE product_migration_history SET migration_sha256=? WHERE version=1", ("0" * 64,))
        conn.commit()
    before = _files(root)
    with pytest.raises(LegacyImportError, match="migration history"):
        LegacySource(root)
    assert _files(root) == before


def test_corrupt_object_or_missing_parent_is_rejected(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        digest = conn.execute("SELECT object_sha256 FROM records WHERE record_type='snapshot' LIMIT 1").fetchone()[0]
    path = root / "objects" / "sha256" / digest[:2] / digest[2:]
    path.write_bytes(b"partial bytes")
    before = _files(root)
    with LegacySource(root) as source, pytest.raises(LegacyImportError, match="hash or length"):
        source.export_project(project_id)
    assert _files(root) == before


def test_confirmed_preview_cannot_be_remapped(tmp_path):
    root, _, _, _ = legacy_journey(tmp_path)
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        preview_id = conn.execute("SELECT preview_id FROM product_confirmations").fetchone()[0]
    with pytest.raises(LegacyImportConflict, match="confirmed relation"):
        LegacySource(root, preview_projects={preview_id: "p3-project-" + "9" * 64})


def test_uncheckpointed_wal_and_source_mutation_are_rejected(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    wal = root / "project-store.sqlite3-wal"
    wal.write_bytes(b"not a checkpointed copy")
    with pytest.raises(LegacyImportError, match="WAL"):
        LegacySource(root)
    wal.unlink()
    source = LegacySource(root)
    assert source.export_project(project_id)
    database = root / "project-store.sqlite3"
    database.write_bytes(database.read_bytes() + b"changed")
    with pytest.raises(LegacyImportError, match="changed"):
        source.close()


def _cli():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts" / "k05_import_local.py"
    spec = importlib.util.spec_from_file_location("k05_import_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_mapping_is_versioned_explicit_and_rejects_duplicate_projects(tmp_path):
    import json
    cli = _cli()
    path = tmp_path / "mapping.json"
    target = {"project_id": "p3-project-" + "1" * 64, "workspace_id": "workspace", "user_id": "owner"}
    path.write_text(json.dumps({"schema_version": "k05-local-import/v1", "projects": [target], "preview_projects": {}}), encoding="utf-8")
    targets, previews = cli.mapping(path)
    assert targets == (ImportTarget(**target),) and previews == {}
    path.write_text(json.dumps({"schema_version": "k05-local-import/v1", "projects": [target, target], "preview_projects": {}}), encoding="utf-8")
    with pytest.raises(LegacyImportError, match="One explicit"):
        cli.mapping(path)


def test_cli_failure_does_not_print_secrets_or_private_paths(capsys):
    cli = _cli()
    assert cli.main(["--source-copy", "private-sensitive-source", "--mapping", "mapping", "--object-root", "objects", "--schema", "public", "--batch-id", "test"]) == 1
    output = capsys.readouterr().out
    assert "legacy_import_failed" in output and "private-sensitive-source" not in output


def test_missing_canonical_parent_is_rejected(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        conn.execute("DELETE FROM records WHERE record_type='project_bundle'")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _files(root)
    with LegacySource(root) as source, pytest.raises(LegacyImportError, match="canonical parent"):
        source.export_project(project_id)
    assert _files(root) == before


def test_confirmation_target_kind_must_match_local_facade_semantics(tmp_path):
    root, project_id, _, _ = legacy_journey(tmp_path)
    with closing(sqlite3.connect(root / "project-store.sqlite3")) as conn:
        conn.execute("UPDATE product_confirmations SET project_state_record_id=(SELECT record_id FROM product_records WHERE record_kind='result' LIMIT 1)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with LegacySource(root) as source, pytest.raises(LegacyImportError, match="confirmation"):
        source.export_project(project_id)


def test_invalid_project_state_reference_type_is_rejected():
    from aletheia_lab.product.lifecycle import build_product_lifecycle_record
    project_id = "p3-project-" + "1" * 64
    state = build_product_lifecycle_record(record_kind="project_state", project_id=project_id, payload={"snapshot_id": []})
    with pytest.raises(LegacyImportError, match="reference is invalid"):
        validate_project_models((state,), project_id)
