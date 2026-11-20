-- Additive K-04 extension of the existing J-01 identity/source schema.
ALTER TABLE j01_sources ADD CONSTRAINT j01_sources_workspace_source UNIQUE(workspace_id,source_id);
ALTER TABLE j01_upload_snapshots ADD CONSTRAINT j01_snapshots_source_scope
    FOREIGN KEY(workspace_id,source_id) REFERENCES j01_sources(workspace_id,source_id);

CREATE TABLE j01_source_grants (
    workspace_id text NOT NULL,
    source_id text NOT NULL,
    user_id text NOT NULL,
    actions text[] NOT NULL CHECK(actions <@ ARRAY['read','write','review','export','delete']::text[]
                                AND array_position(actions,NULL) IS NULL),
    revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
    PRIMARY KEY(workspace_id,source_id,user_id),
    FOREIGN KEY(workspace_id,source_id) REFERENCES j01_sources(workspace_id,source_id),
    FOREIGN KEY(workspace_id,user_id) REFERENCES j01_memberships(workspace_id,user_id)
);

CREATE TABLE j01_invitations (
    invitation_id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    user_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
    role text NOT NULL CHECK(role IN ('admin','analyst','reviewer','viewer')),
    creator_id text NOT NULL,
    creator_revision bigint NOT NULL CHECK(creator_revision>=0),
    token_digest text NOT NULL UNIQUE CHECK(token_digest ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    accepted_at timestamptz,
    FOREIGN KEY(workspace_id,creator_id) REFERENCES j01_memberships(workspace_id,user_id)
);

CREATE FUNCTION j01_workspace_owner_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE scope text;
BEGIN
    IF TG_OP='DELETE' THEN scope := OLD.workspace_id; ELSE scope := NEW.workspace_id; END IF;
    -- A row write also fences stale REPEATABLE READ/SERIALIZABLE owner checks.
    UPDATE j01_workspaces SET name=name WHERE workspace_id=scope;
    IF FOUND AND NOT EXISTS(SELECT 1 FROM j01_memberships m JOIN hosted_identity_bindings a ON a.user_id=m.user_id
                           WHERE m.workspace_id=scope AND m.active AND m.role='owner' AND a.enabled AND a.email_verified) THEN
        RAISE EXCEPTION 'Workspace requires an active owner' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
        UPDATE j01_workspaces SET name=name WHERE workspace_id=OLD.workspace_id;
        IF FOUND AND NOT EXISTS(SELECT 1 FROM j01_memberships m JOIN hosted_identity_bindings a ON a.user_id=m.user_id
                               WHERE m.workspace_id=OLD.workspace_id AND m.active AND m.role='owner' AND a.enabled AND a.email_verified) THEN
            RAISE EXCEPTION 'Workspace requires an active owner' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER j01_memberships_owner_guard
AFTER INSERT OR UPDATE OR DELETE ON j01_memberships DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION j01_workspace_owner_guard();

CREATE CONSTRAINT TRIGGER j01_workspaces_owner_guard
AFTER INSERT ON j01_workspaces DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION j01_workspace_owner_guard();

CREATE FUNCTION j01_membership_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.role,OLD.active) IS DISTINCT FROM (NEW.role,NEW.active) AND NEW.revision<=OLD.revision THEN
        RAISE EXCEPTION 'Membership revision must advance' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER j01_memberships_revision_guard BEFORE UPDATE ON j01_memberships
FOR EACH ROW EXECUTE FUNCTION j01_membership_revision_guard();
