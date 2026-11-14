# Fresh-login DB watermark — K-03/K-05 handoff

K-03 reproduced a real PostgreSQL ordering defect: an intent begun before logout-all could commit after revoke when host-created time was ahead of database time within the allowed skew. A host timestamp cannot establish ordering against the database logout watermark.

K-05 appended V7, preserving V1–V6 SQL/checksums:

- `j01_fresh_login_intents.database_created_at timestamptz NOT NULL DEFAULT clock_timestamp()` is database-generated at insertion. Public DTOs and existing host-created/nonce/PKCE fields remain unchanged.
- The explicit migration locks the intent table and expires existing pending/claimed intents before backfilling the new stamp. Committed history and original host timestamps are preserved. The historical backfill is migration-time metadata, not reconstructed creation time.
- Rerun does not expire new intents. Baseline adoption accepts the exact legacy shape or the known trailing new column with exact type/nullability/default, and rejects incompatible extras.
- K-03 owns adapter/readiness and compares the DB stamp to DB logout_all_at under existing lock ordering; it retains the existing issued-time calculation. J-01 owns matching experimental DDL/package integration.

Focused V7 tests: **19 passed in 22.08s**, real PostgreSQL in owned UUID schemas, no skips. Cases include genuine V6→V7, old pending/claimed expiry, committed/host-created history preservation, rollback/resume, unaffected fresh intents on rerun, exact modern shape adoption and rejection of nullable/wrong default/type/extra columns. No schema in use was migrated. K-03's controlled adapter clock-ahead/revoke integration remains a separate evidence gate until reported.

An operator rollout must stop/drain auth nodes, apply V7 and deploy the new adapter across API nodes before resuming. Existing pending login attempts expire deliberately. An old adapter still comparing host time is not safe in a mixed rollout. This document authorizes no production apply and makes no mixed-version safety claim.
