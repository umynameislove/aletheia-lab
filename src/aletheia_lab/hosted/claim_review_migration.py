"""Append-only migration for independently retained claim review receipts."""
from .job_analysis_version import HISTORICAL_STATE_ANALYSIS_VERSION_DDL

_REVIEW_REQUEST = """
 WHEN operation='review_claim' THEN COALESCE(
  request ?& ARRAY['workspace_id','project_id','snapshot_id','result_id','claim_id','source_id',
  'source_sha256','result_sha256','claim_sha256','evidence_sha256','receipt_sha256','contract_sha256',
  'asserted_verdict','policy','candidate_sha256']
  AND request->>'workspace_id'=workspace_id
  AND request->>'project_id'~'^p3-project-[0-9a-f]{64}$'
  AND request->>'result_id'~'^p6-result-[0-9a-f]{64}$'
  AND request->>'claim_id'~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
  AND request->>'asserted_verdict' IN ('allowed','denied')
  AND request->>'policy'='historical-state-authorization/v1'
  AND request->>'candidate_sha256'~'^[0-9a-f]{64}$',false)
"""

CLAIM_REVIEW_DDL = HISTORICAL_STATE_ANALYSIS_VERSION_DDL.replace(
    " WHEN operation='follow_up' THEN", _REVIEW_REQUEST + " WHEN operation='follow_up' THEN",
) + """
ALTER TABLE j01_jobs DROP CONSTRAINT k05_job_operation_v6;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_operation_v6 CHECK(operation IN
 ('preview','confirm','analyze','follow_up','refresh','review_claim'));
ALTER TABLE k09_queue_turns DROP CONSTRAINT k09_queue_turns_operation_check;
ALTER TABLE k09_queue_turns ADD CONSTRAINT k09_queue_turns_operation_check CHECK(operation IN
 ('preview','confirm','analyze','follow_up','refresh','review_claim'));
CREATE TABLE k20_claim_reviews (
 workspace_id text NOT NULL,job_id text NOT NULL,project_id text NOT NULL,
 result_id text NOT NULL,claim_id text NOT NULL,object_sha256 text NOT NULL,binding jsonb NOT NULL,
 PRIMARY KEY(workspace_id,job_id),
 CHECK(job_id~'^job_[0-9a-f]{32}$' AND claim_id~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
 CHECK((binding->>'workspace_id'=workspace_id AND binding->>'project_id'=project_id
  AND binding->>'result_id'=result_id AND binding->>'claim_id'=claim_id) IS TRUE),
 FOREIGN KEY(workspace_id,project_id,result_id) REFERENCES k05_records(workspace_id,project_id,record_id),
 FOREIGN KEY(workspace_id,result_id,object_sha256)
  REFERENCES k05_object_references(workspace_id,record_id,sha256) ON DELETE CASCADE);
CREATE INDEX k20_claim_review_parent ON k20_claim_reviews(workspace_id,project_id,result_id);
"""
