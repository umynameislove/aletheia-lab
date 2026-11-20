"""Operator-only signed recovery bundles; no per-request or public backup endpoint."""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import subprocess
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aletheia_lab.hosted.content_hashing import content_hasher

from .object_store import FilesystemObjectStore, ObjectDescriptor, _regular
from .persistence_migration import migration_schema

# GC takes shared transaction locks. A backup operator holds an exclusive session
# lock while copying the closure of one repeatable-read PostgreSQL snapshot.
BACKUP_ADVISORY_KEY = 0x414C455448424B50
_FORMAT = 'hosted-recovery-bundle/v1'


class RecoveryRejected(ValueError):
    def __init__(self) -> None:
        super().__init__('Recovery bundle or target preconditions did not match.')


def file_digest(path: Path) -> str:
    _regular(path)
    result = content_hasher()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise RecoveryRejected()
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def _manifest_stamp(value: object) -> None:
    if not isinstance(value, str):
        raise RecoveryRejected()
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise RecoveryRejected()
    except (ValueError, TypeError):
        raise RecoveryRejected() from None


def _manifest_count(value: object, *, positive: bool = False) -> None:
    if type(value) is not int or value < int(positive):
        raise RecoveryRejected()


def _manifest_file(root: Path, name: str, record: object) -> None:
    if not isinstance(record, dict) or set(record) != {'sha256', 'bytes'}:
        raise RecoveryRejected()
    if not isinstance(record['sha256'], str) or re.fullmatch('[0-9a-f]{64}', record['sha256']) is None:
        raise RecoveryRejected()
    _manifest_count(record['bytes'])
    candidate = root / name
    if file_digest(candidate) != record['sha256'] or candidate.stat().st_size != record['bytes']:
        raise RecoveryRejected()


def _pg_environment(database: str) -> dict[str, str]:
    from psycopg.conninfo import conninfo_to_dict

    fields = conninfo_to_dict(database)
    allowed = {'host': 'PGHOST', 'hostaddr': 'PGHOSTADDR', 'port': 'PGPORT', 'user': 'PGUSER',
               'password': 'PGPASSWORD', 'dbname': 'PGDATABASE', 'sslmode': 'PGSSLMODE',
               'sslrootcert': 'PGSSLROOTCERT', 'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY'}
    if 'dbname' not in fields or set(fields) - (set(allowed) | {'options', 'connect_timeout', 'application_name'}):
        raise RecoveryRejected()
    env = {name: value for name, value in os.environ.items() if not name.startswith('PG')}
    env.update({target: str(fields[name]) for name, target in allowed.items() if fields.get(name) is not None})
    env.update(PGCONNECT_TIMEOUT='5', PGAPPNAME='aletheia-recovery-operator')
    return env


def _tool(binary: Path, args: list[str], database: str, timeout: float, error_file: Path, *,
          input_file: Path | None = None, output_file: Path | None = None) -> None:
    if not binary.is_file() or not math.isfinite(timeout) or timeout <= 0 or timeout > 1800:
        raise RecoveryRejected()
    # Python opens archive paths because Windows PostgreSQL CLI builds can use narrow argv.
    with ExitStack() as stack:
        error = stack.enter_context(error_file.open('xb'))
        error_file.chmod(0o600)
        source = subprocess.DEVNULL if input_file is None else stack.enter_context(input_file.open('rb'))
        target = subprocess.DEVNULL if output_file is None else stack.enter_context(output_file.open('xb'))
        if output_file is not None:
            output_file.chmod(0o600)
        result = subprocess.run([str(binary), *args], env=_pg_environment(database),
            stdin=source, stdout=target, stderr=error, timeout=timeout, check=False)
    if result.returncode:
        raise RecoveryRejected()


def capture_bundle(database: str, schema: str, object_root: Path, output: Path, *,
                   pg_bin: Path, signing_key: bytes, timeout_seconds: float = 300,
                   maximum_bytes: int = 12 * 1024**3) -> dict[str, Any]:
    """Capture schema+objects at a common snapshot; this is logical backup, not PITR.

    Database owner is required. All deployed physical-GC implementations must
    honor BACKUP_ADVISORY_KEY; no request/worker admission or writer is paused.
    The caller archives a *later* signed authority/deletion checkpoint separately
    and applies it before exposing a restored instance.
    """
    import psycopg
    from psycopg import sql

    schema = migration_schema(schema)
    _key(signing_key)
    if (output.exists() or not (object_root / '.k05-object-store').is_file()
            or type(maximum_bytes) is not int or maximum_bytes < 1
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 1800):
        raise RecoveryRejected()
    source = FilesystemObjectStore(object_root)
    output.mkdir(parents=True, mode=0o700)
    store = FilesystemObjectStore(output / 'objects')
    index = output / 'objects.jsonl'
    count = byte_count = 0
    deadline = time.monotonic() + timeout_seconds
    with psycopg.connect(database, autocommit=True) as conn:
        owner = conn.execute('SELECT current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s', (schema,)).fetchone()
        if owner is None or owner[0] != owner[1]:
            raise RecoveryRejected()
        if conn.execute('SELECT pg_try_advisory_lock(%s)', (BACKUP_ADVISORY_KEY,)).fetchone() != (True,):
            raise RecoveryRejected()
        try:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                stamp = conn.execute('SELECT pg_export_snapshot(),clock_timestamp()').fetchone()
                if stamp is None or len(stamp) != 2:
                    raise RecoveryRejected()
                snapshot, at = stamp
                dump = output / 'database.dump'
                _tool(pg_bin / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump'),
                    ['--format=custom', '--no-owner', '--no-privileges', '--schema=' + schema,
                     '--snapshot=' + snapshot], database, timeout_seconds, output / 'private-pg-dump.stderr',
                    output_file=dump)
                if dump.stat().st_size > maximum_bytes or time.monotonic() > deadline:
                    raise RecoveryRejected()
                with conn.cursor(name='aletheia_recovery_objects') as cursor, index.open('xb') as handle:
                    cursor.execute('SELECT sha256,min(byte_size),max(byte_size) FROM k05_objects GROUP BY sha256 ORDER BY sha256')
                    for sha, size, largest in cursor:
                        if size != largest or time.monotonic() > deadline:
                            raise RecoveryRejected()
                        media, version = 'application/octet-stream', None
                        descriptor = ObjectDescriptor(sha, size, media, version)
                        if byte_count + size + dump.stat().st_size > maximum_bytes:
                            raise RecoveryRejected()
                        data = source.read(descriptor)
                        copied = store.publish(data, media_type=media, schema_version=version)
                        if copied != descriptor:
                            raise RecoveryRejected()
                        handle.write(_canonical({'sha256': sha, 'byte_size': size, 'media_type': media, 'schema_version': version}) + b'\n')
                        count += 1
                        byte_count += size
                    handle.flush()
                    os.fsync(handle.fileno())
        finally:
            conn.execute('SELECT pg_advisory_unlock(%s)', (BACKUP_ADVISORY_KEY,))
    body = {'format': _FORMAT, 'backup_id': uuid4().hex, 'schema': schema,
            'snapshot_at': at.astimezone(UTC).isoformat(), 'objects': count, 'object_bytes': byte_count,
            'files': {name: {'sha256': file_digest(output / name), 'bytes': (output / name).stat().st_size}
                      for name in ('database.dump', 'objects.jsonl')},
            'scope': 'Logical PostgreSQL exported snapshot and immutable object closure; no PITR/host-loss claim.'}
    signed = {'manifest': body, 'hmac_sha256': hmac.digest(_key(signing_key), _canonical(body), 'sha256').hex()}
    manifest = output / 'manifest.json'
    with manifest.open('xb') as handle:
        handle.write(_canonical(signed) + b'\n')
        handle.flush()
        os.fsync(handle.fileno())
    # Driver diagnostics remain private on failure, and are unnecessary for successful bundles.
    (output / 'private-pg-dump.stderr').unlink()
    return body


def verify_bundle(bundle: Path, signing_key: bytes) -> dict[str, Any]:
    """Authenticate before inspecting or executing a database archive."""
    try:
        _regular(bundle, directory=True)
        path = bundle / 'manifest.json'
        _regular(path)
        if path.stat().st_size > 16384:
            raise RecoveryRejected()
        signed = json.loads(path.read_bytes())
        if not isinstance(signed, dict) or set(signed) != {'manifest', 'hmac_sha256'}:
            raise RecoveryRejected()
        body: dict[str, Any] = signed['manifest']
        if not isinstance(body, dict):
            raise RecoveryRejected()
        expected = hmac.digest(_key(signing_key), _canonical(body), 'sha256').hex()
        if not isinstance(signed['hmac_sha256'], str) or not hmac.compare_digest(expected, signed['hmac_sha256']):
            raise RecoveryRejected()
        if (set(body) != {'format', 'backup_id', 'schema', 'snapshot_at', 'objects', 'object_bytes', 'files', 'scope'}
                or body['format'] != _FORMAT or migration_schema(body['schema']) != body['schema']
                or set(body['files']) != {'database.dump', 'objects.jsonl'}):
            raise RecoveryRejected()
        _validate_backup_metadata(body)
        for name, item in body['files'].items():
            _manifest_file(bundle, name, item)
        _verify_bundle_objects(bundle, body)
        return body
    except RecoveryRejected:
        raise
    except Exception:
        raise RecoveryRejected() from None


def restore_bundle(database: str, bundle: Path, object_root: Path, *, pg_bin: Path,
                   signing_key: bytes, timeout_seconds: float = 300) -> dict[str, Any]:
    """Restore only into a fresh owner-only database, never an active deployment.

    This intentionally does not issue readiness or runtime grants. The operator
    must apply latest authenticated deletion/authority history, revoke restored
    sessions, verify content+schema and then enable the target runtime roles.
    """
    import psycopg
    from psycopg import sql

    body = verify_bundle(bundle, signing_key)
    if object_root.exists() or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 1800:
        raise RecoveryRejected()
    deadline = time.monotonic() + timeout_seconds
    with psycopg.connect(database) as conn:
        owner = conn.execute('SELECT current_database(),current_user,pg_get_userbyid(datdba) '
            'FROM pg_database WHERE datname=current_database()').fetchone()
        tables = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','S')").fetchone()
        # --no-privileges omits archive GRANT statements; target default ACLs
        # could still grant access as each restored object is created.
        defaults = conn.execute('SELECT count(*) FROM pg_default_acl').fetchone()
        routines = conn.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()
        namespaces = conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
            "AND nspname NOT IN ('public','information_schema')").fetchone()
        other_grants = conn.execute("SELECT count(*) FROM pg_database d,LATERAL aclexplode("
            "COALESCE(d.datacl,acldefault('d',d.datdba))) a WHERE datname=current_database() "
            'AND a.grantee NOT IN (0,d.datdba)').fetchone()
        other_sessions = conn.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() '
            'AND usename IS DISTINCT FROM current_user').fetchone()
        if (owner is None or owner[1] != owner[2] or tables != (0,) or defaults != (0,)
                or routines != (0,) or namespaces != (0,) or other_grants != (0,) or other_sessions != (0,)):
            raise RecoveryRejected()
        # Seal before archive execution. In particular, restored SECURITY
        # DEFINER functions must never have a PUBLIC-executable exposure window.
        conn.execute(sql.SQL('REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(owner[0])))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    diagnostics = bundle.parent / ('private-restore-' + uuid4().hex + '.stderr')
    _tool(pg_bin / ('pg_restore.exe' if os.name == 'nt' else 'pg_restore'),
        ['--single-transaction', '--no-owner', '--no-privileges', '--dbname=' + _pg_environment(database)['PGDATABASE']],
        database, timeout_seconds, diagnostics, input_file=bundle / 'database.dump')
    # --no-privileges intentionally withholds runtime grants. Re-establish the
    # known owner-only trigger-function ACLs before validating restored ledgers.
    with psycopg.connect(database) as conn:
        for name in ('k13_capture_deletion', 'k13_no_resurrection'):
            conn.execute(sql.SQL('REVOKE ALL ON FUNCTION {}.{}() FROM PUBLIC').format(
                sql.Identifier(body['schema']), sql.Identifier(name)))
    source, target = FilesystemObjectStore(bundle / 'objects'), FilesystemObjectStore(object_root)
    with (bundle / 'objects.jsonl').open('rb') as handle:
        for line in handle:
            if time.monotonic() > deadline:
                raise RecoveryRejected()
            descriptor = ObjectDescriptor(**json.loads(line))
            target.publish(source.read(descriptor), media_type=descriptor.media_type, schema_version=descriptor.schema_version)
    diagnostics.unlink()
    return {'status': 'restored_not_ready', 'backup_id': body['backup_id'], 'schema': body['schema'],
            'objects': body['objects'], 'authority_checkpoint_required': True, 'sessions_must_be_revoked': True,
            'database_public_connect_revoked': True}
def _verify_bundle_objects(bundle: Path, body: dict[str, Any]) -> None:
    _regular(bundle / 'objects', directory=True)
    _regular(bundle / 'objects/.k05-object-store')
    store = FilesystemObjectStore(bundle / 'objects')
    count = byte_count = 0
    with (bundle / 'objects.jsonl').open('rb') as handle:
        previous = ''
        for line in handle:
            if len(line) > 4096:
                raise RecoveryRejected()
            row = json.loads(line)
            descriptor = ObjectDescriptor(**row)
            if descriptor.sha256 <= previous:
                raise RecoveryRejected()
            store.read(descriptor)
            previous = descriptor.sha256
            count += 1
            byte_count += descriptor.byte_size
    if (count, byte_count) != (body['objects'], body['object_bytes']):
        raise RecoveryRejected()

def _validate_backup_metadata(body: dict[str, Any]) -> None:
    if not isinstance(body['backup_id'], str) or re.fullmatch('[0-9a-f]{32}', body['backup_id']) is None:
        raise RecoveryRejected()
    if not isinstance(body['scope'], str) or not 1 <= len(body['scope']) <= 1024:
        raise RecoveryRejected()
    _manifest_stamp(body['snapshot_at'])
    _manifest_count(body['objects'])
    _manifest_count(body['object_bytes'])

