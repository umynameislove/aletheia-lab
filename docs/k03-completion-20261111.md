# K-03/J-02 — independent local completion, 2026-11-11

K-03's independent auth implementation and available local recovery/J-02
qualification are complete. This does not close the joint hosted-browser or
production acceptance. The current six-row matrix is
[k03-coverage-20261111.md](k03-coverage-20261111.md).

## Source and ownership

The existing checkout is C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án/aletheia-j01-api,
branch feat/product-api, HEAD 2bbcdc2a7637c840babd8b71910306c64072bc4b plus local WIP.
All 621 received runtime/config files initially matched K02 production-freeze-v2
(runtime-set 488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3).
That receipt is a starting baseline, not a claim that later WIP equals it.

The new K03 production delta consists of a private fixed-stage diagnostic helper
and observations in start_http.py, callback_http.py and login.py. No HTTP DTO,
wire status/reason, migration, session/admission policy, deadline, retry,
identity/clock/current-authority guard or dependency value was changed. One
example-config comment was corrected to reflect implemented shared admission.

K03 repaired its worker-binding test against the actual canonical worker and
applied K04's exact two-line WorkspaceManagement readiness fake to its own
runtime-integration test. The worker/canonical checker/admin CLI/shared
repositories/migrations and production runtime remain owned by K04; K03 did
not edit them. K04 made concurrent managed_runtime/workspace_management changes;
their observed hashes and the exact K03 caller patch are in the final receipt.
No UI/consumer, branch/worktree, WIP copy, stage, commit, push or merge.

## Qualified checks

All paths below are under C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/
k03-verification-20261111. Each receipt binds actual before/after hashes; counts
overlap and are not additive.

| Check | Result and exact evidence |
| --- | --- |
| Stable core auth component | 607 passed in 23.73s; 16 test files / 135 actual imported-source, owned-auth, SQL and config inputs unchanged. core-auth-703ebca912c54bc4a16b57fc9b2a640b |
| Broader existing callers | Latest 1079 passed in 45.41s; Ruff/C901 and strict mypy (27 auth source files) passed. Concurrent K04 source/test changes make full-WIP qualification false. offline-c217f721fa554bb696e351046ced1626; earlier stable 1077 snapshot in offline-a9cca8805371483da7e64b8e53d130ff |
| Worker binding fixture repair | 101 focused callers passed; Ruff/C901 passed. worker-binding-applied-7f42c6e5fef24bd9ad9b9b3d2352aa65 |
| K04 readiness caller handoff | 10 focused tests passed; Ruff/C901 passed. workspace-health-caller-edddf580c7e34128921e4281bfe3f311 |
| Private PostgreSQL readiness | Actual PostgreSQL 16.15 on an ephemeral loopback port; own container/anonymous volume removed. resume-private-pg-fa9e232da34143a9a9e3ab6d8bc2f9f1 |
| HTTP/session/account boundary | Renew/early renewal, logout-all, A/B isolation, fresh login after revoke/expiry, live-context conflict denial, disabled/email policy, no email-based new-subject link and equal-watermark fence passed. HTTP function returned successfully inside the historical combined run; the later J02 failure does not turn that entire run into a pass. private-acceptance-6d8fc78a0c51471cb1d499e1252b4978 |
| Real extended recovery/MFA | PASS 146.0s; private-acceptance-64228102b8a447f9b37ed05858d0bdf9 |
| Local J02 backend | PASS 13.156s; private-acceptance-000840acef8741e3ac64a6ba3c359a7c |
| Wheel/sdist/installed migration | SQL bytes in wheel and sdist, rebuilt-wheel SQL, installed CLI and installed auth imports passed; all 620 installed source files matched the 622-input build snapshot. package-f8e04ea033164c6892d9ede5fe36130d |
| Canonical browser preflight | Existing J01 checker validated 20 payloads; HostedReady=false. coverage-review-20261111/hosted-preflight-inventory.json |

The wheel SHA256 is 95fb2dfb1a7e69380cfb2e7b293df51dc7b5a4b75e5294c6497a1f24cd1ed39e.
Later K04 runtime/workspace changes are outside that package snapshot; it proves
K03 auth/SQL/CLI packaging, not a release of the latest complete shared WIP.
Canonical admission SQL, V7 LOGIN_CLOCK_DDL and migration versions 1–10 were
preserved; no shared deployment/schema was automatically migrated. V7 SQL SHA256:
54345cefb305b31e62c0babbfcc33e6e6a4e4436a14ec160e8f19eed581ef303.

The real recovery run used pinned Keycloak 26.8.2, Mailpit v1.33.1, strict TLS
verification, Authlib/joserfc and private PostgreSQL. It proved real expired and
replayed reset links, old-password denial, original OTP retained/required after
reset, exact subject continuity, disabled-provider login denial, actual operator
CLI revoke of A while B remained authenticated, A-link-in-B with no app/factor
transfer, and a real signed new subject reusing A's email denied as unprovisioned
without a session. The cross-context reset action was not applied in this fixture;
this does not define a blanket policy that all valid bearer reset links must fail.
This is local retained-factor policy, not a production IdP selection.

The J02 run used actual PostgreSQL, composed in-process ASGI routes and a real
bounded compute child. Its OIDC provider and several result fixtures were
synthetic. It proved normal multi-rotation job continuity while old API cookies
were denied, five roles/current grants, foreign scope and forged identity denial,
per-account locking without blocking B, snapshot lease checks, current-authority
publication, targeted trusted-operator session/pending-login/renew/job revocation,
concurrent renew/logout-all without resurrection, and reconstruction/callback
replay denial. Local membership denial measured 0.078s and operator denial 0.110s
after trusted commit. These do not measure browser tabs or delay from an external
IdP event before the app receives a trusted revocation command.

The four private proofs have unchanged input manifests and verified cleanup;
final owner-labelled container/network counts are zero, and PG anonymous-volume
removal is checked. Fixture summary:
resume-fixtures-c24407f879af431ebd5dd47040ab8ee2/resume-fixture-receipt.json
SHA256 ba8a4d3b5840cbe194f83cd0c3a6ac9c1db15279ec01f24251ccf01e6a03dca7.
Actual psycopg is 3.3.8; psycopg-pool is 3.3.3. Older documents naming pool 3.3.8
are historical and are not the dependency version used here.

## Transients and diagnostic limit

The historical J01 runs 88da4240b21843b8893096526edaf079 and
ff10dccabe8b474b9a8a03b92fefabd3 lacked request-bound stage/elapsed evidence.
Their 503/idp_unavailable cannot establish an IdP-only cause. The initial local
combined run 6d8fc also stopped at its second login-start after unbound-operator
ordering. Its cause remains UNKNOWN. The later single diagnostic J02 run passed;
observation/assertion changes are not labelled a production fix for the old error.

The new logger aletheia.auth.diagnostics emits only auth_stage_failed with fixed
route/stage/outcome and elapsed_ms. It preserves timeout/cancellation and HTTP
behavior. New J02 observation records only status, allowlisted media/code and
elapsed_ms; a callback success requires exact /app, so a failure 303 cannot be
mistaken for success through an older valid session. No URL/query/state/code/
cookie/subject/provider body or exception details are logged. The historical J01
child globally disables logging; a future diagnostic runner must explicitly
capture only this safe namespace. See k03-auth-transient-investigation-20261111.md.

The first 1061-pass/7-stale-worker-fixture failures and the later 1078-pass/one-caller
failure with four concurrent K04 path changes are retained. Later 1079-pass runs
also retain their explicit moving-K04 manifests. The first core run observed a
changing K04 catalog dependency and was unqualified. The final 16-file core cut
records its actual imported-source closure with no changed inputs; the operator
CLI's tests and real operation have separate receipts. No moving shared snapshot
is relabelled as a complete-WIP pass. Fixture fixes preserve production guards.

## Remaining external acceptance and handoff

- Quân owns actual hosted mounting, renew/logout-all/signed-out actions, scoped
  private-state fencing, multiple tabs/BFCache/account switching and same-release
  browser acceptance. No browser-ready hosted bundle or coordinated browser-trusted
  origin/CA was supplied. Canonical fixtures are valid; that is not browser proof.
- The environment/IdP owner chooses production issuer/client/HTTPS, assurance and
  lost-factor recovery policy and qualifies deployed budgets/keys/logging/rollout.
  The approved local policy retains OTP when resetting the password.
- Trusted manual revoke is implemented and tested. No second identity repository,
  email auto-link, application password/reset service or speculative webhook was
  introduced. The current-session API and existing operator authority are covered;
  no unapproved public multi-device inventory contract is invented.
- Joint J02 rows and release gates need the real receiver/environment receipt.
  K03 supplies the exact seven-row personal tracker proposal in
  coverage-review-20261111/tracker-seven-rows-DRAFT.json. Shared CSVs remain with
  the coordinator's single writer; no joint pass or release SHA was fabricated.
- K03-to-K04-handoff.txt carries stable auth hashes and the applied K03 caller
  target 6920db383d198ac74380707d6bc70020b7ea270cafca1d8d3667008dce07ba30.
  Runtime integration stays with K04. The TXT for Kiên to send Quân is unsent.

The final completion-receipt.json records source and primary-evidence hashes, the received
baseline delta, current Git/index state and verification limits. Older sealed J01
artifacts and other chats' WIP were preserved.
