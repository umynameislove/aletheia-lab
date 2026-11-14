from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import Authorization
from aletheia_lab.hosted.import_errors import MappingInvalid
from aletheia_lab.hosted.imports_http import LeasedJobs
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.preview_leases import PreviewExpired
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.hosted.upload_operations import UploadOperations
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt


class QueueDouble:
    """Existing queue/ledger projection only; preview policy belongs to the wrapper."""
    _receipt = staticmethod(PostgresJobs._receipt)

    def __init__(self, request=None):
        self.request = request
        self.ledger = {}
        self.retry_calls = 0
        self.status_calls = 0
        self.revoked = False
        owner = self

        class Cursor:
            def __init__(self, row):
                self.row = row

            async def fetchone(self):
                return self.row

        class Connection:
            async def execute(self, query, params):
                assert query.startswith('SELECT job_id,request_hash,request_id')
                return Cursor(owner.ledger.get(params[-1]))

        class Pool:
            @asynccontextmanager
            async def connection(self):
                yield Connection()

        self._pool = Pool()

    async def _authorize(self, *args):
        if self.revoked:
            raise SourceNotFound()
        return Authorization('actor', 'analyst', 1, 'wa')

    async def _control_job(self, conn, digest, workspace_id, job_id):
        return await self._authorize(), ('failed', 'actor', 'confirm', self.request)

    async def enqueue(self, **kwargs):
        raise AssertionError('Invalid preview reached queue admission.')

    async def retry(self, **kwargs):
        self.retry_calls += 1
        # Match the independently specified original queue fingerprint below.
        fingerprint = '{"request":{"mapping":{"_preview_id":"prev_original"},"source_id":"sa"},"retry_of_job_id":"job_original"}'
        row = ('job_retried', hashlib.sha256(fingerprint.encode()).hexdigest(), 'req_retried')
        self.ledger[hashlib.sha256(kwargs['request_key'].get_secret_value().encode()).hexdigest()] = row
        return self._receipt('wa', row, row[1])

    async def status(self, **kwargs):
        self.status_calls += 1
        await self._authorize()
        return Job(schema_version='product-api/v1-draft', workspace_id='wa', job_id='job_failed', status='failed', progress=None)

    async def output(self, **kwargs):
        raise AssertionError('Failed diagnostics must not become a successful job output.')


def test_worker_path_is_private_bounded_and_removed_after_success_and_failure(tmp_path):
    data = b'synthetic immutable bytes'
    sha = hashlib.sha256(data).hexdigest()
    paths = []

    class Sources:
        async def read(self, **kwargs):
            return data

    class Worker:
        def __init__(self, failed=False):
            self.failed = failed

        async def run_upload(self, payload):
            request = json.loads(payload)
            assert 'source_base64' not in request
            path = Path(request['source_path'])
            assert path.parent == tmp_path / 'inputs' and path.read_bytes() == data
            paths.append(path)
            if self.failed:
                raise OSError('synthetic private worker failure')
            return json.dumps({'source_sha256': sha, 'source_state_sha256': 'b' * 64,
                               'mapping_candidates': {}}).encode()

    async def run():
        args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa', source_id='sa')
        preview = await UploadOperations(Sources(), Worker(), private_root=tmp_path).preview(**args)
        assert preview.source_sha256 == sha and all(not path.exists() for path in paths)
        with pytest.raises(OSError):
            await UploadOperations(Sources(), Worker(True), private_root=tmp_path).preview(**args)
        assert all(not path.exists() for path in paths)
    asyncio.run(run())


def test_safe_mapping_error_does_not_reflect_worker_exception_or_arbitrary_fields():
    valid = {'schema_version': 'hosted-upload-error/v1', 'code': 'mapping_invalid',
             'issues': [{'code': 'missing_metric', 'field': 'metric_definitions[0].metric_name'}]}
    with pytest.raises(MappingInvalid) as caught:
        UploadOperations._worker_result(json.dumps(valid).encode())
    assert caught.value.failure.model_dump(exclude_none=True) == valid
    with pytest.raises(ValueError):
        MappingInvalid({**valid, 'raw_source': 'private'})


def test_confirm_requires_preview_and_expiry_prevents_durable_enqueue():
    class Leases:
        async def validate(self, **kwargs):
            raise PreviewExpired()

    async def run():
        jobs = LeasedJobs(QueueDouble(), Leases(), None)
        args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa',
                    operation='confirm', request_key=SecretStr('synthetic-key-0001'))
        for mapping, expected in (({}, 422), ({'_preview_id': 'prev_' + 'a' * 32}, 410)):
            with pytest.raises(RequestRejected) as caught:
                await jobs.enqueue(**args, request={'source_id': 'sa', 'mapping': mapping})
            assert caught.value.status == expected
    asyncio.run(run())


def test_confirm_retry_replays_accepted_key_after_expiry_but_new_key_is_rejected():
    class Leases:
        expired = False
        validations = 0

        async def validate(self, **kwargs):
            self.validations += 1
            if self.expired:
                raise PreviewExpired()

    async def run():
        queue, leases = QueueDouble({'source_id': 'sa', 'mapping': {'_preview_id': 'prev_original'}}), Leases()
        jobs = LeasedJobs(queue, leases, None)
        args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa', job_id='job_original')
        key = SecretStr('synthetic-retry-key-001')
        receipt = await jobs.retry(**args, request_key=key)
        assert isinstance(receipt, JobReceipt) and queue.retry_calls == 1 and leases.validations == 1
        leases.expired = True
        assert await jobs.retry(**args, request_key=key) == receipt
        assert queue.retry_calls == 1 and leases.validations == 1
        with pytest.raises(RequestRejected) as caught:
            await jobs.retry(**args, request_key=SecretStr('synthetic-new-retry-key-001'))
        assert caught.value.status == 410 and queue.retry_calls == 1
        queue.revoked = True
        with pytest.raises(SourceNotFound):
            await jobs.retry(**args, request_key=key)

    asyncio.run(run())


@pytest.mark.parametrize('revoke_during_read', [False, True])
def test_failed_mapping_diagnostics_recheck_current_authority(revoke_during_read):
    async def run():
        queue = QueueDouble()
        failure = {'schema_version': 'hosted-upload-error/v1', 'code': 'mapping_invalid',
                   'issues': [{'code': 'missing_metric', 'field': 'metric_definitions[0].metric_name'}]}

        class Failures:
            async def read(self, **kwargs):
                queue.revoked = revoke_during_read
                return failure

        jobs = LeasedJobs(queue, None, Failures())
        args = dict(credential_digest=SecretStr('a' * 64), workspace_id='wa', job_id='job_failed')
        if revoke_during_read:
            with pytest.raises(SourceNotFound):
                await jobs.output(**args)
        else:
            assert (await jobs.output(**args))['result'] == failure
        assert queue.status_calls == 2

    asyncio.run(run())
