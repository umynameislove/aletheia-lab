"""Opt-in migration/rollout checks on a dedicated disposable PostgreSQL container."""

import asyncio
import hmac
import os
import re
import subprocess
import sys
import traceback
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.abuse import AuthAdmission, RatePolicy
from aletheia_lab.hosted_auth.admission_migration import MigrationRejected, change_policy, migrate
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.repositories import RepositoryUnavailable


def _container(value):
    if not isinstance(value, str) or not re.fullmatch(r"aletheia-k03-rollout-[0-9a-f]{32}", value):
        raise ValueError("Dedicated K-03 rollout container required.")
    return value


def _db(*, user="k03_rollout", password=None, schema=None):
    port = int(os.environ["K03_ROLLOUT_PORT"])
    if not 1024 <= port <= 65535 or port == 15432:
        raise ValueError("Dedicated loopback database port required.")
    values = {"host": "127.0.0.1", "port": port, "dbname": "k03_rollout", "user": user,
              "password": password or os.environ["K03_ROLLOUT_PASSWORD"], "connect_timeout": 3}
    if schema is not None:
        values["options"] = "-c search_path=" + schema
    return values


async def _connect(**kwargs):
    import psycopg
    return await psycopg.AsyncConnection.connect(**_db(**kwargs))


async def _cli(schema, expected, *, policy_change=None):
    from psycopg.conninfo import make_conninfo

    def run():
        environment = dict(os.environ)
        environment["ALETHEIA_HOSTED_MIGRATION_DATABASE_URL"] = make_conninfo(**_db())
        target = environment.get("K03_ROLLOUT_INSTALLED_TARGET")
        cwd = None
        entry = ["-m", "aletheia_lab.hosted_auth.admission_migrate_cli"]
        if target is not None:
            root = Path(__file__).resolve().parents[2]
            environment["PYTHONPATH"] = os.pathsep.join([target, *(item for item in environment.get("PYTHONPATH", "").split(os.pathsep)
                if item and Path(item).resolve() != (root / "src").resolve())])
            entry = ["-c", "from pathlib import Path; import os; from aletheia_lab.hosted_auth import admission_migrate_cli as c; "
                     "assert Path(c.__file__).resolve().is_relative_to(Path(os.environ['K03_ROLLOUT_INSTALLED_TARGET']).resolve()); raise SystemExit(c.main())"]
            cwd = str(Path(target).parent)
        arguments = ["migrate"]
        if policy_change is not None:
            namespace, old, new = policy_change
            arguments = ["change-policy", "--namespace", namespace,
                "--expected", *(str(value) for value in (old.global_attempts, old.subject_attempts, old.window_seconds)),
                "--replacement", *(str(value) for value in (new.global_attempts, new.subject_attempts, new.window_seconds))]
        result = subprocess.run([sys.executable, "-B", *entry,
            "--schema", schema, *arguments], env=environment, capture_output=True, text=True, timeout=35,
            cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        assert result.returncode == 0
        assert result.stdout.strip() == "K03_ADMISSION_MIGRATION=" + expected
    await asyncio.to_thread(run)


async def _rows(schema):
    async with await _connect(schema=schema) as connection:
        return await (await connection.execute("SELECT * FROM k03_auth_admission ORDER BY namespace,operation")).fetchall()


async def _migration_cases(schema):
    async with await _connect() as connection:
        await connection.execute('CREATE SCHEMA "' + schema + '"')
    await _cli(schema, "CREATED")
    await asyncio.gather(_cli(schema, "UNCHANGED"), _cli(schema, "UNCHANGED"))
    async with await _connect(schema=schema) as connection:
        # The original unversioned DDL simulates the pre-migration release.
        await connection.execute('CREATE SCHEMA "' + schema + '_legacy"')
        ddl = (Path(__file__).resolve().parents[2] / "src/aletheia_lab/hosted_auth/admission_schema.sql").read_text()
        await connection.execute(ddl.replace("CREATE TABLE k03_auth_admission", 'CREATE TABLE "' + schema + '_legacy".k03_auth_admission'))
    from psycopg_pool import AsyncConnectionPool
    legacy = schema + "_legacy"
    async with AsyncConnectionPool(conninfo="", kwargs=_db(schema=legacy), min_size=1, max_size=2) as pool:
        store = PostgresAuthAdmission(pool, namespace="legacy")
        await store.prepare(RatePolicy(7, 3, 3600))
        assert (await store.consume("callback", SecretStr("a" * 64), RatePolicy(7, 3, 3600))).allowed
    before = await _rows(legacy)
    await _cli(legacy, "ADOPTED")
    assert await _rows(legacy) == before
    await _cli(legacy, "UNCHANGED")
    assert await _rows(legacy) == before
    print("K03_MIGRATION_FRESH_CONCURRENT_RERUN_AND_LEGACY_PRESERVE=PASS", flush=True)

    corruptions = {
        "checksum": "UPDATE k03_auth_admission_migrations SET checksum=repeat('0',64)",
        "future_version": "INSERT INTO k03_auth_admission_migrations (version,checksum) VALUES (2,repeat('0',64))",
        "missing_table": "DROP TABLE k03_auth_admission",
        "counter": "UPDATE k03_auth_admission SET subjects='{}' WHERE attempts>0",
        "rls": "ALTER TABLE k03_auth_admission ENABLE ROW LEVEL SECURITY",
        "index": "CREATE INDEX k03_unexpected ON k03_auth_admission(attempts)",
        "inheritance": "CREATE TABLE k03_unexpected_child () INHERITS (k03_auth_admission)",
        "trigger": "CREATE FUNCTION k03_block() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$; "
                   "CREATE TRIGGER k03_unexpected BEFORE UPDATE ON k03_auth_admission FOR EACH ROW EXECUTE FUNCTION k03_block()",
    }
    for query in corruptions.values():
        with pytest.raises(MigrationRejected):
            async with await _connect(schema=legacy) as connection:
                await connection.execute(query)
                await migrate(connection, legacy)
        assert await _rows(legacy) == before
    print("K03_MIGRATION_TAMPER_FUTURE_VERSION_AND_MISSING_TABLE_REJECTED=PASS", flush=True)

    with pytest.raises(RuntimeError, match="injected"):
        async with await _connect() as connection:
            await connection.execute('CREATE SCHEMA "' + schema + '_rollback"')
            await migrate(connection, schema + "_rollback")
            raise RuntimeError("injected")
    async with await _connect() as connection:
        row = await (await connection.execute("SELECT to_regnamespace(%s)", (schema + "_rollback",))).fetchone()
        assert row == (None,)
        await connection.execute('CREATE SCHEMA "' + schema + '_drift"')
        await connection.execute(ddl.replace("CREATE TABLE k03_auth_admission", 'CREATE TABLE "' + schema + '_drift".k03_auth_admission')
            .replace("global_limit BETWEEN 1 AND 10000", "global_limit BETWEEN 1 AND 20000"))
    with pytest.raises(MigrationRejected):
        async with await _connect() as connection:
            await migrate(connection, schema + "_drift")
    async with await _connect() as connection:
        row = await (await connection.execute("SELECT to_regclass(%s)", ('"' + schema + '_drift".k03_auth_admission_migrations',))).fetchone()
        assert row == (None,)
    print("K03_MIGRATION_ATOMIC_ROLLBACK_AND_SCHEMA_DRIFT=PASS", flush=True)


async def _wait_window(pool, namespace):
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT max((window_number+1)*window_seconds) FROM k03_auth_admission WHERE namespace=%s AND attempts>0", (namespace,))).fetchone()
        now = await (await connection.execute("SELECT extract(epoch FROM clock_timestamp())")).fetchone()
    if row[0] is not None:
        await asyncio.sleep(max(0, float(row[0]) - float(now[0])) + 0.03)


async def _policy_cases(schema):
    from psycopg_pool import AsyncConnectionPool

    old, new = RatePolicy(7, 3, 5), RatePolicy(10, 4, 5)
    async with AsyncConnectionPool(conninfo="", kwargs=_db(schema=schema), min_size=1, max_size=3) as pool:
        store = PostgresAuthAdmission(pool, namespace="rollout")
        await store.prepare(old)
        async with pool.connection() as connection:
            sampled = await (await connection.execute("SELECT extract(epoch FROM clock_timestamp())")).fetchone()
        await asyncio.sleep(5 - float(sampled[0]) % 5 + 0.03)
        assert (await store.consume("callback", SecretStr("a" * 64), old)).allowed
        before = await _rows(schema)
        with pytest.raises(MigrationRejected):
            async with await _connect() as connection:
                await change_policy(connection, schema, "rollout", expected=old, replacement=new)
        assert await _rows(schema) == before
        await _wait_window(pool, "rollout")
        await _cli(schema, "POLICY_CHANGED", policy_change=("rollout", old, new))
        await _cli(schema, "POLICY_UNCHANGED", policy_change=("rollout", new, new))
        assert not await store.is_ready(old) and await store.is_ready(new)
        with pytest.raises(RepositoryUnavailable):
            await AuthAdmission(store, old, timeout_seconds=2).check("callback", SecretStr("a" * 64))
        assert (await store.consume("callback", SecretStr("b" * 64), new)).allowed
        with pytest.raises(MigrationRejected):
            async with await _connect() as connection:
                await change_policy(connection, schema, "rollout", expected=new, replacement=old)
        await _wait_window(pool, "rollout")
        await _cli(schema, "POLICY_CHANGED", policy_change=("rollout", new, old))
        assert await store.is_ready(old) and not await store.is_ready(new)
    print("K03_ROLLOUT_ACTIVE_WINDOW_BLOCKED_OLD_WORKER_DENIED_AND_SAFE_ROLLBACK=PASS", flush=True)


def _api_password(role):
    return hmac.digest(os.environ["K03_ROLLOUT_PASSWORD"].encode(), role.encode(), "sha256").hex()


async def _api_role(schema, role, window):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    async with await _connect() as connection:
        await connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(_api_password(role))))
        await connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
        await connection.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON {}.k03_auth_admission TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
    async with AsyncConnectionPool(conninfo="", kwargs=_db(schema=schema, user=role, password=_api_password(role)), min_size=1, max_size=2) as pool:
        store = PostgresAuthAdmission(pool, namespace="api")
        await store.prepare(RatePolicy(7, 3, window))
        for _ in range(3):
            assert (await store.consume("callback", SecretStr("c" * 64), RatePolicy(7, 3, window))).allowed
        assert not (await store.consume("callback", SecretStr("c" * 64), RatePolicy(7, 3, window))).allowed
    for query in ("CREATE TABLE forbidden_test (n integer)", "ALTER TABLE k03_auth_admission ADD COLUMN forbidden integer",
                  "SELECT * FROM k03_auth_admission_migrations"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with await _connect(schema=schema, user=role, password=_api_password(role)) as connection:
                await connection.execute(query)
    print("K03_ROLLOUT_API_ROLE_DML_WITHOUT_SCHEMA_OR_LEDGER_CONTROL=PASS", flush=True)


async def _restart_and_check(schema, role, window):
    import psycopg
    from psycopg_pool import AsyncConnectionPool

    container = _container(os.environ["K03_ROLLOUT_CONTAINER"])
    await asyncio.to_thread(subprocess.run, ["docker", "restart", container], check=True, capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    published = await asyncio.to_thread(subprocess.run, ["docker", "port", container, "5432/tcp"],
        check=True, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
    binding = re.fullmatch(r"127\.0\.0\.1:(\d+)", published.stdout.strip())
    if binding is None:
        raise RuntimeError("Unexpected restarted database binding.")
    if os.environ["K03_ROLLOUT_PORT"] != binding[1]:
        print("K03_ROLLOUT_EPHEMERAL_PORT_CHANGED_AFTER_RESTART=TRUE", flush=True)
    os.environ["K03_ROLLOUT_PORT"] = binding[1]
    for _ in range(30):
        try:
            async with await _connect() as connection:
                await connection.execute("SELECT 1")
            break
        except psycopg.OperationalError as error:
            if error.sqlstate is not None:
                raise
            await asyncio.sleep(0.5)
    else:
        raise RuntimeError("Dedicated database restart did not recover.")
    await _cli(schema, "UNCHANGED")
    async with AsyncConnectionPool(conninfo="", kwargs=_db(schema=schema, user=role, password=_api_password(role)), min_size=1, max_size=2) as pool:
        store = PostgresAuthAdmission(pool, namespace="api")
        policy = RatePolicy(7, 3, window)
        assert await store.is_ready(policy)
        assert not (await store.consume("callback", SecretStr("c" * 64), policy)).allowed
    print("K03_ROLLOUT_DATABASE_RESTART_PRESERVES_LEDGER_QUOTA_AND_API_GRANTS=PASS", flush=True)


async def _live():
    schema = "k03_rollout_" + uuid.uuid4().hex
    role = "k03_api_" + uuid.uuid4().hex
    await _migration_cases(schema)
    await _policy_cases(schema)
    async with await _connect() as connection:
        sampled = await (await connection.execute("SELECT extract(epoch FROM clock_timestamp())")).fetchone()
    epoch = int(sampled[0])
    window = next(value for value in range(3000, 3601) if value - epoch % value > 180)
    await _api_role(schema, role, window)
    await _restart_and_check(schema, role, window)
    print("K03_MIGRATION_AND_LOCAL_ROLLOUT=PASS", flush=True)


@pytest.mark.parametrize("name", ["aletheia-j01-postgres-review", "postgres", "aletheia-k03-rollout-bad", None])
def test_restart_refuses_any_non_owned_container(name):
    with pytest.raises(ValueError):
        _container(name)


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(_live())
    except Exception as error:
        print("K03_ROLLOUT_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_ROLLOUT_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
