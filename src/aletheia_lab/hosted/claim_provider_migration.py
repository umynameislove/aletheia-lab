"""Store the one-send intent before handing an approved request to a provider."""

CLAIM_PROVIDER_DDL = """
CREATE TABLE k21_claim_provider_attempts (
 workspace_id text NOT NULL REFERENCES j01_workspaces(workspace_id),
 plan_sha256 text NOT NULL CHECK(plan_sha256~'^[0-9a-f]{64}$'),
 job_id text NOT NULL CHECK(job_id~'^job_[0-9a-f]{32}$'),
 attempt_id text NOT NULL UNIQUE CHECK(attempt_id~'^[0-9a-f]{32}$'),
 request_sha256 text NOT NULL CHECK(request_sha256~'^[0-9a-f]{64}$'),
 model text NOT NULL CHECK(length(model) BETWEEN 1 AND 128),
 held_microusd integer NOT NULL CHECK(held_microusd>0),
 dispatched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deadline_at timestamptz NOT NULL,
 received_at timestamptz,
 outcome jsonb,
 PRIMARY KEY(workspace_id,plan_sha256), UNIQUE(workspace_id,job_id),
 CHECK(dispatched_at<deadline_at),
 CHECK((received_at IS NULL)=(outcome IS NULL)),
 CHECK(received_at IS NULL OR received_at>=dispatched_at),
 CHECK(outcome IS NULL OR jsonb_typeof(outcome)='object'));
"""
