-- V2: retain the exact annotation history rules while accepting native upload results.
CREATE OR REPLACE FUNCTION j01_annotation_insert_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE
    result_kind text;
    prior_revision bigint;
    prior_author text;
BEGIN
    SELECT record_type INTO result_kind FROM k05_records
      WHERE workspace_id=NEW.workspace_id AND project_id=NEW.project_id AND record_id=NEW.result_id;
    IF result_kind IS NULL OR result_kind NOT IN ('result','upload_result') THEN
        RAISE EXCEPTION 'Annotation parent must be a result' USING ERRCODE='23514';
    END IF;
    SELECT revision,author_id INTO prior_revision,prior_author FROM j01_annotations
      WHERE workspace_id=NEW.workspace_id AND result_id=NEW.result_id AND annotation_id=NEW.annotation_id
      ORDER BY revision DESC LIMIT 1;
    IF NEW.revision <> COALESCE(prior_revision,0)+1
       OR (prior_author IS NOT NULL AND prior_author <> NEW.author_id) THEN
        RAISE EXCEPTION 'Annotation history must advance for its author' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
