"""Read explicitly provisioned identities from PostgreSQL.

This is only the identity lookup port. It does not create sessions or establish
authorization: login commit must recheck the account inside its transaction.
The schema is intentionally not installed automatically.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from pydantic import SecretStr, ValidationError

from .repositories import AccountBinding, RepositoryDataRejected


class IdentityCursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...


class IdentityConnection(Protocol):
    async def execute(self, query: str, params: tuple[str, str]) -> IdentityCursor: ...


class IdentityPool(Protocol):
    def connection(self) -> AbstractAsyncContextManager[IdentityConnection]: ...


class PostgresIdentityDirectory:
    """Lookup by the exact issuer and subject; never infer identity from email."""

    def __init__(self, pool: IdentityPool) -> None:
        self._pool = pool

    async def find_identity(self, *, issuer: str, subject: SecretStr) -> AccountBinding | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT issuer, subject, user_id, display_name, email, email_verified, "
                "enabled, account_epoch, onboarding, last_workspace_id "
                "FROM hosted_identity_bindings WHERE issuer = %s AND subject = %s",
                (issuer, subject.get_secret_value()),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            if len(row) != 10 or not isinstance(row[0], str) or not isinstance(row[1], str):
                raise ValueError("invalid identity record")
            return AccountBinding.model_validate({
                "issuer": row[0], "subject": SecretStr(row[1]),
                "actor": {"user_id": row[2], "display_name": row[3], "email": row[4],
                          "email_verified": row[5]},
                "enabled": row[6], "account_epoch": row[7], "onboarding": row[8],
                "last_workspace_id": row[9],
            })
        except (ValidationError, TypeError, ValueError, IndexError):
            raise RepositoryDataRejected() from None
