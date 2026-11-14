-- Isolated evaluation schema; requires experimental session tables.
CREATE TABLE j01_workspaces (
    workspace_id text PRIMARY KEY, name text NOT NULL
);
CREATE TABLE j01_memberships (
    workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    user_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
    role text NOT NULL CHECK(role IN ('owner','admin','analyst','reviewer','viewer')),
    active boolean NOT NULL, revision bigint NOT NULL CHECK(revision >= 0),
    PRIMARY KEY(workspace_id,user_id)
);
CREATE TABLE j01_sources (
    source_id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    creator_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
    request_key_hash text NOT NULL, sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
    payload bytea NOT NULL, manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(workspace_id,creator_id,request_key_hash)
);
CREATE TABLE j01_upload_snapshots (
    snapshot_id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    source_id text NOT NULL REFERENCES j01_sources(source_id),
    payload jsonb NOT NULL
);
