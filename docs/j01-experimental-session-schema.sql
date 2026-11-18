-- Isolated testing only. This is not a production migration.
CREATE TABLE hosted_identity_bindings (
    issuer text NOT NULL, subject text NOT NULL,
    user_id text PRIMARY KEY, display_name text NOT NULL, email text NOT NULL,
    email_verified boolean NOT NULL, enabled boolean NOT NULL,
    account_epoch bigint NOT NULL CHECK (account_epoch >= 0),
    onboarding text NOT NULL CHECK (onboarding IN ('ready', 'needs_workspace')),
    last_workspace_id text, logout_all_at timestamptz,
    UNIQUE (issuer, subject)
);
CREATE TABLE j01_sessions (
    context_id text PRIMARY KEY,
    user_id text NOT NULL REFERENCES hosted_identity_bindings(user_id),
    csrf_token text NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'revoked', 'expired')),
    issued_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL,
    rotated_at timestamptz NOT NULL, idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    generation bigint NOT NULL CHECK (generation >= 0),
    session_epoch bigint NOT NULL CHECK (session_epoch >= 0),
    CHECK (issued_at <= rotated_at AND rotated_at <= last_seen_at AND
           last_seen_at < idle_expires_at AND idle_expires_at <= absolute_expires_at)
);
CREATE INDEX j01_sessions_user_id ON j01_sessions(user_id);
CREATE TABLE j01_session_credentials (
    digest text PRIMARY KEY CHECK (digest ~ '^[0-9a-f]{64}$'),
    context_id text NOT NULL REFERENCES j01_sessions(context_id),
    state text NOT NULL CHECK (state IN ('active', 'rotated')),
    generation bigint NOT NULL CHECK (generation >= 0)
);
CREATE UNIQUE INDEX j01_session_one_current_credential ON j01_session_credentials(context_id)
    WHERE state = 'active';
