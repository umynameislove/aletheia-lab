"""Versioned result read models, published atomically with immutable records.

HTTP reads never build this index or open a canonical object. Missing/old indexes
fail closed; an operator can rebuild a bounded batch explicitly from canonical
records. No private payload cache bypasses current authorization.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from pydantic import BaseModel, SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.hosted_auth.postgres_sessions import _operator_namespace as _owned_namespace
from aletheia_lab.product.view import (
    AnyProductView,
    ProductClaim,
    ProductEvidence,
    ProductGraph,
    ProductGraphEdge,
    ProductGraphNode,
)
from aletheia_lab.project.identity import content_hasher

from .async_work import run_blocking
from .authorization import AuthorizationDenied
from .cursors import CursorInvalid
from .object_store import ObjectDescriptor
from .postgres_catalog import (
    CatalogIntegrityError,
    CatalogModel,
    PostgresCatalog,
    RecordIndex,
    _all,
    _index,
    _limit,
    _verify_projections,
    decode_record,
)
from .read_indexes import verified_extension

PROJECTION_VERSION = 1
PAGE_PAYLOAD_BYTES = 256 * 1024
Kind: TypeAlias = Literal['claim', 'evidence', 'node', 'edge']
Item: TypeAlias = ProductClaim | ProductEvidence | ProductGraphNode | ProductGraphEdge
_MODELS: dict[str, type[BaseModel]] = {
    'claim': ProductClaim, 'evidence': ProductEvidence, 'node': ProductGraphNode, 'edge': ProductGraphEdge,
}


class ProjectionUnavailable(RuntimeError):
    """A canonical record exists but no complete supported read model is ready."""


@dataclass(frozen=True)
class ResultProjection:
    project_id: str
    result_id: str
    snapshot_id: str
    canonical_sha256: str
    projection_sha256: str
    claim_count: int
    evidence_count: int
    node_count: int
    edge_count: int
    header_sha256: str = ''


@dataclass(frozen=True)
class PreparedProjection:
    manifest: ResultProjection
    # kind, identity, canonical JSON, digest, source, target, indexed text
    items: tuple[tuple[str, str, str, str, str | None, str | None, str], ...] = field(repr=False)
    links: tuple[tuple[str, str, str], ...] = field(repr=False)
    header: str = field(repr=False)


def prepare_projection(view: AnyProductView, canonical_sha256: str) -> PreparedProjection:
    """Called at the trusted producer boundary, outside its commit transaction."""
    items = []
    groups: tuple[tuple[str, tuple[Item, ...]], ...] = (
        ('claim', view.claims), ('evidence', view.evidence), ('node', view.graph.nodes), ('edge', view.graph.edges))
    for kind, values in groups:
        for value in sorted(values, key=lambda item: item.id):
            payload = value.model_dump_json()
            if len(payload.encode()) > 65536:
                raise ValueError('A result projection item exceeds its bounded read contract.')
            items.append((kind, value.id, payload, content_hasher(payload.encode()).hexdigest(),
                          value.source if isinstance(value, ProductGraphEdge) else None,
                          value.target if isinstance(value, ProductGraphEdge) else None,
                          (value.text if isinstance(value, ProductClaim) else
                           value.title + '\n' + value.text if isinstance(value, ProductEvidence) else '')))
    links = tuple(sorted((claim.id, identity, relation) for claim in view.claims
                  for relation, identities in (('citation', claim.citation_ids),
                                               ('counterevidence', claim.counterevidence_ids))
                  for identity in identities))
    header = json.dumps({'view_schema_version': view.schema_version,
        'project': view.project.model_dump(mode='json'), 'runtime': view.runtime.model_dump(mode='json'),
        'result': view.result.model_dump(mode='json'), 'conversation_id': view.conversation.id,
        'turn_count': len(view.conversation.turns)}, separators=(',', ':'), ensure_ascii=False)
    if len(header.encode()) > 32768:
        raise ValueError('The result header exceeds its bounded read contract.')
    header_sha256 = content_hasher(header.encode()).hexdigest()
    checksum = content_hasher(json.dumps(
        {'version': PROJECTION_VERSION, 'canonical': canonical_sha256,
         'header': header_sha256, 'items': [(row[0], row[1], row[3]) for row in items], 'links': links},
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    return PreparedProjection(ResultProjection(view.project.id, view.result.id, view.snapshot.id,
        canonical_sha256, checksum, len(view.claims), len(view.evidence), len(view.graph.nodes), len(view.graph.edges), header_sha256),
        tuple(items), links, header)


async def projection_installed(conn: Connection) -> bool:
    row = await (await conn.execute("SELECT to_regclass('k10_result_views') IS NOT NULL")).fetchone()
    return row == (True,)


async def publish_projection(conn: Connection, workspace: str, prepared: PreparedProjection) -> None:
    """Enlist in the record publisher's transaction; no commit and no partial visibility."""
    if conn.autocommit:
        raise RuntimeError('Transactional connection required')
    if not await projection_installed(conn):
        return  # Explicit legacy schema: capability is off and paged reads fail closed.
    view = prepared.manifest
    existing = await (await conn.execute(
        'SELECT projection_sha256 FROM k10_result_views WHERE workspace_id=%s AND result_id=%s '
        'AND projection_version=%s', (workspace, view.result_id, PROJECTION_VERSION))).fetchone()
    if existing is not None:
        if existing != (view.projection_sha256,):
            raise CatalogIntegrityError()
        return
    for kind, identity, payload, digest, source, target, text in prepared.items:
        await conn.execute('INSERT INTO k10_result_items(workspace_id,result_id,projection_version,kind,item_id,'
            'payload,sha256,source_id,target_id,search_text) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (workspace, view.result_id, PROJECTION_VERSION, kind, identity, payload, digest, source, target, text))
    for claim, evidence, relation in prepared.links:
        await conn.execute('INSERT INTO k10_evidence_links(workspace_id,result_id,projection_version,claim_id,'
            'evidence_id,relation) VALUES(%s,%s,%s,%s,%s,%s)',
            (workspace, view.result_id, PROJECTION_VERSION, claim, evidence, relation))
    # The complete manifest is last. Rollback (including rebuild interruption) hides all rows.
    await conn.execute('INSERT INTO k10_result_views(workspace_id,result_id,projection_version,project_id,'
        'snapshot_id,canonical_sha256,projection_sha256,claim_count,evidence_count,node_count,edge_count,header,header_sha256) '
        'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (workspace, view.result_id, PROJECTION_VERSION, view.project_id, view.snapshot_id,
         view.canonical_sha256, view.projection_sha256, view.claim_count, view.evidence_count,
         view.node_count, view.edge_count, prepared.header, view.header_sha256))


def _search(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or len(value) > 128
                              or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('A bounded search query is required.')
    return value.strip() or None if value is not None else None


def _item(kind: str, row: tuple[object, ...]) -> Item:
    if len(row) != 3 or not all(isinstance(value, str) for value in row):
        raise CatalogIntegrityError()
    identity, payload, digest = cast(tuple[str, str, str], row)
    if content_hasher(payload.encode()).hexdigest() != digest:
        raise CatalogIntegrityError()
    model = cast(Item, _MODELS[kind].model_validate_json(payload))
    if model.id != identity:
        raise CatalogIntegrityError()
    return model


class ResultViews:
    def __init__(self, catalog: PostgresCatalog) -> None:
        self.catalog = catalog

    async def ready(self) -> bool:
        try:
            async with self.catalog.pool.connection() as conn:
                if not await verified_extension(conn):
                    return False
                await conn.execute('SELECT m.project_id,m.snapshot_id,m.canonical_sha256,m.projection_sha256,'
                    'm.claim_count,m.evidence_count,m.node_count,m.edge_count,m.projection_version,m.header,m.header_sha256,'
                    'i.kind,i.item_id,i.payload,i.sha256,i.source_id,i.target_id,i.search_document,'
                    'l.claim_id,l.evidence_id,l.relation,r.revision FROM k10_result_views m '
                    'CROSS JOIN k10_result_items i CROSS JOIN k10_evidence_links l '
                    'CROSS JOIN k10_catalog_revisions r LIMIT 0')
            return True
        except Exception:
            return False

    async def manifest(self, digest: SecretStr, workspace: str, result: str) -> ResultProjection:
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, digest.get_secret_value(), workspace, 'read')
            record = await (await conn.execute('SELECT project_id,canonical_sha256,snapshot_id FROM k05_records '
                "WHERE workspace_id=%s AND record_id=%s AND record_type IN ('result','upload_result')",
                (workspace, result))).fetchone()
            if record is None or not isinstance(record[0], str):
                raise AuthorizationDenied()
            await self.catalog.policy.authorize_record(conn, auth, workspace, record[0], result, 'read')
            if not await projection_installed(conn) or not await verified_extension(conn):
                raise ProjectionUnavailable()
            row = await (await conn.execute('SELECT project_id,snapshot_id,canonical_sha256,projection_sha256,'
                'claim_count,evidence_count,node_count,edge_count,header_sha256 FROM k10_result_views WHERE workspace_id=%s '
                'AND result_id=%s AND projection_version=%s', (workspace, result, PROJECTION_VERSION))).fetchone()
            if row is None:
                raise ProjectionUnavailable()
            if (len(row) != 9 or row[:3] != (record[0], record[2], record[1])
                    or not all(isinstance(value, str) for value in row[:4])
                    or not all(type(value) is int and value >= 0 for value in row[4:8]) or not isinstance(row[8], str)):
                raise CatalogIntegrityError()
            return ResultProjection(cast(str, row[0]), result, cast(str, row[1]), cast(str, row[2]),
                cast(str, row[3]), *cast(tuple[int, int, int, int], row[4:8]), row[8])

    async def header(self, digest: SecretStr, workspace: str, manifest: ResultProjection) -> dict[str, object]:
        await self.manifest(digest, workspace, manifest.result_id)
        async with self.catalog.pool.connection() as conn:
            row = await (await conn.execute('SELECT header,header_sha256 FROM k10_result_views WHERE workspace_id=%s '
                'AND result_id=%s AND projection_version=%s', (workspace, manifest.result_id, PROJECTION_VERSION))).fetchone()
        if (row is None or len(row) != 2 or not isinstance(row[0], str) or row[1] != manifest.header_sha256
                or content_hasher(row[0].encode()).hexdigest() != manifest.header_sha256):
            raise CatalogIntegrityError()
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise CatalogIntegrityError()
        return value

    async def page(self, digest: SecretStr, workspace: str, manifest: ResultProjection, *, kind: Kind,
                   after: str = '', limit: int = 50, search: str | None = None) -> tuple[tuple[Item, ...], str | None]:
        _limit(limit)
        search = _search(search)
        if kind not in _MODELS or not isinstance(after, str) or len(after) > 128:
            raise CursorInvalid()
        # Reauthorize on every page, even for a hot immutable projection.
        current = await self.manifest(digest, workspace, manifest.result_id)
        if current != manifest:
            raise CursorInvalid()
        async with self.catalog.pool.connection() as conn:
            if after and await (await conn.execute('SELECT item_id FROM k10_result_items WHERE workspace_id=%s '
                'AND result_id=%s AND projection_version=%s AND kind=%s AND item_id=%s',
                (workspace, manifest.result_id, PROJECTION_VERSION, kind, after))).fetchone() is None:
                raise CursorInvalid()
            query = 'SELECT item_id,payload,sha256 FROM k10_result_items WHERE workspace_id=%s AND result_id=%s '
            query += 'AND projection_version=%s AND kind=%s AND item_id>%s '
            params: tuple[object, ...] = (workspace, manifest.result_id, PROJECTION_VERSION, kind, after)
            if search is not None:
                query += "AND search_document @@ websearch_to_tsquery('simple',%s) "
                params += (search,)
            rows = await _all(await conn.execute(query + 'ORDER BY item_id LIMIT %s', params + (limit + 1,)))
        selected: list[Item] = []
        size = 0
        for row in rows[:limit]:
            item = _item(kind, row)
            item_bytes = len(cast(str, row[1]).encode())
            if selected and size + item_bytes > PAGE_PAYLOAD_BYTES:
                break
            selected.append(item)
            size += item_bytes
        items = tuple(selected)
        return items, items[-1].id if len(rows) > len(items) else None

    async def evidence(self, digest: SecretStr, workspace: str, manifest: ResultProjection,
                       identity: str, claim_id: str | None) -> tuple[ProductEvidence, str | None]:
        await self.manifest(digest, workspace, manifest.result_id)
        async with self.catalog.pool.connection() as conn:
            row = await (await conn.execute('SELECT item_id,payload,sha256 FROM k10_result_items '
                "WHERE workspace_id=%s AND result_id=%s AND projection_version=%s AND kind='evidence' AND item_id=%s",
                (workspace, manifest.result_id, PROJECTION_VERSION, identity))).fetchone()
            if row is None:
                raise AuthorizationDenied()
            relation = None
            if claim_id is not None:
                link = await (await conn.execute('SELECT relation FROM k10_evidence_links WHERE workspace_id=%s '
                    'AND result_id=%s AND projection_version=%s AND claim_id=%s AND evidence_id=%s',
                    (workspace, manifest.result_id, PROJECTION_VERSION, claim_id, identity))).fetchone()
                if link is None or link[0] not in ('citation', 'counterevidence'):
                    raise AuthorizationDenied()
                relation = link[0]
        return cast(ProductEvidence, _item('evidence', row)), relation

    async def _node_graph(self, workspace: str, manifest: ResultProjection,
                          items: tuple[object, ...], after: str,
                          next_id: str | None) -> tuple[ProductGraph, dict[str, object] | None]:
        # Preserve the original one-response behavior for genuinely small graphs.
        if not after and next_id is None and manifest.edge_count <= 1000:
            async with self.catalog.pool.connection() as conn:
                rows = await _all(await conn.execute('SELECT item_id,payload,sha256 FROM k10_result_items '
                    "WHERE workspace_id=%s AND result_id=%s AND projection_version=%s AND kind='edge' "
                    'ORDER BY item_id LIMIT 1000', (workspace, manifest.result_id, PROJECTION_VERSION)))
            if len(rows) != manifest.edge_count:
                raise CatalogIntegrityError()
            return ProductGraph(nodes=cast(tuple[ProductGraphNode, ...], items),
                edges=cast(tuple[ProductGraphEdge, ...], tuple(_item('edge', row) for row in rows))), None
        continuation: dict[str, object] | None = ({'phase': 'nodes', 'after': next_id} if next_id else
            {'phase': 'edges', 'after': ''} if manifest.edge_count else None)
        return ProductGraph(nodes=cast(tuple[ProductGraphNode, ...], items), edges=()), continuation

    async def graph(self, digest: SecretStr, workspace: str, manifest: ResultProjection, *,
                    phase: str, after: str, limit: int) -> tuple[ProductGraph, dict[str, object] | None]:
        if phase not in ('nodes', 'edges'):
            raise CursorInvalid()
        kind: Kind = 'node' if phase == 'nodes' else 'edge'
        _limit(limit)
        if phase == 'nodes':
            items, next_id = await self.page(digest, workspace, manifest, kind=kind, after=after, limit=limit)
        else:
            await self.manifest(digest, workspace, manifest.result_id)
            async with self.catalog.pool.connection() as conn:
                if after and await (await conn.execute('SELECT item_id FROM k10_result_items WHERE workspace_id=%s '
                    "AND result_id=%s AND projection_version=%s AND kind='edge' AND item_id=%s",
                    (workspace, manifest.result_id, PROJECTION_VERSION, after))).fetchone() is None:
                    raise CursorInvalid()
                rows = await _all(await conn.execute('SELECT item_id,payload,sha256 FROM k10_result_items '
                    "WHERE workspace_id=%s AND result_id=%s AND projection_version=%s AND kind='edge' AND item_id>%s "
                    'ORDER BY item_id LIMIT 1001', (workspace, manifest.result_id, PROJECTION_VERSION, after)))
            items = tuple(_item('edge', row) for row in rows[:1000])
            next_id = items[-1].id if len(rows) > 1000 else None
        if phase == 'nodes':
            return await self._node_graph(workspace, manifest, items, after, next_id)
        edges: list[ProductGraphEdge] = []
        endpoints: set[str] = set()
        for item in cast(tuple[ProductGraphEdge, ...], items):
            if len(endpoints | {item.source, item.target}) > max(2, limit):
                break
            edges.append(item)
            endpoints.update((item.source, item.target))
        if not edges:
            raise CursorInvalid()
        async with self.catalog.pool.connection() as conn:
            rows = await _all(await conn.execute('SELECT item_id,payload,sha256 FROM k10_result_items WHERE '
                "workspace_id=%s AND result_id=%s AND projection_version=%s AND kind='node' AND item_id=ANY(%s) "
                'ORDER BY item_id LIMIT %s', (workspace, manifest.result_id, PROJECTION_VERSION,
                                            sorted(endpoints), len(endpoints))))
        nodes = cast(tuple[ProductGraphNode, ...], tuple(_item('node', row) for row in rows))
        if {node.id for node in nodes} != endpoints:
            raise CatalogIntegrityError()
        more = next_id is not None or len(edges) < len(items)
        return ProductGraph(nodes=nodes, edges=tuple(edges)), {'phase': 'edges', 'after': edges[-1].id} if more else None


@dataclass(frozen=True)
class RebuildPage:
    examined: int
    next_after: str | None
    projection_version: int = PROJECTION_VERSION


async def rebuild_result_views(catalog: PostgresCatalog, schema: str, workspace: str, *,
                               after: str = '', limit: int = 10, replace: bool = False) -> RebuildPage:
    """Explicit owner-only bounded backfill/repair; no caller impersonation or GET fallback.

    Each immutable result commits independently, making interruption resumable by
    its last result ID. Replacement deletes and publishes in one transaction;
    readers see the complete old or complete new version, never partial rows.
    """
    from psycopg import sql

    from .native_upload_records import CanonicalResultRecord, result_view

    def prepare(index: RecordIndex, payload: bytes) -> tuple[CatalogModel, PreparedProjection]:
        model = decode_record(index, payload)
        return model, prepare_projection(result_view(cast(CanonicalResultRecord, model)), index.canonical_sha256)

    _limit(limit)
    if not isinstance(after, str) or len(after) > 128 or type(replace) is not bool:
        raise ValueError('A bounded operator rebuild is required.')
    async with catalog.pool.connection() as conn:
        namespace = await _owned_namespace(conn, schema)
        await conn.execute('SET LOCAL search_path TO ' + namespace[:-1] + ',pg_catalog')
        rows = await _all(await conn.execute(
            'SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,'
            'r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id,o.byte_size,o.media_type,o.schema_version '
            'FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 '
            "WHERE r.workspace_id=%s AND r.record_type IN ('result','upload_result') AND r.record_id>%s "
            'ORDER BY r.record_id LIMIT %s', (workspace, after, limit + 1)))
    for row in rows[:limit]:
        index = _index(row[:9])
        descriptor = ObjectDescriptor(index.object_sha256, cast(int, row[9]), cast(str, row[10]), cast(str | None, row[11]))
        model, prepared = await run_blocking(prepare, index, await catalog.objects.read(workspace, descriptor))
        async with catalog.pool.connection() as conn:
            namespace = await _owned_namespace(conn, schema)
            await conn.execute('SET LOCAL search_path TO ' + namespace[:-1] + ',pg_catalog')
            if not await projection_installed(conn):
                raise ProjectionUnavailable()
            current = await (await conn.execute('SELECT canonical_sha256,object_sha256 FROM k05_records '
                'WHERE workspace_id=%s AND record_id=%s FOR SHARE', (workspace, index.record_id))).fetchone()
            if current != (index.canonical_sha256, index.object_sha256):
                raise CatalogIntegrityError()
            await _verify_projections(conn, workspace, index, model)
            if replace:
                for table in ('k10_evidence_links', 'k10_result_items', 'k10_result_views'):
                    await conn.execute(sql.SQL('DELETE FROM {} WHERE workspace_id=%s AND result_id=%s '
                                       'AND projection_version=%s').format(sql.Identifier(table)).as_string(),
                                       (workspace, index.record_id, PROJECTION_VERSION))
            await publish_projection(conn, workspace, prepared)
    return RebuildPage(min(limit, len(rows)), cast(str, rows[limit - 1][0]) if len(rows) > limit else None)
