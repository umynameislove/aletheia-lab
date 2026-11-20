"""Shared fixed-window auth budgets; caller installs the reviewed schema first."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import SecretStr

from .abuse import AuthOperation, RateDecision, RatePolicy
from .postgres_sessions import Connection, Pool
from .repositories import RepositoryDataRejected
from .settings import AuthConfigurationRejected

AUTH_OPERATIONS: tuple[AuthOperation, ...] = ("login_start", "callback", "renew", "logout", "logout_all")
_NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class PostgresAuthAdmission:
    """One locked row per configured namespace/operation; no local counter/cache.

    At most global_attempts digests (capped at 10,000) fit in each row. New windows
    replace the map, so source churn cannot create unbounded historical rows.
    Policy mismatches and backward DB time fail closed rather than reset quota.
    shortcut: row/JSON writes serialize an operation, benchmark before high-volume deployment.
    """

    def __init__(self, pool: Pool, *, namespace: str) -> None:
        if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
            raise AuthConfigurationRejected()
        self._pool, self._namespace = pool, namespace

    async def is_ready(self, policy: RatePolicy) -> bool:
        """Non-mutating readiness; quota lookup still rechecks inside its transaction."""
        try:
            async with self._pool.connection() as connection:
                row = await (await connection.execute(
                    "SELECT count(*),bool_and(global_limit=%s AND subject_limit=%s AND window_seconds=%s "
                    "AND last_seen<=clock_timestamp()) FROM k03_auth_admission WHERE namespace=%s",
                    (policy.global_attempts, policy.subject_attempts, policy.window_seconds, self._namespace),
                )).fetchone()
            return row is not None and len(row) == 2 and type(row[0]) is int and row[0] == 5 and row[1] is True
        except Exception:
            return False

    async def prepare(self, policy: RatePolicy) -> None:
        """Verify all shared policies/schema transactionally, without consuming quota."""
        if not isinstance(policy, RatePolicy) or policy.global_attempts > 10000:
            raise AuthConfigurationRejected()
        policy = RatePolicy(policy.global_attempts, policy.subject_attempts, policy.window_seconds)
        limits = (policy.global_attempts, policy.subject_attempts, policy.window_seconds)
        async with self._pool.connection() as connection:
            if connection.autocommit:
                raise AuthConfigurationRejected()
            for operation in AUTH_OPERATIONS:
                row = await self._locked(connection, operation, "0" * 64, limits)
                observed = await (await connection.execute("SELECT clock_timestamp()")).fetchone()
                self._validate(row, observed, limits)
                assert row is not None and observed is not None
                now, last_seen = observed[0], row[4]
                assert isinstance(now, datetime) and isinstance(last_seen, datetime)
                if now < last_seen:
                    raise RepositoryDataRejected()

    async def _locked(
        self, connection: Connection, operation: AuthOperation, subject: str, limits: tuple[int, int, int],
    ) -> tuple[object, ...] | None:
        key = (self._namespace, operation)
        await connection.execute(
            "INSERT INTO k03_auth_admission "
            "(namespace,operation,global_limit,subject_limit,window_seconds,window_number,last_seen,attempts,subjects) "
            "VALUES (%s,%s,%s,%s,%s,-1,to_timestamp(0),0,'{}'::jsonb) "
            "ON CONFLICT (namespace,operation) DO NOTHING", (*key, *limits),
        )
        return await (await connection.execute(
            "SELECT global_limit,subject_limit,window_seconds,window_number,last_seen,attempts, "
            "COALESCE((subjects->>%s)::bigint,0) FROM k03_auth_admission "
            "WHERE namespace=%s AND operation=%s FOR UPDATE", (subject, *key),
        )).fetchone()

    async def consume(
        self, operation: AuthOperation, subject_digest: SecretStr, policy: RatePolicy,
    ) -> RateDecision:
        if (
            operation not in AUTH_OPERATIONS or not isinstance(subject_digest, SecretStr)
            or not _DIGEST.fullmatch(subject_digest.get_secret_value())
            or not isinstance(policy, RatePolicy) or policy.global_attempts > 10000
        ):
            raise AuthConfigurationRejected()
        policy = RatePolicy(policy.global_attempts, policy.subject_attempts, policy.window_seconds)
        subject = subject_digest.get_secret_value()
        limits = (policy.global_attempts, policy.subject_attempts, policy.window_seconds)
        key = (self._namespace, operation)
        async with self._pool.connection() as connection:
            if connection.autocommit:
                raise AuthConfigurationRejected()
            row = await self._locked(connection, operation, subject, limits)
            observed = await (await connection.execute("SELECT clock_timestamp()")).fetchone()
            self._validate(row, observed, limits)
            assert row is not None and observed is not None
            now, last_seen = observed[0], row[4]
            assert isinstance(now, datetime) and isinstance(last_seen, datetime)
            if now < last_seen:
                raise RepositoryDataRejected()
            epoch = int(now.timestamp())
            window = epoch // policy.window_seconds
            old_window, attempts, used = row[3], row[5], row[6]
            assert isinstance(old_window, int) and isinstance(attempts, int) and isinstance(used, int)
            if window < old_window:
                raise RepositoryDataRejected()
            if window != old_window:
                attempts = used = 0
            allowed = attempts < policy.global_attempts and used < policy.subject_attempts
            if allowed:
                await connection.execute(
                    "UPDATE k03_auth_admission SET window_number=%s,last_seen=%s,attempts=%s, "
                    "subjects=jsonb_set(CASE WHEN window_number=%s THEN subjects ELSE '{}'::jsonb END, "
                    "ARRAY[%s]::text[],to_jsonb(%s::bigint),true) WHERE namespace=%s AND operation=%s",
                    (window, now, attempts + 1, window, subject, used + 1, *key),
                )
            else:
                await connection.execute("UPDATE k03_auth_admission SET last_seen=%s "
                                         "WHERE namespace=%s AND operation=%s", (now, *key))
        # The pool context has committed before any allowance can escape.
        return RateDecision(allowed, 0 if allowed else policy.window_seconds - epoch % policy.window_seconds)

    @staticmethod
    def _validate(
        row: tuple[object, ...] | None, observed: tuple[object, ...] | None,
        limits: tuple[int, int, int],
    ) -> None:
        if row is None or len(row) != 7 or observed is None or len(observed) != 1:
            raise RepositoryDataRejected()
        if tuple(row[:3]) != limits or any(type(row[i]) is not int for i in (0, 1, 2, 3, 5, 6)):
            raise RepositoryDataRejected()
        if not all(isinstance(value, datetime) and value.utcoffset() is not None for value in (row[4], observed[0])):
            raise RepositoryDataRejected()
        window, attempts, used = row[3], row[5], row[6]
        assert isinstance(window, int) and isinstance(attempts, int) and isinstance(used, int)
        if window < -1 or not 0 <= used <= attempts <= limits[0] or used > limits[1]:
            raise RepositoryDataRejected()
