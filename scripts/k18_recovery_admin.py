"""Offline signed backup/restore; the command never enables runtime credentials."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from aletheia_lab.hosted.object_store import FilesystemObjectStore, _regular
from aletheia_lab.hosted.persistence_migration import migration_schema
from aletheia_lab.hosted.recovery_checkpoint import apply_checkpoint, capture_checkpoint
from aletheia_lab.hosted.recovery_points import (
    RecoveryRejected,
    capture_bundle,
    file_digest,
    restore_bundle,
    verify_bundle,
)


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'checkpoint', 'verify', 'restore'))
    parser.add_argument('--key-file', required=True, type=Path)
    parser.add_argument('--database-file', type=Path)
    parser.add_argument('--schema')
    parser.add_argument('--object-root', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--checkpoint-sha256', help='Latest trusted external inventory value, required for restore')
    parser.add_argument('--pg-bin', type=Path)
    parser.add_argument('--maximum-bytes', type=int, default=12 * 1024**3)
    parser.add_argument('--timeout-seconds', type=float, default=300)
    args = parser.parse_args(argv)
    required = {
        'capture': ('database_file', 'schema', 'object_root', 'bundle', 'pg_bin'),
        'checkpoint': ('database_file', 'schema', 'object_root', 'checkpoint'),
        'verify': ('bundle',),
        'restore': ('database_file', 'object_root', 'bundle', 'pg_bin', 'checkpoint', 'checkpoint_sha256'),
    }
    if any(getattr(args, name) is None for name in required[args.action]):
        parser.error('Missing required arguments for this action: ' + ', '.join(required[args.action]))
    if not 0 < args.timeout_seconds <= 1800 or args.maximum_bytes < 1:
        parser.error('Timeout must be 0..1800 seconds and maximum bytes must be positive')
    return args


def _private(path: Path, maximum: int) -> bytes:
    _regular(path)
    info = path.stat()
    if info.st_size > maximum or (os.name != 'nt' and stat.S_IMODE(info.st_mode) & 0o077):
        raise RecoveryRejected()
    return path.read_bytes()


async def _owner_action(args: argparse.Namespace, database: str, key: bytes, schema: str) -> dict[str, Any]:
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.action_audit_migration import verify as verify_audit
    from aletheia_lab.hosted.persistence_migration import verify
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
    from aletheia_lab.hosted.postgres_objects import PostgresObjects
    from aletheia_lab.hosted.project_policy_migration import verify as verify_policy
    from aletheia_lab.hosted.retention import PostgresRetention
    from aletheia_lab.hosted.review_migration import verify as verify_reviews

    scoped = make_conninfo(database, options='-csearch_path=' + migration_schema(schema)
        + ' -clock_timeout=1000 -cstatement_timeout=30000 -cidle_in_transaction_session_timeout=30000',
        connect_timeout=5, application_name='aletheia-recovery-operator')
    async with AsyncConnectionPool(scoped, min_size=1, max_size=1, open=False) as pool:
        await pool.wait(timeout=10)
        async with pool.connection() as conn:
            if not (await verify(conn, schema) and await verify_reviews(conn, schema)
                    and await verify_policy(conn, schema) and await verify_audit(conn, schema)):
                raise RecoveryRejected()
        objects = PostgresObjects(pool, FilesystemObjectStore(args.object_root))
        retention = PostgresRetention(pool, PostgresCatalog(pool, objects))
        if args.action == 'checkpoint':
            return await capture_checkpoint(retention, args.checkpoint, schema=schema, signing_key=key)
        result = await apply_checkpoint(retention, args.checkpoint, schema=schema, signing_key=key,
                                        expected_manifest_sha256=args.checkpoint_sha256)
        # Recheck ledger drift after all authority/fence application. Roles stay closed.
        async with pool.connection() as conn:
            if not await verify(conn, schema):
                raise RecoveryRejected()
        return {**result, 'schema_verified': True, 'action_audit_required': True,
                'runtime_grants_issued': False}


def execute(args: argparse.Namespace) -> dict[str, Any]:
    key = _private(args.key_file, 4096)
    for folder in (args.bundle, args.checkpoint):
        if folder is not None and args.key_file.resolve().is_relative_to(folder.resolve()):
            raise RecoveryRejected()
    if args.action == 'verify':
        return {**verify_bundle(args.bundle, key), 'manifest_sha256': file_digest(args.bundle / 'manifest.json')}
    database = _private(args.database_file, 65536).decode().strip()
    if not database:
        raise RecoveryRejected()
    if args.action == 'capture':
        return {**capture_bundle(database, args.schema, args.object_root, args.bundle, pg_bin=args.pg_bin,
                signing_key=key, timeout_seconds=args.timeout_seconds, maximum_bytes=args.maximum_bytes),
                'manifest_sha256': file_digest(args.bundle / 'manifest.json')}
    if args.action == 'restore':
        # Authenticate the latest checkpoint before writing the fresh target.
        from aletheia_lab.hosted.recovery_checkpoint import verify_checkpoint

        body = verify_bundle(args.bundle, key)
        schema = body['schema']
        checkpoint = verify_checkpoint(args.checkpoint, schema=schema, signing_key=key,
                                       expected_manifest_sha256=args.checkpoint_sha256)
        if datetime.fromisoformat(checkpoint['captured_at']) < datetime.fromisoformat(body['snapshot_at']):
            raise RecoveryRejected()
        restore_bundle(database, args.bundle, args.object_root, pg_bin=args.pg_bin,
                       signing_key=key, timeout_seconds=args.timeout_seconds)
    else:
        schema = migration_schema(args.schema)
        if not (args.object_root / '.k05-object-store').is_file():
            raise RecoveryRejected()
    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(args.timeout_seconds):
            return await _owner_action(args, database, key, schema)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        return runner.run(bounded())


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        print(json.dumps(execute(args), ensure_ascii=False, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        print(json.dumps({'error': 'recovery_admin_failed', 'action': args.action,
                          'runtime_must_remain_disabled': True}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
