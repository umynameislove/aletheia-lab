CREATE TABLE j01_project_grants (
    workspace_id text NOT NULL,
    project_id text NOT NULL,
    user_id text NOT NULL,
    actions text[] NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (workspace_id,project_id,user_id),
    FOREIGN KEY (workspace_id,project_id) REFERENCES k05_projects(workspace_id,project_id),
    FOREIGN KEY (workspace_id,user_id) REFERENCES j01_memberships(workspace_id,user_id),
    CHECK (actions <@ ARRAY['read','write','review','export','delete']::text[]),
    CHECK (array_position(actions,NULL) IS NULL),
    CHECK (cardinality(actions)=0 OR (array_ndims(actions)=1 AND 'read'=ANY(actions)))
);
CREATE FUNCTION j01_project_grant_revision_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF (NEW.workspace_id,NEW.project_id,NEW.user_id) IS DISTINCT FROM
       (OLD.workspace_id,OLD.project_id,OLD.user_id)
       OR NEW.revision <> OLD.revision+1 THEN
        RAISE EXCEPTION 'Project grant revision must advance' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER j01_project_grants_revision_guard BEFORE UPDATE ON j01_project_grants
FOR EACH ROW EXECUTE FUNCTION j01_project_grant_revision_guard();
