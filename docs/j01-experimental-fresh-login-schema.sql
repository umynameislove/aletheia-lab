-- Fresh-login experiment only; requires the experimental session schema.
CREATE TABLE j01_fresh_login_browsers (
    browser_digest text PRIMARY KEY,
    epoch bigint NOT NULL CHECK (epoch > 0),
    latest_intent text NOT NULL,
    current_context text REFERENCES j01_sessions(context_id)
);
CREATE TABLE j01_fresh_login_intents (
    intent_id text PRIMARY KEY, browser_digest text NOT NULL REFERENCES j01_fresh_login_browsers(browser_digest),
    state_digest text NOT NULL, issuer text NOT NULL, client_id text NOT NULL, redirect_uri text NOT NULL,
    sealed_proof bytea NOT NULL, created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
    browser_epoch bigint NOT NULL, return_to text NOT NULL, reauthenticate boolean NOT NULL,
    previous_context_id text REFERENCES j01_sessions(context_id),
    phase text NOT NULL CHECK (phase IN ('pending', 'claimed', 'committed')),
    claim_digest text, UNIQUE (browser_digest, state_digest)
);
