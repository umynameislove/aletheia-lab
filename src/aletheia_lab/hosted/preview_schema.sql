CREATE TABLE k07_import_previews (
    preview_id text PRIMARY KEY CHECK (preview_id ~ '^prev_[0-9a-f]{32}$'),
    workspace_id text NOT NULL,
    source_id text NOT NULL,
    creator_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_state_sha256 text NOT NULL CHECK (source_state_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=1048576),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workspace_id,source_id) REFERENCES j01_sources(workspace_id,source_id),
    CHECK (payload ?& ARRAY['preview_id','workspace_id','source_id','source_sha256','source_state_sha256','expires_at']
       AND payload->>'preview_id'=preview_id AND payload->>'workspace_id'=workspace_id
       AND payload->>'source_id'=source_id AND payload->>'source_sha256'=source_sha256
       AND payload->>'source_state_sha256'=source_state_sha256
       AND (payload->>'expires_at')::timestamptz=expires_at)
);
CREATE INDEX k07_import_previews_expiry ON k07_import_previews(expires_at,preview_id);
CREATE INDEX k07_jobs_preview_ref ON j01_jobs(workspace_id,(request->'mapping'->>'_preview_id'))
    WHERE operation='confirm';

CREATE TABLE k07_import_failures (
    job_id text PRIMARY KEY REFERENCES j01_jobs(job_id),
    workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workspace_id,job_id) REFERENCES j01_jobs(workspace_id,job_id)
);

CREATE FUNCTION k07_confirm_preview_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pinned k07_import_previews%ROWTYPE;
    source_hash text;
    saved jsonb;
BEGIN
    IF NEW.operation<>'confirm' OR NEW.state<>'succeeded' OR OLD.state='succeeded' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO pinned FROM k07_import_previews
      WHERE preview_id=NEW.request->'mapping'->>'_preview_id'
        AND workspace_id=NEW.workspace_id AND source_id=NEW.request->>'source_id'
        AND creator_id=NEW.creator_id FOR SHARE;
    IF NOT FOUND OR pinned.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'The preview is unavailable.' USING ERRCODE='23514';
    END IF;
    SELECT sha256 INTO source_hash FROM j01_sources
      WHERE workspace_id=NEW.workspace_id AND source_id=pinned.source_id FOR SHARE;
    IF source_hash IS DISTINCT FROM pinned.source_sha256 THEN
        RAISE EXCEPTION 'The source changed.' USING ERRCODE='23514';
    END IF;
    SELECT payload INTO saved FROM j01_upload_snapshots
      WHERE snapshot_id=NEW.result->>'snapshot_id' AND workspace_id=NEW.workspace_id
        AND source_id=pinned.source_id FOR SHARE;
    IF saved IS NULL OR saved->>'source_sha256' IS DISTINCT FROM pinned.source_sha256 THEN
        RAISE EXCEPTION 'The snapshot source is unavailable.' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER k07_confirm_preview_guard BEFORE UPDATE OF state,result ON j01_jobs
    FOR EACH ROW EXECUTE FUNCTION k07_confirm_preview_guard();
