"""Scoped local and hosted decoding checks retain integrity with unrelated growth."""
from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError, RecordIndex, decode_record
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.project.lineage import build_lineage_graph, build_lineage_node
from aletheia_lab.project.persistence import ProjectStore, ProjectStoreError, _json_bytes


def _graph():
    project_id = "p3-project-" + "1" * 64
    project = build_lineage_node(project_id=project_id, kind="project", source_id=project_id,
                                 source_sha256="1" * 64, visibility="public")
    return build_lineage_graph(project_id, (project,), ())


def test_typed_decoder_rejects_metadata_and_payload_corruption():
    model = _graph()
    payload = _json_bytes(model.model_dump(mode="json"))
    from aletheia_lab.project.identity import canonical_project_sha256

    index = RecordIndex(model.graph_id, model.project_id, "lineage_graph", model.schema_version,
                        canonical_project_sha256(model.model_dump(mode="json")),
                        hashlib.sha256(payload).hexdigest(), None, None)
    assert decode_record(index, payload) == model
    for damaged in (replace(index, project_id="p3-project-" + "2" * 64),
                    replace(index, canonical_sha256="0" * 64),
                    replace(index, record_id="p3-lineage-graph-" + "0" * 64)):
        with pytest.raises(CatalogIntegrityError):
            decode_record(damaged, payload)
    with pytest.raises(CatalogIntegrityError):
        decode_record(index, b'{"partial":')


def test_lifecycle_decoder_keeps_canonical_identity_and_snapshot_scope():
    model = build_product_lifecycle_record(record_kind="turn", project_id="p3-project-" + "1" * 64,
        snapshot_id="p3-snapshot-" + "2" * 64, payload={"question": "Bằng chứng nào còn thiếu?"})
    payload = _json_bytes(model.model_dump(mode="json"))
    index = RecordIndex(model.record_id, model.project_id, "turn", model.schema_version,
                        model.canonical_sha256(), hashlib.sha256(payload).hexdigest(), model.snapshot_id, None)
    assert decode_record(index, payload) == model
    with pytest.raises(CatalogIntegrityError):
        decode_record(replace(index, snapshot_id="p3-snapshot-" + "3" * 64), payload)


@pytest.mark.parametrize("unrelated", [0, 100, 1000])
def test_local_scoped_load_opens_one_object_and_scrub_finds_unrelated_damage(tmp_path, monkeypatch, unrelated):
    graph = _graph()
    with ProjectStore(tmp_path) as store:
        store.persist((graph,))
        for number in range(unrelated):
            payload = str(number).encode()
            digest = hashlib.sha256(payload).hexdigest()
            path = store._object_path(digest)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(payload)
            store._connection.execute("INSERT INTO objects VALUES(?,?,?,?)",
                (digest, len(payload), "application/octet-stream", path.relative_to(store.root).as_posix()))
        store._connection.commit()
        if unrelated:
            path.write_bytes(b"damaged unrelated object")
    opened = []
    original = ProjectStore.read_object

    def read(self, digest):
        opened.append(digest)
        return original(self, digest)

    monkeypatch.setattr(ProjectStore, "read_object", read)
    with ProjectStore(tmp_path) as reopened:
        assert opened == []
        assert reopened.load(graph.graph_id) == graph
        assert len(opened) == 1
        if unrelated:
            with pytest.raises(ProjectStoreError, match="integrity"):
                reopened.verify_integrity()
        else:
            reopened.verify_integrity()


def test_admin_rejects_unsafe_schema_and_requires_explicit_object_scope():
    path = Path(__file__).parents[2] / "scripts" / "k05_storage_admin.py"
    spec = importlib.util.spec_from_file_location("k05_storage_admin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.arguments(["scrub", "--schema", "k05_test"])
    with pytest.raises(SystemExit):
        module.arguments(["observe", "--schema", "public"])