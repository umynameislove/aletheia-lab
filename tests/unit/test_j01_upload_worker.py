import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor, WorkerRejected


def test_worker_process_preview_and_confirm(tmp_path):
    source = io.BytesIO()
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('dataset.csv', 'id,target\n1,0\n2,1\n')
        archive.writestr('metrics.csv', 'run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n')
        archive.writestr('config.json', '{"seed":42}\n')
    env = {key: os.environ[key] for key in ('SYSTEMROOT','WINDIR','TEMP','TMP') if key in os.environ}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    def run(value):
        return subprocess.run([sys.executable,'-B','-m','aletheia_lab.hosted.upload_worker',str(tmp_path)],
            input=json.dumps(value).encode(),capture_output=True,env=env,timeout=30)
    request={'operation':'preview','source_base64':base64.b64encode(source.getvalue()).decode(),
             'captured_at':'2026-11-10T00:00:00Z'}
    preview=run(request)
    assert preview.returncode==0, preview.stderr
    candidates=json.loads(preview.stdout)['mapping_candidates']
    target,metric,config=(candidates[key][0] for key in ('targets','metric_sources','configs'))
    mapping={'target':{'mapping_id':target['mapping_id'],'project_item_id':target['project_item_id'],'target_field':'target','identifier_field':'id'},
        'metric_sources':[{'mapping_id':metric['mapping_id'],'project_item_id':metric['project_item_id'],
            'format':'csv','records_path':[],'metric_name_field':'name','metric_value_field':'value','run_id_field':'run','step_field':None}],
        'runs':[{'run_id':'baseline','config_item_ids':[]},{'run_id':'candidate','config_item_ids':[config['project_item_id']]}],
        'baseline_run_id':'baseline','metric_definitions':[{'metric_name':'loss','direction':'lower_is_better','regression_threshold':0.08}]}
    confirmed=run({**request,'operation':'confirm','workspace_id':'ws1','source_id':'src1','mapping':mapping})
    assert confirmed.returncode==0, confirmed.stderr
    snapshot = json.loads(confirmed.stdout)
    assert snapshot['git_provenance']=='not_supplied'
    analysis = run({'operation': 'analyze', 'snapshot': snapshot, 'question': 'What is observed?'})
    assert analysis.returncode == 0, analysis.stderr
    view = json.loads(analysis.stdout)
    assert view['schema_version'] == 'p6-product-view/v1'
    assert view['snapshot']['id'] == snapshot['snapshot_id']
    assert view['result']['disposition'] == 'abstain'
    bad=run({**request,'source_base64':'not-base64!'})
    assert bad.returncode==2 and not bad.stdout and bad.stderr.strip()==b'worker_failed'


def test_supervisor_runs_upload_worker_and_rejects_bad_input(tmp_path):
    source = io.BytesIO()
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('dataset.csv', 'id,target\n1,0\n2,1\n')
        archive.writestr('metrics.csv', 'run,name,value\nbaseline,loss,0.5\n')
    request = json.dumps({'operation':'preview','source_base64':base64.b64encode(source.getvalue()).decode(),
                          'captured_at':'2026-11-10T00:00:00Z'}).encode()

    async def run():
        supervisor=UploadWorkerSupervisor(python=Path(sys.executable), source_root=Path(__file__).resolve().parents[2] / 'src',
                                           private_root=tmp_path, slots=1, timeout_seconds=30)
        try:
            result=json.loads(await supervisor.run_upload(request))
            assert result['mapping_candidates']['targets']
            with pytest.raises(WorkerRejected):
                await supervisor.run_upload(b'{invalid')
        finally:
            await supervisor.aclose()
    asyncio.run(run())
