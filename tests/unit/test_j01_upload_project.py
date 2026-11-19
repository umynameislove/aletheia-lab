import io
import zipfile

from aletheia_lab.hosted.upload_project import UploadProject


def test_real_zip_preview_confirm_and_owned_materialization_cleanup(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('dataset.csv', 'id,target\n1,0\n2,1\n')
        archive.writestr('metrics.csv', 'run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n')
        archive.writestr('config.json', '{"seed":42}\n')
    payload = buffer.getvalue()
    project = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    captured = '2026-11-10T00:00:00Z'
    preview = project.preview(payload, captured_at=captured)
    candidates = preview['mapping_candidates']
    target, metric, config = (candidates[key][0] for key in ('targets','metric_sources','configs'))
    mapping = {
        'target': {'mapping_id':target['mapping_id'],'project_item_id':target['project_item_id'],
                   'target_field':'target','identifier_field':'id'},
        'metric_sources':[{'mapping_id':metric['mapping_id'],'project_item_id':metric['project_item_id'],
            'format':'csv','records_path':[],'metric_name_field':'name','metric_value_field':'value',
            'run_id_field':'run','step_field':None}],
        'runs':[{'run_id':'baseline','config_item_ids':[]},
                {'run_id':'candidate','config_item_ids':[config['project_item_id']]}],
        'baseline_run_id':'baseline',
        'metric_definitions':[{'metric_name':'loss','direction':'lower_is_better','regression_threshold':0.08}],
    }
    snapshot = project.confirm(payload, workspace_id='ws1',source_id='src1',mapping=mapping,captured_at=captured)
    assert snapshot.git_provenance == 'not_supplied'
    assert {(row.run_id,row.metric_value) for row in snapshot.mapping_result.metric_observations} == {('baseline',0.5),('candidate',0.6)}
    assert project.preview(payload,captured_at=captured) == preview
    assert project.confirm(payload,workspace_id='ws1',source_id='src1',mapping=mapping,captured_at=captured) == snapshot
    assert list(tmp_path.iterdir()) == []
