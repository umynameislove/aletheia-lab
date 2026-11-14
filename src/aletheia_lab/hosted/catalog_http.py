"""Scoped canonical catalog/review HTTP and bounded, revalidated export downloads."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeVar, cast
from urllib.parse import parse_qsl
from uuid import uuid4

from pydantic import BaseModel, SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE, read_opaque_cookie
from aletheia_lab.hosted_auth.csrf import CsrfMiddleware, CsrfTokens, SessionBindingLoader
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.deletions import DeletionStatus
from aletheia_lab.hosted_contract.workspaces import (
    Annotation,
    AnnotationPage,
    CanonicalRecord,
    ChangeGrant,
    ClaimPage,
    DeletedProject,
    Empty,
    EvidencePage,
    EvidenceView,
    ExportDescriptor,
    ExportRequest,
    GraphView,
    ProjectGrant,
    ProjectPage,
    PutAnnotation,
    RecordPage,
    ResultHeader,
    ResultView,
    ResultViewV2,
)
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.view import (
    AnyProductView,
    ProductGraph,
    ProductGraphEdge,
    ProductViewV2,
    parse_product_view,
)
from aletheia_lab.project.identity import content_hasher
from aletheia_lab.project.persistence import _model_identity

from .action_audit import report_outcome, request_context
from .application import _ACCESS_ERRORS, VERSION, AccessDenied, Send, _cookie, _problem, _Response
from .async_work import run_blocking
from .authorization import AuthorizationDenied
from .catalog_access import CatalogAccess
from .cursors import CursorExpired, CursorInvalid
from .durable_exports import EXPORT_HANDLE, StoredExport
from .edge import EdgeGuard
from .native_upload_records import (
    UploadResultRecord,
    UploadResultRecordV2,
    native_metadata,
    parse_upload_result_record,
)
from .postgres_catalog import KINDS, CatalogReadTooLarge
from .project_access import PostgresProjectAccess
from .project_mutations import PostgresProjectMutations
from .read_backend import SessionRecord, SessionRepository
from .read_indexes import ReadIndexesRejected
from .request_boundary import (
    BodyLimits,
    Receive,
    RequestRejected,
    _collect_body,
    decode_json_model,
    read_json_body,
)
from .result_views import ProjectionUnavailable, _search
from .retention import PostgresRetention
from .review_repository import PostgresReviews
from .settings import RuntimeSettings
from .upload_comparison import UploadGenerationComparison, comparison_metadata
from .upload_snapshot import UploadSnapshot, UploadSnapshotV2, parse_upload_snapshot
from .worker_limits import MAX_OUTPUT_BYTES
from .workspace_http import WorkspaceApplication
from .workspace_management import WorkspaceConflict, WorkspaceDenied

_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_PREFIX = r"/v1/workspaces/(?P<workspace>" + _ID + r")"
_PROJECT = re.compile(_PREFIX + r"/projects(?:/(?P<project>" + _ID + r")(?:(?P<group>/snapshots|/records)(?:/(?P<record>" + _ID + r"))?|/grants/(?P<user>" + _ID + r"))?)?")
_RESULT = re.compile(_PREFIX + r"/results(?:/(?P<result>" + _ID + r")(?P<tail>/header|/claims|/graph|/exports|/evidence(?:/" + _ID + r")?|/annotations/" + _ID + r"(?:/history)?)?)?")
_EVENTS = re.compile(_PREFIX + r"/events")
_DOWNLOAD = re.compile(_PREFIX + r"/exports/(?P<export>" + _ID + r")/download")
_DELETION = re.compile(_PREFIX + r"/projects/(?P<project>" + _ID + r")/deletion-status")
_EXPORT = EXPORT_HANDLE
_CHUNK_BYTES = 64 * 1024
Model = TypeVar("Model", bound=BaseModel)
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]
Checkpoint = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class _Delivery:
    response: _Response
    checkpoint: Checkpoint | None = None
    headers: tuple[tuple[bytes, bytes], ...] = ()
    streaming: bool = False


class _RangeRejected(RequestRejected):
    def __init__(self, size: int) -> None:
        super().__init__(416, "range_not_satisfiable", "The requested range is unavailable.")
        self.size = size


def _query(scope: Mapping[str, object], allowed: set[str]) -> dict[str, str]:
    raw = scope.get("query_string", b"")
    if not isinstance(raw, bytes) or len(raw) > 8192:
        raise RequestRejected(400, "invalid_request", "Query parameters are invalid.")
    try:
        text = raw.decode("ascii")
        if re.search(r"%(?![0-9A-Fa-f]{2})", text):
            raise ValueError()
        pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True, max_num_fields=8, errors="strict")
        result: dict[str, str] = {}
        for key, value in pairs:
            if key not in allowed or key in result or not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError()
            result[key] = value
        return result
    except (ValueError, UnicodeError):
        raise RequestRejected(400, "invalid_request", "Query parameters are invalid.") from None


def _limit(query: dict[str, str]) -> int:
    value = query.get("limit", "50")
    if re.fullmatch(r"[1-9][0-9]{0,2}", value) is None or int(value) > 100:
        raise RequestRejected(400, "invalid_request", "The page limit is invalid.")
    return int(value)


def _identifier(query: dict[str, str], field: str, *, required: bool = False) -> str | None:
    value = query.get(field)
    if (required and value is None) or (value is not None and re.fullmatch(_ID, value) is None):
        raise RequestRejected(400, "invalid_request", "A valid scoped identifier is required.")
    return value


def _byte_range(headers: Sequence[tuple[bytes, bytes]], size: int) -> tuple[int, int, bool]:
    values = [value for name, value in headers if name.lower() == b"range"]
    if not values:
        return 0, size, False
    match = re.fullmatch(rb"bytes=([0-9]{0,20})-([0-9]{0,20})", values[0]) if len(values) == 1 else None
    if match is None or size == 0 or not any(match.groups()):
        raise _RangeRejected(size)
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix == 0:
            raise _RangeRejected(size)
        return max(0, size - suffix), size, True
    begin, end = int(first), int(last) + 1 if last else size
    if begin >= size or end <= begin:
        raise _RangeRejected(size)
    return begin, min(size, end), True


def _edge_page(source: ProductGraph, after: str, limit: int) -> tuple[ProductGraph, dict[str, object] | None]:
    edges = sorted(source.edges, key=lambda item: item.id)
    if after and after not in {edge.id for edge in edges}:
        raise CursorInvalid()
    remaining = [edge for edge in edges if not after or edge.id > after]
    if not remaining:
        raise CursorInvalid()
    selected: list[ProductGraphEdge] = []
    context: set[str] = set()
    for edge in remaining:
        endpoints = {edge.source, edge.target}
        if len(selected) == 1000 or len(context | endpoints) > max(2, limit):
            break
        selected.append(edge)
        context.update(endpoints)
    nodes = tuple(node for node in sorted(source.nodes, key=lambda item: item.id) if node.id in context)
    continuation: dict[str, object] | None = (
        {"phase": "edges", "after": selected[-1].id} if len(selected) < len(remaining) else None)
    return ProductGraph(nodes=nodes, edges=tuple(selected)), continuation


def _graph_page(source: ProductGraph, phase: str, after: str, limit: int) -> tuple[ProductGraph, dict[str, object] | None]:
    if type(limit) is not int or not 1 <= limit <= 100 or phase not in ("nodes", "edges"):
        raise CursorInvalid()
    if phase == "edges":
        return _edge_page(source, after, limit)
    nodes = sorted(source.nodes, key=lambda item: item.id)
    if after and after not in {node.id for node in nodes}:
        raise CursorInvalid()
    if not after and len(nodes) <= limit and len(source.edges) <= 1000:
        return ProductGraph(nodes=tuple(nodes), edges=tuple(sorted(source.edges, key=lambda item: item.id))), None
    remaining = [node for node in nodes if not after or node.id > after]
    if not remaining:
        raise CursorInvalid()
    selected = tuple(remaining[:limit])
    continuation: dict[str, object] | None = None
    if len(remaining) > limit:
        continuation = {"phase": "nodes", "after": selected[-1].id}
    elif source.edges:
        continuation = {"phase": "edges", "after": ""}
    return ProductGraph(nodes=selected, edges=()), continuation


class CatalogApplication:
    def __init__(self, fallback: Application, access: CatalogAccess, project_access: PostgresProjectAccess,
                 reviews: PostgresReviews, sessions: SessionRepository, tokens: CsrfTokens,
                 runtime: RuntimeSettings, *, body_limits: BodyLimits,
                 clock_check: Callable[[], Awaitable[bool]],
                 project_mutations: PostgresProjectMutations | None = None,
                 retention: PostgresRetention | None = None) -> None:
        self._fallback, self._access, self._projects, self._reviews = fallback, access, project_access, reviews
        self._sessions, self._runtime, self._limits, self._clock = sessions, runtime, body_limits, clock_check
        self._mutations = project_mutations
        self._retention = retention
        # Bound retained 32 MiB artifact buffers independently of metadata reads.
        self._download_slots = asyncio.BoundedSemaphore(4)
        self._guard = EdgeGuard(runtime.public_origin, CsrfMiddleware(
            self._route, tokens, SessionBindingLoader(sessions), runtime,
            lookup_timeout_seconds=runtime.api.backend_timeout_seconds))

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not any(pattern.fullmatch(path) for pattern in (_PROJECT, _RESULT, _EVENTS, _DOWNLOAD, _DELETION)):
            await self._fallback(scope, receive, send)
            return
        await self._guard(scope, receive, send)

    async def _route(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        request_id = "req_" + uuid4().hex
        with request_context(request_id):
            if not _DOWNLOAD.fullmatch(str(scope.get("path", ""))):
                await self._route_with_context(scope, receive, send, request_id)
                return
            if self._download_slots.locked():
                output = _Delivery(_problem(503, "service_unavailable", "Downloads are busy. Try again shortly.", request_id))
                async with asyncio.timeout(min(1.0, self._runtime.api.backend_timeout_seconds)):
                    await self._deliver(output, scope, send, request_id)
                return
            await self._download_slots.acquire()
            try:
                await self._route_with_context(scope, receive, send, request_id)
            finally:
                self._download_slots.release()

    async def _route_with_context(self, scope: Mapping[str, object], receive: Receive, send: Send,
                                  request_id: str) -> None:
        started = False

        async def timed_send(message: Mapping[str, object]) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            async with asyncio.timeout(min(1.0, self._runtime.api.backend_timeout_seconds)):
                await send(message)

        try:
            async with asyncio.timeout(self._runtime.api.request_timeout_seconds):
                output = await self._resolve(scope, receive, request_id)
                await self._deliver(output, scope, timed_send, request_id)
        except Exception:
            # Once headers started, abort the partial response; a second status would lie about completion.
            if started:
                raise
            response = _problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id)
            await timed_send({"type": "http.response.start", "status": response.status,
                "headers": [(b"content-type", response.content_type), (b"cache-control", b"no-store"),
                            (b"x-content-type-options", b"nosniff"), (b"content-length", str(len(response.body)).encode())]})
            await timed_send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else response.body})

    def _cursor_error(self, error: CursorExpired | CursorInvalid, request_id: str) -> _Delivery:
        expired = isinstance(error, CursorExpired)
        output = _Delivery(_problem(410 if expired else 400, "cursor_expired" if expired else "cursor_invalid",
            "The continuation has expired." if expired else "The continuation is invalid for this request.", request_id))
        return output

    def _authority_error(self, error: Exception, request_id: str) -> _Delivery:
        if isinstance(error, AuthorizationDenied):
            return _Delivery(_problem(404, "not_found", "The requested resource is unavailable.", request_id))
        if isinstance(error, WorkspaceDenied):
            return _Delivery(_problem(403, "permission_denied", "This operation is not permitted.", request_id))
        return _Delivery(_problem(412, "revision_conflict", "The resource changed. Refresh before trying again.", request_id))

    async def _resolve(self, scope: Mapping[str, object], receive: Receive, request_id: str) -> _Delivery:
        try:
            async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
                output = await self._dispatch(scope, receive)
                await self._checkpoint(output.checkpoint)
        except _RangeRejected as error:
            output = _Delivery(_problem(error.status, error.code, error.safe_message, request_id),
                headers=((b"content-range", f"bytes */{error.size}".encode()),))
        except RequestRejected as error:
            output = _Delivery(_problem(error.status, error.code, error.safe_message, request_id,
                allow=self._allow(str(scope.get("path", ""))) if error.status == 405 else None))
        except (AuthorizationDenied, WorkspaceDenied, WorkspaceConflict) as error:
            output = self._authority_error(error, request_id)
        except (CursorExpired, CursorInvalid) as error:
            output = self._cursor_error(error, request_id)
        except AccessDenied as error:
            status, detail = _ACCESS_ERRORS[error.code]
            output = _Delivery(_problem(status, error.code, detail, request_id))
        except TimeoutError:
            output = _Delivery(_problem(503, "service_unavailable", "The service is temporarily unavailable.", request_id))
        except CatalogReadTooLarge:
            output = _Delivery(_problem(413, 'response_too_large',
                'The canonical object exceeds the delivery limit. Use bounded read pages or operator inspection.', request_id))
        except (ProjectionUnavailable, ReadIndexesRejected):
            output = _Delivery(_problem(503, "service_unavailable", "The bounded read index is not ready. Retry later.", request_id))
        except Exception:
            output = _Delivery(_problem(500, "service_failure", "The service could not complete the request.", request_id))
        return output

    async def _deliver(self, output: _Delivery, scope: Mapping[str, object], timed_send: Send,
                       request_id: str) -> None:
        response = output.response
        if 400 <= response.status < 500:
            report_outcome("rejected")
        headers = [(b"content-type", response.content_type), (b"content-length", str(len(response.body)).encode()),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
            (b"x-request-id", request_id.encode()), *output.headers]
        if response.allow is not None:
            headers.append((b"allow", response.allow))
        await timed_send({"type": "http.response.start", "status": response.status, "headers": headers})
        if scope.get("method") == "HEAD":
            await timed_send({"type": "http.response.body", "body": b""})
        elif output.streaming:
            for offset in range(0, len(response.body), _CHUNK_BYTES):
                await self._checkpoint(output.checkpoint)
                chunk = response.body[offset:offset + _CHUNK_BYTES]
                await timed_send({"type": "http.response.body", "body": chunk,
                                  "more_body": offset + len(chunk) < len(response.body)})
            if not response.body:
                await timed_send({"type": "http.response.body", "body": b""})
        else:
            await timed_send({"type": "http.response.body", "body": response.body})

    async def _checkpoint(self, checkpoint: Checkpoint | None) -> None:
        async with asyncio.timeout(self._runtime.api.backend_timeout_seconds):
            if await self._clock() is not True:
                raise TimeoutError()
            if checkpoint is not None:
                await checkpoint()

    @staticmethod
    def _allow(path: str) -> bytes:
        project, result = _PROJECT.fullmatch(path), _RESULT.fullmatch(path)
        if project and project["user"]:
            return b"GET, HEAD, PUT"
        if project and project["project"] and project["group"] is None:
            return b"GET, HEAD, DELETE"
        if result and result["tail"]:
            if result["tail"] == "/exports":
                return b"POST"
            if result["tail"].startswith("/annotations/") and not result["tail"].endswith("/history"):
                return b"GET, HEAD, PUT"
        return b"GET, HEAD"

    def _json(self, model: type[Model], value: object, *, checkpoint: Checkpoint | None = None, status: int = 200) -> _Delivery:
        parsed = model.model_validate(value)
        body = parsed.model_dump_json().encode("utf-8")
        if len(body) > self._runtime.api.maximum_response_bytes:
            raise RequestRejected(413, 'response_too_large',
                'This record exceeds the delivery limit. Use bounded read pages or operator inspection.')
        revision = getattr(parsed, "revision", None)
        headers = ((b"etag", f'"{revision}"'.encode()),) if type(revision) is int else ()
        return _Delivery(_Response(status, body), checkpoint, headers)

    async def _session(self, scope: Mapping[str, object]) -> tuple[SecretStr, SessionRecord]:
        cookie = _cookie(scope, self._runtime.api.maximum_cookie_bytes)
        opaque = read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
        if cookie is None or opaque is None:
            raise AccessDenied("session_expired")
        session = await self._sessions.resolve_cookie(cookie)
        if session is None:
            raise AccessDenied("session_expired")
        return credential_digest(opaque), session

    async def _result(self, digest: SecretStr, workspace: str, result: str, *,
                      maximum_bytes: int | None = None) -> tuple[str, AnyProductView]:
        options = {} if maximum_bytes is None else {'maximum_bytes': maximum_bytes}
        project, view = await self._access.result(digest, workspace, result, **options)
        view = await run_blocking(lambda: parse_product_view(view.model_dump(mode="python")))
        if view.project.id != project or view.result.id != result:
            raise ValueError("Catalog result parent mismatch.")
        return project, view

    async def _binding(self, digest: SecretStr, workspace: str, project: str, session: SessionRecord,
                       query: dict[str, object]) -> dict[str, object]:
        auth = await self._access.authorize(digest, workspace, project, "read")
        if auth.user_id != session.actor.user_id or auth.workspace_id != workspace:
            raise AuthorizationDenied()
        return {"workspace_id": workspace, "user_id": auth.user_id, "context_id": session.context_id,
                "membership_revision": auth.revision, "query": query}

    async def _record(self, digest: SecretStr, workspace: str, project: str,
                      record: str | None, kind: str | None = None) -> _Delivery:
        model = await (self._access.catalog.current(credential_digest=digest, workspace_id=workspace, project_id=project,
                                                  maximum_bytes=self._runtime.api.maximum_response_bytes)
            if record is None else self._access.catalog.read_record(credential_digest=digest, workspace_id=workspace,
                project_id=project, record_id=record, maximum_bytes=self._runtime.api.maximum_response_bytes))
        actual_kind: str
        parent: str | None
        if isinstance(model, (UploadSnapshot, UploadSnapshotV2, UploadResultRecord, UploadResultRecordV2)):
            native = (parse_upload_snapshot(model.model_dump(mode="python")) if isinstance(model, (UploadSnapshot, UploadSnapshotV2))
                      else parse_upload_result_record(model.model_dump(mode="python")))
            if native.workspace_id != workspace:
                raise ValueError("Native record workspace scope mismatch.")
            actual_kind, identity, parent, _, _ = native_metadata(native)
            model = native
        elif isinstance(model, UploadGenerationComparison):
            model = UploadGenerationComparison.model_validate(model.model_dump(mode='python'))
            if model.workspace_id != workspace:
                raise ValueError('Comparison workspace scope mismatch.')
            actual_kind, identity, parent, _, _ = comparison_metadata(model)
        elif isinstance(model, ProductLifecycleRecord):
            actual_kind, identity, parent = model.record_kind, model.record_id, model.project_id
        else:
            actual_kind, identity, parent, _ = _model_identity(model)
        if parent != project or (record is not None and identity != record):
            raise ValueError("Canonical record parent mismatch.")
        if kind is not None and actual_kind != kind and not (kind == "snapshot" and actual_kind == "upload_snapshot"):
            raise AuthorizationDenied()
        return self._json(CanonicalRecord, {"schema_version": VERSION, "workspace_id": workspace,
            "project_id": project, "record_id": identity, "record_kind": actual_kind,
            "data": model.model_dump(mode="json")},
            checkpoint=self._access.checkpoint(digest, workspace, project, record_id=identity))

    async def _deletion_status(self, scope: Mapping[str, object], digest: SecretStr,
                               workspace: str, deletion: re.Match[str]) -> _Delivery:
        _query(scope, set())
        retention = self._retention
        if retention is None:
            raise TimeoutError()
        async def check_deletion() -> None:
            await retention.status(credential_digest=digest, workspace_id=workspace,
                                         project_id=deletion["project"])
        value = await retention.status(credential_digest=digest, workspace_id=workspace,
                                             project_id=deletion["project"])
        if value.workspace_id != workspace or value.project_id != deletion["project"]:
            raise ValueError("Deletion status scope mismatch.")
        return self._json(DeletionStatus, value, checkpoint=check_deletion)

    async def _dispatch(self, scope: Mapping[str, object], receive: Receive) -> _Delivery:
        path, method = str(scope.get("path", "")), str(scope.get("method", ""))
        if method.encode() not in self._allow(path).split(b", "):
            raise RequestRejected(405, "method_not_allowed", "This method is not supported.")
        if await self._clock() is not True:
            raise TimeoutError()
        digest, session = await self._session(scope)
        headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        project_match, result_match = _PROJECT.fullmatch(path), _RESULT.fullmatch(path)
        download, events, deletion = _DOWNLOAD.fullmatch(path), _EVENTS.fullmatch(path), _DELETION.fullmatch(path)
        match = project_match or result_match or download or events or deletion
        if match is None:
            raise AuthorizationDenied()
        workspace = match["workspace"]
        if deletion:
            return await self._deletion_status(scope, digest, workspace, deletion)
        if download:
            _query(scope, set())
            return await self._download(digest, workspace, download["export"], headers)
        if project_match:
            return await self._project_dispatch(scope, receive, digest, session, workspace, project_match, headers)
        if events or (result_match and result_match["result"] is None):
            return await self._result_page(scope, digest, session, workspace, events is not None)
        if result_match is None:
            raise AuthorizationDenied()
        return await self._result_dispatch(scope, receive, digest, session, workspace, result_match, headers)

    async def _project_dispatch(self, scope: Mapping[str, object], receive: Receive, digest: SecretStr,
                                session: SessionRecord, workspace: str, match: re.Match[str],
                                headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        method = str(scope.get("method", ""))
        project, record, group, user = (match[name] for name in ("project", "record", "group", "user"))
        if user:
            _query(scope, set())
            return await self._grant(scope, receive, digest, workspace, cast(str, project), user, headers)
        if project is None:
            query = _query(scope, {"limit", "cursor", "search"})
            value = await self._access.projects(digest, workspace, session.context_id,
                limit=_limit(query), cursor=query.get("cursor"), search=_identifier(query, "search"))
            if value.get("workspace_id") != workspace:
                raise ValueError("Project page scope mismatch.")
            return self._json(ProjectPage, value)
        if method == "DELETE":
            _query(scope, set())
            return await self._delete(scope, receive, digest, workspace, project, headers)
        if record is not None or group is None:
            _query(scope, set())
            return await self._record(digest, workspace, project, record, "snapshot" if group == "/snapshots" else None)
        query = _query(scope, {"limit", "cursor", "kind", "snapshot_id", "search"})
        kind = "snapshot" if group == "/snapshots" else query.get("kind")
        if kind not in KINDS or kind == "preview" or (group == "/snapshots" and "kind" in query):
            raise RequestRejected(400, "invalid_request", "A confirmed record kind is required.")
        value = await self._access.records(digest, workspace, project, session.context_id, kind=kind,
            snapshot_id=_identifier(query, "snapshot_id"), limit=_limit(query), cursor=query.get("cursor"),
            search=_identifier(query, "search"))
        return self._record_page(value, digest, workspace, project)

    async def _result_page(self, scope: Mapping[str, object], digest: SecretStr, session: SessionRecord,
                           workspace: str, events: bool) -> _Delivery:
        query = _query(scope, {"project_id", "snapshot_id", "limit", "cursor", "search"})
        project = _identifier(query, "project_id")
        if project is None:
            if "snapshot_id" in query:
                raise RequestRejected(400, "invalid_request", "Snapshot filters require a project scope.")
            value = await self._access.workspace_records(digest, workspace, session.context_id,
                kind="regression_event" if events else "result", limit=_limit(query),
                cursor=query.get("cursor"), search=_identifier(query, "search"))
            return self._record_page(value, digest, workspace, None)
        value = await self._access.records(digest, workspace, project, session.context_id,
            kind="regression_event" if events else "result", snapshot_id=_identifier(query, "snapshot_id"),
            limit=_limit(query), cursor=query.get("cursor"), search=_identifier(query, "search"))
        return self._record_page(value, digest, workspace, project)

    async def _result_dispatch(self, scope: Mapping[str, object], receive: Receive, digest: SecretStr,
                               session: SessionRecord, workspace: str, result_match: re.Match[str],
                               headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        result, tail = result_match["result"], result_match["tail"]
        if tail == "/exports":
            _query(scope, set())
            raw = await read_json_body(receive, headers, self._limits)
            request = decode_json_model(raw, ExportRequest, maximum_bytes=self._limits.maximum_bytes,
                                        maximum_depth=self._runtime.api.maximum_json_depth)
            item = await self._export(digest, workspace, result, request.format, headers)
            return self._json(ExportDescriptor, {"schema_version": VERSION, "workspace_id": workspace,
                "project_id": item.project_id, "result_id": result, "format": item.format, "export_id": item.export_id,
                "byte_size": item.descriptor.byte_size, "sha256": item.descriptor.sha256,
                "download_url": f"/v1/workspaces/{workspace}/exports/{item.export_id}/download",
                "expires_at": item.expires_at}, status=201,
                checkpoint=self._access.export_checkpoint(digest, item))
        if tail and tail.startswith("/annotations/"):
            return await self._annotation(scope, receive, digest, session, workspace, result, tail.split("/")[2], headers)
        if tail in ('/header', '/claims', '/evidence', '/graph') or tail and tail.startswith('/evidence/'):
            return await self._projected_result(scope, digest, session, workspace, result, tail)
        project, view = await self._result(digest, workspace, result, maximum_bytes=self._runtime.api.maximum_response_bytes)
        check = self._access.checkpoint(digest, workspace, project, record_id=result)
        if tail is None:
            _query(scope, set())
            return self._json(ResultViewV2 if isinstance(view, ProductViewV2) else ResultView,
                {"schema_version": VERSION, "workspace_id": workspace,
                "project_id": project, "result_id": result, "view": view}, checkpoint=check)
        raise RequestRejected(404, 'not_found', 'The requested resource is unavailable.')

    async def _projected_result(self, scope: Mapping[str, object], digest: SecretStr, session: SessionRecord,
                                workspace: str, result: str, tail: str) -> _Delivery:
        if tail == '/header':
            _query(scope, set())
            value = await self._access.result_header(digest, workspace, result)
            model: type[BaseModel] = ResultHeader
        elif tail == '/graph':
            query = _query(scope, {'limit', 'cursor'})
            value = await self._access.result_graph(digest, workspace, result, session.context_id,
                                                    limit=_limit(query), cursor=query.get('cursor'))
            model = GraphView
        elif tail in ('/claims', '/evidence'):
            query = _query(scope, {'limit', 'cursor', 'search'})
            try:
                search = _search(query.get('search'))
            except ValueError:
                raise RequestRejected(400, 'invalid_request', 'A bounded search query is required.') from None
            value = await self._access.result_items(digest, workspace, result, session.context_id,
                kind='claim' if tail == '/claims' else 'evidence', limit=_limit(query),
                cursor=query.get('cursor'), search=search)
            model = ClaimPage if tail == '/claims' else EvidencePage
        else:
            query = _query(scope, {'claim_id'})
            value = await self._access.result_evidence(digest, workspace, result, tail.split('/')[2],
                                                        _identifier(query, 'claim_id'))
            model = EvidenceView
        project = value.get('project_id')
        if not isinstance(project, str) or value.get('workspace_id') != workspace or value.get('result_id') != result:
            raise ValueError('Result projection scope mismatch.')
        return self._json(model, value, checkpoint=self._access.checkpoint(
            digest, workspace, project, record_id=result))

    def _record_page(self, value: dict[str, object], digest: SecretStr, workspace: str, project: str | None) -> _Delivery:
        if value.get("workspace_id") != workspace:
            raise ValueError("Record page scope mismatch.")
        page = RecordPage.model_validate({**value, "project_id": project})
        return self._json(RecordPage, page, checkpoint=self._access.records_checkpoint(
            digest, workspace, tuple(item.record_id for item in page.items), project=project))

    async def _delete(self, scope: Mapping[str, object], receive: Receive, digest: SecretStr, workspace: str,
                      project: str, headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        revision = WorkspaceApplication._revision(headers)
        if any(name.lower() == b"content-type" for name, _ in headers):
            raw = await read_json_body(receive, headers, self._limits)
        else:
            observed_body = False

            async def observed_receive() -> Mapping[str, object]:
                nonlocal observed_body
                event = await receive()
                observed_body |= isinstance(event.get("body"), bytes) and bool(event.get("body"))
                return event

            try:
                raw = await _collect_body(observed_receive, headers, self._limits)
            except RequestRejected as error:
                if observed_body and error.code == "invalid_body_length":
                    raise RequestRejected(415, "unsupported_media_type", "A JSON body requires its media type.") from None
                raise
            if raw:
                raise RequestRejected(415, "unsupported_media_type", "A JSON body requires its media type.")
        if raw:
            decode_json_model(raw, Empty, maximum_bytes=self._limits.maximum_bytes,
                              maximum_depth=self._runtime.api.maximum_json_depth)
        if self._mutations is None:
            raise TimeoutError()
        value = DeletedProject.model_validate(await self._mutations.delete(credential_digest=digest,
            workspace_id=workspace, project_id=project, expected_revision=revision))
        if (value.workspace_id, value.project_id, value.revision) != (workspace, project, revision + 1):
            raise ValueError("Deletion receipt scope mismatch.")
        return self._json(DeletedProject, value)

    def _continuation(self, cursor: str | None, purpose: str, binding: dict[str, object], kind: type) -> object:
        if cursor is None:
            return "" if kind is str else 0
        value = self._access.cursors.read(purpose=purpose, binding=binding, cursor=cursor)
        if set(value) != {"after"} or type(value["after"]) is not kind:
            raise CursorInvalid()
        if kind is int and not 0 <= cast(int, value["after"]) <= 9223372036854775806:
            raise CursorInvalid()
        return value["after"]

    async def _grant(self, scope: Mapping[str, object], receive: Receive, digest: SecretStr, workspace: str,
                     project: str, user: str, headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        if scope.get("method") == "PUT":
            revision = WorkspaceApplication._revision(headers)
            raw = await read_json_body(receive, headers, self._limits)
            change = decode_json_model(raw, ChangeGrant, maximum_bytes=self._limits.maximum_bytes,
                                       maximum_depth=self._runtime.api.maximum_json_depth)
            value = await self._projects.set_grant(credential_digest=digest, workspace_id=workspace,
                project_id=project, user_id=user, actions=list(change.actions), expected_revision=revision)
        else:
            value = await self._projects.read_grant(credential_digest=digest, workspace_id=workspace,
                                                  project_id=project, user_id=user)
        checked = ProjectGrant.model_validate(value)
        if (checked.workspace_id, checked.project_id, checked.user_id) != (workspace, project, user):
            raise ValueError("Project grant response scope mismatch.")
        return self._json(ProjectGrant, checked)

    async def _annotation(self, scope: Mapping[str, object], receive: Receive, digest: SecretStr, session: SessionRecord,
                          workspace: str, result: str, annotation: str, headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        project, _ = await self._result(digest, workspace, result)
        check = self._access.checkpoint(digest, workspace, project, record_id=result)
        if scope.get("method") == "PUT":
            _query(scope, set())
            revision = WorkspaceApplication._revision(headers)
            raw = await read_json_body(receive, headers, self._limits)
            request = decode_json_model(raw, PutAnnotation, maximum_bytes=self._limits.maximum_bytes,
                                        maximum_depth=self._runtime.api.maximum_json_depth)
            value = Annotation.model_validate(await self._reviews.put(credential_digest=digest, workspace_id=workspace,
                result_id=result, annotation_id=annotation, body=request.body,
                status=request.status, expected_revision=revision))
            if (value.workspace_id, value.project_id, value.result_id, value.annotation_id, value.author_id) != (
                    workspace, project, result, annotation, session.actor.user_id):
                raise ValueError("Annotation response scope mismatch.")
            return self._json(Annotation, value, checkpoint=check)
        query = _query(scope, {"limit", "cursor"})
        limit = _limit(query)
        binding = await self._binding(digest, workspace, project, session,
                                      {"result_id": result, "annotation_id": annotation, "limit": limit})
        after = cast(int, self._continuation(query.get("cursor"), "annotation_history", binding, int))
        history = await self._reviews.list_history(credential_digest=digest, workspace_id=workspace,
            result_id=result, annotation_id=annotation, limit=limit, after_revision=after)
        if (history.get("workspace_id"), history.get("project_id"), history.get("result_id"), history.get("annotation_id")) != (
                workspace, project, result, annotation):
            raise ValueError("Annotation history response scope mismatch.")
        next_after = history.pop("next_after_revision")
        if next_after is not None and (type(next_after) is not int or next_after <= after):
            raise ValueError("Annotation continuation is invalid.")
        cursor = None if next_after is None else self._access.cursors.issue(
            purpose="annotation_history", binding=binding, value={"after": next_after})
        return self._json(AnnotationPage, {**history, "next_cursor": cursor, "total": None, "total_kind": "unknown"}, checkpoint=check)

    async def _export(self, digest: SecretStr, workspace: str, result: str,
                      report_format: Literal["json", "markdown", "pdf"],
                      headers: Sequence[tuple[bytes, bytes]]) -> StoredExport:
        if re.fullmatch(r"p6-result-[0-9a-f]{64}", result) is None:
            raise AuthorizationDenied()
        keys = [value for name, value in headers if name.lower() == b"idempotency-key"]
        if keys and (len(keys) != 1 or re.fullmatch(rb"[A-Za-z0-9_-]{16,128}", keys[0]) is None):
            raise RequestRejected(400, "invalid_request", "A valid request key is required.")
        item = await self._access.create_export(digest, workspace, result, report_format,
            key_hash=content_hasher(keys[0]).hexdigest() if keys else None)
        self._export_scope(item, workspace, item.export_id)
        if item.result_id != result or item.format != report_format:
            raise ValueError("Export response scope failed validation.")
        return item

    @staticmethod
    def _export_scope(item: StoredExport, workspace: str, export_id: str) -> None:
        match = _EXPORT.fullmatch(export_id)
        if (match is None or item.workspace_id != workspace or item.export_id != export_id
                or (item.format, item.result_id) != (match[1], match[2])
                or item.descriptor.byte_size > MAX_OUTPUT_BYTES):
            raise ValueError("Export response scope failed validation.")

    async def _download(self, digest: SecretStr, workspace: str, export_id: str,
                        headers: Sequence[tuple[bytes, bytes]]) -> _Delivery:
        if _EXPORT.fullmatch(export_id) is None:
            raise AuthorizationDenied()
        item, payload = await self._access.download_export(digest, workspace, export_id)
        self._export_scope(item, workspace, export_id)
        if (not isinstance(payload, bytes) or len(payload) != item.descriptor.byte_size
                or content_hasher(payload).hexdigest() != item.descriptor.sha256):
            raise ValueError("Export bytes failed validation.")
        # Authorization and expiry precede Range parsing and every emitted chunk.
        check = self._access.export_checkpoint(digest, item)
        await self._checkpoint(check)
        begin, end, partial = _byte_range(headers, len(payload))
        sha = item.descriptor.sha256
        extension = {"json": "json", "markdown": "md", "pdf": "pdf"}[item.format]
        extra = [(b"etag", ('"' + sha + '"').encode()), (b"accept-ranges", b"bytes"),
                 (b"content-disposition", f'attachment; filename="aletheia-{sha[:16]}.{extension}"'.encode())]
        if partial:
            extra.append((b"content-range", f"bytes {begin}-{end - 1}/{len(payload)}".encode()))
        media = item.descriptor.media_type.encode()
        return _Delivery(_Response(206 if partial else 200, payload[begin:end], media), check, tuple(extra), True)
