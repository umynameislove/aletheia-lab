"""Authorized HTTP projections over the canonical catalog, not another store."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Literal, Protocol, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import _digest
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _view_from_record
from aletheia_lab.product.view import AnyProductView, parse_product_view
from aletheia_lab.project.identity import content_hasher

from .application import VERSION
from .async_work import run_blocking
from .authorization import Authorization, AuthorizationDenied
from .cursors import CursorInvalid, ScopedCursors
from .durable_exports import DurableExports, StoredExport
from .native_upload_records import (
    UploadResultRecord,
    UploadResultRecordV2,
    parse_upload_result_record,
)
from .postgres_catalog import KINDS, CatalogCursor, CatalogModel, PostgresCatalog
from .resource_admission import ProcessSlots
from .result_views import PROJECTION_VERSION, ResultProjection, ResultViews, _search

EXPORT_ACTIVE_PER_PROCESS = 1
EXPORT_WAITERS_PER_PROCESS = 4
_EXPORT_SLOTS = ProcessSlots(EXPORT_ACTIVE_PER_PROCESS, EXPORT_WAITERS_PER_PROCESS)


class Renderer(Protocol):
    async def render(self, report_format: Literal["json", "markdown", "pdf"], payload: bytes) -> bytes: ...


class CatalogAccess:
    def __init__(self, catalog: PostgresCatalog, cursors: ScopedCursors, renderer: Renderer) -> None:
        self.catalog, self.cursors, self.renderer = catalog, cursors, renderer
        self.views = ResultViews(catalog)

    @property
    def exports(self) -> DurableExports:
        return DurableExports(self.catalog)

    async def paged_ready(self) -> bool:
        return await self.views.ready()

    async def is_ready(self) -> bool:
        """Check current principal access without reading private rows or hashing schemas."""
        try:
            async with self.catalog.pool.connection() as conn:
                await conn.execute(
                    "SELECT p.*,r.*,o.*,c.*,h.*,f.*,l.*,g.*,a.*,e.* FROM k05_projects p "
                    "CROSS JOIN k05_records r CROSS JOIN k05_objects o "
                    "CROSS JOIN k05_current_pointers c CROSS JOIN k05_record_parents h "
                    "CROSS JOIN k05_object_references f CROSS JOIN k05_object_leases l "
                    "CROSS JOIN j01_project_grants g CROSS JOIN j01_annotations a CROSS JOIN k02_exports e LIMIT 0")
            return True
        except Exception:
            return False

    async def authorize(self, digest: SecretStr, workspace: str, project: str,
                        action: str = "read", *, record_id: str | None = None) -> Authorization:
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, action)
            if record_id is None:
                await self.catalog.policy.authorize_project(conn, auth, workspace, project, action)
            else:
                await self.catalog.policy.authorize_record(conn, auth, workspace, project, record_id, action)
            return auth

    async def _binding(self, digest: SecretStr, workspace: str, context: str,
                       query: dict[str, object], *, project: str | None = None) -> dict[str, object]:
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, "read")
            if project is not None:
                await self.catalog.policy.authorize_project(conn, auth, workspace, project, "read")
            current = await (await conn.execute(
                "SELECT s.context_id FROM j01_session_credentials c JOIN j01_sessions s ON s.context_id=c.context_id "
                "WHERE c.digest=%s AND s.user_id=%s", (_digest(digest), auth.user_id),
            )).fetchone()
            if current != (context,):
                raise AuthorizationDenied()
            binding: dict[str, object] = {"workspace_id": workspace, "user_id": auth.user_id, "context_id": context,
                                         "membership_revision": auth.revision, "query": query}
            installed = await (await conn.execute("SELECT to_regclass('k10_catalog_revisions') IS NOT NULL")).fetchone()
            if installed == (True,):
                row = await (await conn.execute('SELECT revision FROM k10_catalog_revisions WHERE workspace_id=%s',
                                               (workspace,))).fetchone()
                binding['catalog_revision'] = 0 if row is None else row[0]
            return binding

    def _after(self, cursor: str | None, purpose: str, binding: dict[str, object]) -> dict[str, object]:
        return {} if cursor is None else self.cursors.read(purpose=purpose, binding=binding, cursor=cursor)

    def _page(self, workspace: str, items: list[object], *, purpose: str,
              binding: dict[str, object], continuation: dict[str, object] | None) -> dict[str, object]:
        return {"schema_version": VERSION, "workspace_id": workspace, "items": items,
                "next_cursor": None if continuation is None else self.cursors.issue(
                    purpose=purpose, binding=binding, value=continuation), "total": None, "total_kind": "unknown"}

    async def _unchanged(self, digest: SecretStr, workspace: str, context: str,
                         binding: dict[str, object], *, project: str | None = None) -> None:
        # Detect a publication/revoke racing the SQL page query. Never mint a cursor
        # for rows fetched under a different catalog generation.
        if 'catalog_revision' in binding:
            current = await self._binding(digest, workspace, context, cast(dict[str, object], binding['query']),
                                          project=project)
            if current != binding:
                raise CursorInvalid()

    async def projects(self, digest: SecretStr, workspace: str, context: str, *, limit: int = 50,
                       cursor: str | None = None, search: str | None = None) -> dict[str, object]:
        binding = await self._binding(digest, workspace, context, {"limit": limit, "search": search})
        value = self._after(cursor, "projects", binding)
        if value and (set(value) != {"after"} or not isinstance(value["after"], str)):
            raise CursorInvalid()
        after = cast(str, value.get("after", ""))
        rows = await self.catalog.list_projects(credential_digest=digest, workspace_id=workspace,
                                                after=after, limit=limit, search=search)
        more = rows and len(rows) == limit and bool(await self.catalog.list_projects(
            credential_digest=digest, workspace_id=workspace, after=rows[-1][0], limit=1, search=search))
        items: list[object] = [{"project_id": row[0], "revision": row[1]} for row in rows]
        await self._unchanged(digest, workspace, context, binding)
        return self._page(workspace, items, purpose="projects", binding=binding,
                          continuation={"after": rows[-1][0]} if more else None)

    async def records(self, digest: SecretStr, workspace: str, project: str, context: str, *,
                      kind: str, snapshot_id: str | None = None, limit: int = 50,
                      cursor: str | None = None, search: str | None = None) -> dict[str, object]:
        if kind not in KINDS or kind == "preview":
            raise ValueError("A confirmed record kind is required.")
        binding = await self._binding(digest, workspace, context,
            {"project_id": project, "kind": kind, "snapshot_id": snapshot_id, "limit": limit,
             "search": search}, project=project)
        value = self._after(cursor, "records", binding)
        continuation = None
        if value:
            if set(value) != {"after"} or not isinstance(value["after"], str):
                raise CursorInvalid()
            continuation = CatalogCursor(workspace, project, kind, snapshot_id,
                cast(str, binding["user_id"]), cast(int, binding["membership_revision"]), value["after"], search)
        page = await self.catalog.list_records(credential_digest=digest, workspace_id=workspace,
            project_id=project, record_type=kind, snapshot_id=snapshot_id, cursor=continuation, limit=limit,
            search=search)
        items = [asdict(record) for record in page.records]
        # Storage object digests are not public download handles.
        for item in items:
            del item["object_sha256"]
            item.pop("source_id", None)
        await self._unchanged(digest, workspace, context, binding, project=project)
        return self._page(workspace, cast(list[object], items), purpose="records", binding=binding,
                          continuation={"after": page.next_cursor.after} if page.next_cursor else None)

    async def workspace_records(self, digest: SecretStr, workspace: str, context: str, *,
                                kind: str = "result", limit: int = 50, cursor: str | None = None,
                                search: str | None = None) -> dict[str, object]:
        binding = await self._binding(digest, workspace, context,
                                      {"kind": kind, "limit": limit, "search": search})
        value = self._after(cursor, "workspace-records", binding)
        if value and (set(value) != {"after"} or not isinstance(value["after"], str)):
            raise CursorInvalid()
        page = await self.catalog.list_workspace_records(credential_digest=digest, workspace_id=workspace,
            kind=kind, after=cast(str, value.get("after", "")), limit=limit, search=search)
        items = [asdict(record) for record in page.records]
        for item in items:
            del item["object_sha256"]
            item.pop("source_id", None)
        await self._unchanged(digest, workspace, context, binding)
        return self._page(workspace, cast(list[object], items), purpose="workspace-records", binding=binding,
                          continuation={"after": page.next_cursor.after} if page.next_cursor else None)

    async def _view_binding(self, digest: SecretStr, workspace: str, context: str,
                            manifest: ResultProjection, query: dict[str, object]) -> dict[str, object]:
        return await self._binding(digest, workspace, context,
            {'result_id': manifest.result_id, 'snapshot_id': manifest.snapshot_id,
             'projection_version': PROJECTION_VERSION, 'projection_sha256': manifest.projection_sha256,
             **query}, project=manifest.project_id)

    async def result_items(self, digest: SecretStr, workspace: str, result: str, context: str, *,
                           kind: Literal['claim', 'evidence'], limit: int = 50, cursor: str | None = None,
                           search: str | None = None) -> dict[str, object]:
        search = _search(search)
        manifest = await self.views.manifest(digest, workspace, result)
        binding = await self._view_binding(digest, workspace, context, manifest,
                                           {'kind': kind, 'limit': limit, 'search': search})
        value = self._after(cursor, 'result-items', binding)
        if value and (set(value) != {'after'} or not isinstance(value['after'], str)):
            raise CursorInvalid()
        items, after = await self.views.page(digest, workspace, manifest, kind=kind,
            after=cast(str, value.get('after', '')), limit=limit, search=search)
        await self._unchanged(digest, workspace, context, binding, project=manifest.project_id)
        output = self._page(workspace, list(items), purpose='result-items', binding=binding,
                            continuation={'after': after} if after else None)
        output.update(project_id=manifest.project_id, result_id=result, snapshot_id=manifest.snapshot_id)
        if search is None:
            output.update(total=manifest.claim_count if kind == 'claim' else manifest.evidence_count, total_kind='exact')
        return output

    async def result_evidence(self, digest: SecretStr, workspace: str, result: str,
                              evidence: str, claim: str | None) -> dict[str, object]:
        manifest = await self.views.manifest(digest, workspace, result)
        item, relation = await self.views.evidence(digest, workspace, manifest, evidence, claim)
        return {'schema_version': VERSION, 'workspace_id': workspace, 'project_id': manifest.project_id,
                'result_id': result, 'evidence_id': evidence, 'claim_id': claim, 'relation': relation, 'evidence': item}

    async def result_header(self, digest: SecretStr, workspace: str, result: str) -> dict[str, object]:
        manifest = await self.views.manifest(digest, workspace, result)
        header = await self.views.header(digest, workspace, manifest)
        return {**header, 'schema_version': VERSION, 'workspace_id': workspace, 'project_id': manifest.project_id,
            'result_id': result, 'snapshot_id': manifest.snapshot_id, 'claim_count': manifest.claim_count,
            'evidence_count': manifest.evidence_count, 'node_count': manifest.node_count, 'edge_count': manifest.edge_count}

    async def result_graph(self, digest: SecretStr, workspace: str, result: str, context: str, *,
                           limit: int = 50, cursor: str | None = None) -> dict[str, object]:
        manifest = await self.views.manifest(digest, workspace, result)
        binding = await self._view_binding(digest, workspace, context, manifest, {'limit': limit})
        value = {'phase': 'nodes', 'after': ''} if cursor is None else self._after(cursor, 'result-graph', binding)
        if (set(value) != {'phase', 'after'} or not isinstance(value['phase'], str)
                or not isinstance(value['after'], str)):
            raise CursorInvalid()
        graph, continuation = await self.views.graph(digest, workspace, manifest, phase=value['phase'],
                                                     after=value['after'], limit=limit)
        await self._unchanged(digest, workspace, context, binding, project=manifest.project_id)
        return {'schema_version': VERSION, 'workspace_id': workspace, 'project_id': manifest.project_id,
                'result_id': result, 'graph': graph,
                'truncated': len(graph.nodes) < manifest.node_count or len(graph.edges) < manifest.edge_count,
                'next_cursor': None if continuation is None else self.cursors.issue(
                    purpose='result-graph', binding=binding, value=continuation)}

    @staticmethod
    def _view(record: CatalogModel, workspace: str, project: str, result_id: str) -> AnyProductView:
        if isinstance(record, (UploadResultRecord, UploadResultRecordV2)):
            checked = parse_upload_result_record(record.model_dump(mode="python"))
            if (checked.workspace_id, checked.project_id, checked.record_id) != (workspace, project, result_id):
                raise ValueError("Native result scope failed its integrity check.")
            return parse_product_view(checked.view.model_dump(mode="python"))
        if isinstance(record, ProductLifecycleRecord) and record.record_kind == "result":
            return _view_from_record(record)
        raise AuthorizationDenied()

    async def result(self, digest: SecretStr, workspace: str, result_id: str, *,
                     maximum_bytes: int | None = None) -> tuple[str, AnyProductView]:
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, "read")
            row = await (await conn.execute("SELECT project_id FROM k05_records WHERE workspace_id=%s "
                "AND record_id=%s AND record_type IN ('result','upload_result')", (workspace, result_id))).fetchone()
            if row is None or not isinstance(row[0], str):
                raise AuthorizationDenied()
            project = row[0]
            await self.catalog.policy.authorize_project(conn, auth, workspace, project, "read")
        options = {} if maximum_bytes is None else {'maximum_bytes': maximum_bytes}
        record = await self.catalog.read_record(credential_digest=digest, workspace_id=workspace,
                                               project_id=project, record_id=result_id, **options)
        view = await run_blocking(self._view, record, workspace, project, result_id)
        if view.result.id != result_id or view.project.id != project:
            raise ValueError("Result scope failed its integrity check.")
        return project, view

    async def export(self, digest: SecretStr, workspace: str, result_id: str,
                     report_format: Literal["json", "markdown", "pdf"]) -> tuple[str, bytes, str]:
        item = await self.create_export(digest, workspace, result_id, report_format)
        _, payload = await self.download_export(digest, workspace, item.export_id)
        return item.project_id, payload, content_hasher(payload).hexdigest()

    async def create_export(self, digest: SecretStr, workspace: str, result_id: str,
                            report_format: Literal["json", "markdown", "pdf"], *,
                            key_hash: str | None = None) -> StoredExport:
        # Only scalar scope/key metadata may wait; loading a full view before
        # admission would retain one large private payload for every waiter.
        project = await self._export_project(digest, workspace, result_id)
        existing = await self.exports.find(digest, workspace, project, result_id, report_format, key_hash)
        if existing is not None:
            return existing
        await _EXPORT_SLOTS.acquire()
        try:
            # A queued duplicate may now have a durable receipt; this also
            # rechecks current export/source/parent authority before any read.
            existing = await self.exports.find(digest, workspace, project, result_id, report_format, key_hash)
            if existing is not None:
                return existing
            current_project, view = await self.result(digest, workspace, result_id)
            if current_project != project:
                raise ValueError('Result export scope changed after admission.')
            data = await run_blocking(lambda: view.model_dump_json().encode('utf-8'))
            payload = await self.renderer.render(report_format, data)
            # The existing renderer drains cancellation through child cleanup;
            # publication independently checks current authority in its commit.
            return await self.exports.publish(digest, workspace, project, result_id, report_format, payload, key_hash)
        finally:
            _EXPORT_SLOTS.release()

    async def _export_project(self, digest: SecretStr, workspace: str, result_id: str) -> str:
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, 'export')
            row = await (await conn.execute('SELECT project_id FROM k05_records WHERE workspace_id=%s '
                "AND record_id=%s AND record_type IN ('result','upload_result')", (workspace, result_id))).fetchone()
            if row is None or not isinstance(row[0], str):
                raise AuthorizationDenied()
            project = row[0]
            kind = await self.catalog.policy.authorize_record(conn, auth, workspace, project, result_id, 'export')
            if kind not in ('result', 'upload_result'):
                raise AuthorizationDenied()
            return project

    async def download_export(self, digest: SecretStr, workspace: str, export_id: str) -> tuple[StoredExport, bytes]:
        return await self.exports.read(digest, workspace, export_id)

    def export_checkpoint(self, digest: SecretStr, item: StoredExport) -> Callable[[], Awaitable[None]]:
        async def check() -> None:
            current = await self.exports.get(digest, item.workspace_id, item.export_id)
            if current != item:
                raise ValueError("Stored export changed during delivery.")
        return check

    def records_checkpoint(self, digest: SecretStr, workspace: str, record_ids: tuple[str, ...], *,
                           project: str | None = None) -> Callable[[], Awaitable[None]]:
        async def check() -> None:
            await self.catalog.check_records(credential_digest=digest, workspace_id=workspace,
                                            record_ids=record_ids, project_id=project)
        return check

    def checkpoint(self, digest: SecretStr, workspace: str, project: str,
                   action: str = "read", *, record_id: str | None = None) -> Callable[[], Awaitable[None]]:
        async def check() -> None:
            await self.authorize(digest, workspace, project, action, record_id=record_id)
        return check
