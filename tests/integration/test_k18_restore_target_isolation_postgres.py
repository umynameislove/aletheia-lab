"""An empty row catalog alone is not a fresh, owner-only restore target."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from aletheia_lab.hosted import recovery_points
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.recovery_points import RecoveryRejected, _canonical, restore_bundle

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K18_TEST_DATABASE_URL'),
    reason='Explicit private K18 PostgreSQL database-owner permission required')]
KEY = b'synthetic-k18-review-signing-key-not-a-deployment-secret'


def _bundle(root):
    root.mkdir()
    FilesystemObjectStore(root / 'objects')
    (root / 'database.dump').write_bytes(b'No archive execution is needed for target preflight tests.')
    (root / 'objects.jsonl').write_bytes(b'')
    body = {'format': 'hosted-recovery-bundle/v1', 'backup_id': 'a' * 32,
        'schema': 'restored_app', 'snapshot_at': '2026-11-13T00:00:00+00:00',
        'objects': 0, 'object_bytes': 0, 'scope': 'Signed synthetic preflight fixture only.',
        'files': {name: {'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest(),
                         'bytes': (root / name).stat().st_size} for name in ('database.dump', 'objects.jsonl')}}
    (root / 'manifest.json').write_bytes(_canonical({'manifest': body,
        'hmac_sha256': hmac.digest(KEY, _canonical(body), 'sha256').hex()}))
    return root


@contextmanager
def _target():
    database = os.environ['K18_TEST_DATABASE_URL']
    name = 'k18_isolation_' + uuid4().hex
    values = conninfo_to_dict(database)
    values.update(dbname=name)
    values.pop('options', None)
    target = make_conninfo(**values)
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            yield target, name, admin
        finally:
            admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (name,))
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


@pytest.mark.parametrize('preexisting', ['public_security_definer', 'empty_custom_schema'])
def test_restore_rejects_preexisting_routine_or_namespace_before_archive_execution(tmp_path, monkeypatch, preexisting):
    with _target() as (target, _, _):
        with psycopg.connect(target) as conn:
            if preexisting == 'public_security_definer':
                conn.execute("CREATE FUNCTION public.leak_restored_credentials() RETURNS SETOF text LANGUAGE plpgsql SECURITY DEFINER "
                    "AS $$ BEGIN RETURN QUERY EXECUTE 'SELECT digest FROM restored_app.j01_session_credentials'; END $$")
                assert conn.execute("SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                    "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
                    "WHERE p.oid='public.leak_restored_credentials()'::regprocedure "
                    "AND acl.grantee=0 AND acl.privilege_type='EXECUTE')").fetchone() == (True,)
            else:
                conn.execute('CREATE SCHEMA preexisting_empty')
            assert conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','S')").fetchone() == (0,)

        def forbidden_tool(*args, **options):
            raise AssertionError('An unsafe target reached external archive execution.')
        monkeypatch.setattr(recovery_points, '_tool', forbidden_tool)
        with pytest.raises(RecoveryRejected):
            restore_bundle(target, _bundle(tmp_path / 'bundle'), tmp_path / 'restored',
                pg_bin=Path('/unused'), signing_key=KEY)
        assert not (tmp_path / 'restored').exists()


def test_restore_seals_nonowner_connection_before_archive_execution_and_checkpoint(tmp_path, monkeypatch):
    class ReachedSealedArchive(Exception):
        pass

    with _target() as (target, name, admin):
        role, password = 'k18_read_' + uuid4().hex, secrets.token_urlsafe(32)
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(password)))
        try:
            values = conninfo_to_dict(target)
            values.update(user=role, password=password)
            reader = make_conninfo(**values)
            with psycopg.connect(target) as owner:
                assert owner.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')", (role,)).fetchone() == (True,)

            def sealed_tool(*args, **options):
                with psycopg.connect(target) as owner:
                    assert owner.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT'),"
                        "has_database_privilege(%s,current_database(),'TEMP')", (role, role)).fetchone() == (False, False)
                with pytest.raises(psycopg.OperationalError):
                    psycopg.connect(reader, connect_timeout=2)
                raise ReachedSealedArchive
            monkeypatch.setattr(recovery_points, '_tool', sealed_tool)
            with pytest.raises(ReachedSealedArchive):
                restore_bundle(target, _bundle(tmp_path / 'bundle'), tmp_path / 'restored',
                    pg_bin=Path('/unused'), signing_key=KEY)
            assert admin.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (role, name)).fetchone() == (False,)
        finally:
            # Database default/public ACLs contain no direct dependency on this role.
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
