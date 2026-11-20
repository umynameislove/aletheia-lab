CREATE TABLE k06_action_audit (
    audit_id text PRIMARY KEY CHECK (audit_id ~ '^audit_[0-9a-f]{32}$'),
    request_id text CHECK (request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    operation_id text NOT NULL CHECK (operation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    actor_id text NOT NULL CHECK (actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    workspace_id text NOT NULL CHECK (workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    action text NOT NULL CHECK (action ~ '^[a-z][a-z0-9_.:-]{0,63}$'),
    effect_id text NOT NULL CHECK (effect_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    revision bigint CHECK (revision >= 0),
    job_id text CHECK (job_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workspace_id,actor_id) REFERENCES j01_memberships(workspace_id,user_id),
    UNIQUE (workspace_id,actor_id,action,operation_id,effect_id)
);
CREATE INDEX k06_action_audit_operation ON k06_action_audit(workspace_id,actor_id,operation_id,audit_id);
CREATE INDEX k06_action_audit_request ON k06_action_audit(request_id,audit_id) WHERE request_id IS NOT NULL;
CREATE FUNCTION k06_action_audit_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'Committed action metadata is immutable' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER k06_action_audit_immutable BEFORE UPDATE OR DELETE ON k06_action_audit
FOR EACH ROW EXECUTE FUNCTION k06_action_audit_immutable();
CREATE TRIGGER k06_action_audit_no_truncate BEFORE TRUNCATE ON k06_action_audit
FOR EACH STATEMENT EXECUTE FUNCTION k06_action_audit_immutable();
