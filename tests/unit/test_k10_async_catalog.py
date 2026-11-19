"""Large canonical validation leaves the event loop and rechecks live authority."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k05_catalog import _graph

import aletheia_lab.hosted.postgres_catalog as module
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_catalog import CatalogReadTooLarge, PostgresCatalog, RecordIndex
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import _json_bytes


@pytest.mark.parametrize('revoke_during_decode', [False, True, 'cancel'])
def test_decode_is_off_loop_and_outside_transaction_before_final_policy_check(monkeypatch, revoke_during_decode):
    async def run():
        loop, main = asyncio.get_running_loop(), threading.get_ident()
        graph = _graph()
        payload = _json_bytes(graph.model_dump(mode='json'))
        index = RecordIndex(graph.graph_id, graph.project_id, 'lineage_graph', graph.schema_version,
            canonical_project_sha256(graph.model_dump(mode='json')), hashlib.sha256(payload).hexdigest(), None, None)
        state = SimpleNamespace(active=False, allowed=True, checks=0, verified=False)
        entered, release = asyncio.Event(), threading.Event()
        decode = module.decode_record

        def held_decode(*args):
            assert threading.get_ident() != main and not state.active
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return decode(*args)

        class Connection:
            async def execute(self, query, params=()):
                assert state.active
                return SimpleNamespace(fetchone=self.fetchone)

            async def fetchone(self):
                return (index.record_id, index.project_id, index.record_type, index.schema_version,
                    index.canonical_sha256, index.object_sha256, None, None, None, len(payload), 'application/json', graph.schema_version)

        class Pool:
            @asynccontextmanager
            async def connection(self):
                state.active = True
                try:
                    yield Connection()
                finally:
                    state.active = False

        async def read(workspace, descriptor):
            assert not state.active and threading.get_ident() == main
            return payload

        async def access(*args):
            assert state.active and threading.get_ident() == main
            state.checks += 1
            if not state.allowed:
                raise AuthorizationDenied()

        async def verify(*args):
            assert state.active and threading.get_ident() == main
            state.verified = True

        monkeypatch.setattr(module, 'decode_record', held_decode)
        monkeypatch.setattr(module, '_verify_projections', verify)
        catalog = PostgresCatalog(Pool(), SimpleNamespace(read=read))
        catalog._access = access
        task = asyncio.create_task(catalog.read_record(credential_digest=SecretStr('a' * 64),
            workspace_id='wa', project_id=graph.project_id, record_id=graph.graph_id))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not task.done() and not state.active
            state.allowed = not revoke_during_decode
            if revoke_during_decode == 'cancel':
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()  # the actual CPU task still owns its execution slot
        finally:
            release.set()
        if revoke_during_decode == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
            assert state.checks == 1 and not state.verified
        elif revoke_during_decode:
            with pytest.raises(AuthorizationDenied):
                await task
            assert not state.verified
        else:
            assert await task == graph and state.verified
        if revoke_during_decode != 'cancel':
            assert state.checks == 2
    asyncio.run(run())


def test_native_validation_recompute_and_parent_metadata_run_off_loop_before_async_stage(tmp_path, monkeypatch):
    from test_k05_native_upload_records import upload_case

    import aletheia_lab.hosted.canonical_results as results
    from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
    from aletheia_lab.hosted.object_store import FilesystemObjectStore, ObjectDescriptor
    from aletheia_lab.hosted.postgres_objects import StagedObject

    snapshot, view = upload_case(tmp_path)
    record = build_initial_upload_result(snapshot, view.conversation.turns[0].question, view)
    main, calls = threading.get_ident(), []
    store = FilesystemObjectStore(tmp_path / 'prepared-content')
    describe = store.describe

    def off_loop(name, callback):
        def checked(*args, **kwargs):
            assert threading.get_ident() != main
            calls.append(name)
            return callback(*args, **kwargs)
        return checked

    monkeypatch.setattr(results, 'parse_upload_snapshot', off_loop('validate_snapshot', results.parse_upload_snapshot))
    monkeypatch.setattr(results, 'build_initial_upload_result', off_loop('recompute', results.build_initial_upload_result))
    monkeypatch.setattr(store, 'describe', off_loop('metadata', describe))

    class Objects:
        async def stage(self, workspace, payload, *, media_type, schema_version):
            assert threading.get_ident() == main
            calls.append('stage')
            return StagedObject(ObjectDescriptor(hashlib.sha256(payload).hexdigest(), len(payload),
                                                  media_type, schema_version), 'synthetic-prepared-lease')

    objects = Objects()
    objects.store = store
    prepared = asyncio.run(results.CanonicalResults(objects).prepare_initial_result(
        snapshot.workspace_id, snapshot=snapshot, record=record))
    assert {'validate_snapshot', 'recompute', 'metadata'} <= set(calls)
    assert calls[-1] == 'stage' and calls.count('metadata') == 2
    assert prepared.result.record_id == record.record_id
    assert prepared.staged.descriptor.sha256 == hashlib.sha256(_json_bytes(record.model_dump(mode='json'))).hexdigest()


@pytest.mark.parametrize('current', [False, True])
@pytest.mark.parametrize('denied', [False, True])
def test_object_size_guard_checks_exact_record_authority_before_any_payload_io(current, denied):
    async def run():
        graph = _graph()
        payload = _json_bytes(graph.model_dump(mode='json'))
        calls = []

        class Connection:
            async def execute(self, query, params=()):
                calls.append(('query', query))
                if 'SELECT c.record_id' in query:
                    row = (graph.graph_id,)
                else:
                    row = (graph.graph_id, graph.project_id, 'lineage_graph', graph.schema_version,
                        canonical_project_sha256(graph.model_dump(mode='json')), hashlib.sha256(payload).hexdigest(),
                        None, None, None, len(payload), 'application/json', graph.schema_version)

                async def fetchone():
                    return row
                return SimpleNamespace(fetchone=fetchone)

        class Pool:
            @asynccontextmanager
            async def connection(self):
                yield Connection()

        async def forbidden(*args):
            raise AssertionError('Oversized/denied delivery opened the full object')

        async def access(conn, credential, workspace, project, record=None):
            calls.append(('authorize', record))
            if record is not None and denied:
                raise AuthorizationDenied()

        catalog = PostgresCatalog(Pool(), SimpleNamespace(read=forbidden))
        catalog._access = access
        args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa', project_id=graph.project_id,
                    maximum_bytes=len(payload) - 1)
        call = catalog.current(**args) if current else catalog.read_record(**args, record_id=graph.graph_id)
        with pytest.raises(AuthorizationDenied if denied else CatalogReadTooLarge):
            await call
        assert ('authorize', graph.graph_id) in calls
        if current:
            assert calls[:2] == [('authorize', None), ('query', calls[1][1])]
        authorized = next(i for i, value in enumerate(calls) if value == ('authorize', graph.graph_id))
        assert not any('SELECT r.record_id' in value for kind, value in calls[:authorized] if kind == 'query')
        if denied:
            assert not any('SELECT r.record_id' in value for kind, value in calls if kind == 'query')
    asyncio.run(run())


@pytest.mark.parametrize('maximum', [True, 0, -1, 1.5])
@pytest.mark.parametrize('current', [False, True])
def test_non_integer_or_nonpositive_object_bounds_fail_before_database(maximum, current):
    class NoDatabase:
        def connection(self):
            raise AssertionError('Invalid object bound opened a database connection')

    catalog = PostgresCatalog(NoDatabase(), None)
    args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa', project_id='project', maximum_bytes=maximum)
    with pytest.raises(ValueError, match='positive integer'):
        asyncio.run(catalog.current(**args) if current else catalog.read_record(**args, record_id='record'))
