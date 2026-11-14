# K-03/J-02 recovery and MFA: isolated local evidence

Later scoped K06 audit-interface follow-up: the worker-only adapter now forwards
the canonical audit flag (default False). **74 affected tests and the real PG
HTTP/J02 pair passed**; see the final supplement below. The 1587-test receipt
retains its earlier exact hashes; it is not relabelled as a run of later WIP.

2026-11-10. Kiên approved the local fixture policy: reset password retains the
registered OTP; factor-loss recovery waits for a separate policy. Production IdP
selection and joint hosted acceptance remain open.

## Observed result

The real Keycloak → SMTP/Mailpit → Authlib/JOSE → PostgreSQL flow exited 0 in
155.88 seconds. Imported repository sources and explicit fixture/SQL inputs had
identical hashes before and after this run.

| Check | Observed result |
| --- | --- |
| Normal MFA | Password alone stops at OTP; wrong OTP is denied; original seed completes verified OIDC and real PG session creation. |
| Expired reset | Actual email link, read-back two-second TTL, real five-second wait, fresh HTTP context: denied, no password change; old password still needs OTP. |
| Reset | Real forgot-password forms/email change the password; force-login prevents automatic app callback and OTP enrollment; original OTP credential ID retained. |
| Replay | Completed link denied in both existing and fresh HTTP contexts, without another password update. |
| Old/new password | Old password denied; new password alone still stops at OTP. |
| App epoch | IdP reset alone leaves independent app session valid. Explicit trusted-SQL fixture increments epoch/watermark; old app cookie denied. NOT an IdP event bridge. |
| Fresh MFA | New password and old OTP create a new app context with same actor/subject and epoch 1. |
| Disabled IdP account | Read-back confirms disabled; login reaches neither OTP nor app callback. |
| Cleanup | Own UUID PG schema, own containers/bridge and TLS TEMP removed successfully. |

Only the opt-in driver fills provider forms. App HTTP APIs never collect
password/OTP/reset tokens. TLS CA and hostname verification remain enabled;
navigation is restricted to the exact local realm. The captured app callback
URL is not fetched externally: the driver invokes the existing coordinator.
Default pytest starts no Docker, SMTP or PostgreSQL service.

## Exact version and evidence

Existing checkout aletheia-j01-api, feat/product-api, HEAD
2bbcdc2a7637c840babd8b71910306c64072bc4b plus shared uncommitted WIP.

Evidence directory:
C:/Users/TRUNGK~1/AppData/Local/Temp/aletheia-k03-real-recovery-evidence-3588h1gp

- source-manifest.json SHA256:
  49466b692497a148d1b64f7772d7e14972f1908b33aa1a74cc86a4b316bcef95.
- safe-outcomes.log SHA256:
  48dc49ebedc93ee33392fb3242b3b659a18c67a45d7552f5f03360ff90c107fe.
- changed_during_test: [] for the imported repository/fixture/SQL scope.
- test_k03_keycloak_recovery.py:
  4f51edc2c62d904dc6b08186d17dec4edec4bbabb39cc5a7e71f71a2ad00ef33.
- test_k03_recovery_environment.py:
  525355bd351655f37645edaca54428f9236b413f14c5cc8aa6a2ebc4ceb3b5fd.
- boundary.py:
  64c0661e7b9ff564cc4f9f21b18652e6dbb11f8650b6bbe45c639a8d801a8aa7.
- login.py:
  b05f40998720a6be9b284811ca0f878d06b0d9384eb2ceb43b0d15cb5907793a.
- Live run oidc_discovery.py:
  5dff8dd20c538836005631630dd43bed3aca73dfa21f709a20f3328613301c8a.
  Later annotation-only version:
  5cfa306fba2b0f0e6318dd13a70bb9f9764d399e61fcdeb14a60b153d2d9c336.
  Do not describe the live result as a run of another source hash.

Setup representation hash:
096fbaac77017567a3c0faccda68ff46c4583e0b649b765bb34ec3d0c088ced1.
This is a fixture setup hash, not a reviewed production realm export/approval.

| Runtime | Version / pinned digest |
| --- | --- |
| Python | 3.12.15 |
| Authlib / joserfc | 1.9.5 / 1.8.0 |
| HTTP clients | httpx 0.28.1; httpx2 2.16.0 |
| Crypto / PG | cryptography 50.0.2; psycopg and psycopg-pool 3.3.8 |
| Keycloak | 26.8.2; sha256:3879c623248408de29a5303178b5816c77e8dfef3890c361f8c0bbeaa979ba3b |
| Mailpit | v1.33.1; sha256:aef2aacc11f075ec0fb6874275ee4a421e688e11a96ebb296134d1735c7761ce |

## Scope limits

The browser flow requires password and OTP, without an SSO-cookie shortcut.
The copied reset flow disables its conditional OTP reset and sets force-login=true.
Signed acr='1' is checked only for this pinned fixture; that number alone does
not define MFA. Wrong-factor denial and continued old-seed necessity are the
factor evidence.

Provider metadata/JWKS generation is warmed with a bounded request. Functional
fixture budgets are IdP 10s and backend/coordinator 15s; production defaults
are unchanged. Disposable admin token lasts 900s to span real OTP time steps.
This is not a cold-start/three-second performance result.

The epoch fixture uses real DB time and begins after the real watermark,
without weakening pending-intent checks or mocking clocks. The recorded DB
watermark was 5.942s behind host time. Production clock/propagation needs its
own operator evidence.

Earlier trials exposed bootstrap/JWKS, admin expiry, non-JSON Mailpit DELETE,
provider-page recognition and epoch-bound fresh-login issues. Each owned
environment/schema was removed. Failed trials are not acceptance evidence or
proof of a production defect; the final exact run above passed.

Production IdP/client/HTTPS/assurance and lost-factor policy remain open.
The trusted IdP/admin → app epoch transaction needs K04/K05/operator coordination.
This single-actor fixture does not test A's recovery link in B's authenticated
context or alternate fallback; existing DB tests separately deny new-subject/
same-email account linking. Real hosted two-tab/BFCache/late-response/cache/
draft/download and end-to-end five-second propagation remain joint checks.

J01 owns auth wire/contract/hosted integration, K04 owns workspace/runtime,
K03 owns auth domain and these tests. See k03-auth-wire-interface-review.md for
typed callback outcomes and coordinated future ID-token-hint retention;
k03-hosted-browser-preflight.md records browser readiness gates.
No stage/commit/push/merge, clone/branch switch/worktree or WIP copy performed.


## Final coordinated integration checkpoint

- Selected J01/K02/K03/K04 unit run: **1587 passed in 71.45s**.
  Manifest c56ebf6680aa42536d00fc565e222cf705f69538bf29e0e40765c4445712298b.
  The broad inventory detected only K06's transactions.py changing during this
  run. Source search finds its callers only in K06 tests, outside this selection;
  it is excluded from this acceptance scope. No whole-repository/future-WIP
  freeze is claimed. Current K03 auth/tests, worker, binding/settings and Session
  hashes were separately compared to the run's after-map and all still match.
- Real PG HTTP and J02 compute/rotation/publication/race harnesses: both exit 0
  in 10.95s, imported-module/SQL scope unchanged. Manifest:
  e84b7285dbe91ba62e86debaaf0f9712653e80380d602bec2f1feefd1326db2d.
  Local logout-all denial 0.063s; membership/publication denial 0.093s.
  These use synthetic OIDC and own UUID schemas, all removed.
- Ruff/C901 passed for all K03 tests/auth sources and managed_worker.
  Strict mypy passed on 27 source files after the coordinated type fixes.
- Focused domain246, callback60, CSRF112 and K02legacy70 results overlap the
  final unit run; do not add these counts together.
- The earlier 13 startup-test-double failures and the legacy Session round-trip
  failure were fixed by their respective K03/J01/K02 file owners. Frozen v7
  fixtures and production guards were retained.

Durable artifact directory (hash metadata and safe outcome logs only; no WIP
source copies):
C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k03-verification-20261110/recovery-final-d6c938c8037c48d6ae305f80d0e01850

receipt.json SHA256:
538f714edc04ffb795c1a7c27515dce56b2fd881b33eb55ff65b1ccbe4b04e27.
Original TEMP paths above remain historical evidence locations; byte-identical
manifest/log copies are retained under recovery/, database/ and unit/ here.

| Coordinated current source | SHA256 |
| --- | --- |
| csrf.py | 12129e3b04363c4ec2c3e330a1781c8e1983bbb7282c833efd65b55c337840e7 |
| test_k03_csrf_boundary.py | dbd6e0c79dc0c18b1e65a25039fc4236b8637c5931cedca55a841ee2a9fc3f3d |
| callback_http.py, J01 writer | 0c6a040e829b2a9d521793424d732edfc04600fcddbcd067a6d6c62fcee9eb24 |
| logout_http.py, J01 writer | bf14e5e2110056a77655f98fe22dbd74ef25299ba0eb9bf7d1a0406ea40b1af2 |
| managed_worker.py checkpoint | 3b1296289efa23c07e38f6539222c7e9419d3e3c1eb8518d2654087f5f528919 |

CSRF verifies source/current proof before effects. It permits missing Content-Type
only for DELETE; explicit invalid media and other unsafe methods still reject.
The route must inspect the actual bounded stream and accept only a truly empty
missing-media DELETE. Content-Length:0 is not proof of emptiness. K04 owns that
route/parser. Duplicate If-Match rejects before session lookup; validated other
headers retain multiplicity for downstream CL/idempotency parsers. Cookie is
joined once and Authorization stripped so library Bearer exemption cannot
replace cookie-bound proof.

J01's fixed callback failure redirect and K03 caller expectations agree.
Success helpers assert the exact allowed destination, so a failed 303 cannot
masquerade as a successful login. Existing replay/no-cookie/no-partial-session
and generic lost-claim assertions remain.

Worker writer is handed to K02 for the separately coordinated P19 operation
hook after this checkpoint. Preserve canonical binding startup, per-account
worker authority and strict API cookies. Later hooks/K04/K06 repository patches
need their own source hashes and affected-caller checks; these results do not
qualify future edits. K03 remains auth-domain owner; no parallel queue/store/
contract was introduced. Git index was empty and branch/HEAD unchanged.


## Later audit-interface supplement

K06 requested review of its prepared worker audit patch. K03 verified its manifest
hashes as data, compared the diff and applied only the owned two-line adapter
change after the canonical parent constructor was present. Worker authority SQL,
original context, per-account SHARE locking, current credential/epoch/role/source
checks and strict API behavior are unchanged.

worker_sessions.py now aad5d3a8c9531c525719bc7242d0585a47a8fc0facd6cd3101a7cd15d76c8b05.
Owned worker test now 10d8b23bda3748aea0752aadad845c01474bce01200fbe6ab9bd733574376186.
False/True forwarding and worker-specific authorization tests plus the existing
worker/binding/K02 lifecycle callers: 74 passed in 5.08s; Ruff/C901 and strict
mypy for the adapter passed. These are affected-path checks after the broad
1587-test checkpoint, not additional unique test totals.

The subsequent real PG HTTP/J02 pair both exited 0 with unchanged imported-source
and SQL hashes. Its manifest SHA256 is
b67cfc6acbed253c546b78bc62d32dc4640fbaa5ded0d3eabdfe2591a9004291.
Owned UUID schemas were removed; default audit=False compatibility was exercised.
Audit=True constructor forwarding does not prove enabled audit event/migration/
rollback integration; those checks belong to K06/K02.

The prepared managed_worker patch queried audit schema before checking actual
runtime DB binding. K03 reported this ordering defect to K06/K02/J01 and did not
apply that runtime patch. Binding must precede audit queries under the bounded
startup/cleanup flow. K02 remains the only worker runtime writer for P19+audit.

Supplement receipt and byte-identical DB hash/log artifacts are in audit-forward/
under the durable directory above. Supplement receipt SHA256:
190cd4e78277ca2c897b1894add2ae6aa632d443b411e7848bcea95b3fb72f5f.
