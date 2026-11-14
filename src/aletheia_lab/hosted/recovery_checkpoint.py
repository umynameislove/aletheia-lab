"""Latest owner checkpoint for preventing restored deletion/authority resurrection."""
from __future__ import annotations

import hmac
import json
import re
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import uuid4

from aletheia_lab.project.identity import content_hasher

from .deletion_checkpoint import DeletionCheckpointPage
from .persistence_migration import migration_schema
from .recovery_points import (
    RecoveryRejected,
    _canonical,
    _key,
    _manifest_count,
    _manifest_file,
    _manifest_stamp,
    file_digest,
)

# Fixed columns only: no credential, cookie, question, source or evaluator payload.
_AUTHORITY = {
    'identities': 'SELECT user_id,enabled,account_epoch,issuer,subject FROM hosted_identity_bindings ORDER BY user_id',
    'memberships': 'SELECT workspace_id,user_id,role,active,revision FROM j01_memberships ORDER BY workspace_id,user_id',
    'project_grants': 'SELECT workspace_id,project_id,user_id,actions,revision FROM j01_project_grants ORDER BY workspace_id,project_id,user_id',
    'source_grants': 'SELECT workspace_id,source_id,user_id,actions,revision FROM j01_source_grants ORDER BY workspace_id,source_id,user_id',
}


async def capture_checkpoint(retention: Any, output: Path, *, schema: str,
                             signing_key: bytes, maximum_rows: int = 1000000) -> dict[str, Any]:
    """Store this checkpoint outside the older backup; retain its hash out-of-band."""
    from psycopg import sql

    schema = migration_schema(schema)
    _key(signing_key)
    if output.exists() or type(maximum_rows) is not int or maximum_rows < 1:
        raise RecoveryRejected()
    output.mkdir(parents=True, mode=0o700)
    authority = output / 'authority.jsonl'
    deletion = output / 'deletions.jsonl'
    rows = pages = 0
    async with retention.pool.connection() as conn, conn.transaction():
        await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        await conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        owner = await (await conn.execute('SELECT current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s', (schema,))).fetchone()
        if owner is None or owner[0] != owner[1]:
            raise RecoveryRejected()
        # Conservative lower bound: the RR snapshot may have been acquired by
        # the earlier owner query. A later wall-clock read could mislabel an
        # overlapping older checkpoint as newer than the backup snapshot.
        stamp = await (await conn.execute('SELECT transaction_timestamp()')).fetchone()
        rows = await _capture_authority(conn, authority, maximum_rows)
        after: tuple[str, str] | None = None
        with deletion.open('xb') as handle:
            while True:
                page = await retention.export_checkpoint_page(limit=1000, after=after, connection=conn)
                handle.write(page.model_dump_json().encode() + b'\n')
                pages += 1
                if page.next_cursor is None:
                    break
                if after is not None and page.next_cursor <= after:
                    raise RecoveryRejected()
                after = page.next_cursor
    if stamp is None:
        raise RecoveryRejected()
    body = {'format': 'hosted-authority-checkpoint/v1', 'checkpoint_id': uuid4().hex,
            'schema': schema, 'captured_at': stamp[0].astimezone(UTC).isoformat(),
            'authority_rows': rows, 'deletion_pages': pages,
            'files': {name: {'sha256': file_digest(output / name), 'bytes': (output / name).stat().st_size}
                      for name in ('authority.jsonl', 'deletions.jsonl')}}
    signed = {'manifest': body, 'hmac_sha256': hmac.digest(_key(signing_key), _canonical(body), 'sha256').hex()}
    manifest = output / 'manifest.json'
    manifest.write_bytes(_canonical(signed) + b'\n')
    for path in output.iterdir():
        path.chmod(0o600)
    return {**body, 'manifest_sha256': file_digest(manifest)}


def verify_checkpoint(folder: Path, *, schema: str, signing_key: bytes,
                      expected_manifest_sha256: str) -> dict[str, Any]:
    """The expected hash must be the *latest* trusted external inventory value."""
    try:
        if re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256) is None:
            raise RecoveryRejected()
        manifest = folder / 'manifest.json'
        if file_digest(manifest) != expected_manifest_sha256 or manifest.stat().st_size > 16384:
            raise RecoveryRejected()
        value = json.loads(manifest.read_bytes())
        if not isinstance(value, dict) or set(value) != {'manifest', 'hmac_sha256'}:
            raise RecoveryRejected()
        body: dict[str, Any] = value['manifest']
        mac = hmac.digest(_key(signing_key), _canonical(body), 'sha256').hex()
        if not hmac.compare_digest(mac, value['hmac_sha256']):
            raise RecoveryRejected()
        if (set(body) != {'format', 'checkpoint_id', 'schema', 'captured_at', 'authority_rows', 'deletion_pages', 'files'}
                or body['format'] != 'hosted-authority-checkpoint/v1' or body['schema'] != migration_schema(schema)
                or set(body['files']) != {'authority.jsonl', 'deletions.jsonl'}):
            raise RecoveryRejected()
        if not isinstance(body['checkpoint_id'], str) or re.fullmatch('[0-9a-f]{32}', body['checkpoint_id']) is None:
            raise RecoveryRejected()
        _manifest_stamp(body['captured_at'])
        _manifest_count(body['authority_rows'])
        _manifest_count(body['deletion_pages'], positive=True)
        for name, record in body['files'].items():
            _manifest_file(folder, name, record)
        _verify_deletion_chain(folder, body['deletion_pages'])
        return body
    except RecoveryRejected:
        raise
    except Exception:
        raise RecoveryRejected() from None


async def apply_checkpoint(retention: Any, folder: Path, *, schema: str,
                           signing_key: bytes, expected_manifest_sha256: str) -> dict[str, Any]:
    """Fail closed before runtime grants; restore authority conservatively.

    New identities/grants absent from the older database require explicit owner
    re-provisioning. An owner-invariant conflict aborts, never preserves an old
    owner silently. Restored sessions are revoked; old bearer/session material
    must not regain validity simply because a database snapshot was restored.
    """
    from psycopg import sql

    body = verify_checkpoint(folder, schema=schema, signing_key=signing_key,
                             expected_manifest_sha256=expected_manifest_sha256)
    applied = 0
    with (folder / 'deletions.jsonl').open('rb') as handle:
        for line in handle:
            page = DeletionCheckpointPage.model_validate_json(line)
            applied += await retention.apply_checkpoint_page(page)
    # Temporary fixed-shape tables are owner-controlled and vanish at commit.
    async with retention.pool.connection() as conn, conn.transaction():
        await conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        owner = await (await conn.execute('SELECT current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s', (schema,))).fetchone()
        if owner is None or owner[0] != owner[1]:
            raise RecoveryRejected()
        await conn.execute('CREATE TEMP TABLE k18_identity(user_id text PRIMARY KEY,enabled boolean,epoch bigint,binding text) ON COMMIT DROP')
        await conn.execute('CREATE TEMP TABLE k18_membership(workspace_id text,user_id text,role text,active boolean,revision bigint,PRIMARY KEY(workspace_id,user_id)) ON COMMIT DROP')
        await conn.execute('CREATE TEMP TABLE k18_project(workspace_id text,object_id text,user_id text,actions text[],revision bigint,PRIMARY KEY(workspace_id,object_id,user_id)) ON COMMIT DROP')
        await conn.execute('CREATE TEMP TABLE k18_source(LIKE k18_project INCLUDING ALL) ON COMMIT DROP')
        shapes = {'identities': ('k18_identity', 4), 'memberships': ('k18_membership', 5),
                  'project_grants': ('k18_project', 5), 'source_grants': ('k18_source', 5)}
        seen = 0
        with (folder / 'authority.jsonl').open('rb') as handle:
            for line in handle:
                if len(line) > 4096:
                    raise RecoveryRejected()
                row = json.loads(line)
                table, count = shapes[row['kind']]
                if len(row['values']) != count:
                    raise RecoveryRejected()
                await conn.execute(sql.SQL('INSERT INTO {} VALUES({})').format(sql.Identifier(table),
                    sql.SQL(',').join(sql.Placeholder() for _ in range(count))).as_string(), tuple(row['values']))
                seen += 1
        if seen != body['authority_rows']:
            raise RecoveryRejected()
        # Match the original issuer/subject binding without putting either in the checkpoint.
        identities = await (await conn.execute('SELECT user_id,issuer,subject FROM hosted_identity_bindings')).fetchall()
        for user, issuer, subject in identities:
            binding = content_hasher(_canonical([issuer, subject])).hexdigest()
            await conn.execute('UPDATE hosted_identity_bindings SET enabled=COALESCE((SELECT enabled FROM k18_identity WHERE user_id=%s AND binding=%s),false),'
                'account_epoch=GREATEST(account_epoch,COALESCE((SELECT epoch FROM k18_identity WHERE user_id=%s AND binding=%s),account_epoch)),logout_all_at=clock_timestamp() WHERE user_id=%s',
                (user, binding, user, binding, user))
        await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE state='active'")
        await conn.execute('UPDATE j01_memberships m SET role=cp.role,active=cp.active,revision=m.revision+1 FROM k18_membership cp '
            'WHERE m.workspace_id=cp.workspace_id AND m.user_id=cp.user_id AND (m.role,m.active) IS DISTINCT FROM (cp.role,cp.active)')
        await conn.execute('UPDATE j01_memberships m SET active=false,revision=revision+1 WHERE active AND NOT EXISTS('
            'SELECT 1 FROM k18_membership cp WHERE cp.workspace_id=m.workspace_id AND cp.user_id=m.user_id)')
        for table, scratch, column in [('j01_project_grants', 'k18_project', 'project_id'), ('j01_source_grants', 'k18_source', 'source_id')]:
            await conn.execute(sql.SQL('UPDATE {} g SET actions=cp.actions,revision=g.revision+1 FROM {} cp '
                'WHERE g.workspace_id=cp.workspace_id AND g.{}=cp.object_id AND g.user_id=cp.user_id AND g.actions IS DISTINCT FROM cp.actions')
                .format(sql.Identifier(table), sql.Identifier(scratch), sql.Identifier(column)).as_string())
            await conn.execute(sql.SQL("UPDATE {} g SET actions='{{}}',revision=revision+1 WHERE cardinality(actions)>0 AND NOT EXISTS(SELECT 1 FROM {} cp "
                'WHERE cp.workspace_id=g.workspace_id AND cp.object_id=g.{} AND cp.user_id=g.user_id)')
                .format(sql.Identifier(table), sql.Identifier(scratch), sql.Identifier(column)).as_string())
    return {'status': 'checkpoint_applied_runtime_still_disabled', 'checkpoint_id': body['checkpoint_id'],
            'deletion_items_applied': applied, 'restored_sessions_revoked': True,
            'missing_post_backup_identities_and_grants_require_reprovisioning': True}
async def _capture_authority(conn: Any, authority: Path, maximum_rows: int) -> int:
    rows = 0
    with authority.open('xb') as handle:
        for kind, query in _AUTHORITY.items():
            async with conn.cursor(name='k18_' + kind) as cursor:
                await cursor.execute(query)
                async for row in cursor:
                    values = list(row)
                    if kind == 'identities':
                        values = [row[0], row[1], row[2], content_hasher(_canonical([row[3], row[4]])).hexdigest()]
                    handle.write(_canonical({'kind': kind, 'values': values}) + b'\n')
                    rows += 1
                    if rows > maximum_rows:
                        raise RecoveryRejected()
    return rows

def _verify_deletion_chain(folder: Path, expected_pages: int) -> None:
    # Prove a complete producer chain before applying even its first page.
    previous: tuple[str, str] | None = None
    pages = 0
    complete = False
    with (folder / 'deletions.jsonl').open('rb') as handle:
        while line := handle.readline(128 * 1024**2 + 1):
            if len(line) > 128 * 1024**2 or complete:
                raise RecoveryRejected()
            page = DeletionCheckpointPage.model_validate_json(line)
            if page.after != previous:
                raise RecoveryRejected()
            pages += 1
            previous = page.next_cursor
            complete = previous is None
    if not complete or pages != expected_pages:
        raise RecoveryRejected()

