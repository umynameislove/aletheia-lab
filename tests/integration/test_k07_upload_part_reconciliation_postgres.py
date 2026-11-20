"""A part's unknown outcome remains unknown until fresh authorized DB evidence.

Faults are controlled connection-context failures around actual PostgreSQL
commit/rollback. They do not claim to reproduce the Windows COMMIT stall.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager

import psycopg
import pytest
from pydantic import SecretStr
from test_k07_upload_sizes_postgres import _physical_bundle
from test_k07_upload_sizes_postgres import (
    test_real_physical_resumable_transfer_to_verified_source as _transfer_probe,
)
from test_k07_upload_staging_postgres import _create, _database, _digest, _manifest, _run, _sources

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.transactions import TransactionOutcomeUnknown
from aletheia_lab.hosted.upload_staging import UploadRejected

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K07_TEST_DATABASE_URL'),
    reason='Explicit private K07 native PostgreSQL URL required')]


class _ObservedPool:
    def __init__(self, pool):
        self.pool, self.part_inserts, self.part_payload_bytes = pool, 0, 0

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as actual:
            owner = self
            class Connection:
                autocommit = False

                async def execute(self, query, params=(), **options):
                    if 'INSERT INTO k07_upload_parts' in query:
                        owner.part_inserts += 1
                        owner.part_payload_bytes += sum(len(value) for value in params if isinstance(value, bytes))
                    return await actual.execute(query, params, **options)
            yield Connection()


@pytest.mark.parametrize('fault', ['after_commit', 'before_commit_rollback'])
def test_unknown_part_commit_reconciles_same_intent_without_payload_reinsertion(fault):
    async def run():
        async with _database() as (pool, _):
            observed = _ObservedPool(pool)
            payload = _physical_bundle(4 * 1024**2)
            manifest, parts = _manifest(payload, part_size=4 * 1024**2)

            def sources(target):
                return PostgresSources(target, maximum_upload_bytes=4 * 1024**2,
                    maximum_expanded_bytes=8 * 1024**2, maximum_entries=100)

            receipt = await sources(observed).create_upload(credential_digest=_digest(), workspace_id='a',
                request_key=SecretStr('synthetic-k07-unknown-part-key'), manifest=manifest)
            class LostAcknowledgement:
                entries = 0

                @asynccontextmanager
                async def connection(self):
                    self.entries += 1
                    async with observed.connection() as connection:
                        yield connection
                        if fault == 'before_commit_rollback':
                            raise psycopg.OperationalError('synthetic context failure before actual commit')
                    raise psycopg.OperationalError('synthetic lost acknowledgement after actual commit')

            faulty = LostAcknowledgement()
            args = dict(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id,
                        part_number=1, payload=parts[0])
            with pytest.raises(TransactionOutcomeUnknown):
                await sources(faulty).put_upload_part(**args)
            assert faulty.entries == observed.part_inserts == 1  # Never automatically retry unknown.
            current = sources(observed)  # Reconstructed repository, same retained intent.
            status = await current.upload_status(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            assert status.received_parts == ([1] if fault == 'after_commit' else [])
            replayed = await current.put_upload_part(**args)
            assert replayed.received_parts == [1] and replayed.received_bytes == len(payload)
            expected_inserts = 1 if fault == 'after_commit' else 2
            assert observed.part_inserts == expected_inserts
            assert observed.part_payload_bytes == expected_inserts * len(payload)
            assert await current.put_upload_part(**args) == replayed
            assert observed.part_inserts == expected_inserts
            async with pool.connection() as conn:
                rows = await (await conn.execute('SELECT byte_size,sha256,payload FROM k07_upload_parts '
                    'WHERE workspace_id=%s AND upload_id=%s', ('a', receipt.upload_id), binary=True)).fetchall()
            assert len(rows) == 1 and rows[0][0] == len(payload) and rows[0][1] == hashlib.sha256(rows[0][2]).hexdigest()
            assert rows[0][2] == payload
            ready = await current.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            assert ready.state == 'ready' and ready.sha256 == hashlib.sha256(payload).hexdigest()
            assert await current.read(credential_digest=_digest(), workspace_id='a', source_id=ready.source_id) == payload
            assert await current.put_upload_part(**args) == ready
            assert observed.part_inserts == expected_inserts
    _run(run())


@pytest.mark.parametrize('loss', ['revoked', 'expired', 'cancelled', 'stored_metadata_conflict'])
def test_committed_part_fast_replay_preserves_current_authority_state_and_integrity(loss):
    async def run():
        async with _database() as (pool, _):
            observed = _ObservedPool(pool)
            current = _sources(observed)
            receipt, parts = await _create(current, b'synthetic bounded source bytes', part_size=100)
            args = dict(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id,
                        part_number=1, payload=parts[0])
            await current.put_upload_part(**args)
            assert observed.part_inserts == 1
            if loss == 'cancelled':
                await current.cancel_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            else:
                async with pool.connection() as conn:
                    if loss == 'revoked':
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='analyst'")
                    elif loss == 'expired':
                        await conn.execute("UPDATE k07_uploads SET created_at=clock_timestamp()-interval '2 days',"
                            "expires_at=clock_timestamp()-interval '1 second' WHERE upload_id=%s", (receipt.upload_id,))
                    else:
                        await conn.execute("UPDATE k07_upload_parts SET sha256=%s WHERE upload_id=%s", ('0' * 64, receipt.upload_id))
            expected = AuthorizationDenied if loss == 'revoked' else UploadRejected
            with pytest.raises(expected) as caught:
                await current.put_upload_part(**args)
            if loss != 'revoked':
                assert caught.value.code == {'expired': 'upload_expired', 'cancelled': 'upload_conflict',
                    'stored_metadata_conflict': 'upload_part_conflict'}[loss]
            assert observed.part_inserts == 1
    _run(run())


def test_part_failure_diagnostic_observes_committed_metadata_without_hiding_unknown(tmp_path, monkeypatch):
    import test_k07_upload_sizes_postgres as transfer

    original = transfer.PostgresSources
    failure = TransactionOutcomeUnknown()
    calls = []

    class LostPartResult(original):
        async def put_upload_part(self, **arguments):
            calls.append(True)
            await super().put_upload_part(**arguments)
            raise failure  # Controlled result loss after a real commit; the outer test must still fail.

    monkeypatch.setattr(transfer, 'PostgresSources', LostPartResult)
    monkeypatch.setenv('K07_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    with pytest.raises(TransactionOutcomeUnknown) as caught:
        _transfer_probe(1)
    assert caught.value is failure and len(calls) == 1
    saved = json.loads((tmp_path / 'evidence/transfer-1MiB_failure-result.json').read_text())
    assert saved['schema_version'] == 'k07-transfer-observation/v2' and saved['observation_kind'] == 'failure'
    assert saved['transaction_outcome_unknown'] and not saved['recovered_or_retried_unknown']
    assert saved['phase'] == {'name': 'part', 'part_number': 1}
    assert saved['last_acknowledged_bytes'] == 0 and not saved['current_part']['acknowledged']
    assert saved['current_part']['bytes'] == 1024**2
    assert saved['current_part']['sha256'] == saved['current_part']['expected_sha256']
    metadata = saved['read_only_failure_metadata']
    assert metadata['available'] and metadata['original_failure_retained'] and not metadata['automatic_replay']
    assert metadata['target_part_present_observed'] and metadata['received_part_count_observed'] == 1
    assert metadata['target_part_bytes_observed'] == metadata['received_bytes_observed'] == 1024**2
    assert metadata['target_part_sha256_observed'] == saved['current_part']['sha256']
    assert metadata['absence_is_not_a_rollback_or_commit_certificate']
    assert saved['connection_timings'][-1]['body_returned']
    assert saved['connection_timings'][-1]['context_returned']
