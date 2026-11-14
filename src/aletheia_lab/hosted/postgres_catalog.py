"""Bounded, typed reads over the canonical hosted P3/P6 catalog.

This storage facade is not an HTTP adapter. Public cursor signing stays with
K-06. Project authorization delegates to the existing K-04 policy extension.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Cursor, Pool
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _view_from_record
from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import (
    _MODEL_BY_TYPE,
    _model_identity,
    parse_stored_project_model,
)

from .async_work import run_blocking
from .authorization import (
    Authorization,
    AuthorizationDenied,
    PostgresAuthorization,
    record_source_filter,
)
from .legacy_import import Model, model_metadata, record_parents
from .native_upload_records import (
    NativeUploadModel,
    UploadResultRecord,
    UploadResultRecordV2,
    native_metadata,
    native_parents,
    parse_upload_result_record,
)
from .object_store import ObjectDescriptor, ObjectIntegrityError
from .postgres_objects import PostgresObjects
from .read_indexes import ReadIndexesRejected, verified_extension
from .upload_comparison import (
    COMPARISON_SCHEMA,
    UploadGenerationComparison,
    comparison_metadata,
    comparison_parents,
)
from .upload_snapshot import UploadSnapshot, UploadSnapshotV2, parse_upload_snapshot

CatalogModel: TypeAlias = Model | NativeUploadModel | UploadGenerationComparison
KINDS = frozenset(_MODEL_BY_TYPE) | {"preview", "project_state", "result", "turn", "deletion", "upload_snapshot", "upload_result"}


class CatalogIntegrityError(ValueError):
    def __init__(self) -> None:
        super().__init__("Stored catalog data failed its integrity check.")


class CatalogReadTooLarge(ValueError):
    def __init__(self) -> None:
        super().__init__('The canonical object exceeds the configured delivery bound.')


def _maximum_bytes(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError('A positive integer object read bound is required.')


@dataclass(frozen=True)
class RecordIndex:
    record_id: str
    project_id: str | None
    record_type: str
    schema_version: str
    canonical_sha256: str
    object_sha256: str
    snapshot_id: str | None
    parent_result_id: str | None
    source_id: str | None = None


@dataclass(frozen=True)
class CatalogCursor:
    """Internal continuation, bound to the authenticated query and revision."""
    workspace_id: str
    project_id: str
    record_type: str
    snapshot_id: str | None
    user_id: str
    membership_revision: int
    after: str
    search: str | None = None


@dataclass(frozen=True)
class RecordPage:
    records: tuple[RecordIndex, ...]
    next_cursor: CatalogCursor | None


def canonical_metadata(model: CatalogModel) -> tuple[str, str, str | None, str, str]:
    if isinstance(model, UploadGenerationComparison):
        return comparison_metadata(model)
    return native_metadata(model) if isinstance(model, (UploadSnapshot, UploadSnapshotV2, UploadResultRecord, UploadResultRecordV2)) else model_metadata(model)


def canonical_parents(model: CatalogModel) -> tuple[tuple[str, str], ...]:
    if isinstance(model, UploadGenerationComparison):
        return comparison_parents(model)
    return native_parents(model) if isinstance(model, (UploadSnapshot, UploadSnapshotV2, UploadResultRecord, UploadResultRecordV2)) else record_parents(model)


def decode_record(index: RecordIndex, payload: bytes) -> CatalogModel:
    """Apply the same canonical identity validators as the local domain facade."""
    try:
        if index.record_type == 'snapshot_comparison' and index.schema_version == COMPARISON_SCHEMA:
            comparison = UploadGenerationComparison.model_validate_json(payload)
            if comparison_metadata(comparison) != (index.record_type, index.record_id, index.project_id,
                index.schema_version, index.canonical_sha256) or any(value is not None for value in
                    (index.snapshot_id, index.parent_result_id, index.source_id)):
                raise CatalogIntegrityError()
            return comparison
        if index.record_type in ("upload_snapshot", "upload_result"):
            return _decode_native_record(index, payload)
        if index.source_id is not None:
            raise CatalogIntegrityError()
        if index.record_type in _MODEL_BY_TYPE:
            project_model = parse_stored_project_model(index.record_type, payload)
            kind, identity, project, schema = _model_identity(project_model)
            actual = canonical_project_sha256(project_model.model_dump(mode="json"))
            if (kind, identity, project, schema, actual) != (
                index.record_type, index.record_id, index.project_id,
                index.schema_version, index.canonical_sha256,
            ) or index.snapshot_id is not None or index.parent_result_id is not None:
                raise CatalogIntegrityError()
            return cast(Model, project_model)
        model = ProductLifecycleRecord.model_validate_json(payload)
        if (model.record_kind, model.record_id, model.project_id, model.schema_version,
            model.canonical_sha256(), model.snapshot_id, model.parent_result_id) != (
            index.record_type, index.record_id, index.project_id, index.schema_version,
            index.canonical_sha256, index.snapshot_id, index.parent_result_id,
        ):
            raise CatalogIntegrityError()
        if model.record_kind == "result":
            _view_from_record(model)
        return cast(Model, model)
    except (ValueError, TypeError, KeyError):
        raise CatalogIntegrityError() from None


async def _all(cursor: Cursor) -> list[tuple[object, ...]]:
    rows = []
    while (row := await cursor.fetchone()) is not None:
        rows.append(row)
    return rows


def _index(row: tuple[object, ...]) -> RecordIndex:
    if len(row) not in (8, 9) or any(not isinstance(row[i], str) for i in (0, 2, 3, 4, 5)) or any(
        row[i] is not None and not isinstance(row[i], str) for i in ((1, 6, 7, 8) if len(row) == 9 else (1, 6, 7))
    ):
        raise CatalogIntegrityError()
    return RecordIndex(cast(str, row[0]), cast(str | None, row[1]), cast(str, row[2]),
                       cast(str, row[3]), cast(str, row[4]), cast(str, row[5]),
                       cast(str | None, row[6]), cast(str | None, row[7]),
                       cast(str | None, row[8]) if len(row) == 9 else None)


def _limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("page size must be between 1 and 100")
    return value


def _record_types(kind: str) -> list[str]:
    return {"result": ["result", "upload_result"], "snapshot": ["snapshot", "upload_snapshot"]}.get(kind, [kind])


def _search(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or len(value) > 128):
        raise ValueError("bounded literal ID search required")
    return value or None


async def _indexed_profile(conn: Connection) -> bool:
    indexed = await verified_extension(conn)
    row = await (await conn.execute("SELECT to_regclass('k10_result_views'),to_regclass('k10_catalog_revisions')")).fetchone()
    if row == (None, None):
        return False
    if row is None or any(value is None for value in row) or not indexed:
        raise ReadIndexesRejected()
    return True


def _visible_projects(auth: Authorization, workspace: str) -> tuple[str, tuple[object, ...]]:
    if auth.role in ('owner', 'admin'):
        return '', ()
    return (
        'JOIN (SELECT project_id FROM k05_projects WHERE workspace_id=%s AND creator_id=%s '
        'UNION SELECT project_id FROM j01_project_grants WHERE workspace_id=%s AND user_id=%s '
        "AND 'read'=ANY(actions)) visible ON visible.project_id=p.project_id ",
        (workspace, auth.user_id, workspace, auth.user_id),
    )


class PostgresCatalog:
    def __init__(self, pool: Pool, objects: PostgresObjects, *,
                 policy: PostgresAuthorization | None = None) -> None:
        self.pool, self.objects = pool, objects
        self.policy = PostgresAuthorization() if policy is None else policy

    async def _access(self, conn: Connection, credential: SecretStr,
                      workspace_id: str, project_id: str, record_id: str | None = None) -> Authorization:
        auth = await self.policy.authorize(conn, credential.get_secret_value(), workspace_id, "read")
        if record_id is None:
            await self.policy.authorize_project(conn, auth, workspace_id, project_id, "read")
        else:
            await self.policy.authorize_record(conn, auth, workspace_id, project_id, record_id, "read")
        return auth

    async def list_projects(self, *, credential_digest: SecretStr, workspace_id: str,
                            after: str = "", limit: int = 50, search: str | None = None) -> tuple[tuple[str, int], ...]:
        _limit(limit)
        search = _search(search)
        async with self.pool.connection() as conn:
            auth = await self.policy.authorize(conn, credential_digest.get_secret_value(), workspace_id, "read")
            if await _indexed_profile(conn):
                base = ('SELECT p.project_id,p.revision FROM k05_projects p {join} WHERE p.workspace_id=%s '
                    'AND p.project_id>%s AND (%s::text IS NULL OR p.project_id=%s) '
                    'AND NOT EXISTS (SELECT 1 FROM k05_records d WHERE d.workspace_id=p.workspace_id '
                    "AND d.project_id=p.project_id AND d.record_type='deletion') {access} ORDER BY p.project_id LIMIT %s")
                common: tuple[object, ...] = (workspace_id, after, search, search)
                if auth.role in ('owner', 'admin'):
                    query, params = base.format(join='', access=''), common + (limit,)
                else:
                    owned = base.format(join='', access='AND p.creator_id=%s')
                    granted = base.format(join='JOIN j01_project_grants g ON g.workspace_id=p.workspace_id '
                        'AND g.project_id=p.project_id', access="AND g.user_id=%s AND 'read'=ANY(g.actions) AND p.creator_id<>%s")
                    query = 'SELECT project_id,revision FROM ((' + owned + ') UNION ALL (' + granted + ')) v ORDER BY project_id LIMIT %s'
                    params = common + (auth.user_id, limit) + common + (auth.user_id, auth.user_id, limit) + (limit,)
                return cast(tuple[tuple[str, int], ...], tuple(await _all(await conn.execute(query, params))))
            cursor = await conn.execute(
                "SELECT project_id,revision FROM k05_projects WHERE workspace_id=%s AND project_id>%s "
                "AND (%s::text IS NULL OR project_id=%s) "
                "AND (%s OR creator_id=%s OR EXISTS (SELECT 1 FROM j01_project_grants g "
                "WHERE g.workspace_id=k05_projects.workspace_id AND g.project_id=k05_projects.project_id "
                "AND g.user_id=%s AND 'read'=ANY(g.actions))) AND NOT EXISTS (SELECT 1 FROM k05_records d "
                "WHERE d.workspace_id=k05_projects.workspace_id AND d.project_id=k05_projects.project_id "
                "AND d.record_type='deletion') ORDER BY project_id LIMIT %s",
                (workspace_id, after, search, search, auth.role in ("owner", "admin"), auth.user_id, auth.user_id, limit),
            )
            return cast(tuple[tuple[str, int], ...], tuple(await _all(cursor)))

    async def check_records(self, *, credential_digest: SecretStr, workspace_id: str,
                            record_ids: tuple[str, ...], project_id: str | None = None) -> None:
        """Recheck the exact bounded page under current source and project policy."""
        from psycopg import sql

        if len(record_ids) > 100 or any(not isinstance(value, str) or not 1 <= len(value) <= 128
                                        for value in record_ids):
            raise ValueError("A bounded record page is required.")
        async with self.pool.connection() as conn:
            auth = await self.policy.authorize(conn, credential_digest.get_secret_value(), workspace_id, "read")
            if project_id is not None:
                await self.policy.authorize_project(conn, auth, workspace_id, project_id, "read")
            source_filter, source_params = record_source_filter(auth)
            row = await (await conn.execute(
                sql.SQL(
                    'SELECT count(*) FROM k05_records r JOIN k05_projects p ON p.workspace_id=r.workspace_id AND '
                    'p.project_id=r.project_id WHERE r.workspace_id=%s AND r.record_id=ANY(%s) AND (%s::text IS NULL OR '
                    'r.project_id=%s) AND (%s OR p.creator_id=%s OR EXISTS (SELECT 1 FROM j01_project_grants g WHERE '
                    "g.workspace_id=p.workspace_id AND g.project_id=p.project_id AND g.user_id=%s AND 'read'=ANY(g.actions))) "
                    'AND NOT EXISTS (SELECT 1 FROM k05_records d WHERE d.workspace_id=p.workspace_id AND '
                    "d.project_id=p.project_id AND d.record_type='deletion') AND {}"
                ).format(sql.SQL(source_filter)).as_string(),
                (workspace_id, list(record_ids), project_id, project_id,
                 auth.role in ("owner", "admin"), auth.user_id, auth.user_id) + source_params)).fetchone()
            if row != (len(record_ids),):
                raise AuthorizationDenied()

    async def list_records(self, *, credential_digest: SecretStr, workspace_id: str,
                           project_id: str, record_type: str, snapshot_id: str | None = None,
                           cursor: CatalogCursor | None = None, limit: int = 50,
                           search: str | None = None) -> RecordPage:
        _limit(limit)
        search = _search(search)
        if record_type not in KINDS or record_type == "preview":
            raise ValueError("confirmed record type required")
        async with self.pool.connection() as conn:
            auth = await self._access(conn, credential_digest, workspace_id, project_id)
            after = ""
            if cursor is not None:
                if not isinstance(cursor, CatalogCursor) or (
                    cursor.workspace_id, cursor.project_id, cursor.record_type, cursor.snapshot_id,
                    cursor.user_id, cursor.membership_revision, cursor.search,
                ) != (workspace_id, project_id, record_type, snapshot_id, auth.user_id, auth.revision, search):
                    raise ValueError("cursor does not match the current authorized query")
                after = cursor.after
            query = (
                "SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,"
                "r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id FROM k05_records r "
                "WHERE r.workspace_id=%s AND r.project_id=%s AND r.record_type=ANY(%s) AND r.record_id>%s "
            )
            params: tuple[object, ...] = (workspace_id, project_id, _record_types(record_type), after)
            if snapshot_id is not None:
                query += "AND r.snapshot_id=%s "
                params += (snapshot_id,)
            if search is not None:
                query += "AND (r.record_id=%s OR r.project_id=%s) "
                params += (search, search)
            source_filter, source_params = record_source_filter(auth)
            query += "AND " + source_filter + " "
            rows = await _all(await conn.execute(query + "ORDER BY r.record_id LIMIT %s", params + source_params + (limit + 1,)))
        records = tuple(_index(row) for row in rows[:limit])
        continuation = None
        if len(rows) > limit:
            continuation = CatalogCursor(workspace_id, project_id, record_type, snapshot_id,
                                         auth.user_id, auth.revision, records[-1].record_id, search)
        return RecordPage(records, continuation)

    async def list_workspace_records(self, *, credential_digest: SecretStr, workspace_id: str,
                                     kind: str = "result", after: str = "", limit: int = 50,
                                     search: str | None = None) -> RecordPage:
        """Internal keyset: the HTTP owner binds/signs after with actor and query."""
        _limit(limit)
        search = _search(search)
        if kind not in KINDS or kind == "preview" or not isinstance(after, str) or len(after) > 128:
            raise ValueError("bounded confirmed-record query required")
        async with self.pool.connection() as conn:
            auth = await self.policy.authorize(conn, credential_digest.get_secret_value(), workspace_id, "read")
            indexed = await _indexed_profile(conn)
            visible, visible_params = _visible_projects(auth, workspace_id) if indexed else ('', ())
            query = (
                "SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,"
                "r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id FROM k05_records r "
                "JOIN k05_projects p ON p.workspace_id=r.workspace_id AND p.project_id=r.project_id "
                + visible +
                "WHERE r.workspace_id=%s AND r.record_type=ANY(%s) AND r.record_id>%s "
                + ("" if indexed else "AND (%s OR p.creator_id=%s OR EXISTS (SELECT 1 FROM j01_project_grants g "
                "WHERE g.workspace_id=p.workspace_id AND g.project_id=p.project_id AND g.user_id=%s "
                "AND 'read'=ANY(g.actions))) ") + "AND NOT EXISTS (SELECT 1 FROM k05_records d "
                "WHERE d.workspace_id=p.workspace_id AND d.project_id=p.project_id AND d.record_type='deletion') "
            )
            params: tuple[object, ...] = visible_params + (workspace_id, _record_types(kind), after)
            if not indexed:
                params += (auth.role in ("owner", "admin"), auth.user_id, auth.user_id)
            if search is not None:
                query += "AND (r.record_id=%s OR r.project_id=%s) "
                params += (search, search)
            source_filter, source_params = record_source_filter(auth)
            query += "AND " + source_filter + " "
            rows = await _all(await conn.execute(query + "ORDER BY r.record_id LIMIT %s", params + source_params + (limit + 1,)))
        records = tuple(_index(row) for row in rows[:limit])
        continuation = None
        if len(rows) > limit:
            continuation = CatalogCursor(workspace_id, "", kind, None, auth.user_id, auth.revision,
                                         records[-1].record_id, search)
        return RecordPage(records, continuation)

    async def read_record(self, *, credential_digest: SecretStr, workspace_id: str,
                          project_id: str, record_id: str, maximum_bytes: int | None = None) -> CatalogModel:
        _maximum_bytes(maximum_bytes)
        async with self.pool.connection() as conn:
            await self._access(conn, credential_digest, workspace_id, project_id, record_id)
            row = await (await conn.execute(
                "SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,"
                "r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id,o.byte_size,o.media_type,o.schema_version "
                "FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 "
                "WHERE r.workspace_id=%s AND r.project_id=%s AND r.record_id=%s",
                (workspace_id, project_id, record_id),
            )).fetchone()
            if row is None:
                raise AuthorizationDenied()
            index = _index(row[:9])
            descriptor = ObjectDescriptor(index.object_sha256, cast(int, row[9]), cast(str, row[10]), cast(str | None, row[11]))
            if maximum_bytes is not None and descriptor.byte_size > maximum_bytes:
                raise CatalogReadTooLarge()
        payload = await self.objects.read(workspace_id, descriptor)
        model = await run_blocking(decode_record, index, payload)
        # Revalidate current policy after object I/O before returning private content.
        async with self.pool.connection() as conn:
            await self._access(conn, credential_digest, workspace_id, project_id, record_id)
            await _verify_projections(conn, workspace_id, index, model)
        return model

    async def current(self, *, credential_digest: SecretStr, workspace_id: str,
                      project_id: str, pointer_name: str | None = None, maximum_bytes: int | None = None) -> CatalogModel:
        _maximum_bytes(maximum_bytes)
        async with self.pool.connection() as conn:
            await self._access(conn, credential_digest, workspace_id, project_id)
            row = await (await conn.execute(
                "SELECT c.record_id FROM k05_current_pointers c JOIN k05_projects p "
                "ON p.workspace_id=c.workspace_id AND p.project_id=c.project_id "
                "WHERE c.workspace_id=%s AND c.project_id=%s AND c.pointer_name=coalesce(%s::text, "
                "CASE WHEN p.provenance_kind='upload' THEN 'snapshot' ELSE 'project_state' END)",
                (workspace_id, project_id, pointer_name),
            )).fetchone()
        if row is None:
            raise AuthorizationDenied()
        return await self.read_record(credential_digest=credential_digest, workspace_id=workspace_id,
                                      project_id=project_id, record_id=cast(str, row[0]), maximum_bytes=maximum_bytes)

@dataclass(frozen=True)
class CatalogScrubReport:
    examined: int
    bytes_checked: int
    corrupt_record_ids: tuple[str, ...]
    next_cursor: str | None


async def scrub_catalog(pool: Pool, objects: PostgresObjects, workspace_id: str, *,
                        limit: int = 100, after: str = "") -> CatalogScrubReport:
    """Operator-only background audit of canonical models and parent projections."""
    _limit(limit)
    async with pool.connection() as conn:
        rows = await _all(await conn.execute(
            "SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,"
            "r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id,o.byte_size,o.media_type,o.schema_version "
            "FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 "
            "WHERE r.workspace_id=%s AND r.record_id>%s ORDER BY r.record_id LIMIT %s",
            (workspace_id, after, limit),
        ))
    bad, checked = [], 0
    for row in rows:
        index = _index(row[:9])
        try:
            descriptor = ObjectDescriptor(index.object_sha256, cast(int, row[9]), cast(str, row[10]), cast(str | None, row[11]))
            payload = await objects.read(workspace_id, descriptor)
            checked += len(payload)
            model = await run_blocking(decode_record, index, payload)
            async with pool.connection() as conn:
                await _verify_projections(conn, workspace_id, index, model)
        except (CatalogIntegrityError, ObjectIntegrityError):
            bad.append(index.record_id)
    return CatalogScrubReport(len(rows), checked, tuple(bad), cast(str, rows[-1][0]) if len(rows) == limit else None)


async def _verify_projections(conn: Connection, workspace_id: str, index: RecordIndex, model: CatalogModel) -> None:
    if isinstance(model, (UploadSnapshot, UploadSnapshotV2, UploadResultRecord, UploadResultRecordV2, UploadGenerationComparison)) and model.workspace_id != workspace_id:
        raise CatalogIntegrityError()
    expected = canonical_parents(model)
    links = await _all(await conn.execute(
        "SELECT relation,parent_id FROM k05_record_parents "
        "WHERE workspace_id=%s AND record_id=%s ORDER BY relation,parent_id LIMIT %s",
        (workspace_id, index.record_id, len(expected) + 1),
    ))
    if tuple(links) != expected:
        raise CatalogIntegrityError()
    if isinstance(model, (UploadSnapshot, UploadSnapshotV2)):
        source = await (await conn.execute(
            "SELECT r.source_id,s.sha256 FROM k05_records r JOIN j01_sources s "
            "ON s.workspace_id=r.workspace_id AND s.source_id=r.source_id "
            "WHERE r.workspace_id=%s AND r.record_id=%s", (workspace_id, index.record_id),
        )).fetchone()
        if source != (model.source_id, model.source_sha256):
            raise CatalogIntegrityError()
    if isinstance(model, ProjectBundle):
        expected_artifacts = sorted({(item.artifact.sha256, item.artifact.byte_size, item.artifact.media_type)
                                     for item in model.items})
        artifacts = await _all(await conn.execute(
            "SELECT ref.sha256,o.byte_size,o.media_type FROM k05_object_references ref "
            "JOIN k05_objects o ON o.workspace_id=ref.workspace_id AND o.sha256=ref.sha256 "
            "WHERE ref.workspace_id=%s AND ref.record_id=%s ORDER BY ref.sha256 LIMIT %s",
            (workspace_id, index.record_id, len(expected_artifacts) + 1),
        ))
        if artifacts != expected_artifacts:
            raise CatalogIntegrityError()
def _decode_native_record(index: RecordIndex, payload: bytes) -> NativeUploadModel:
    native: NativeUploadModel = (parse_upload_snapshot(payload) if index.record_type == "upload_snapshot"
                                 else parse_upload_result_record(payload))
    kind, identity, project, schema, actual = native_metadata(native)
    snapshot = native.snapshot_id if isinstance(native, (UploadResultRecord, UploadResultRecordV2)) else None
    parent = native.parent_result_id if isinstance(native, (UploadResultRecord, UploadResultRecordV2)) else None
    source = native.source_id if isinstance(native, (UploadSnapshot, UploadSnapshotV2)) else None
    if (kind, identity, project, schema, actual, snapshot, parent, source) != (
        index.record_type, index.record_id, index.project_id, index.schema_version,
        index.canonical_sha256, index.snapshot_id, index.parent_result_id, index.source_id,
    ):
        raise CatalogIntegrityError()
    return native

