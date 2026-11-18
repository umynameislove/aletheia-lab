-- Additive K-03 proposal; K-05 owns production migration/privilege review.
-- Install only through an explicit operator migration, never HTTP/startup DDL.
CREATE TABLE k03_auth_admission (
    namespace varchar(128) NOT NULL,
    operation text NOT NULL CHECK (operation IN ('login_start','callback','renew','logout','logout_all')),
    global_limit integer NOT NULL CHECK (global_limit BETWEEN 1 AND 10000),
    subject_limit integer NOT NULL CHECK (subject_limit BETWEEN 1 AND global_limit),
    window_seconds integer NOT NULL CHECK (window_seconds BETWEEN 1 AND 3600),
    window_number bigint NOT NULL CHECK (window_number >= -1),
    last_seen timestamptz NOT NULL,
    attempts integer NOT NULL CHECK (attempts BETWEEN 0 AND global_limit),
    subjects jsonb NOT NULL CHECK (jsonb_typeof(subjects) = 'object'),
    PRIMARY KEY (namespace, operation)
);
