-- V3: ordinary review history stays immutable; only an owner purging an eligible
-- tombstoned project may remove its private annotation bodies.
CREATE OR REPLACE FUNCTION j01_annotation_immutable_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF TG_OP='DELETE'
       AND EXISTS(SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
                  WHERE c.oid='j01_annotations'::regclass
                    AND (r.rolname=current_user OR (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)))
       AND EXISTS(SELECT 1 FROM k13_deletions d JOIN k05_records marker
                  ON marker.workspace_id=d.workspace_id AND marker.project_id=d.project_id
                    AND marker.record_id=d.tombstone_id AND marker.record_type='deletion'
                  WHERE d.workspace_id=OLD.workspace_id AND d.project_id=OLD.project_id
                    AND d.eligible_at<=clock_timestamp() AND d.state='purging') THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Annotation history is immutable' USING ERRCODE='23514';
END;
$$;
DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION %I.j01_annotation_immutable_guard() SET search_path TO pg_catalog,%I,pg_temp',
                   current_schema(),current_schema());
END $$;
