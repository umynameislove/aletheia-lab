"""Independent additive annotation history extension over canonical K-05 records."""

from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .project_policy_migration import ProjectPolicyMigrationRejected, _context, _rows

ReviewMigrationRejected = ProjectPolicyMigrationRejected


async def _signature(conn: Connection) -> str:
    queries = (
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid),c.relkind,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid IN "
        "('j01_annotations'::regclass,'k04_review_migrations'::regclass) AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY c.relname,a.attnum",
        "SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid IN ('j01_annotations'::regclass,'k04_review_migrations'::regclass) "
        "ORDER BY conrelid::regclass::text,conname",
        "SELECT tgname,tgtype,tgenabled,tgdeferrable,tginitdeferred,tgfoid::regprocedure::text,pg_get_triggerdef(oid) "
        "FROM pg_trigger WHERE tgrelid='j01_annotations'::regclass AND NOT tgisinternal ORDER BY tgname",
        "SELECT p.proname,p.prosrc,p.proconfig,p.prosecdef,p.provolatile,l.lanname FROM pg_proc p "
        "JOIN pg_language l ON l.oid=p.prolang WHERE p.oid IN ('j01_annotation_insert_guard()'::regprocedure,"
        "'j01_annotation_immutable_guard()'::regprocedure) ORDER BY p.proname",
        "SELECT c.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE i.indrelid IN "
        "('j01_annotations'::regclass,'k04_review_migrations'::regclass) ORDER BY c.relname,pg_get_indexdef(i.indexrelid)",
    )
    return content_hasher(
        json.dumps(
            [await _rows(conn, query) for query in queries], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


_REVISIONS = ("review_schema.sql", "review_native_schema.sql", "review_retention_schema.sql")


def _checksums() -> tuple[str, ...]:
    return tuple(
        content_hasher(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in _REVISIONS
    )


def _valid_history(rows: list[tuple[object, ...]], count: int) -> bool:
    checksums = _checksums()
    return (
        count in (1, 2, 3)
        and len(rows) == count
        and all(
            len(row) == 3
            and type(row[0]) is int
            and row[:2] == (version, checksums[version - 1])
            and isinstance(row[2], str)
            and len(row[2]) == 64
            and all(character in "0123456789abcdef" for character in row[2])
            for version, row in enumerate(rows, 1)
        )
    )


async def _latest_signature(conn: Connection, prefix: tuple[object, ...]) -> str:
    # Preserve V1's installed signature and bind it into V2, so later history edits fail closed.
    value = {"structure": await _signature(conn), "v1": prefix}
    return content_hasher(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _retention_ready(conn: Connection, schema: str | None) -> bool:
    exists = await (await conn.execute("SELECT to_regclass('k13_deletions')")).fetchone()
    if exists is None or exists[0] is None:
        return False
    from .persistence_migration import verify as verify_persistence

    current = await (await conn.execute("SELECT current_schema()")).fetchone()
    if (
        current is None
        or len(current) != 1
        or not isinstance(current[0], str)
        or not await verify_persistence(conn, schema or current[0])
    ):
        raise ReviewMigrationRejected()
    return True


async def _retention_signature(conn: Connection, prefix: list[tuple[object, ...]]) -> str:
    value = {"structure": await _signature(conn), "history": prefix}
    return content_hasher(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def verify(conn: Connection, schema: str | None = None) -> bool:
    """Verify V2 legacy or V3 owner-only retention, without changing old checksums."""
    await _context(conn, schema)
    exists = await (await conn.execute("SELECT to_regclass('k04_review_migrations')")).fetchone()
    if exists is None or exists[0] is None:
        return False
    rows = await _rows(
        conn, "SELECT version,checksum,signature FROM k04_review_migrations ORDER BY version"
    )
    if await _retention_ready(conn, schema):
        return _valid_history(rows, 3) and rows[2][2] == await _retention_signature(conn, rows[:2])
    return _valid_history(rows, 2) and rows[1][2] == await _latest_signature(conn, rows[0])


async def migrate(conn: Connection, schema: str) -> str:
    """Explicit caller-owned transaction: create V1 then V2, or verify before upgrading V1."""
    schema = await _context(conn, schema)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k04-reviews:" + schema,)
    )
    await conn.execute(
        "SELECT workspace_id,project_id,record_id,record_type FROM k05_records LIMIT 0"
    )
    await conn.execute("SELECT workspace_id,user_id FROM j01_memberships LIMIT 0")
    await conn.execute(
        "SELECT workspace_id,project_id,user_id,actions,revision FROM j01_project_grants LIMIT 0"
    )
    await conn.execute("LOCK TABLE k05_records,j01_memberships IN SHARE ROW EXCLUSIVE MODE")
    exists = await (
        await conn.execute(
            "SELECT to_regclass('k04_review_migrations'),to_regclass('j01_annotations')"
        )
    ).fetchone()
    created = exists is not None and exists == (None, None)
    if created:
        ddl = Path(__file__).with_name(_REVISIONS[0]).read_bytes()
        await conn.execute(ddl.decode("utf-8"))
        await conn.execute(
            "CREATE TABLE k04_review_migrations(version integer PRIMARY KEY CHECK(version>0),"
            "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),"
            "signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),"
            "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
        )
        await conn.execute(
            "INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(1,%s,%s)",
            (content_hasher(ddl).hexdigest(), await _signature(conn)),
        )
    elif exists is None or exists[0] is None or exists[1] is None:
        raise ReviewMigrationRejected()
    await conn.execute(
        "LOCK TABLE j01_annotations,k04_review_migrations IN SHARE ROW EXCLUSIVE MODE"
    )
    rows = await _rows(
        conn, "SELECT version,checksum,signature FROM k04_review_migrations ORDER BY version"
    )
    retention = await _retention_ready(conn, schema)
    if _valid_history(rows, 3):
        if not retention or rows[2][2] != await _retention_signature(conn, rows[:2]):
            raise ReviewMigrationRejected()
        return "unchanged"
    if _valid_history(rows, 2):
        if rows[1][2] != await _latest_signature(conn, rows[0]):
            raise ReviewMigrationRejected()
        if not retention:
            return "unchanged"
    else:
        if not _valid_history(rows, 1) or rows[0][2] != await _signature(conn):
            raise ReviewMigrationRejected()
        # Never rewrite V1 or adopt a ledger whose full V1 structure has drifted.
        ddl = Path(__file__).with_name(_REVISIONS[1]).read_bytes()
        await conn.execute(ddl.decode("utf-8"))
        await conn.execute(
            "INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(2,%s,%s)",
            (content_hasher(ddl).hexdigest(), await _latest_signature(conn, rows[0])),
        )
        rows = await _rows(
            conn, "SELECT version,checksum,signature FROM k04_review_migrations ORDER BY version"
        )
    if retention:
        ddl = Path(__file__).with_name(_REVISIONS[2]).read_bytes()
        await conn.execute(ddl.decode("utf-8"))
        await conn.execute(
            "INSERT INTO k04_review_migrations(version,checksum,signature) VALUES(3,%s,%s)",
            (content_hasher(ddl).hexdigest(), await _retention_signature(conn, rows[:2])),
        )
    return "created" if created else "upgraded"
