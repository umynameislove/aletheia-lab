import asyncio
import hashlib
import json

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.upload_operations import UploadOperations


def test_preview_rechecks_access_and_rejects_worker_source_mismatch():
    class Sources:
        def __init__(self, fail_second=False):
            self.calls = 0
            self.fail_second = fail_second
        async def read(self, **kwargs):
            self.calls += 1
            if self.fail_second and self.calls == 2:
                raise SourceNotFound()
            return b'synthetic'
    class Worker:
        def __init__(self, digest):
            self.digest = digest
        async def run_upload(self, payload):
            return json.dumps({'source_sha256': self.digest, 'source_state_sha256': 'a'*64,
                               'mapping_candidates': {}}).encode()
    async def run():
        args = dict(credential_digest=SecretStr('a'*64), workspace_id='ws1', source_id='src1')
        sources = Sources()
        result = await UploadOperations(sources, Worker(hashlib.sha256(b'synthetic').hexdigest())).preview(**args)
        assert result.source_sha256 == hashlib.sha256(b'synthetic').hexdigest() and sources.calls == 2
        with pytest.raises(SourceNotFound):
            await UploadOperations(Sources(True), Worker('a'*64)).preview(**args)
        with pytest.raises(ValueError):
            await UploadOperations(Sources(), Worker('a'*64)).preview(**args)
    asyncio.run(run())
