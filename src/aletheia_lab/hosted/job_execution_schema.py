"""Append-only K09 migration bytes; the canonical migration engine owns execution."""

JOB_EXECUTION_DDL = """
ALTER TABLE j01_jobs DROP CONSTRAINT k05_job_state_v3;
ALTER TABLE j01_jobs ADD CONSTRAINT k05_job_state_v3 CHECK(state IN
 ('queued','running','cancel_requested','succeeded','failed','cancelled','expired'));
ALTER TABLE j01_jobs DROP CONSTRAINT k02_job_terminal_time;
ALTER TABLE j01_jobs ADD CONSTRAINT k02_job_terminal_time CHECK(terminal_at IS NULL OR
 (state IN ('succeeded','failed','cancelled','expired') AND terminal_at>=created_at));
ALTER TABLE j01_jobs ADD COLUMN failure_kind text CHECK(failure_kind IN
 ('transient','permanent','authorization','attempts_exhausted','deadline','cancelled'));
ALTER TABLE j01_jobs ADD COLUMN available_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE TABLE k09_job_attempts (
 workspace_id text NOT NULL,job_id text NOT NULL,epoch integer NOT NULL CHECK(epoch BETWEEN 1 AND 3),
 started_at timestamptz,heartbeat_at timestamptz,lease_until timestamptz,
 finished_at timestamptz,outcome text NOT NULL CHECK(outcome IN
 ('running','cancel_requested','succeeded','failed','cancelled','expired','lease_lost','retrying')),
 failure_kind text CHECK(failure_kind IN
 ('transient','permanent','authorization','attempts_exhausted','deadline','cancelled')),
 PRIMARY KEY(workspace_id,job_id,epoch),
 FOREIGN KEY(workspace_id,job_id) REFERENCES j01_jobs(workspace_id,job_id),
 CHECK((outcome IN ('running','cancel_requested'))=(finished_at IS NULL)),
 CHECK(started_at IS NULL OR heartbeat_at>=started_at),
 CHECK(started_at IS NULL OR finished_at IS NULL OR finished_at>=started_at));
CREATE TABLE k09_queue_turns (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 operation text NOT NULL CHECK(operation IN ('preview','confirm','analyze','follow_up','refresh')),
 last_claim_at timestamptz NOT NULL,
 PRIMARY KEY(workspace_id,operation));
CREATE INDEX k09_jobs_pending ON j01_jobs(workspace_id,operation,available_at,created_at,job_id)
 WHERE state='queued';
CREATE INDEX k09_jobs_active ON j01_jobs(workspace_id,operation,lease_until)
 WHERE state IN ('running','cancel_requested');
CREATE OR REPLACE FUNCTION k02_job_terminal_stamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('succeeded','failed','cancelled','expired') AND NEW.state<>OLD.state THEN
  RAISE EXCEPTION 'terminal job cannot reopen';
 END IF;
 IF NEW.deadline_at>OLD.deadline_at THEN RAISE EXCEPTION 'job deadline cannot extend'; END IF;
 IF NEW.state='running' AND (OLD.state<>'running' OR NEW.lease_digest IS DISTINCT FROM OLD.lease_digest
  OR OLD.lease_until<clock_timestamp()) THEN
  NEW.attempt_count:=OLD.attempt_count+1;
  NEW.failure_kind:=NULL;
 END IF;
 IF NEW.state IN ('succeeded','failed','cancelled','expired') AND
  OLD.state IN ('queued','running','cancel_requested') THEN
  NEW.terminal_at:=clock_timestamp();
 END IF;
 RETURN NEW;
END; $$;
CREATE FUNCTION k09_attempt_stamp() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=clock_timestamp();
BEGIN
 IF NEW.attempt_count>OLD.attempt_count THEN
  UPDATE k09_job_attempts SET outcome='lease_lost',finished_at=stamp
   WHERE workspace_id=OLD.workspace_id AND job_id=OLD.job_id AND epoch=OLD.attempt_count
    AND finished_at IS NULL;
  INSERT INTO k09_job_attempts(workspace_id,job_id,epoch,started_at,heartbeat_at,lease_until,outcome)
   VALUES(NEW.workspace_id,NEW.job_id,NEW.attempt_count,stamp,stamp,NEW.lease_until,'running');
 ELSIF NEW.attempt_count>0 AND NEW.state IN ('running','cancel_requested') THEN
  UPDATE k09_job_attempts SET heartbeat_at=CASE WHEN NEW.state='running' THEN stamp ELSE heartbeat_at END,
   lease_until=NEW.lease_until,outcome=NEW.state
   WHERE workspace_id=NEW.workspace_id AND job_id=NEW.job_id AND epoch=NEW.attempt_count
    AND finished_at IS NULL;
 END IF;
 IF NEW.attempt_count>0 AND NEW.state IN ('queued','succeeded','failed','cancelled','expired') THEN
  UPDATE k09_job_attempts SET outcome=CASE WHEN NEW.state='queued' THEN 'retrying' ELSE NEW.state END,
   finished_at=stamp,failure_kind=NEW.failure_kind
   WHERE workspace_id=NEW.workspace_id AND job_id=NEW.job_id AND epoch=NEW.attempt_count
    AND finished_at IS NULL;
 END IF;
 RETURN NULL;
END; $$;
CREATE TRIGGER k09_attempt_stamp AFTER UPDATE ON j01_jobs FOR EACH ROW EXECUTE FUNCTION k09_attempt_stamp();
INSERT INTO k09_job_attempts(workspace_id,job_id,epoch,lease_until,finished_at,outcome)
 SELECT workspace_id,job_id,attempt_count,lease_until,
 CASE WHEN state IN ('running','cancel_requested') THEN NULL ELSE COALESCE(terminal_at,clock_timestamp()) END,
 CASE WHEN state='queued' THEN 'retrying' ELSE state END
 FROM j01_jobs WHERE attempt_count>0;
CREATE FUNCTION k09_attempt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.finished_at IS NOT NULL OR (NEW.workspace_id,NEW.job_id,NEW.epoch,NEW.started_at)
  IS DISTINCT FROM (OLD.workspace_id,OLD.job_id,OLD.epoch,OLD.started_at) THEN
  RAISE EXCEPTION 'attempt history is immutable';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER k09_attempt_immutable BEFORE UPDATE ON k09_job_attempts
 FOR EACH ROW EXECUTE FUNCTION k09_attempt_immutable();
"""

# Keep V12 bytes frozen; existing terminal rows remain unchanged during V17.
JOB_CONFLICT_DDL = """
ALTER TABLE j01_jobs DROP CONSTRAINT j01_jobs_failure_kind_check;
ALTER TABLE j01_jobs ADD CONSTRAINT k09_job_failure_kind_v2 CHECK(failure_kind IN
 ('transient','permanent','authorization','attempts_exhausted','deadline','cancelled','conflict'));
ALTER TABLE k09_job_attempts DROP CONSTRAINT k09_job_attempts_failure_kind_check;
ALTER TABLE k09_job_attempts ADD CONSTRAINT k09_attempt_failure_kind_v2 CHECK(failure_kind IN
 ('transient','permanent','authorization','attempts_exhausted','deadline','cancelled','conflict'));
"""
