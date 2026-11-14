-- Local experiments only; not a production migration.
CREATE TABLE j01_jobs (
    job_id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
    creator_id text NOT NULL REFERENCES hosted_identity_bindings(user_id), credential_digest text NOT NULL,
    operation text NOT NULL CHECK(operation IN ('preview','confirm','analyze')),
    key_hash text NOT NULL, request_hash text NOT NULL, request jsonb NOT NULL,
    request_id text NOT NULL, state text NOT NULL CHECK(state IN ('queued','running','succeeded','failed')),
    lease_digest text, lease_until timestamptz, result jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(workspace_id, creator_id, operation, key_hash)
);
