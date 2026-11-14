"""Additive K13 retention metadata; the canonical migration engine owns DDL."""

RETENTION_TABLES = (
    "k13_retention_policy",
    "k13_deletions",
    "k13_project_objects",
    "k13_restore_fences",
)

RETENTION_DDL = """
CREATE TABLE k13_retention_policy (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 project_days integer NOT NULL DEFAULT 30 CHECK(project_days BETWEEN 0 AND 3650),
 terminal_job_days integer NOT NULL DEFAULT 7 CHECK(terminal_job_days BETWEEN 0 AND 3650),
 object_grace_seconds integer NOT NULL DEFAULT 3600 CHECK(object_grace_seconds BETWEEN 60 AND 2592000));
INSERT INTO k13_retention_policy(singleton) VALUES(true);
CREATE TABLE k13_deletions (
 workspace_id text NOT NULL,project_id text NOT NULL,tombstone_id text NOT NULL,
 revision bigint NOT NULL CHECK(revision>=0),
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 eligible_at timestamptz NOT NULL,project_days integer NOT NULL CHECK(project_days BETWEEN 0 AND 3650),
 time_basis text NOT NULL CHECK(time_basis IN ('delete_commit','migration_first_seen','restore_checkpoint')),
 state text NOT NULL DEFAULT 'retained' CHECK(state IN ('retained','purging','purged')),
 purged_at timestamptz,records_removed bigint NOT NULL DEFAULT 0 CHECK(records_removed>=0),
 PRIMARY KEY(workspace_id,project_id),
 FOREIGN KEY(workspace_id,project_id) REFERENCES k05_projects(workspace_id,project_id),
 FOREIGN KEY(workspace_id,project_id,tombstone_id) REFERENCES k05_records(workspace_id,project_id,record_id)
 DEFERRABLE INITIALLY DEFERRED,
 CHECK(eligible_at>=requested_at),CHECK(eligible_at=requested_at+project_days*interval '24 hours'),
 CHECK((state='purged')=(purged_at IS NOT NULL)));
CREATE INDEX k13_deletions_due ON k13_deletions(eligible_at,workspace_id,project_id) WHERE state<>'purged';
CREATE TABLE k13_project_objects (
 workspace_id text NOT NULL,project_id text NOT NULL,sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','removed','shared')),
 PRIMARY KEY(workspace_id,project_id,sha256),
 FOREIGN KEY(workspace_id,project_id) REFERENCES k13_deletions(workspace_id,project_id));
CREATE FUNCTION k13_capture_deletion() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path FROM CURRENT AS $$
DECLARE stamp timestamptz:=clock_timestamp(); days integer; current_revision bigint;
BEGIN
 IF TG_OP<>'INSERT' OR TG_RELID<>'k05_records'::regclass
 OR TG_TABLE_SCHEMA<>(SELECT n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                     WHERE c.oid='k05_records'::regclass) THEN
  RAISE EXCEPTION 'Deletion capture trigger scope does not match' USING ERRCODE='23514';
 END IF;
 IF NEW.record_type<>'deletion' THEN RETURN NULL; END IF;
 SELECT project_days INTO STRICT days FROM k13_retention_policy WHERE singleton;
 SELECT COALESCE(deletion_revision,revision) INTO STRICT current_revision FROM k05_projects
  WHERE workspace_id=NEW.workspace_id AND project_id=NEW.project_id;
 INSERT INTO k13_deletions(workspace_id,project_id,tombstone_id,revision,requested_at,eligible_at,project_days,time_basis)
 VALUES(NEW.workspace_id,NEW.project_id,NEW.record_id,current_revision,stamp,stamp+days*interval '24 hours',days,'delete_commit')
 ON CONFLICT(workspace_id,project_id) DO NOTHING;
 RETURN NULL;
END $$;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION %I.k13_capture_deletion() SET search_path TO pg_catalog,%I,pg_temp',
                current_schema(),current_schema());
END $$;
REVOKE ALL ON FUNCTION k13_capture_deletion() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER k13_deletion_recorded AFTER INSERT ON k05_records
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION k13_capture_deletion();
CREATE FUNCTION k13_deletion_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Deletion fences must be retained' USING ERRCODE='23514'; END IF;
 IF (NEW.workspace_id,NEW.project_id,NEW.tombstone_id,NEW.revision,NEW.requested_at,NEW.eligible_at,NEW.project_days,NEW.time_basis)
 IS DISTINCT FROM
 (OLD.workspace_id,OLD.project_id,OLD.tombstone_id,OLD.revision,OLD.requested_at,OLD.eligible_at,OLD.project_days,OLD.time_basis)
 OR NEW.records_removed<OLD.records_removed OR OLD.state='purged' AND NEW IS DISTINCT FROM OLD
 OR OLD.state='purging' AND NEW.state='retained' THEN
  RAISE EXCEPTION 'Deletion history cannot be rewritten' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER k13_deletion_immutable BEFORE UPDATE OR DELETE ON k13_deletions
 FOR EACH ROW EXECUTE FUNCTION k13_deletion_immutable();
INSERT INTO k13_deletions(workspace_id,project_id,tombstone_id,revision,requested_at,eligible_at,project_days,time_basis)
 SELECT DISTINCT ON(r.workspace_id,r.project_id) r.workspace_id,r.project_id,r.record_id,
 COALESCE(p.deletion_revision,p.revision),stamp.at,stamp.at+policy.project_days*interval '24 hours',
 policy.project_days,'migration_first_seen' FROM k05_records r JOIN k05_projects p
 ON p.workspace_id=r.workspace_id AND p.project_id=r.project_id
 CROSS JOIN k13_retention_policy policy CROSS JOIN (SELECT clock_timestamp() AS at) stamp
 WHERE r.record_type='deletion' ORDER BY r.workspace_id,r.project_id,r.record_id;
CREATE TABLE k13_restore_fences (
 workspace_id text NOT NULL CHECK(workspace_id~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
 project_id text NOT NULL CHECK(project_id~'^p3-project-[0-9a-f]{64}$'),
 tombstone_id text NOT NULL CHECK(tombstone_id~'^p6-deletion-[0-9a-f]{64}$'),
 checkpoint_sha256 text NOT NULL CHECK(checkpoint_sha256~'^[0-9a-f]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(workspace_id,project_id));
CREATE FUNCTION k13_restore_fence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Restored deletion fences are immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER k13_restore_fence_immutable BEFORE UPDATE OR DELETE ON k13_restore_fences
 FOR EACH ROW EXECUTE FUNCTION k13_restore_fence_immutable();
CREATE FUNCTION k13_no_resurrection() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path FROM CURRENT AS $$
BEGIN
 IF TG_OP<>'INSERT' OR TG_RELID<>'k05_projects'::regclass
 OR TG_TABLE_SCHEMA<>(SELECT n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                     WHERE c.oid='k05_projects'::regclass) THEN
  RAISE EXCEPTION 'Project fence trigger scope does not match' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM k13_restore_fences WHERE workspace_id=NEW.workspace_id AND project_id=NEW.project_id) THEN
  RAISE EXCEPTION 'Restored deletion history forbids project recreation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION %I.k13_no_resurrection() SET search_path TO pg_catalog,%I,pg_temp',
                current_schema(),current_schema());
END $$;
REVOKE ALL ON FUNCTION k13_no_resurrection() FROM PUBLIC;
CREATE TRIGGER k13_no_resurrection BEFORE INSERT ON k05_projects
 FOR EACH ROW EXECUTE FUNCTION k13_no_resurrection();
CREATE OR REPLACE FUNCTION k05_object_reference_retention() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM k05_records WHERE workspace_id=OLD.workspace_id AND record_id=OLD.record_id)
 AND NOT EXISTS(SELECT 1 FROM k05_object_references WHERE workspace_id=OLD.workspace_id
                AND record_id=OLD.record_id AND sha256=OLD.sha256) THEN
  IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
            WHERE c.oid='k13_deletions'::regclass
              AND (r.rolname=current_user OR (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)))
  AND EXISTS(SELECT 1 FROM k13_deletions d JOIN k05_records record
             ON record.workspace_id=d.workspace_id AND record.project_id=d.project_id
             WHERE record.workspace_id=OLD.workspace_id AND record.record_id=OLD.record_id
               AND d.state='purging' AND d.eligible_at<=clock_timestamp()) THEN
   RETURN NULL;
  END IF;
  RAISE EXCEPTION 'Canonical artifact references must remain reachable' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION %I.k05_object_reference_retention() SET search_path TO pg_catalog,%I,pg_temp',
                current_schema(),current_schema());
END $$;
"""
