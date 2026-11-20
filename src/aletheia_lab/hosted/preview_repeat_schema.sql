CREATE OR REPLACE FUNCTION k07_confirm_preview_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
    -- A repeated upload may reuse its first capture, bound to identical bytes in this workspace.
    SELECT snapshot.payload INTO saved FROM j01_upload_snapshots snapshot
      JOIN j01_sources origin_source ON origin_source.workspace_id=snapshot.workspace_id
        AND origin_source.source_id=snapshot.source_id AND origin_source.sha256=pinned.source_sha256
      WHERE snapshot.snapshot_id=NEW.result->>'snapshot_id' AND snapshot.workspace_id=NEW.workspace_id
        AND snapshot.payload->>'source_id'=snapshot.source_id FOR SHARE OF snapshot,origin_source;
    IF saved IS NULL OR saved->>'source_sha256' IS DISTINCT FROM pinned.source_sha256 THEN
        RAISE EXCEPTION 'The snapshot source is unavailable.' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
