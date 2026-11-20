"""Canonical derived-result publication in real caller-owned PG transactions."""
from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager

import pytest
from test_k05_legacy_import_postgres import _identities, _source
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.canonical_results import (
    CanonicalResultRejected,
    CanonicalResults,
    PublishedResult,
)
from aletheia_lab.hosted.legacy_import import (
    ImportTarget,
    LegacyImporter,
    model_metadata,
    record_parents,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError, PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_project_policy
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.lifecycle import ProductLifecycleRecord, build_product_lifecycle_record
from aletheia_lab.product.results import ProductResultEnvelope, _view_from_record
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit K05 test database URL required")]


@asynccontextmanager
async def _case(tmp_path):
    source, _, followed = _source(tmp_path)
    parent = next(model for model in source.models if isinstance(model, ProductLifecycleRecord) and model.record_id == followed["result"]["id"])
    async with _database() as (pool, schema):
        async with pool.connection() as conn:
            await migrate(conn, schema)
            await migrate_project_policy(conn, schema)
        a, b = await _identities(pool)
        objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "canonical-objects"))
        await LegacyImporter(pool, objects).import_project(source, ImportTarget(source.project_id, "a", "owner_a"), credential_digest=a, batch_id="canonical-parent")
        yield pool, objects, PostgresCatalog(pool, objects), CanonicalResults(objects), a, b, parent, source


def _child(parent, question):
    view = _view_from_record(parent)
    selection = resolve_product_follow_up_selection(view, selection_kind="claim", selection_id=view.claims[0].id, question=question)
    derived = build_product_follow_up_view(view, selection)
    return build_product_lifecycle_record(record_kind="result", project_id=parent.project_id,
        snapshot_id=parent.snapshot_id, parent_result_id=parent.record_id,
        payload=ProductResultEnvelope(view=derived.model_dump(mode="json")).model_dump(mode="json"))


async def _publish(pool, writer, credential, prepared, expected_revision):
    async with pool.connection() as conn:
        auth = await PostgresAuthorization().authorize(conn, credential.get_secret_value(), "a", "analyze")
        result = await writer.enlist_result(conn, authorization=auth, prepared=prepared, expected_revision=expected_revision)
    return result


async def _assert_absent(pool, child, *, revision=0):
    digest = hashlib.sha256(_json_bytes(child.model_dump(mode="json"))).hexdigest()
    async with pool.connection() as conn:
        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_id=%s", (child.record_id,))).fetchone() == (0,)
        assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (child.project_id,))).fetchone() == (revision,)
        assert await (await conn.execute("SELECT count(*) FROM k05_objects WHERE workspace_id='a' AND sha256=%s", (digest,))).fetchone() == (0,)
        assert (await (await conn.execute("SELECT count(*) FROM k05_object_leases WHERE workspace_id='a' AND sha256=%s AND expires_at>clock_timestamp()", (digest,))).fetchone())[0] >= 1


def test_two_authorized_connections_same_revision_have_one_canonical_winner(tmp_path):
    async def run():
        async with _case(tmp_path) as (pool, _, catalog, writer, a, _, parent, source):
            children = [_child(parent, question) for question in ("Which evidence remains uncertain?", "What remains unknown in the selected evidence?")]
            prepared = [await writer.prepare_result("a", parent=parent, record=child) for child in children]
            barrier = asyncio.Barrier(2)
            async def contender(value):
                try:
                    async with pool.connection() as conn:
                        auth = await PostgresAuthorization().authorize(conn, a.get_secret_value(), "a", "analyze")
                        await asyncio.wait_for(barrier.wait(), 10)
                        return await writer.enlist_result(conn, authorization=auth, prepared=value, expected_revision=0)
                except WorkspaceConflict as error:
                    return error
            outcomes = await asyncio.wait_for(asyncio.gather(*(contender(value) for value in prepared)), 20)
            winners = [value for value in outcomes if isinstance(value, PublishedResult)]
            assert len(winners) == 1 and sum(isinstance(value, WorkspaceConflict) for value in outcomes) == 1
            winner = winners[0]
            assert (winner.project_id, winner.revision, winner.disposition) == (source.project_id, 1, "created")
            child = next(value for value in children if value.record_id == winner.record_id)
            assert await catalog.read_record(credential_digest=a, workspace_id="a", project_id=source.project_id, record_id=winner.record_id) == child
            assert await catalog.read_record(credential_digest=a, workspace_id="a", project_id=source.project_id, record_id=parent.record_id) == parent
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (source.project_id,))).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_id=ANY(%s)", ([value.record_id for value in children],))).fetchone() == (1,)
                expected_pointers = {**dict(source.current_pointers), "result": winner.record_id}
                assert await (await conn.execute("SELECT pointer_name,record_id FROM k05_current_pointers WHERE workspace_id='a' AND project_id=%s ORDER BY pointer_name", (source.project_id,))).fetchall() == sorted(expected_pointers.items())
    _run(run())


def test_identical_result_current_revision_is_noop_but_stale_always_conflicts(tmp_path):
    async def run():
        async with _case(tmp_path) as (pool, _, catalog, writer, a, _, parent, source):
            child = _child(parent, "What remains uncertain after this turn?")
            first = await _publish(pool, writer, a, await writer.prepare_result("a", parent=parent, record=child), 0)
            duplicate = await _publish(pool, writer, a, await writer.prepare_result("a", parent=parent, record=child), 1)
            assert (first.record_id, first.revision, first.disposition) == (child.record_id, 1, "created")
            assert (duplicate.record_id, duplicate.revision, duplicate.disposition) == (child.record_id, 1, "identical")
            stale = await writer.prepare_result("a", parent=parent, record=child)
            with pytest.raises(WorkspaceConflict):
                await _publish(pool, writer, a, stale, 0)
            assert _view_from_record(await catalog.read_record(credential_digest=a, workspace_id="a", project_id=source.project_id, record_id=child.record_id)) == _view_from_record(child)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' AND record_id=%s", (child.record_id,))).fetchone() == (1,)
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (source.project_id,))).fetchone() == (1,)
    _run(run())


@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
def test_later_fence_failure_or_cancellation_rolls_back_result_revision_and_lease_release(tmp_path, interruption):
    async def run():
        async with _case(tmp_path) as (pool, _, _, writer, a, _, parent, _):
            child = _child(parent, "Can the caller safely retry this publication?")
            prepared = await writer.prepare_result("a", parent=parent, record=child)
            with pytest.raises(interruption, match="synthetic later fence failure"):
                async with pool.connection() as conn:
                    auth = await PostgresAuthorization().authorize(conn, a.get_secret_value(), "a", "analyze")
                    published = await writer.enlist_result(conn, authorization=auth, prepared=prepared, expected_revision=0)
                    assert published.disposition == "created"
                    raise interruption("synthetic later fence failure")
            await _assert_absent(pool, child)
            resumed = await _publish(pool, writer, a, prepared, 0)
            assert (resumed.record_id, resumed.revision, resumed.disposition) == (child.record_id, 1, "created")
    _run(run())


@pytest.mark.parametrize("corruption", ["metadata", "projection"])
def test_corrupt_parent_index_or_projection_prevents_result_publication(tmp_path, corruption):
    async def run():
        async with _case(tmp_path) as (pool, _, _, writer, a, _, parent, _):
            child = _child(parent, "Does the parent integrity boundary remain enforced?")
            prepared = await writer.prepare_result("a", parent=parent, record=child)
            async with pool.connection() as conn:
                if corruption == "projection":
                    await conn.execute("DELETE FROM k05_record_parents WHERE workspace_id='a' AND record_id=%s AND relation='snapshot'", (parent.record_id,))
                else:
                    saved = await (await conn.execute("SELECT workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256,snapshot_id,parent_result_id FROM k05_records WHERE workspace_id='a' AND record_id=%s", (parent.record_id,))).fetchone()
                    # Explicit owner fault: remove only the derived read index
                    # before replacing its referenced canonical metadata. Keep
                    # every FK/check/immutable trigger enabled throughout.
                    for table in ('k10_evidence_links', 'k10_result_items', 'k10_result_views'):
                        await conn.execute('DELETE FROM ' + table + " WHERE workspace_id='a' AND result_id=%s",
                                           (parent.record_id,))
                    await conn.execute("DELETE FROM k05_record_parents WHERE workspace_id='a' AND record_id=%s", (parent.record_id,))
                    await conn.execute("DELETE FROM k05_records WHERE workspace_id='a' AND record_id=%s", (parent.record_id,))
                    values = (*saved[:5], "0" * 64, *saved[6:])
                    await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256,snapshot_id,parent_result_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", values)
                    for relation, parent_id in record_parents(parent):
                        await conn.execute("INSERT INTO k05_record_parents VALUES('a',%s,%s,%s,%s)", (parent.project_id, parent.record_id, relation, parent_id))
            with pytest.raises(CatalogIntegrityError):
                await _publish(pool, writer, a, prepared, 0)
            await _assert_absent(pool, child)
    _run(run())


def test_prepare_rejects_project_snapshot_and_parent_mismatch_before_staging(tmp_path):
    async def run():
        async with _case(tmp_path) as (pool, _, _, writer, _, _, parent, _):
            child = _child(parent, "Must every immutable result keep its parent scope?")
            for project, snapshot, parent_id in (("p3-project-" + "f" * 64, parent.snapshot_id, parent.record_id),
                    (parent.project_id, "p3-snapshot-" + "f" * 64, parent.record_id),
                    (parent.project_id, parent.snapshot_id, "p6-result-" + "f" * 64)):
                wrong = build_product_lifecycle_record(record_kind="result", project_id=project, snapshot_id=snapshot, parent_result_id=parent_id, payload=child.payload())
                with pytest.raises(CanonicalResultRejected):
                    await writer.prepare_result("a", parent=parent, record=wrong)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)
                assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' AND project_id=%s", (parent.project_id,))).fetchone() == (0,)
    _run(run())


@pytest.mark.parametrize("change", ["membership", "grant", "tombstone"])
def test_enlist_rechecks_current_membership_write_grant_and_tombstone(tmp_path, change):
    async def run():
        async with _case(tmp_path) as (pool, objects, _, writer, a, b, parent, source):
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'")
            await PostgresProjectAccess(pool).set_grant(credential_digest=a, workspace_id="a", project_id=source.project_id, user_id="owner_b", actions=["read", "write"], expected_revision=0)
            child = _child(parent, "Will changed authorization deny this exact publication?")
            prepared = await writer.prepare_result("a", parent=parent, record=child)
            tombstone = build_product_lifecycle_record(record_kind="deletion", project_id=source.project_id, payload={"state": "deleted", "synthetic": True})
            staged = await objects.stage("a", _json_bytes(tombstone.model_dump(mode="json")), media_type="application/json", schema_version=tombstone.schema_version) if change == "tombstone" else None
            with pytest.raises(AuthorizationDenied):
                async with pool.connection() as conn:
                    auth = await PostgresAuthorization().authorize(conn, b.get_secret_value(), "a", "analyze")
                    if change == "membership":
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'")
                    elif change == "grant":
                        await conn.execute("UPDATE j01_project_grants SET actions=ARRAY['read']::text[],revision=revision+1 WHERE workspace_id='a' AND project_id=%s AND user_id='owner_b'", (source.project_id,))
                    else:
                        await objects.ready(conn, "a", staged)
                        kind, record_id, project_id, version, canonical = model_metadata(tombstone)
                        await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) VALUES('a',%s,%s,%s,%s,%s,%s)", (record_id, project_id, kind, version, canonical, staged.descriptor.sha256))
                        await objects.finish(conn, "a", staged)
                    await writer.enlist_result(conn, authorization=auth, prepared=prepared, expected_revision=0)
            await _assert_absent(pool, child)
    _run(run())
