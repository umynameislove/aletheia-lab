"""Worker-only authorization across rotation within one original session context.

Never inject this adapter into HTTP/API composition. Captured credentials are
durable job anchors, not accepted browser credentials. Each access resolves
again while holding the original account's SHARE lock, which serializes with
auth writers' account UPDATE locks. No authority is cached across computation.
Credential anchors must outlive dependent jobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from aletheia_lab.hosted.authorization import Authorization, PostgresAuthorization
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound

from .postgres_sessions import Connection, Pool, _digest
from .repositories import RepositoryDataRejected

if TYPE_CHECKING:
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog

_CAPTURED_CONTEXT = (
    "SELECT s.user_id,s.context_id FROM j01_session_credentials captured "
    "JOIN j01_sessions s ON s.context_id=captured.context_id WHERE captured.digest=%s"
)
_CURRENT_CREDENTIAL = (
    "SELECT current_credential.digest FROM j01_session_credentials captured "
    "JOIN j01_sessions s ON s.context_id=captured.context_id "
    "JOIN j01_session_credentials current_credential ON current_credential.context_id=s.context_id "
    "WHERE captured.digest=%s AND s.user_id=%s AND s.context_id=%s "
    "AND ((captured.state='active' AND captured.generation=s.generation) "
    "OR (captured.state='rotated' AND captured.generation<s.generation)) "
    "AND current_credential.state='active' AND current_credential.generation=s.generation"
)


def _writer_action(action: str) -> str:
    return "preview" if action == "read" else action


def _anchor(row: tuple[object, ...] | None) -> tuple[str, str]:
    if row is None:
        raise SourceNotFound()
    if not isinstance(row, tuple) or len(row) != 2 or not isinstance(row[0], str) or not isinstance(row[1], str):
        raise RepositoryDataRejected()
    return row[0], row[1]


def _current_digest(row: tuple[object, ...] | None) -> str:
    if row is None:
        raise SourceNotFound()
    if not isinstance(row, tuple) or len(row) != 1 or not isinstance(row[0], str):
        raise RepositoryDataRejected()
    try:
        return _digest(SecretStr(row[0]))
    except ValueError:
        raise RepositoryDataRejected() from None


class _WorkerAuthorization(PostgresAuthorization):
    async def authorize_source(self, conn: Connection, auth: Authorization, workspace_id: str,
                               source_id: str, action: str) -> None:
        # Worker reads must retain current source-write grants, including after compute.
        await super().authorize_source(conn, auth, workspace_id, source_id, _writer_action(action))


class WorkerPostgresSources(PostgresSources):
    """Current writer/source grants for jobs anchored to their original context."""

    def __init__(self, pool: Pool, *, maximum_upload_bytes: int, maximum_expanded_bytes: int,
                 maximum_entries: int, audit: bool = False, catalog: PostgresCatalog | None = None) -> None:
        super().__init__(pool, maximum_upload_bytes=maximum_upload_bytes,
                         maximum_expanded_bytes=maximum_expanded_bytes, maximum_entries=maximum_entries,
                         audit=audit, catalog=catalog)
        self._authorization = _WorkerAuthorization()

    async def _access(self, conn: Connection, digest: str, workspace_id: str, *, action: str) -> Authorization:
        if conn.autocommit:
            raise RuntimeError("transactional connection required")
        if not isinstance(digest, str):
            raise SourceNotFound()
        try:
            digest = _digest(SecretStr(digest))
        except ValueError:
            raise SourceNotFound() from None
        user_id, context_id = _anchor(await (await conn.execute(_CAPTURED_CONTEXT, (digest,))).fetchone())
        account = await (await conn.execute(
            "SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR SHARE", (user_id,),
        )).fetchone()
        if account != (user_id,):
            raise SourceNotFound()
        current = _current_digest(await (await conn.execute(
            _CURRENT_CREDENTIAL, (digest, user_id, context_id),
        )).fetchone())
        return await super()._access(conn, current, workspace_id, action=_writer_action(action))
