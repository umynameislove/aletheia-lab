import asyncio
import base64
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from aletheia_lab.hosted.windows_worker import WindowsUploadWorker


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows event-loop integration')
def test_selector_loop_uses_proactor_worker_and_closes(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('dataset.csv', 'id,target\n1,0\n2,1\n')
        archive.writestr('metrics.csv', 'run,name,value\nbaseline,loss,0.5\n')
    payload = json.dumps({'operation':'preview','source_base64':base64.b64encode(data.getvalue()).decode(),
                          'captured_at':'2026-11-10T00:00:00Z'}).encode()
    async def scenario():
        worker = WindowsUploadWorker(python=Path(sys.executable),source_root=Path(__file__).resolve().parents[2]/'src',
                                     private_root=tmp_path,slots=1,timeout_seconds=30)
        try:
            result = json.loads(await worker.run_upload(payload))
            assert result['mapping_candidates']['targets']
        finally:
            await worker.aclose()
        assert not worker._thread.is_alive()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())
