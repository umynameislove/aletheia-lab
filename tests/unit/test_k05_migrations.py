from __future__ import annotations

import asyncio

import pytest

from aletheia_lab.hosted.persistence_migration import (
    PersistenceMigrationRejected,
    migrate,
    migration_schema,
)


@pytest.mark.parametrize('value', ['public', 'pg_temp', 'information_schema', '', 'x;DROP SCHEMA x', 'X', 'x' * 64, None])
def test_rejects_unsafe_schema_before_sql(value):
    with pytest.raises(PersistenceMigrationRejected):
        migration_schema(value)


def test_requires_transaction_before_sql():
    class Connection:
        autocommit = True
        async def execute(self, *args):
            raise AssertionError('No SQL allowed before transaction validation')
    with pytest.raises(PersistenceMigrationRejected):
        asyncio.run(migrate(Connection(), 'k05_test'))


def test_v1_through_v7_migration_checksums_are_frozen_when_appending_new_versions():
    import hashlib

    from aletheia_lab.hosted.persistence_migration import MIGRATIONS

    assert [hashlib.sha256(ddl.encode()).hexdigest() for ddl in MIGRATIONS[:7]] == [
        '14d20cd30f19721fdef7040de98ce3fb8ec3529e37a4044d39c749c0087dee02',
        '02de9ef1e42a70a22e6a58780a129c933a126065f1362bee1db942890558f909',
        '7adfd5cc1a634d4af0e066dba37b9d05803b9bc6a2d118dbbc88a1e4a51dc3f1',
        'ef58d1ae91ed8d639ebea91beea1d93526448786e122f723c3c2b984833a67b4',
        'a1df129ca809eb6f7a7ee8e375c1d4cbe11a18b4ef3036333c14b4c22020bc21',
        '5ddd9335d7ec3bd5215f124534f7647f0791038c7f1c8866ce0165f68eb92d54',
        '54345cefb305b31e62c0babbfcc33e6e6a4e4436a14ec160e8f19eed581ef303',
    ]
    assert len(MIGRATIONS) >= 7
