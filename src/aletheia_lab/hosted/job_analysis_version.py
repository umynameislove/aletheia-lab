"""Pin analysis behavior in each durable request without changing the wire DTO."""

ANALYSIS_VERSION_DDL = """
ALTER TABLE j01_jobs DROP CONSTRAINT k05_job_request_shape_v6;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_request_shape_v6 CHECK(jsonb_typeof(request)='object' AND
 CASE WHEN operation='refresh' THEN
  request ?& ARRAY['project_id','parent_snapshot_id','expected_revision','source_id','origin_source_sha256']
  AND request-ARRAY['project_id','parent_snapshot_id','expected_revision','source_id','origin_source_sha256']='{}'::jsonb
  AND jsonb_typeof(request->'project_id')='string' AND refresh_project_id~'^p3-project-[0-9a-f]{64}$'
  AND jsonb_typeof(request->'parent_snapshot_id')='string' AND parent_snapshot_id~'^upload-snapshot-[0-9a-f]{64}$'
  AND jsonb_typeof(request->'source_id')='string' AND refresh_source_id~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
  AND jsonb_typeof(request->'origin_source_sha256')='string' AND request->>'origin_source_sha256'~'^[0-9a-f]{64}$'
  AND CASE WHEN jsonb_typeof(request->'expected_revision')='number'
   AND (request->'expected_revision')::text~'^(0|[1-9][0-9]*)$'
   THEN (request->>'expected_revision')::numeric BETWEEN 0 AND 9223372036854775806 ELSE false END
 WHEN operation='follow_up' THEN
  request ?& ARRAY['project_id','parent_result_id','expected_revision','selection_kind','selection_id','question']
  AND request-ARRAY['project_id','parent_result_id','expected_revision','selection_kind','selection_id','question']='{}'::jsonb
  AND jsonb_typeof(request->'project_id')='string' AND canonical_project_id~'^p3-project-[0-9a-f]{64}$'
  AND jsonb_typeof(request->'parent_result_id')='string' AND parent_result_id~'^p6-result-[0-9a-f]{64}$'
  AND CASE WHEN jsonb_typeof(request->'expected_revision')='number'
   AND (request->'expected_revision')::text~'^(0|[1-9][0-9]*)$'
   THEN (request->>'expected_revision')::numeric BETWEEN 0 AND 9223372036854775807 ELSE false END
  AND jsonb_typeof(request->'selection_kind')='string' AND request->>'selection_kind' IN ('claim','node')
  AND jsonb_typeof(request->'selection_id')='string' AND length(request->>'selection_id') BETWEEN 1 AND 128
  AND jsonb_typeof(request->'question')='string' AND length(request->>'question') BETWEEN 1 AND 4000
 WHEN operation='analyze' THEN snapshot_id IS NOT NULL AND jsonb_typeof(request->'snapshot_id')='string'
  AND request ?& ARRAY['snapshot_id','question']
  AND request-ARRAY['snapshot_id','question','analysis_model']='{}'::jsonb
  AND jsonb_typeof(request->'question')='string' AND length(request->>'question') BETWEEN 1 AND 4000
  AND (NOT request ? 'analysis_model' OR (jsonb_typeof(request->'analysis_model')='string'
   AND request->>'analysis_model' IN ('upload-snapshot-observation/v1','upload-snapshot-metric-observation/v2')))
 ELSE source_id IS NOT NULL AND jsonb_typeof(request->'source_id')='string' END);
"""
