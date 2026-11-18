"""Append-only v16 status-snapshot revision; no historical event-delivery claim."""

JOB_UPDATES_DDL = """
ALTER TABLE j01_jobs ADD COLUMN status_revision bigint NOT NULL DEFAULT 1
 CHECK(status_revision BETWEEN 1 AND 9223372036854775807);
CREATE FUNCTION k15_job_status_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.state,NEW.operation,NEW.created_at,NEW.deadline_at,NEW.terminal_at,
     NEW.attempt_count,NEW.max_attempts)
  IS DISTINCT FROM
    (OLD.state,OLD.operation,OLD.created_at,OLD.deadline_at,OLD.terminal_at,
     OLD.attempt_count,OLD.max_attempts) THEN
  NEW.status_revision:=OLD.status_revision+1;
 ELSE
  NEW.status_revision:=OLD.status_revision;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER k15_job_status_revision BEFORE UPDATE ON j01_jobs
 FOR EACH ROW EXECUTE FUNCTION k15_job_status_revision();
"""
