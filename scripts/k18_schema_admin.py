"""Explicit owner schema migration/verification and narrow pre-existing role grants."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from k18_recovery_admin import _private

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.action_audit_migration import verify as verify_audit
from aletheia_lab.hosted.persistence_migration import migrate, migration_schema, verify
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_migration import verify as verify_preview
from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_policy
from aletheia_lab.hosted.project_policy_migration import verify as verify_policy
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_migration import verify as verify_reviews
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_upload
from aletheia_lab.hosted.upload_staging_migration import verify as verify_upload


async def execute(args: argparse.Namespace) -> dict[str, object]:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    database = _private(args.database_file, 65536).decode().strip()
    scoped = make_conninfo(database, options='-csearch_path=' + args.schema
        + ' -clock_timeout=1000 -cstatement_timeout=30000 -cidle_in_transaction_session_timeout=30000',
        connect_timeout=5, application_name='aletheia-schema-operator')
    async with await psycopg.AsyncConnection.connect(scoped) as conn:
        owner = await (await conn.execute('SELECT current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s', (args.schema,))).fetchone()
        if owner is None or owner[0] != owner[1]:
            raise ValueError('Existing schema owner required.')
        await conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(args.schema)))
        if args.action == 'migrate':
            await migrate(conn, args.schema)
            await migrate_policy(conn, args.schema)
            await migrate_reviews(conn, args.schema)
            await migrate_audit(conn, args.schema)
            await migrate_upload(conn, args.schema)
            await migrate_preview(conn, args.schema)
        if not (await verify(conn, args.schema) and await verify_policy(conn, args.schema)
                and await verify_reviews(conn, args.schema) and await verify_audit(conn, args.schema)
                and await verify_upload(conn, args.schema) and await verify_preview(conn)):
            raise ValueError('Current schema verification failed.')
        if args.action == 'grant':
            await grant_preview_runtime(conn, args.schema, args.role, profile=args.profile, audit=True)
            database_name = (await (await conn.execute('SELECT current_database()')).fetchone())[0]
            await conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(database_name), sql.Identifier(args.role)))
    return {'action': args.action, 'schema': args.schema, 'verified': True,
            'audit_required': True, 'runtime_grants_issued': args.action == 'grant'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('migrate', 'verify', 'grant'))
    parser.add_argument('--database-file', required=True, type=Path)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--role')
    parser.add_argument('--profile', choices=('api', 'worker'))
    args = parser.parse_args(argv)
    try:
        args.schema = migration_schema(args.schema)
    except Exception:
        parser.error('Explicit non-system schema required')
    if bool(args.role and args.profile) != (args.action == 'grant') or (args.action != 'grant' and (args.role or args.profile)):
        parser.error('Only grant requires --role and --profile')
    async def bounded() -> dict[str, object]:
        async with asyncio.timeout(120):
            return await execute(args)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
            print(json.dumps(runner.run(bounded()), sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        print(json.dumps({'error': 'schema_admin_failed', 'transaction_rolled_back': True}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
