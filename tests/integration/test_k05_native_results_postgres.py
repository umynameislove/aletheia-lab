"""Native uploaded snapshots/results enlist in one real caller-owned PG commit."""
from __future__ import annotations

import asyncio
import io
import os
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k05_legacy_import_postgres import _identities
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.canonical_results import CanonicalResults, PublishedResult
from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecord,
    build_initial_upload_result,
    build_upload_follow_up_result,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError, PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_project_policy
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.upload_publication import SnapshotAlreadyPublished, UploadPublication
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.product.demo_flow import _demo_mapping
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import ProductLifecycleRecord

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit K05 test database URL required")]


def _zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
        archive.writestr("config.json", '{"seed":42}\n')
    return stream.getvalue()


@asynccontextmanager
async def _case(tmp_path):
    payload = _zip()
    async with _database() as (pool, schema):
        async with pool.connection() as conn:
            await migrate(conn, schema)
            await migrate_project_policy(conn, schema)
        a, b = await _identities(pool)
        sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
        receipt = await sources.put(credential_digest=a, workspace_id="a", request_key=SecretStr("synthetic-native-source-01"), payload=payload)
        private = tmp_path / "worker-materialization"
        private.mkdir()
        worker = UploadProject(private, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
        captured = "2026-11-10T00:00:00Z"
        preview = worker.preview(payload, captured_at=captured)
        snapshot = worker.confirm(payload, workspace_id="a", source_id=receipt.source_id, mapping=_demo_mapping(preview), captured_at=captured)
        view = analyze_initial_upload(snapshot, "What is observed in this exact uploaded source?")
        initial = build_initial_upload_result(snapshot, view.conversation.turns[0].question, view)
        objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "native-objects"))
        yield SimpleNamespace(pool=pool, a=a, b=b, sources=sources, payload=payload, receipt=receipt,
            snapshot=snapshot, view=view, initial=initial, objects=objects, catalog=PostgresCatalog(pool, objects),
            uploads=UploadPublication(objects), results=CanonicalResults(objects))


async def _auth(conn, case, action="analyze"):
    return await PostgresAuthorization().authorize(conn, case.a.get_secret_value(), "a", action)


def _child(parent, question):
    selection = resolve_product_follow_up_selection(parent.view, selection_kind="claim", selection_id=parent.view.claims[0].id, question=question)
    return build_upload_follow_up_result("a", parent.view, build_product_follow_up_view(parent.view, selection))


async def _initial(case):
    snapshot = await case.uploads.prepare_snapshot(case.snapshot)
    result = await case.results.prepare_initial_result("a", snapshot=case.snapshot, record=case.initial)
    async with case.pool.connection() as conn:
        published_snapshot = await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=snapshot)
        published_result = await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=result, expected_revision=published_snapshot.revision)
    return published_snapshot, published_result


def test_native_initial_and_follow_up_preserve_ids_models_source_and_snapshot_pointer(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            snapshot, initial = await _initial(case)
            assert (snapshot.snapshot_id, snapshot.project_id, snapshot.revision, snapshot.disposition) == (case.snapshot.snapshot_id, case.snapshot.mapping.project_id, 1, "created")
            assert (initial.record_id, initial.project_id, initial.revision, initial.disposition) == (case.view.result.id, case.snapshot.mapping.project_id, 2, "created")
            assert case.snapshot.git_provenance == "not_supplied" and case.snapshot.snapshot_id.startswith("upload-snapshot-")
            kwargs = dict(credential_digest=case.a, workspace_id="a", project_id=initial.project_id)
            stored = await case.catalog.read_record(**kwargs, record_id=initial.record_id)
            assert await case.catalog.read_record(**kwargs, record_id=snapshot.snapshot_id) == case.snapshot
            assert isinstance(stored, UploadResultRecord) and not isinstance(stored, ProductLifecycleRecord)
            assert stored == case.initial and stored.view == case.view
            child = _child(case.initial, "What remains uncertain in the source evidence?")
            assert child.record_id == _child(case.initial, "What remains uncertain in the source evidence?").record_id
            prepared = await case.results.prepare_result("a", parent=case.initial, record=child)
            async with case.pool.connection() as conn:
                following = await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=prepared, expected_revision=2)
            assert (following.record_id, following.revision, following.disposition) == (child.record_id, 3, "created")
            assert await case.catalog.read_record(**kwargs, record_id=child.record_id) == child
            assert await case.catalog.read_record(**kwargs, record_id=initial.record_id) == case.initial
            assert await case.catalog.current(**kwargs, pointer_name="snapshot") == case.snapshot
            assert await case.catalog.current(**kwargs) == case.snapshot
            page = await case.catalog.list_records(**kwargs, record_type="result", limit=1)
            assert len(page.records) == 1 and page.records[0].record_type == "upload_result" and page.next_cursor is not None
            following_page = await case.catalog.list_records(**kwargs, record_type="result", cursor=page.next_cursor, limit=1)
            assert len(following_page.records) == 1 and following_page.next_cursor is None
            assert {page.records[0].record_id, following_page.records[0].record_id} == {initial.record_id, child.record_id}
            snapshots = await case.catalog.list_records(**kwargs, record_type="snapshot", limit=1)
            assert [(value.record_id, value.record_type) for value in snapshots.records] == [(snapshot.snapshot_id, "upload_snapshot")]
            workspace_page = await case.catalog.list_workspace_records(credential_digest=case.a, workspace_id="a", kind="result", limit=1)
            assert len(workspace_page.records) == 1 and workspace_page.records[0].record_type == "upload_result" and workspace_page.next_cursor is not None
            workspace_next = await case.catalog.list_workspace_records(credential_digest=case.a, workspace_id="a", kind="result", after=workspace_page.next_cursor.after, limit=1)
            assert len(workspace_next.records) == 1 and workspace_next.next_cursor is None
            assert {workspace_page.records[0].record_id, workspace_next.records[0].record_id} == {initial.record_id, child.record_id}
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT provenance_kind,revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (initial.project_id,))).fetchone() == ("upload", 3)
                assert await (await conn.execute("SELECT record_type,source_id FROM k05_records WHERE workspace_id='a' AND record_id=%s", (snapshot.snapshot_id,))).fetchone() == ("upload_snapshot", case.receipt.source_id)
                assert await (await conn.execute("SELECT record_type,snapshot_id,parent_result_id,source_id FROM k05_records WHERE workspace_id='a' AND record_id=%s", (child.record_id,))).fetchone() == ("upload_result", snapshot.snapshot_id, initial.record_id, None)
                assert await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
def test_native_snapshot_and_initial_result_rollback_with_later_fence_or_cancel(tmp_path, interruption):
    async def run():
        async with _case(tmp_path) as case:
            snapshot = await case.uploads.prepare_snapshot(case.snapshot)
            initial = await case.results.prepare_initial_result("a", snapshot=case.snapshot, record=case.initial)
            with pytest.raises(interruption, match="synthetic native terminal fence"):
                async with case.pool.connection() as conn:
                    published = await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=snapshot)
                    result = await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=initial, expected_revision=published.revision)
                    assert result.revision == 2
                    raise interruption("synthetic native terminal fence")
            async with case.pool.connection() as conn:
                for table in ("k05_projects", "k05_records", "k05_record_parents", "k05_current_pointers", "k05_objects"):
                    assert await (await conn.execute("SELECT count(*) FROM " + table)).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (1,)
                assert (await (await conn.execute("SELECT count(*) FROM k05_object_leases WHERE expires_at>clock_timestamp()")).fetchone())[0] >= 2
            async with case.pool.connection() as conn:
                published = await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=snapshot)
                result = await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=initial, expected_revision=published.revision)
            assert result.revision == 2
    _run(run())


def test_native_same_revision_race_exact_replay_and_stale_rejection(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            await _initial(case)
            children = [_child(case.initial, question) for question in ("Which uncertainty is unchanged?", "What additional evidence is still missing?")]
            prepared = [await case.results.prepare_result("a", parent=case.initial, record=child) for child in children]
            barrier = asyncio.Barrier(2)
            async def contender(item):
                try:
                    async with case.pool.connection() as conn:
                        auth = await _auth(conn, case)
                        await asyncio.wait_for(barrier.wait(), 10)
                        return await case.results.enlist_result(conn, authorization=auth, prepared=item, expected_revision=2)
                except WorkspaceConflict as error:
                    return error
            outcomes = await asyncio.wait_for(asyncio.gather(*(contender(item) for item in prepared)), 20)
            winners = [value for value in outcomes if isinstance(value, PublishedResult)]
            assert len(winners) == 1 and sum(isinstance(value, WorkspaceConflict) for value in outcomes) == 1
            winner = winners[0]
            child = next(value for value in children if value.record_id == winner.record_id)
            repeated = await case.results.prepare_result("a", parent=case.initial, record=child)
            async with case.pool.connection() as conn:
                replay = await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=repeated, expected_revision=3)
            assert (replay.record_id, replay.revision, replay.disposition) == (winner.record_id, 3, "identical")
            stale = await case.results.prepare_result("a", parent=case.initial, record=child)
            with pytest.raises(WorkspaceConflict):
                async with case.pool.connection() as conn:
                    await case.results.enlist_result(conn, authorization=await _auth(conn, case), prepared=stale, expected_revision=2)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (winner.project_id,))).fetchone() == (3,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_id=ANY(%s)", ([value.record_id for value in children],))).fetchone() == (1,)
    _run(run())


@pytest.mark.parametrize("mismatch", ["workspace", "source", "source_hash", "provenance"])
def test_native_snapshot_denies_foreign_workspace_source_and_local_provenance(tmp_path, mismatch):
    async def run():
        async with _case(tmp_path) as case:
            snapshot = case.snapshot
            if mismatch == "workspace":
                snapshot = build_upload_snapshot(workspace_id="b", source_id=snapshot.source_id, source_sha256=snapshot.source_sha256, mapping=snapshot.mapping, mapping_result=snapshot.mapping_result, captured_at=snapshot.captured_at)
            elif mismatch == "source":
                other = await case.sources.put(credential_digest=case.b, workspace_id="b", request_key=SecretStr("synthetic-native-source-02"), payload=case.payload)
                snapshot = build_upload_snapshot(workspace_id="a", source_id=other.source_id, source_sha256=other.sha256, mapping=snapshot.mapping, mapping_result=snapshot.mapping_result, captured_at=snapshot.captured_at)
            elif mismatch == "source_hash":
                snapshot = build_upload_snapshot(workspace_id="a", source_id=snapshot.source_id, source_sha256="0" * 64, mapping=snapshot.mapping, mapping_result=snapshot.mapping_result, captured_at=snapshot.captured_at)
            else:
                async with case.pool.connection() as conn:
                    await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES('a',%s,'owner_a')", (snapshot.mapping.project_id,))
            prepared = await case.uploads.prepare_snapshot(snapshot)
            expected_error = CatalogIntegrityError if mismatch == "source_hash" else AuthorizationDenied
            with pytest.raises(expected_error):
                async with case.pool.connection() as conn:
                    await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=prepared)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_current_pointers")).fetchone() == (0,)
    _run(run())


def test_same_snapshot_state_keeps_first_canonical_capture_via_verified_reprepare(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            await _initial(case)
            old = case.snapshot
            later = build_upload_snapshot(workspace_id=old.workspace_id, source_id=old.source_id, source_sha256=old.source_sha256, mapping=old.mapping, mapping_result=old.mapping_result, captured_at=old.captured_at + timedelta(seconds=1))
            assert later.snapshot_id == old.snapshot_id and later.record_sha256 != old.record_sha256
            prepared = await case.uploads.prepare_snapshot(later)
            with pytest.raises(SnapshotAlreadyPublished):
                async with case.pool.connection() as conn:
                    await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=prepared, expected_revision=2)
            existing = await case.catalog.read_record(credential_digest=case.a, workspace_id="a", project_id=old.mapping.project_id, record_id=old.snapshot_id)
            assert existing == old
            repeated = await case.uploads.prepare_snapshot(later, existing=existing)
            async with case.pool.connection() as conn:
                receipt = await case.uploads.enlist_snapshot(conn, authorization=await _auth(conn, case, "confirm"), prepared=repeated, expected_revision=2)
            assert (receipt.snapshot_id, receipt.revision, receipt.disposition) == (old.snapshot_id, 2, "identical")
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a", project_id=old.mapping.project_id, record_id=old.snapshot_id) == old
    _run(run())


def test_native_result_rejects_changed_project_provenance_without_effects(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            child = _child(case.initial, "Which invariant survives a provenance change?")
            prepared = await case.results.prepare_result("a", parent=case.initial, record=child)
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE k05_projects SET provenance_kind='local' "
                    "WHERE workspace_id='a' AND project_id=%s", (initial.project_id,))
            with pytest.raises(CatalogIntegrityError):
                async with case.pool.connection() as conn:
                    await case.results.enlist_result(conn, authorization=await _auth(conn, case),
                        prepared=prepared, expected_revision=initial.revision)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT provenance_kind,revision FROM k05_projects "
                    "WHERE workspace_id='a' AND project_id=%s", (initial.project_id,))).fetchone() == ("local", initial.revision)
                assert await (await conn.execute("SELECT count(*) FROM k05_records "
                    "WHERE workspace_id='a' AND record_id=%s", (child.record_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_record_parents "
                    "WHERE workspace_id='a' AND record_id=%s", (child.record_id,))).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases "
                    "WHERE workspace_id='a' AND sha256=%s AND lease_id=%s",
                    (prepared.staged.descriptor.sha256, prepared.staged.lease_id))).fetchone() == (1,)
    _run(run())
