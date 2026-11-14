"""Explicit, transactional hosted schema migration; no startup DDL or down migration.

V1 adopts the original identity/session/source/job schema. V2 adds scoped
canonical persistence, object-ready references and purpose-bound indexes. V3
adds nullable same-workspace retry lineage and the cancelled job terminal. V4
indexes the workspace-wide bounded record list. V5 links typed native upload
records without backfilling legacy rows. V6 reuses durable jobs for follow-ups.
V7 retires pre-clock login claims before adding an authoritative database stamp. Local
SQLite remains local; a migrated project uses this hosted catalog exclusively.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final, TypeAlias

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .job_analysis_version import ANALYSIS_VERSION_DDL
from .job_execution_schema import JOB_CONFLICT_DDL, JOB_EXECUTION_DDL
from .job_updates_schema import JOB_UPDATES_DDL
from .read_indexes import ReadIndexesRejected, install_if_possible, verified_extension
from .read_projection_migration import READ_PROJECTION_DDL, READ_PROJECTION_TABLES
from .retention_schema import RETENTION_DDL, RETENTION_TABLES

_Shape: TypeAlias = tuple[list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]], tuple[object, ...] | None]
_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}")
_PREVIEW_V1_CHECKSUM = "b1ab8af5ccdd1a0729d1baceeec6fdbc153806b2b0ca41ed8ed41c17ad78870f"
_PREVIEW_V2_CHECKSUM = "6146935614fa0fa7523dbb8feea055d29fccc1545439bcd1455ebf322c5f7660"

# SQL is shipped in the Python package, so wheel/sdist use identical migration bytes.
BASE_DDL: Final[str] = """
CREATE TABLE hosted_identity_bindings (
 issuer text NOT NULL, subject text NOT NULL, user_id text PRIMARY KEY,
 display_name text NOT NULL,email text NOT NULL,email_verified boolean NOT NULL,
 enabled boolean NOT NULL,account_epoch bigint NOT NULL CHECK(account_epoch>=0),
 onboarding text NOT NULL CHECK(onboarding IN ('ready','needs_workspace')),
 last_workspace_id text,logout_all_at timestamptz,UNIQUE(issuer,subject));
CREATE TABLE j01_sessions (
 context_id text PRIMARY KEY,user_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
 csrf_token text NOT NULL,state text NOT NULL CHECK(state IN ('active','revoked','expired')),
 issued_at timestamptz NOT NULL,last_seen_at timestamptz NOT NULL,rotated_at timestamptz NOT NULL,
 idle_expires_at timestamptz NOT NULL,absolute_expires_at timestamptz NOT NULL,
 generation bigint NOT NULL CHECK(generation>=0),session_epoch bigint NOT NULL CHECK(session_epoch>=0),
 CHECK(issued_at<=rotated_at AND rotated_at<=last_seen_at AND last_seen_at<idle_expires_at
       AND idle_expires_at<=absolute_expires_at));
CREATE INDEX j01_sessions_user_id ON j01_sessions(user_id);
CREATE TABLE j01_session_credentials (
 digest text PRIMARY KEY CHECK(digest~'^[0-9a-f]{64}$'),
 context_id text NOT NULL REFERENCES j01_sessions(context_id),
 state text NOT NULL CHECK(state IN ('active','rotated')),generation bigint NOT NULL CHECK(generation>=0));
CREATE UNIQUE INDEX j01_session_one_current_credential ON j01_session_credentials(context_id) WHERE state='active';
CREATE TABLE j01_workspaces (workspace_id text PRIMARY KEY,name text NOT NULL);
CREATE TABLE j01_memberships (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 user_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
 role text NOT NULL CHECK(role IN ('owner','admin','analyst','reviewer','viewer')),
 active boolean NOT NULL,revision bigint NOT NULL CHECK(revision>=0),PRIMARY KEY(workspace_id,user_id));
CREATE TABLE j01_sources (
 source_id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 creator_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),request_key_hash text NOT NULL,
 sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),payload bytea NOT NULL,manifest jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(workspace_id,creator_id,request_key_hash));
CREATE TABLE j01_upload_snapshots (
 snapshot_id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 source_id text NOT NULL REFERENCES j01_sources(source_id),payload jsonb NOT NULL);
CREATE TABLE j01_jobs (
 job_id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 creator_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),credential_digest text NOT NULL,
 operation text NOT NULL CHECK(operation IN ('preview','confirm','analyze')),
 key_hash text NOT NULL,request_hash text NOT NULL,request jsonb NOT NULL,request_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','running','succeeded','failed')),
 lease_digest text,lease_until timestamptz,result jsonb,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,creator_id,operation,key_hash));
CREATE TABLE j01_login_browsers (
 browser_digest text PRIMARY KEY CHECK(browser_digest~'^[0-9a-f]{64}$'),
 epoch bigint NOT NULL CHECK(epoch>0),latest_intent text NOT NULL);
CREATE TABLE j01_login_claims (
 intent_id text PRIMARY KEY,browser_digest text NOT NULL REFERENCES j01_login_browsers(browser_digest),
 state_digest text NOT NULL CHECK(state_digest~'^[0-9a-f]{64}$'),browser_epoch bigint NOT NULL CHECK(browser_epoch>0),
 expires_at timestamptz NOT NULL,phase text NOT NULL CHECK(phase IN ('pending','claimed')),
 sealed_proof bytea NOT NULL CHECK(octet_length(sealed_proof) BETWEEN 30 AND 512),
 claim_digest text CHECK(claim_digest IS NULL OR claim_digest~'^[0-9a-f]{64}$'),
 CHECK((phase='pending' AND claim_digest IS NULL) OR (phase='claimed' AND claim_digest IS NOT NULL)),
 UNIQUE(browser_digest,state_digest));
CREATE TABLE j01_fresh_login_browsers (
 browser_digest text PRIMARY KEY,epoch bigint NOT NULL CHECK(epoch>0),latest_intent text NOT NULL,
 current_context text REFERENCES j01_sessions(context_id));
CREATE TABLE j01_fresh_login_intents (
 intent_id text PRIMARY KEY,browser_digest text NOT NULL REFERENCES j01_fresh_login_browsers(browser_digest),
 state_digest text NOT NULL,issuer text NOT NULL,client_id text NOT NULL,redirect_uri text NOT NULL,
 sealed_proof bytea NOT NULL,created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 browser_epoch bigint NOT NULL,return_to text NOT NULL,reauthenticate boolean NOT NULL,
 previous_context_id text REFERENCES j01_sessions(context_id),
 phase text NOT NULL CHECK(phase IN ('pending','claimed','committed')),claim_digest text,
 UNIQUE(browser_digest,state_digest));
"""

CATALOG_DDL: Final[str] = """
ALTER TABLE j01_upload_snapshots ADD CONSTRAINT k05_upload_snapshot_scope UNIQUE(workspace_id,snapshot_id);
ALTER TABLE j01_jobs ADD COLUMN source_id text GENERATED ALWAYS AS
 (CASE WHEN operation IN ('preview','confirm') THEN request->>'source_id' END) STORED;
ALTER TABLE j01_jobs ADD COLUMN snapshot_id text GENERATED ALWAYS AS
 (CASE WHEN operation='analyze' THEN request->>'snapshot_id' END) STORED;
ALTER TABLE j01_sources ADD CONSTRAINT k05_source_creator_scope FOREIGN KEY(workspace_id,creator_id)
 REFERENCES j01_memberships(workspace_id,user_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_creator_scope FOREIGN KEY(workspace_id,creator_id)
 REFERENCES j01_memberships(workspace_id,user_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_source_scope FOREIGN KEY(workspace_id,source_id)
 REFERENCES j01_sources(workspace_id,source_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_snapshot_scope FOREIGN KEY(workspace_id,snapshot_id)
 REFERENCES j01_upload_snapshots(workspace_id,snapshot_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_request_shape CHECK(jsonb_typeof(request)='object' AND
 CASE WHEN operation='analyze' THEN snapshot_id IS NOT NULL AND jsonb_typeof(request->'snapshot_id')='string'
 ELSE source_id IS NOT NULL AND jsonb_typeof(request->'source_id')='string' END);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_digests CHECK(key_hash~'^[0-9a-f]{64}$' AND request_hash~'^[0-9a-f]{64}$'
 AND credential_digest~'^[0-9a-f]{64}$' AND (lease_digest IS NULL OR lease_digest~'^[0-9a-f]{64}$'));
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_lease_pair CHECK((lease_digest IS NULL)=(lease_until IS NULL)
 AND (state<>'running' OR lease_digest IS NOT NULL));
CREATE INDEX k05_jobs_available ON j01_jobs(operation,created_at,job_id)
 WHERE state IN ('queued','running');
CREATE INDEX k05_jobs_lease ON j01_jobs(lease_until) WHERE state='running';
CREATE INDEX k05_memberships_user ON j01_memberships(user_id,workspace_id) WHERE active;
CREATE INDEX k05_upload_snapshot_source ON j01_upload_snapshots(workspace_id,source_id,snapshot_id);
CREATE TABLE k05_projects (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 project_id text NOT NULL CHECK(project_id~'^p3-project-[0-9a-f]{64}$'),creator_id text NOT NULL,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,project_id),
 FOREIGN KEY(workspace_id,creator_id) REFERENCES j01_memberships(workspace_id,user_id));
CREATE TABLE k05_objects (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 byte_size bigint NOT NULL CHECK(byte_size>=0),media_type text NOT NULL CHECK(length(media_type) BETWEEN 1 AND 255),
 schema_version text,ready_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(workspace_id,sha256));
CREATE INDEX k05_objects_digest ON k05_objects(sha256,workspace_id);
CREATE TABLE k05_records (
 workspace_id text NOT NULL,record_id text NOT NULL,project_id text,record_type text NOT NULL,
 schema_version text NOT NULL CHECK(length(schema_version) BETWEEN 1 AND 128),
 CHECK(record_type IN ('project_bundle','snapshot','snapshot_comparison','regression_event','evidence_bundle','lineage_graph','preview','project_state','result','turn','deletion')),
 canonical_sha256 text NOT NULL CHECK(canonical_sha256~'^[0-9a-f]{64}$'),object_sha256 text NOT NULL,
 snapshot_id text,parent_result_id text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,record_id),UNIQUE(workspace_id,project_id,record_id),
 FOREIGN KEY(workspace_id,project_id) REFERENCES k05_projects(workspace_id,project_id),
 FOREIGN KEY(workspace_id,object_sha256) REFERENCES k05_objects(workspace_id,sha256),
 CHECK((record_type='preview')=(project_id IS NULL)),
 CHECK(record_id~('^'||CASE record_type
 WHEN 'project_bundle' THEN 'p3-bundle-' WHEN 'snapshot' THEN 'p3-snapshot-'
 WHEN 'snapshot_comparison' THEN 'p3-comparison-' WHEN 'regression_event' THEN 'p3-event-'
 WHEN 'evidence_bundle' THEN 'p3-evidence-bundle-' WHEN 'lineage_graph' THEN 'p3-lineage-graph-'
 WHEN 'preview' THEN 'p6-preview-' WHEN 'project_state' THEN 'p6-project-state-'
 WHEN 'result' THEN 'p6-result-' WHEN 'turn' THEN 'p6-turn-' WHEN 'deletion' THEN 'p6-deletion-'
 ELSE 'INVALID' END||'[0-9a-f]{64}$')),
 CHECK(snapshot_id IS NULL OR snapshot_id~'^p3-snapshot-[0-9a-f]{64}$'),
 CHECK(parent_result_id IS NULL OR parent_result_id~'^p6-result-[0-9a-f]{64}$'),
 CHECK((record_type IN ('result','turn'))=(snapshot_id IS NOT NULL)),
 CHECK(parent_result_id IS NULL OR record_type IN ('result','turn')),
 FOREIGN KEY(workspace_id,project_id,snapshot_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,project_id,parent_result_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX k05_records_page ON k05_records(workspace_id,project_id,record_type,record_id);
CREATE INDEX k05_records_snapshot ON k05_records(workspace_id,project_id,snapshot_id,record_type,record_id)
 WHERE snapshot_id IS NOT NULL;
CREATE INDEX k05_records_object ON k05_records(object_sha256);
CREATE TABLE k05_record_parents (
 workspace_id text NOT NULL,project_id text NOT NULL,record_id text NOT NULL,
 relation text NOT NULL CHECK(length(relation) BETWEEN 1 AND 64),parent_id text NOT NULL,
 PRIMARY KEY(workspace_id,record_id,relation,parent_id),CHECK(record_id<>parent_id),
 FOREIGN KEY(workspace_id,project_id,record_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,project_id,parent_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX k05_parents_reverse ON k05_record_parents(workspace_id,project_id,parent_id,relation,record_id);
CREATE TABLE k05_current_pointers (
 workspace_id text NOT NULL,project_id text NOT NULL,pointer_name text NOT NULL,
 record_id text NOT NULL,revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 PRIMARY KEY(workspace_id,project_id,pointer_name),
 CHECK((pointer_name='project_state' AND record_id~'^p6-project-state-[0-9a-f]{64}$') OR
 (pointer_name='snapshot' AND record_id~'^p3-snapshot-[0-9a-f]{64}$') OR
 (pointer_name='result' AND record_id~'^p6-result-[0-9a-f]{64}$')),
 FOREIGN KEY(workspace_id,project_id,record_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE k05_confirmations (
 workspace_id text NOT NULL,preview_id text NOT NULL CHECK(preview_id~'^p6-preview-[0-9a-f]{64}$'),
 project_id text NOT NULL,project_state_record_id text NOT NULL CHECK(project_state_record_id~'^p6-project-state-[0-9a-f]{64}$'),
 PRIMARY KEY(workspace_id,preview_id),UNIQUE(workspace_id,project_state_record_id),
 FOREIGN KEY(workspace_id,preview_id) REFERENCES k05_records(workspace_id,record_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,project_id,project_state_record_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE k05_preview_leases (
 workspace_id text NOT NULL,preview_id text NOT NULL CHECK(preview_id~'^p6-preview-[0-9a-f]{64}$'),
 leased_at text NOT NULL,canonical_sha256 text NOT NULL CHECK(canonical_sha256~'^[0-9a-f]{64}$'),
 PRIMARY KEY(workspace_id,preview_id),
 FOREIGN KEY(workspace_id,preview_id) REFERENCES k05_records(workspace_id,record_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE k05_object_references (
 workspace_id text NOT NULL,record_id text NOT NULL,sha256 text NOT NULL,
 PRIMARY KEY(workspace_id,record_id,sha256),
 FOREIGN KEY(workspace_id,record_id) REFERENCES k05_records(workspace_id,record_id),
 FOREIGN KEY(workspace_id,sha256) REFERENCES k05_objects(workspace_id,sha256));
CREATE INDEX k05_references_digest ON k05_object_references(sha256);
CREATE TABLE k05_object_leases (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 lease_id text NOT NULL CHECK(length(lease_id) BETWEEN 1 AND 128),expires_at timestamptz NOT NULL,
 PRIMARY KEY(workspace_id,sha256,lease_id));
CREATE INDEX k05_leases_digest ON k05_object_leases(sha256,expires_at);
CREATE INDEX k05_leases_expiry ON k05_object_leases(expires_at);
CREATE TABLE k05_imports (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),import_id text NOT NULL,
 source_fingerprint text NOT NULL CHECK(source_fingerprint~'^[0-9a-f]{64}$'),
 mapping_fingerprint text NOT NULL CHECK(mapping_fingerprint~'^[0-9a-f]{64}$'),version integer NOT NULL CHECK(version=1),
 state text NOT NULL CHECK(state IN ('running','complete')),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,PRIMARY KEY(workspace_id,import_id),CHECK((state='complete')=(completed_at IS NOT NULL)));
CREATE FUNCTION k05_record_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Canonical records are immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER k05_record_immutable BEFORE UPDATE ON k05_records FOR EACH ROW EXECUTE FUNCTION k05_record_immutable();
CREATE FUNCTION k05_object_reference_retention() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
 -- Artifact reachability is immutable while its canonical record remains live.
 IF EXISTS(SELECT 1 FROM k05_records WHERE workspace_id=OLD.workspace_id AND record_id=OLD.record_id)
 AND NOT EXISTS(SELECT 1 FROM k05_object_references WHERE workspace_id=OLD.workspace_id
                AND record_id=OLD.record_id AND sha256=OLD.sha256) THEN
  RAISE EXCEPTION 'Canonical artifact references must remain reachable' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER k05_object_reference_retention AFTER DELETE OR UPDATE ON k05_object_references
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION k05_object_reference_retention();
"""

JOB_DDL: Final[str] = """
DO $$
DECLARE state_constraint text;
BEGIN
 SELECT conname INTO STRICT state_constraint FROM pg_constraint
 WHERE conrelid='j01_jobs'::regclass AND contype='c'
 AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='j01_jobs'::regclass
                  AND attname='state' AND NOT attisdropped)];
 EXECUTE format('ALTER TABLE j01_jobs DROP CONSTRAINT %I',state_constraint);
END; $$;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_state_v3
 CHECK(state IN ('queued','running','succeeded','failed','cancelled'));
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_identity_scope UNIQUE(workspace_id,job_id);
ALTER TABLE j01_jobs ADD COLUMN retry_of_job_id text;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_retry_scope FOREIGN KEY(workspace_id,retry_of_job_id)
 REFERENCES j01_jobs(workspace_id,job_id);
"""

WORKSPACE_INDEX_DDL: Final[str] = """
CREATE INDEX k05_records_workspace_page ON k05_records(workspace_id,record_type,record_id);
"""

NATIVE_DDL: Final[str] = """
CREATE TEMP TABLE k05_expected_native_record_checks (
 record_type text,record_id text,snapshot_id text,parent_result_id text,
 CHECK(record_type IN ('project_bundle','snapshot','snapshot_comparison','regression_event','evidence_bundle','lineage_graph','preview','project_state','result','turn','deletion')),
 CHECK(record_id~('^'||CASE record_type
 WHEN 'project_bundle' THEN 'p3-bundle-' WHEN 'snapshot' THEN 'p3-snapshot-'
 WHEN 'snapshot_comparison' THEN 'p3-comparison-' WHEN 'regression_event' THEN 'p3-event-'
 WHEN 'evidence_bundle' THEN 'p3-evidence-bundle-' WHEN 'lineage_graph' THEN 'p3-lineage-graph-'
 WHEN 'preview' THEN 'p6-preview-' WHEN 'project_state' THEN 'p6-project-state-'
 WHEN 'result' THEN 'p6-result-' WHEN 'turn' THEN 'p6-turn-' WHEN 'deletion' THEN 'p6-deletion-'
 ELSE 'INVALID' END||'[0-9a-f]{64}$')),
 CHECK(snapshot_id IS NULL OR snapshot_id~'^p3-snapshot-[0-9a-f]{64}$'),
 CHECK((record_type IN ('result','turn'))=(snapshot_id IS NOT NULL)),
 CHECK(parent_result_id IS NULL OR record_type IN ('result','turn')));
CREATE TEMP TABLE k05_expected_native_pointer_checks (
 pointer_name text,record_id text,
 CHECK((pointer_name='project_state' AND record_id~'^p6-project-state-[0-9a-f]{64}$') OR
 (pointer_name='snapshot' AND record_id~'^p3-snapshot-[0-9a-f]{64}$') OR
 (pointer_name='result' AND record_id~'^p6-result-[0-9a-f]{64}$')));
DO $$
DECLARE expected record; installed_name text;
BEGIN
 FOR expected IN
  SELECT 'k05_records' AS target,pg_get_constraintdef(oid) AS definition FROM pg_constraint
   WHERE conrelid='pg_temp.k05_expected_native_record_checks'::regclass AND contype='c'
  UNION ALL
  SELECT 'k05_current_pointers',pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conrelid='pg_temp.k05_expected_native_pointer_checks'::regclass AND contype='c'
 LOOP
  SELECT conname INTO STRICT installed_name FROM pg_constraint
   WHERE conrelid=to_regclass(expected.target) AND contype='c' AND pg_get_constraintdef(oid)=expected.definition;
  EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',expected.target,installed_name);
 END LOOP;
END; $$;
DROP TABLE pg_temp.k05_expected_native_record_checks,pg_temp.k05_expected_native_pointer_checks;
ALTER TABLE k05_records ADD CONSTRAINT k05_record_kind_v5 CHECK(record_type IN
 ('project_bundle','snapshot','snapshot_comparison','regression_event','evidence_bundle','lineage_graph',
  'preview','project_state','result','turn','deletion','upload_snapshot','upload_result'));
ALTER TABLE k05_records ADD CONSTRAINT k05_record_identity_v5 CHECK(record_id~('^'||CASE record_type
 WHEN 'project_bundle' THEN 'p3-bundle-' WHEN 'snapshot' THEN 'p3-snapshot-'
 WHEN 'snapshot_comparison' THEN 'p3-comparison-' WHEN 'regression_event' THEN 'p3-event-'
 WHEN 'evidence_bundle' THEN 'p3-evidence-bundle-' WHEN 'lineage_graph' THEN 'p3-lineage-graph-'
 WHEN 'preview' THEN 'p6-preview-' WHEN 'project_state' THEN 'p6-project-state-'
 WHEN 'result' THEN 'p6-result-' WHEN 'turn' THEN 'p6-turn-' WHEN 'deletion' THEN 'p6-deletion-'
 WHEN 'upload_snapshot' THEN 'upload-snapshot-' WHEN 'upload_result' THEN 'p6-result-'
 ELSE 'INVALID' END||'[0-9a-f]{64}$'));
ALTER TABLE k05_records ADD CONSTRAINT k05_record_snapshot_identity_v5 CHECK(snapshot_id IS NULL OR
 CASE WHEN record_type='upload_result' THEN snapshot_id~'^upload-snapshot-[0-9a-f]{64}$'
 ELSE snapshot_id~'^p3-snapshot-[0-9a-f]{64}$' END);
ALTER TABLE k05_records ADD CONSTRAINT k05_record_snapshot_presence_v5
 CHECK((record_type IN ('result','turn','upload_result'))=(snapshot_id IS NOT NULL));
ALTER TABLE k05_records ADD CONSTRAINT k05_record_parent_kind_v5
 CHECK(parent_result_id IS NULL OR record_type IN ('result','turn','upload_result'));
ALTER TABLE k05_records ADD COLUMN source_id text;
ALTER TABLE k05_records ADD CONSTRAINT k05_native_source_presence
 CHECK((record_type='upload_snapshot')=(source_id IS NOT NULL));
ALTER TABLE k05_records ADD CONSTRAINT k05_native_source_scope FOREIGN KEY(workspace_id,source_id)
 REFERENCES j01_sources(workspace_id,source_id);
ALTER TABLE k05_current_pointers ADD CONSTRAINT k05_pointer_identity_v5
 CHECK((pointer_name='project_state' AND record_id~'^p6-project-state-[0-9a-f]{64}$') OR
 (pointer_name='snapshot' AND record_id~'^(p3-snapshot-|upload-snapshot-)[0-9a-f]{64}$') OR
 (pointer_name='result' AND record_id~'^p6-result-[0-9a-f]{64}$'));
ALTER TABLE k05_projects ADD COLUMN provenance_kind text NOT NULL DEFAULT 'local'
 CHECK(provenance_kind IN ('local','upload'));
ALTER TABLE j01_upload_snapshots ADD COLUMN canonical_record_id text;
ALTER TABLE j01_upload_snapshots ADD CONSTRAINT k05_upload_snapshot_canonical_scope
 FOREIGN KEY(workspace_id,canonical_record_id) REFERENCES k05_records(workspace_id,record_id);
ALTER TABLE j01_upload_snapshots ADD CONSTRAINT k05_upload_snapshot_canonical_identity
 CHECK(canonical_record_id IS NULL OR canonical_record_id=snapshot_id);
ALTER TABLE j01_jobs ADD COLUMN canonical_result_id text;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_canonical_result_scope FOREIGN KEY(workspace_id,canonical_result_id)
 REFERENCES k05_records(workspace_id,record_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_canonical_result_identity
 CHECK(canonical_result_id IS NULL OR canonical_result_id~'^p6-result-[0-9a-f]{64}$');
"""

FOLLOW_UP_DDL: Final[str] = """
CREATE TEMP TABLE k05_expected_follow_up_job_checks (
 operation text,request jsonb,source_id text,snapshot_id text,
 CHECK(operation IN ('preview','confirm','analyze')),
 CHECK(jsonb_typeof(request)='object' AND
 CASE WHEN operation='analyze' THEN snapshot_id IS NOT NULL AND jsonb_typeof(request->'snapshot_id')='string'
 ELSE source_id IS NOT NULL AND jsonb_typeof(request->'source_id')='string' END));
DO $$
DECLARE expected record; installed_name text;
BEGIN
 FOR expected IN SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
  WHERE conrelid='pg_temp.k05_expected_follow_up_job_checks'::regclass AND contype='c'
 LOOP
  SELECT conname INTO STRICT installed_name FROM pg_constraint WHERE conrelid='j01_jobs'::regclass
   AND contype='c' AND pg_get_constraintdef(oid)=expected.definition;
  EXECUTE format('ALTER TABLE j01_jobs DROP CONSTRAINT %I',installed_name);
 END LOOP;
END; $$;
DROP TABLE pg_temp.k05_expected_follow_up_job_checks;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_operation_v6 CHECK(operation IN ('preview','confirm','analyze','follow_up'));
ALTER TABLE j01_jobs ADD COLUMN canonical_project_id text GENERATED ALWAYS AS
 (CASE WHEN operation='follow_up' THEN request->>'project_id' END) STORED;
ALTER TABLE j01_jobs ADD COLUMN parent_result_id text GENERATED ALWAYS AS
 (CASE WHEN operation='follow_up' THEN request->>'parent_result_id' END) STORED;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_follow_up_parent_scope FOREIGN KEY(workspace_id,canonical_project_id,parent_result_id)
 REFERENCES k05_records(workspace_id,project_id,record_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_request_shape_v6 CHECK(jsonb_typeof(request)='object' AND
 CASE WHEN operation='follow_up' THEN
  request ?& ARRAY['project_id','parent_result_id','expected_revision','selection_kind','selection_id','question']
  AND request-ARRAY['project_id','parent_result_id','expected_revision','selection_kind','selection_id','question']='{}'::jsonb
  AND jsonb_typeof(request->'project_id')='string' AND canonical_project_id~'^p3-project-[0-9a-f]{64}$'
  AND jsonb_typeof(request->'parent_result_id')='string' AND parent_result_id~'^p6-result-[0-9a-f]{64}$'
  AND CASE WHEN jsonb_typeof(request->'expected_revision')='number'
   AND (request->'expected_revision')::text~'^(0|[1-9][0-9]*)$'
   THEN (request->>'expected_revision')::numeric BETWEEN 0 AND 9223372036854775807 ELSE false END
  AND jsonb_typeof(request->'selection_kind')='string' AND request->>'selection_kind' IN ('claim','node')
  AND jsonb_typeof(request->'selection_id')='string' AND length(request->>'selection_id') BETWEEN 1 AND 128
  AND jsonb_typeof(request->'question')='string' AND length(request->>'question') BETWEEN 1 AND 2000
 WHEN operation='analyze' THEN snapshot_id IS NOT NULL AND jsonb_typeof(request->'snapshot_id')='string'
 ELSE source_id IS NOT NULL AND jsonb_typeof(request->'source_id')='string' END);
"""

# Preserve V6 bytes for installed ledgers; V8 changes only the question bound.
QUESTION_LIMIT_DDL: Final[str] = (
    "ALTER TABLE j01_jobs DROP CONSTRAINT k05_job_request_shape_v6;\n"
    + FOLLOW_UP_DDL[FOLLOW_UP_DDL.index("ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_request_shape_v6"):]
        .replace("BETWEEN 1 AND 2000", "BETWEEN 1 AND 4000")
)

RUNTIME_OPERATIONS_DDL: Final[str] = """
ALTER TABLE k05_projects ADD COLUMN display_name text
 CHECK(display_name IS NULL OR length(display_name) BETWEEN 1 AND 160);
ALTER TABLE k05_projects ADD COLUMN deletion_revision bigint;
ALTER TABLE k05_projects ADD COLUMN deletion_tombstone_id text;
ALTER TABLE k05_projects ADD COLUMN deleted_by text;
ALTER TABLE k05_projects ADD CONSTRAINT k02_deletion_receipt CHECK(
 (deletion_revision IS NULL AND deletion_tombstone_id IS NULL AND deleted_by IS NULL) OR
 (deletion_revision IS NOT NULL AND deletion_revision>0 AND deletion_revision<=revision
  AND deletion_tombstone_id IS NOT NULL AND deletion_tombstone_id~'^p6-deletion-[0-9a-f]{64}$'
  AND deleted_by IS NOT NULL AND deleted_by~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'));
ALTER TABLE k05_projects ADD CONSTRAINT k02_deletion_record_scope
 FOREIGN KEY(workspace_id,project_id,deletion_tombstone_id)
 REFERENCES k05_records(workspace_id,project_id,record_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE j01_workspaces ADD COLUMN settings_revision bigint NOT NULL DEFAULT 0 CHECK(settings_revision>=0);
ALTER TABLE j01_workspaces ADD COLUMN maximum_upload_bytes bigint CHECK(maximum_upload_bytes>0 AND maximum_upload_bytes<=209715200);
ALTER TABLE j01_workspaces ADD COLUMN maximum_workspace_bytes bigint CHECK(maximum_workspace_bytes>0 AND maximum_workspace_bytes<=1677721600);
ALTER TABLE j01_workspaces ADD CONSTRAINT k02_workspace_policy_bounds
 CHECK(maximum_workspace_bytes IS NULL OR maximum_upload_bytes IS NULL OR maximum_workspace_bytes>=maximum_upload_bytes);
ALTER TABLE j01_jobs DROP CONSTRAINT k05_job_operation_v6;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_operation_v6 CHECK(operation IN ('preview','confirm','analyze','follow_up','refresh'));
ALTER TABLE j01_jobs ADD COLUMN refresh_project_id text GENERATED ALWAYS AS
 (CASE WHEN operation='refresh' THEN request->>'project_id' END) STORED;
ALTER TABLE j01_jobs ADD COLUMN parent_snapshot_id text GENERATED ALWAYS AS
 (CASE WHEN operation='refresh' THEN request->>'parent_snapshot_id' END) STORED;
ALTER TABLE j01_jobs ADD COLUMN refresh_source_id text GENERATED ALWAYS AS
 (CASE WHEN operation='refresh' THEN request->>'source_id' END) STORED;
ALTER TABLE j01_jobs ADD CONSTRAINT k02_refresh_parent_scope FOREIGN KEY(workspace_id,refresh_project_id,parent_snapshot_id)
 REFERENCES k05_records(workspace_id,project_id,record_id);
ALTER TABLE j01_jobs ADD CONSTRAINT k02_refresh_source_scope FOREIGN KEY(workspace_id,refresh_source_id)
 REFERENCES j01_sources(workspace_id,source_id);
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
 ELSE source_id IS NOT NULL AND jsonb_typeof(request->'source_id')='string' END);
ALTER TABLE j01_jobs ADD COLUMN deadline_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '15 minutes');
ALTER TABLE j01_jobs ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3);
ALTER TABLE j01_jobs ADD COLUMN max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts=3);
ALTER TABLE j01_jobs ADD COLUMN terminal_at timestamptz;
ALTER TABLE j01_jobs ADD CONSTRAINT k02_job_deadline_order CHECK(deadline_at>=created_at);
ALTER TABLE j01_jobs ADD CONSTRAINT k02_job_terminal_time CHECK(terminal_at IS NULL OR
 (state IN ('succeeded','failed','cancelled') AND terminal_at>=created_at));
CREATE FUNCTION k02_job_terminal_stamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='running' AND (OLD.state<>'running' OR NEW.lease_digest IS DISTINCT FROM OLD.lease_digest
  OR OLD.lease_until<clock_timestamp()) THEN
  NEW.attempt_count:=OLD.attempt_count+1;
 END IF;
 IF NEW.state IN ('succeeded','failed','cancelled') AND OLD.state IN ('queued','running') THEN
  NEW.terminal_at:=clock_timestamp();
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER k02_job_terminal_stamp BEFORE UPDATE ON j01_jobs FOR EACH ROW EXECUTE FUNCTION k02_job_terminal_stamp();
INSERT INTO k05_current_pointers(workspace_id,project_id,pointer_name,record_id,revision)
 SELECT DISTINCT ON (r.workspace_id,r.project_id) r.workspace_id,r.project_id,'result',r.record_id,0
 FROM k05_records r WHERE r.record_type IN ('result','upload_result')
 ORDER BY r.workspace_id,r.project_id,r.created_at DESC,r.record_id DESC
 ON CONFLICT(workspace_id,project_id,pointer_name) DO NOTHING;
"""

DURABLE_EXPORT_DDL: Final[str] = """
CREATE TABLE k02_exports (
 workspace_id text NOT NULL,export_id text NOT NULL,project_id text NOT NULL,creator_id text NOT NULL,
 result_id text NOT NULL CHECK(result_id~'^p6-result-[0-9a-f]{64}$'),
 format text NOT NULL CHECK(format IN ('json','markdown')),object_sha256 text NOT NULL,
 request_key_hash text CHECK(request_key_hash~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
 PRIMARY KEY(workspace_id,export_id),
 UNIQUE(workspace_id,creator_id,request_key_hash),
 CHECK(export_id~'^ex_(json|markdown)_p6-result-[0-9a-f]{64}_[0-9a-f]{32}$'
  AND export_id='ex_'||format||'_'||result_id||'_'||right(export_id,32)),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '1 hour'),
 FOREIGN KEY(workspace_id,creator_id) REFERENCES j01_memberships(workspace_id,user_id),
 FOREIGN KEY(workspace_id,project_id,result_id) REFERENCES k05_records(workspace_id,project_id,record_id),
 FOREIGN KEY(workspace_id,object_sha256) REFERENCES k05_objects(workspace_id,sha256),
 FOREIGN KEY(workspace_id,result_id,object_sha256) REFERENCES k05_object_references(workspace_id,record_id,sha256));
CREATE INDEX k02_exports_expiry ON k02_exports(expires_at);
CREATE FUNCTION k02_export_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Export artifacts are immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER k02_export_immutable BEFORE UPDATE ON k02_exports FOR EACH ROW EXECUTE FUNCTION k02_export_immutable();
"""

PDF_EXPORT_DDL: Final[str] = """
ALTER TABLE k02_exports DROP CONSTRAINT k02_exports_format_check;
ALTER TABLE k02_exports DROP CONSTRAINT k02_exports_check;
ALTER TABLE k02_exports ADD CONSTRAINT k12_export_format CHECK(format IN ('json','markdown','pdf'));
ALTER TABLE k02_exports ADD CONSTRAINT k12_export_handle
 CHECK(export_id~'^ex_(json|markdown|pdf)_p6-result-[0-9a-f]{64}_[0-9a-f]{32}$'
  AND export_id='ex_'||format||'_'||result_id||'_'||right(export_id,32));
"""

LOGIN_CLOCK_DDL: Final[str] = """
LOCK TABLE j01_fresh_login_intents IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='j01_fresh_login_intents'::regclass
           AND attname='database_created_at' AND NOT attisdropped)
 AND NOT EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attrelid='j01_fresh_login_intents'::regclass AND a.attname='database_created_at' AND NOT a.attisdropped
  AND a.atttypid='timestamptz'::regtype AND a.attnotnull AND a.attidentity='' AND a.attgenerated=''
  AND pg_get_expr(d.adbin,d.adrelid)='clock_timestamp()'
  AND a.attnum=(SELECT max(attnum) FROM pg_attribute WHERE attrelid=a.attrelid AND attnum>0 AND NOT attisdropped)) THEN
  RAISE EXCEPTION 'Login database clock column was not exact' USING ERRCODE='23514';
 END IF;
END; $$;
UPDATE j01_fresh_login_intents SET expires_at=LEAST(expires_at,clock_timestamp()) WHERE phase IN ('pending','claimed');
ALTER TABLE j01_fresh_login_intents ADD COLUMN IF NOT EXISTS database_created_at timestamptz NOT NULL DEFAULT clock_timestamp();
"""

_LOGIN_DB_CLOCK_COLUMN: Final[tuple[object, ...]] = (
    'database_created_at', 'timestamp with time zone', True, 'clock_timestamp()', '', '',
)

_BASE_TABLES = tuple(re.findall(r"CREATE TABLE ([a-z0-9_]+)", BASE_DDL))
_CATALOG_TABLES = tuple(re.findall(r"CREATE TABLE ([a-z0-9_]+)", CATALOG_DDL))
_LEDGER = "k05_persistence_migrations"
_LEDGER_DDL = """CREATE TABLE k05_persistence_migrations (
 version integer PRIMARY KEY CHECK(version>0),checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),
 signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),applied_at timestamptz NOT NULL DEFAULT clock_timestamp());"""
MIGRATIONS = (BASE_DDL, CATALOG_DDL, JOB_DDL, WORKSPACE_INDEX_DDL, NATIVE_DDL, FOLLOW_UP_DDL, LOGIN_CLOCK_DDL, QUESTION_LIMIT_DDL, RUNTIME_OPERATIONS_DDL, DURABLE_EXPORT_DDL, PDF_EXPORT_DDL, JOB_EXECUTION_DDL, READ_PROJECTION_DDL, ANALYSIS_VERSION_DDL, RETENTION_DDL, JOB_UPDATES_DDL, JOB_CONFLICT_DDL)
_ADDED_TABLES = ((10, ("k02_exports",)), (12, ("k09_job_attempts", "k09_queue_turns")),
                 (13, READ_PROJECTION_TABLES), (15, RETENTION_TABLES))


class PersistenceMigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Persistence migration preconditions or installed schema did not match.")


def migration_schema(value: str) -> str:
    if (not isinstance(value, str) or not _SCHEMA.fullmatch(value)
            or value in ("public", "information_schema") or value.startswith("pg_")):
        raise PersistenceMigrationRejected()
    return value


async def _rows(connection: Connection, query: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    cursor = await connection.execute(query, params)
    result = []
    while (row := await cursor.fetchone()) is not None:
        result.append(row)
    return result


async def _exists(connection: Connection, relation: str) -> bool:
    row = await (await connection.execute(
        "SELECT relkind,relpersistence FROM pg_class WHERE oid=to_regclass(%s)", (relation,))).fetchone()
    if row is not None and row != ("r", "p"):
        raise PersistenceMigrationRejected()
    return row is not None


async def _shape(connection: Connection, relation: str, *, exclude_index: str | None = None) -> _Shape:
    columns = await _rows(connection,
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),"
        "a.attidentity,a.attgenerated FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid "
        "AND d.adnum=a.attnum WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
        (relation,))
    constraints = await _rows(connection,
        "SELECT contype,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid=to_regclass(%s) ORDER BY contype,pg_get_constraintdef(oid)", (relation,))
    indexes = await _rows(connection,
        "SELECT i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.indkey::text,"
        "pg_get_expr(i.indexprs,i.indrelid),pg_get_expr(i.indpred,i.indrelid) "
        "FROM pg_index i WHERE i.indrelid=to_regclass(%s) AND i.indexrelid<>COALESCE(to_regclass(%s),0::oid) "
        "ORDER BY i.indkey::text,i.indisunique,i.indisprimary", (relation, exclude_index))
    behavior = await (await connection.execute(
        "SELECT relrowsecurity,relforcerowsecurity,(SELECT count(*) FROM pg_inherits WHERE inhrelid=c.oid OR inhparent=c.oid) "
        "FROM pg_class c WHERE c.oid=to_regclass(%s)", (relation,))).fetchone()
    return columns, constraints, indexes, behavior


def _normal(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).replace("k05_expected_", "")


async def _baseline(connection: Connection, schema: str) -> bool:
    """Adopt only baseline shapes; additive K-04 guards and constraints remain."""
    expected = BASE_DDL
    names = set(_BASE_TABLES) | set(re.findall(r"CREATE (?:UNIQUE )?INDEX ([a-z0-9_]+)", expected))
    for name in sorted(names, key=len, reverse=True):
        expected = re.sub(r"\b" + name + r"\b", "k05_expected_" + name, expected)
    expected = expected.replace("CREATE TABLE ", "CREATE TEMP TABLE ")
    await connection.execute(expected)
    adopted = False
    for table in _BASE_TABLES:
        relation = schema + "." + table
        if not await _exists(connection, relation):
            # Missing tables may be added only; no existing row or table is replaced.
            statement = re.search(r"CREATE TABLE " + table + r" \(.*?;", BASE_DDL, re.S)
            if statement is None:
                raise PersistenceMigrationRejected()
            await connection.execute(statement[0])
            for index in re.finditer(r"CREATE (?:UNIQUE )?INDEX [^;]+ ON " + table + r"\([^;]+;", BASE_DDL):
                await connection.execute(index[0])
        else:
            adopted = True
        actual = await _shape(connection, relation)
        target = await _shape(connection, "pg_temp.k05_expected_" + table)
        columns = actual[0]
        if (table == 'j01_fresh_login_intents'
                and columns == [*target[0], _LOGIN_DB_CLOCK_COLUMN]):
            columns = target[0]
        if (_normal(columns) != _normal(target[0]) or actual[3] != (False, False, 0)
                or not {_normal(row) for row in target[1]}.issubset({_normal(row) for row in actual[1]})
                or not {_normal(row) for row in target[2]}.issubset({_normal(row) for row in actual[2]})):
            raise PersistenceMigrationRejected()
    for table in reversed(_BASE_TABLES):
        await connection.execute("DROP TABLE pg_temp.k05_expected_" + table)
    return adopted


async def _verified_preview_extension(connection: Connection, schema: str) -> bool:
    """Recognize only the pinned, independently verified K-07 job extension."""
    names = ('k07_preview_migrations', 'k07_import_previews', 'k07_import_failures', 'k07_jobs_preview_ref')
    row = await (await connection.execute(
        "SELECT to_regclass(%s),to_regclass(%s),to_regclass(%s),to_regclass(%s),to_regprocedure(%s),"
        "(SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(%s) "
        "AND tgname='k07_confirm_preview_guard' AND NOT tgisinternal)",
        (*(schema + '.' + name for name in names), schema + '.k07_confirm_preview_guard()', schema + '.j01_jobs'),
    )).fetchone()
    if row is None or len(row) != 6:
        raise PersistenceMigrationRejected()
    present = [value is not None for value in row[:5]] + [row[5] != 0]
    if not any(present):
        return False
    if not all(present):
        raise PersistenceMigrationRejected()
    for table in names[:3]:
        if not await _exists(connection, schema + '.' + table):
            raise PersistenceMigrationRejected()
    kind = await (await connection.execute(
        "SELECT relkind,relpersistence FROM pg_class WHERE oid=to_regclass(%s)",
        (schema + '.k07_jobs_preview_ref',),
    )).fetchone()
    if kind != ('i', 'p'):
        raise PersistenceMigrationRejected()
    try:
        from .preview_migration import verify as verify_preview

        raw = Path(__file__).with_name('preview_schema.sql').read_bytes()
        repeat = Path(__file__).with_name('preview_repeat_schema.sql').read_bytes()
        ledger = await _rows(connection, 'SELECT version,checksum FROM "' + schema + '".k07_preview_migrations ORDER BY version')
        if (content_hasher(raw).hexdigest() != _PREVIEW_V1_CHECKSUM
                or content_hasher(repeat).hexdigest() != _PREVIEW_V2_CHECKSUM
                or ledger not in ([(1, _PREVIEW_V1_CHECKSUM)], [(1, _PREVIEW_V1_CHECKSUM), (2, _PREVIEW_V2_CHECKSUM)])
                or not await verify_preview(connection, allow_previous=True)):
            raise PersistenceMigrationRejected()
    except PersistenceMigrationRejected:
        raise
    except Exception:
        raise PersistenceMigrationRejected() from None
    return True


async def _signature(connection: Connection, schema: str, version: int) -> str:
    tables = (*_BASE_TABLES, _LEDGER, *(_CATALOG_TABLES if version >= 2 else ()),
              *(table for introduced, names in _ADDED_TABLES if version >= introduced for table in names))
    preview = await _verified_preview_extension(connection, schema)
    try:
        read_indexes = await verified_extension(connection)
    except ReadIndexesRejected:
        raise PersistenceMigrationRejected() from None
    facts: list[object] = []
    for table in tables:
        excluded = schema + '.k07_jobs_preview_ref' if preview and table == 'j01_jobs' else None
        if read_indexes and table == 'k05_projects':
            excluded = schema + '.k10_projects_creator'
        facts.append((table, await _shape(connection, schema + "." + table, exclude_index=excluded)))
    facts.append(await _rows(connection,
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled,p.prosrc,p.proconfig,p.prosecdef "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname=%s AND NOT t.tgisinternal "
        "AND c.relname=ANY(%s) AND NOT(%s AND c.relname='j01_jobs' AND t.tgname='k07_confirm_preview_guard') "
        "ORDER BY c.relname,t.tgname", (schema, list(tables), preview)))
    if version >= 15:
        privileged = await _rows(connection,
            "SELECT p.proname,p.proowner=n.nspowner,"
            "ARRAY(SELECT a.grantee::text||':'||a.privilege_type||':'||a.is_grantable::text "
            "FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee<>p.proowner ORDER BY a.grantee,a.privilege_type,a.is_grantable) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname=ANY(%s) ORDER BY p.proname",
            (schema, ['k13_capture_deletion', 'k13_no_resurrection']))
        if (len(privileged) != 2 or any(row[1] is not True or row[2] != [] for row in privileged)):
            raise PersistenceMigrationRejected()
        facts.append(privileged)
    return content_hasher(_normal(facts).encode()).hexdigest()


async def _context(connection: Connection, schema: str) -> str:
    schema = migration_schema(schema)
    if connection.autocommit:
        raise PersistenceMigrationRejected()
    if await (await connection.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))).fetchone() != (schema,):
        raise PersistenceMigrationRejected()
    await connection.execute('SET LOCAL search_path TO "' + schema + '",pg_catalog')
    return schema


async def _verified_prefix(connection: Connection, schema: str, rows: list[tuple[object, ...]]) -> bool:
    from .workspace_migration import verify as verify_workspace

    if not 2 <= len(rows) <= len(MIGRATIONS):
        return False
    expected = [(version, content_hasher(ddl.encode()).hexdigest())
                for version, ddl in enumerate(MIGRATIONS[:len(rows)], 1)]
    if [(row[0], row[1]) for row in rows] != expected:
        return False
    for introduced, names in _ADDED_TABLES:
        if len(rows) < introduced and any([await _exists(connection, schema + "." + name) for name in names]):
            return False
    try:
        signature = await _signature(connection, schema, len(rows))
    except PersistenceMigrationRejected:
        return False
    return rows[-1][2] == signature and await verify_workspace(connection)


async def verify(connection: Connection, schema: str) -> bool:
    """Require the complete supported version, checksums and installed shape."""
    schema = await _context(connection, schema)
    if not await _exists(connection, schema + "." + _LEDGER):
        return False
    rows = await _rows(connection, "SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version")
    return len(rows) == len(MIGRATIONS) and await _verified_prefix(connection, schema, rows)


async def migrate(connection: Connection, schema: str) -> str:
    """Explicit operator call; commit on success, roll back on any exception.

    Creates missing baseline tables or adopts their exact required shape, then
    applies K-04's existing migration and additive catalog migration. Existing
    invalid data/unknown versions/drift reject adoption; there is no down DDL.
    K-03 admission migration is separate and remains owned by K-03.
    """
    from .workspace_migration import migrate as migrate_workspace

    schema = await _context(connection, schema)
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("aletheia-k05-persistence:" + schema,))
    if await _exists(connection, schema + "." + _LEDGER):
        rows = await _rows(connection, "SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version")
        if not await _verified_prefix(connection, schema, rows):
            raise PersistenceMigrationRejected()
        if len(rows) == len(MIGRATIONS):
            await install_if_possible(connection, schema)
            return "unchanged"
        affected = ("j01_jobs,j01_upload_snapshots,k05_records,k05_projects,k05_current_pointers,"
                    "j01_fresh_login_intents,j01_workspaces")
        await connection.execute("LOCK TABLE " + affected + " IN SHARE ROW EXCLUSIVE MODE")
        if not await _verified_prefix(connection, schema, rows):
            raise PersistenceMigrationRejected()
        for version in range(len(rows) + 1, len(MIGRATIONS) + 1):
            ddl = MIGRATIONS[version - 1]
            await connection.execute(ddl)
            await connection.execute("INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(%s,%s,%s)",
                (version, content_hasher(ddl.encode()).hexdigest(), await _signature(connection, schema, version)))
        await install_if_possible(connection, schema)
        return "upgraded"
    added = tuple(table for _, names in _ADDED_TABLES for table in names)
    if any([await _exists(connection, schema + "." + table) for table in (*_CATALOG_TABLES, *added)]):
        raise PersistenceMigrationRejected()
    adopted = await _baseline(connection, schema)
    await connection.execute("LOCK TABLE " + ",".join(_BASE_TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
    await connection.execute(_LEDGER_DDL)
    await connection.execute("INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(1,%s,%s)",
        (content_hasher(BASE_DDL.encode()).hexdigest(), await _signature(connection, schema, 1)))
    await migrate_workspace(connection, schema)
    for version, ddl in enumerate(MIGRATIONS[1:], 2):
        await connection.execute(ddl)
        await connection.execute("INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(%s,%s,%s)",
            (version, content_hasher(ddl.encode()).hexdigest(), await _signature(connection, schema, version)))
    await install_if_possible(connection, schema)
    return "adopted" if adopted else "created"



