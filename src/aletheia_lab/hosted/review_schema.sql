CREATE TABLE j01_annotations (
    workspace_id text NOT NULL,
    project_id text NOT NULL,
    result_id text NOT NULL,
    annotation_id text NOT NULL CHECK (annotation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    revision bigint NOT NULL CHECK (revision > 0),
    author_id text NOT NULL,
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000 AND length(btrim(body)) > 0),
    status text NOT NULL CHECK (status IN ('comment','approved','rejected')),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (workspace_id,result_id,annotation_id,revision),
    FOREIGN KEY (workspace_id,project_id,result_id) REFERENCES k05_records(workspace_id,project_id,record_id),
    FOREIGN KEY (workspace_id,author_id) REFERENCES j01_memberships(workspace_id,user_id)
);
CREATE FUNCTION j01_annotation_insert_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE
    result_kind text;
    prior_revision bigint;
    prior_author text;
BEGIN
    SELECT record_type INTO result_kind FROM k05_records
      WHERE workspace_id=NEW.workspace_id AND project_id=NEW.project_id AND record_id=NEW.result_id;
    IF result_kind IS DISTINCT FROM 'result' THEN
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
CREATE FUNCTION j01_annotation_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Annotation history is immutable' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER j01_annotations_insert_guard BEFORE INSERT ON j01_annotations
FOR EACH ROW EXECUTE FUNCTION j01_annotation_insert_guard();
CREATE TRIGGER j01_annotations_immutable_guard BEFORE UPDATE OR DELETE ON j01_annotations
FOR EACH ROW EXECUTE FUNCTION j01_annotation_immutable_guard();
