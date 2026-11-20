-- Experimental component schema. Install only into an isolated test schema.
-- Not a production migration or a complete authentication/session schema.
CREATE TABLE j01_login_browsers (
    browser_digest text PRIMARY KEY CHECK (browser_digest ~ '^[0-9a-f]{64}$'),
    epoch bigint NOT NULL CHECK (epoch > 0),
    latest_intent text NOT NULL
);

CREATE TABLE j01_login_claims (
    intent_id text PRIMARY KEY,
    browser_digest text NOT NULL REFERENCES j01_login_browsers(browser_digest),
    state_digest text NOT NULL CHECK (state_digest ~ '^[0-9a-f]{64}$'),
    browser_epoch bigint NOT NULL CHECK (browser_epoch > 0),
    expires_at timestamptz NOT NULL,
    phase text NOT NULL CHECK (phase IN ('pending', 'claimed')),
    sealed_proof bytea NOT NULL CHECK (octet_length(sealed_proof) BETWEEN 30 AND 512),
    claim_digest text CHECK (claim_digest IS NULL OR claim_digest ~ '^[0-9a-f]{64}$'),
    CHECK ((phase = 'pending' AND claim_digest IS NULL) OR
           (phase = 'claimed' AND claim_digest IS NOT NULL)),
    UNIQUE (browser_digest, state_digest)
);
