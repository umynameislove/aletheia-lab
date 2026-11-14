"""Explicit v1 admission migration and quiet-window policy change; never startup DDL."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from aletheia_lab.hosted.content_hashing import content_hasher

from .abuse import RatePolicy
from .postgres_admission import AUTH_OPERATIONS

_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}")
_NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_TABLE = "k03_auth_admission"
_LEDGER = "k03_auth_admission_migrations"
_LEDGER_DDL = """CREATE TABLE k03_auth_admission_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);"""


class MigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Admission migration or policy transition was rejected.")


class MigrationCursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...

    async def fetchall(self) -> list[tuple[object, ...]]: ...


class MigrationConnection(Protocol):
    @property
    def autocommit(self) -> bool: ...

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> MigrationCursor: ...


def migration_schema(value: str) -> str:
    if (not isinstance(value, str) or not _SCHEMA.fullmatch(value)
            or value in ("public", "information_schema") or value.startswith("pg_")):
        raise MigrationRejected()
    return value


def _qualified(schema: str, table: str) -> str:
    from psycopg import sql

    return sql.Identifier(migration_schema(schema), table).as_string()


def _ddl() -> str:
    return Path(__file__).with_name("admission_schema.sql").read_text(encoding="utf-8")


def _create(ddl: str, table: str, destination: str, *, temporary: bool = False) -> str:
    prefix = "CREATE TABLE " + table
    if ddl.count(prefix) != 1 or not ddl.rstrip().endswith(");"):
        raise MigrationRejected()
    result = ddl.replace(prefix, ("CREATE TEMP TABLE " if temporary else "CREATE TABLE ") + destination, 1)
    return result.rstrip().removesuffix(";") + (" ON COMMIT DROP;" if temporary else ";")


async def _exists(connection: MigrationConnection, relation: str) -> bool:
    row = await (await connection.execute(
        "SELECT relkind,relpersistence FROM pg_class WHERE oid=to_regclass(%s)", (relation,),
    )).fetchone()
    if row is not None and row != ("r", "p"):
        raise MigrationRejected()
    return row is not None


async def _signature(connection: MigrationConnection, relation: str) -> tuple[object, ...]:
    columns = await (await connection.execute(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid), "
        "a.attidentity,a.attgenerated,a.attcollation::regcollation::text "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
        (relation,),
    )).fetchall()
    constraints = await (await connection.execute(
        "SELECT contype,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid=to_regclass(%s) ORDER BY contype,pg_get_constraintdef(oid)", (relation,),
    )).fetchall()
    behavior = await (await connection.execute(
        "SELECT c.relrowsecurity,c.relforcerowsecurity, "
        "(SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal), "
        "(SELECT count(*) FROM pg_rewrite r WHERE r.ev_class=c.oid), "
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid), "
        "(SELECT count(*) FROM pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid) "
        "FROM pg_class c WHERE c.oid=to_regclass(%s)", (relation,),
    )).fetchone()
    indexes = await (await connection.execute(
        "SELECT i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.indkey::text,am.amname, "
        "i.indclass::text,i.indcollation::text,i.indoption::text, "
        "pg_get_expr(i.indexprs,i.indrelid),pg_get_expr(i.indpred,i.indrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam "
        "WHERE i.indrelid=to_regclass(%s) ORDER BY i.indkey::text,i.indisunique,i.indisprimary", (relation,),
    )).fetchall()
    return columns, constraints, behavior, indexes


async def _verify(connection: MigrationConnection, relation: str, ddl: str, table: str) -> None:
    temporary = "k03_expected_" + table
    await connection.execute(_create(ddl, table, temporary, temporary=True))
    reference = await _signature(connection, "pg_temp." + temporary)
    actual = await _signature(connection, relation)
    await connection.execute("DROP TABLE pg_temp." + temporary)
    if reference != actual:
        raise MigrationRejected()


async def _verify_data(connection: MigrationConnection, table: str) -> None:
    from psycopg import sql

    row = await (await connection.execute(
        sql.SQL(
            "SELECT count(*) FROM {} a WHERE (window_number=-1 AND (attempts<>0 OR subjects<>'{{}}'::jsonb)) OR "
            '(window_number>=0 AND window_number<>floor(extract(epoch FROM last_seen)/window_seconds)) OR EXISTS '
            "(SELECT 1 FROM jsonb_each(subjects) e WHERE e.key!~'^[0-9a-f]{{64}}$' OR NOT (CASE WHEN "
            "jsonb_typeof(e.value)='number' AND e.value::text~'^[1-9][0-9]*$' THEN e.value::text::numeric BETWEEN 1 "
            "AND subject_limit ELSE false END)) OR (SELECT COALESCE(sum(CASE WHEN e.value::text~'^[1-9][0-9]*$' THEN "
            'e.value::text::numeric ELSE 0 END),0) FROM jsonb_each(subjects) e)<>attempts'
        ).format(sql.SQL(table)).as_string(),
    )).fetchone()
    if row != (0,):
        raise MigrationRejected()


async def migrate(connection: MigrationConnection, schema: str) -> Literal["created", "adopted", "unchanged"]:
    """Caller must commit before reporting success; any error requires rollback.

    Adopt only the exact original table/valid counters, without resetting quota.
    Unknown versions/checksums/shapes fail closed. No destructive down migration.
    """
    from psycopg import sql

    schema = migration_schema(schema)
    if connection.autocommit:
        raise MigrationRejected()
    row = await (await connection.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))).fetchone()
    if row != (schema,):
        raise MigrationRejected()
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k03-admission-v1:" + schema,))
    table, ledger = _qualified(schema, _TABLE), _qualified(schema, _LEDGER)
    ddl = _ddl()
    checksum = content_hasher(ddl.encode("utf-8")).hexdigest()
    records: list[tuple[object, ...]] = []
    if await _exists(connection, ledger):
        await _verify(connection, ledger, _LEDGER_DDL, _LEDGER)
        records = await (await connection.execute(sql.SQL(
            'SELECT version,checksum FROM {} ORDER BY version'
        ).format(sql.SQL(ledger)).as_string())).fetchall()
        if records not in ([], [(1, checksum)]):
            raise MigrationRejected()
    else:
        await connection.execute(_create(_LEDGER_DDL, _LEDGER, ledger))
    exists = await _exists(connection, table)
    if records and not exists:
        raise MigrationRejected()
    if not exists:
        await connection.execute(_create(ddl, _TABLE, table))
    await connection.execute(sql.SQL(
        'LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE'
    ).format(sql.SQL(table)).as_string())
    await _verify(connection, table, ddl, _TABLE)
    await _verify_data(connection, table)
    if not records:
        await connection.execute(sql.SQL(
            'INSERT INTO {} (version,checksum) VALUES (1,%s)'
        ).format(sql.SQL(ledger)).as_string(), (checksum,))
    return "unchanged" if records else ("adopted" if exists else "created")


def _limits(value: RatePolicy) -> tuple[int, int, int]:
    if not isinstance(value, RatePolicy) or value.global_attempts > 10000:
        raise MigrationRejected()
    RatePolicy(value.global_attempts, value.subject_attempts, value.window_seconds)
    return value.global_attempts, value.subject_attempts, value.window_seconds


async def change_policy(
    connection: MigrationConnection, schema: str, namespace: str, *, expected: RatePolicy, replacement: RatePolicy,
) -> bool:
    """Compare-and-set all five policies after admitted old windows have ended.

    Old workers then fail policy checks; rollback to old values follows the same
    quiet-window rule. This never clears an active allowance history.
    """
    from psycopg import sql

    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        raise MigrationRejected()
    old, new = _limits(expected), _limits(replacement)
    await migrate(connection, schema)
    table = _qualified(schema, _TABLE)
    rows = await (await connection.execute(
        sql.SQL(
            'SELECT operation,global_limit,subject_limit,window_seconds,window_number,last_seen,attempts FROM {} WHERE'
            ' namespace=%s ORDER BY operation FOR UPDATE'
        ).format(sql.SQL(table)).as_string(), (namespace,),
    )).fetchall()
    if len(rows) != 5 or {row[0] for row in rows} != set(AUTH_OPERATIONS) or any(tuple(row[1:4]) != old for row in rows):
        raise MigrationRejected()
    if expected == replacement:
        return False
    observed = await (await connection.execute("SELECT clock_timestamp()")).fetchone()
    if observed is None or not isinstance(observed[0], datetime) or observed[0].utcoffset() is None:
        raise MigrationRejected()
    now = observed[0]
    for row in rows:
        if (type(row[4]) is not int or type(row[6]) is not int
                or not isinstance(row[5], datetime) or now < row[5]):
            raise MigrationRejected()
        window = row[4]
        assert isinstance(window, int)
        if row[6] and now.timestamp() < (window + 1) * expected.window_seconds:
            raise MigrationRejected()
    await connection.execute(
        sql.SQL(
            'UPDATE {} SET global_limit=%s,subject_limit=%s,window_seconds=%s, '
            "window_number=-1,last_seen=%s,attempts=0,subjects='{{}}'::jsonb WHERE namespace=%s"
        ).format(sql.SQL(table)).as_string(),
        (*new, now, namespace),
    )
    return True
