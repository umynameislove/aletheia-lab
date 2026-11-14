"""PostgreSQL session operations using account-before-session row locks."""

from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol

from pydantic import SecretStr, ValidationError

from .repositories import RepositoryDataRejected, RotateSession, SessionSnapshot

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}")


class Cursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...


class Connection(Protocol):
    @property
    def autocommit(self) -> bool: ...

    async def execute(self, query: str, params: tuple[object, ...] = (), *, binary: bool = False) -> Cursor: ...


class Pool(Protocol):
    def connection(self) -> AbstractAsyncContextManager[Connection]: ...


_READ = """
SELECT c.digest, a.user_id, a.display_name, a.email, a.email_verified,
       s.context_id, s.csrf_token, a.onboarding, a.last_workspace_id,
       CASE WHEN s.state = 'active' AND c.state = 'rotated' THEN 'rotated' ELSE s.state END,
       a.enabled, s.issued_at, s.last_seen_at, s.rotated_at, s.idle_expires_at,
       s.absolute_expires_at, s.generation, s.session_epoch, a.account_epoch
FROM j01_session_credentials c JOIN j01_sessions s ON s.context_id = c.context_id
JOIN hosted_identity_bindings a ON a.user_id = s.user_id WHERE c.digest = %s
"""


def _digest(value: SecretStr) -> str:
    if not isinstance(value, SecretStr) or not _DIGEST.fullmatch(value.get_secret_value()):
        raise ValueError("credential digest required")
    return value.get_secret_value()


def _snapshot(row: tuple[object, ...] | None) -> SessionSnapshot | None:
    if row is None:
        return None
    try:
        if len(row) != 19 or not isinstance(row[0], str) or not isinstance(row[6], str):
            raise ValueError("bad record")
        return SessionSnapshot.model_validate({
            "credential_digest": SecretStr(row[0]),
            "actor": {"user_id": row[1], "display_name": row[2], "email": row[3], "email_verified": row[4]},
            "context_id": row[5], "csrf_token": SecretStr(row[6]), "onboarding": row[7],
            "last_workspace_id": row[8], "state": row[9], "actor_enabled": row[10],
            "issued_at": row[11], "last_seen_at": row[12], "rotated_at": row[13],
            "idle_expires_at": row[14], "absolute_expires_at": row[15],
            "generation": row[16], "session_epoch": row[17], "account_epoch": row[18],
        })
    except (ValidationError, ValueError, TypeError, IndexError):
        raise RepositoryDataRejected() from None


async def _revoke_account(conn: Connection, user: str, now: datetime, *, schema: str | None = None) -> None:
    from psycopg import sql

    namespace = sql.SQL("") if schema is None else sql.SQL("{}.").format(sql.Identifier(schema))
    # Account UPDATE lock is held by the caller; the transaction owns both effects.
    await conn.execute(sql.SQL("UPDATE {}hosted_identity_bindings SET account_epoch=account_epoch+1, "
                       "logout_all_at=GREATEST(logout_all_at,%s) WHERE user_id=%s").format(namespace).as_string(), (now, user))
    await conn.execute(sql.SQL("UPDATE {}j01_sessions SET state='revoked' WHERE user_id=%s AND state='active'")
                       .format(namespace).as_string(), (user,))


async def _operator_namespace(conn: Connection, schema: str) -> str:
    if (not isinstance(schema, str) or _SCHEMA.fullmatch(schema) is None
            or schema in ("public", "information_schema") or schema.startswith("pg_")):
        raise ValueError("An explicit owned application schema is required.")
    owner = await (await conn.execute(
        "SELECT current_user,session_user,pg_catalog.pg_get_userbyid(n.nspowner) FROM pg_catalog.pg_namespace n "
        "WHERE n.nspname=%s", (schema,),
    )).fetchone()
    if owner is None or len(owner) != 3 or not isinstance(owner[0], str) or owner[0] != owner[1] or owner[0] != owner[2]:
        raise PermissionError("Owned authentication relations are required.")
    for relation in ("hosted_identity_bindings", "j01_sessions"):
        actual = await (await conn.execute(
            "SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s",
            (schema, relation),
        )).fetchone()
        if actual != ("r", owner[0]):
            raise PermissionError("Owned authentication relations are required.")
    return '"' + schema + '".'


class PostgresSessionStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def is_ready(self) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute("SELECT to_regclass('hosted_identity_bindings'), "
                                        "to_regclass('j01_sessions'), to_regclass('j01_session_credentials')")
            row = await cursor.fetchone()
            return row is not None and len(row) == 3 and all(value is not None for value in row)

    async def inspect(self, credential_digest: SecretStr) -> SessionSnapshot | None:
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            return _snapshot(await (await conn.execute(_READ, (digest,))).fetchone())

    @staticmethod
    async def revoke_identity(conn: Connection, *, schema: str, issuer: str, subject: SecretStr,
                              expected_issuer: str) -> bool:
        """Trusted manual operator only; caller commits/rolls back this connection.

        This does not authenticate an IdP event or change account/membership policy.
        It needs schema/table ownership, not the API/worker principal's privileges.
        """
        from psycopg import sql

        if conn.autocommit:
            raise RuntimeError("transactional connection required")
        if (not isinstance(issuer, str) or not 1 <= len(issuer) <= 2048
                or any(ord(char) <= 32 or ord(char) == 127 for char in issuer)
                or issuer != expected_issuer or not isinstance(subject, SecretStr)):
            raise ValueError("An exact bounded operator identity is required.")
        value = subject.get_secret_value()
        if not 1 <= len(value) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("An exact bounded operator identity is required.")
        await _operator_namespace(conn, schema)
        account = await (await conn.execute(
            sql.SQL("SELECT user_id FROM {}.hosted_identity_bindings WHERE issuer=%s AND subject=%s FOR UPDATE")
                .format(sql.Identifier(schema)).as_string(),
            (issuer, value),
        )).fetchone()
        if account is None:
            return False
        if len(account) != 1 or not isinstance(account[0], str):
            raise RepositoryDataRejected()
        clock = await (await conn.execute("SELECT pg_catalog.clock_timestamp()")).fetchone()
        if (clock is None or len(clock) != 1 or not isinstance(clock[0], datetime)
                or clock[0].utcoffset() is None):
            raise RepositoryDataRejected()
        await _revoke_account(conn, account[0], clock[0], schema=schema)
        return True

    @staticmethod
    async def _lock(conn: Connection, digest: str) -> tuple[object, ...] | None:
        if conn.autocommit:
            raise RuntimeError("transactional connection required")
        row = await (await conn.execute(
            "SELECT s.user_id FROM j01_session_credentials c JOIN j01_sessions s "
            "ON s.context_id=c.context_id WHERE c.digest=%s", (digest,),
        )).fetchone()
        if row is None:
            return None
        account = await (await conn.execute(
            "SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR UPDATE", (row[0],),
        )).fetchone()
        if account is None:
            return None
        return await (await conn.execute(
            "SELECT s.context_id, s.user_id, s.state, s.generation, s.session_epoch, "
            "a.enabled, a.account_epoch, s.idle_expires_at, s.absolute_expires_at, s.issued_at, "
            "s.rotated_at, c.state, c.generation, clock_timestamp() "
            "FROM j01_session_credentials c JOIN j01_sessions s ON s.context_id=c.context_id "
            "JOIN hosted_identity_bindings a ON a.user_id=s.user_id WHERE c.digest=%s FOR UPDATE OF s,c",
            (digest,),
        )).fetchone()

    async def rotate_if_current(self, command: RotateSession) -> SessionSnapshot | None:
        digest, replacement = _digest(command.presented_digest), _digest(command.replacement_digest)
        rules = command.rules
        if (digest == replacement or type(rules.idle_seconds) is not int
                or type(rules.absolute_seconds) is not int or type(rules.rotation_seconds) is not int
                or not 0 < rules.rotation_seconds < rules.idle_seconds <= rules.absolute_seconds <= 604800):
            raise ValueError("invalid session rotation policy")
        async with self._pool.connection() as conn:
            row = await self._lock(conn, digest)
            if row is None:
                return None
            context, user, state, generation, session_epoch, enabled, account_epoch, idle, absolute, issued, rotated, cred_state, cred_gen, now = row
            if (context != command.expected_context_id or user != command.expected_user_id
                    or generation != command.expected_generation or account_epoch != command.expected_account_epoch
                    or state != "active" or cred_state != "active" or cred_gen != generation
                    or enabled is not True or session_epoch != account_epoch):
                return None
            cursor = await conn.execute(
                "UPDATE j01_sessions SET generation=generation+1, rotated_at=%s, last_seen_at=%s, "
                "idle_expires_at=LEAST(%s + %s * interval '1 second', absolute_expires_at, "
                "issued_at + %s * interval '1 second') WHERE context_id=%s AND "
                "idle_expires_at > %s AND absolute_expires_at > %s AND "
                "issued_at + %s * interval '1 second' > %s AND "
                "last_seen_at + %s * interval '1 second' > %s AND "
                "rotated_at + %s * interval '1 second' <= %s RETURNING generation",
                (now, now, now, rules.idle_seconds, rules.absolute_seconds, context, now, now,
                 rules.absolute_seconds, now, rules.idle_seconds, now, rules.rotation_seconds, now),
            )
            if await cursor.fetchone() is None:
                return None
            await conn.execute("UPDATE j01_session_credentials SET state='rotated' WHERE digest=%s", (digest,))
            await conn.execute("INSERT INTO j01_session_credentials(digest,context_id,state,generation) "
                               "VALUES (%s,%s,'active',%s)", (replacement, context, command.expected_generation + 1))
            return _snapshot(await (await conn.execute(_READ, (replacement,))).fetchone())

    async def revoke_presented(self, credential_digest: SecretStr, *, all_sessions: bool) -> bool:
        digest = _digest(credential_digest)
        if type(all_sessions) is not bool:
            raise ValueError("boolean logout scope required")
        async with self._pool.connection() as conn:
            row = await self._lock(conn, digest)
            if row is None:
                return False
            context, user, state, generation, session_epoch, enabled, account_epoch, idle, absolute, _issued, _rotated, cred_state, cred_gen, now = row
            if (not isinstance(idle, datetime) or not isinstance(absolute, datetime)
                    or not isinstance(now, datetime) or type(generation) is not int or not isinstance(user, str)):
                raise RepositoryDataRejected()
            if (state != "active" or enabled is not True or session_epoch != account_epoch
                    or idle <= now or absolute <= now
                    or not ((cred_state == "active" and cred_gen == generation)
                            or (cred_state == "rotated" and cred_gen == generation - 1))):
                return False
            if all_sessions:
                await _revoke_account(conn, user, now)
            else:
                await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id=%s", (context,))
            return True
