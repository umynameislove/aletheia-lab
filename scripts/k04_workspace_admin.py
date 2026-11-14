"""Explicit operator CLI for schema adoption, provisioning and manual session revoke."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from aletheia_lab.hosted.workspace_management import provision_identity
from aletheia_lab.hosted.workspace_migration import migrate, verify
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.repositories import ValidatedIdentity


class ProvisioningInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    identity: ValidatedIdentity
    display_name: str


class RevocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    issuer: str = Field(min_length=1, max_length=2048, repr=False)
    subject: SecretStr

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, value: str) -> str:
        if any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise ValueError("A bounded exact issuer is required.")
        return value

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: SecretStr) -> SecretStr:
        text = value.get_secret_value()
        if not 1 <= len(text) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("A bounded exact subject is required.")
        return value


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's normal error can echo an accidentally supplied secret argument.
        super().error("Invalid operator arguments.")


def _unique_identity_fields(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in items:
        if name in result:
            raise ValueError("Duplicate identity fields are invalid.")
        result[name] = value
    return result


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _SafeParser(description=__doc__)
    parser.add_argument("operation", choices=("migrate", "provision", "revoke-sessions"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--database-url-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--expected-issuer")
    result = parser.parse_args(argv)
    if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", result.schema)
            or result.schema in ("public", "information_schema") or result.schema.startswith("pg_")):
        parser.error("An explicit application schema is required.")
    if result.operation == "provision" and (result.identity_file is None or not result.expected_issuer):
        parser.error("Provision requires --identity-file and --expected-issuer.")
    if result.operation == "revoke-sessions" and (result.identity_file is None or not result.expected_issuer):
        parser.error("Revocation requires --identity-file and --expected-issuer.")
    if (len(result.database_url_env) > 128
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result.database_url_env) is None):
        parser.error("A database environment reference is required.")
    return result


async def execute(options: argparse.Namespace) -> dict[str, object]:
    import psycopg
    from psycopg import sql

    # Only an operator with database credentials can invoke this path; no public route.
    database = os.environ.get(options.database_url_env)
    if not database:
        raise ValueError("Database secret reference is unavailable.")
    value = None
    revocation = None
    if options.operation in ("provision", "revoke-sessions"):
        with options.identity_file.open("rb") as document:
            payload = document.read(8193)
        if len(payload) > 8192:
            raise ValueError("Identity document is too large.")
        if options.operation == "provision":
            value = ProvisioningInput.model_validate_json(payload)
        else:
            revocation = RevocationInput.model_validate(json.loads(payload, object_pairs_hook=_unique_identity_fields))
            if revocation.issuer != options.expected_issuer:
                raise ValueError("An exact configured issuer is required.")
    async with await psycopg.AsyncConnection.connect(database, connect_timeout=10) as conn:
        await conn.execute("SET LOCAL lock_timeout='5s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        if options.operation == "migrate":
            result: dict[str, object] = {"migration": await migrate(conn, options.schema)}
        elif options.operation == "revoke-sessions":
            assert revocation is not None
            await conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(options.schema)))
            # This manual operation requires actual schema/table ownership. It does
            # not run workspace DDL, repair bindings or authenticate provider events.
            result = {"sessions_revoked": await PostgresSessionStore.revoke_identity(
                conn, schema=options.schema, issuer=revocation.issuer, subject=revocation.subject,
                expected_issuer=options.expected_issuer)}
        else:
            assert value is not None
            await conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(options.schema)))
            # Provision never implicitly migrates or repairs an existing workspace.
            if not await verify(conn):
                raise ValueError("Workspace migration is unavailable.")
            result = await provision_identity(conn, value.identity, expected_issuer=options.expected_issuer,
                                              display_name=value.display_name)
    return result


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            result = runner.run(execute(options))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        print("Workspace administration failed; no credentials or identity details were logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
