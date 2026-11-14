# K-03 maintained-library OIDC verification draft

Update 2026-11-10: the final combined offline run passed 453 tests, including
CSRF, bounded auth outage/admission and selected J-01 integration tests. Ruff and
strict mypy passed for ten K-03-owned source files. Exact scope and limitations
are in `k03-verification-20261110.md`; historical runs below are unchanged.

Written on 2026-11-09; corrected library adapter passed the focused offline run. This supplements
the existing K-03 implementation record. Shared models/fixtures, K-02 files and
the existing `pyproject.toml` are unchanged.

## Proposed verification dependencies

| Distribution | Direct pin | Role |
| --- | --- | --- |
| Authlib | 1.9.5 | Authorization Code + PKCE request/exchange and CodeIDToken claims |
| joserfc | 1.8.0 | Maintained JOSE signature/key implementation |
| httpx2 | 2.16.0 | Authlib async transport integration |

Pins were checked against the official release pages/documentation on 2026-11-09:

- [Authlib 1.9.5](https://pypi.org/project/Authlib/1.9.5/)
- [Authlib client/PKCE](https://docs.authlib.org/en/stable/oauth2/client/http/index.html)
- [Authlib changelog](https://docs.authlib.org/en/stable/upgrades/changelog.html)
- [joserfc 1.8.0](https://pypi.org/project/joserfc/1.8.0/)
- [joserfc JWT](https://jose.authlib.org/en/guide/jwt/)
- [HTTPX2 2.16.0](https://pypi.org/project/httpx2/2.16.0/)

These are direct verification pins, not a transitive hash lock, approved release
environment or a claim of compatibility. Pip's report will record the actual
resolved transitive artifacts, including cryptography. Qualification requires
the subsequent tests and runtime/lifecycle review. No implicit replacement or
alias of the existing K-02 `httpx` is performed.

## Operator-supplied installation result

Kiên supplied pip output with `K03_INSTALL_EXIT=0`. The command used the confirmed
K-02 interpreter and a fresh `--target` directory, not installation into the
shared virtual environment:

```text
C:\Users\TRUNGK~1\AppData\Local\Temp\aletheia-k03-auth-deps-913b444034d04a29a7d8c3bdeff32517
```

Resolved distributions reported by pip: Authlib 1.9.5, joserfc 1.8.0,
httpx2/httpcore2 2.16.0, cryptography 50.0.2, anyio 4.16.0, cffi 2.1.1,
h11 0.16.0, idna 3.20, truststore 0.10.5, typing-extensions 4.16.0 and
pycparser 3.12. The installation report is in that temporary directory.
Do not infer library import/ABI compatibility, test success or release approval
from successful package installation. The first verification result and pending
fix are recorded below.

The next run must check that OIDC source is imported from this API worktree,
and Authlib/JOSE/HTTPX2/cryptography are imported from this explicit target.
Prepend it only to the current PowerShell session's PYTHONPATH. Do not install
again or alter the old venv merely to run the prepared offline tests.

## Code scope

`AuthlibOidcClient` reuses K-02 RuntimeSettings and K-03 AuthPolicy. It receives
trusted provider endpoints and a public-key set from the runtime owner, with a
maximum initial validity of 300 seconds. Configured issuer/client/redirect are
not duplicated into a second wire contract. Endpoint origins default to the
issuer's HTTPS origin; additional origins require explicit trusted allowlisting.
The source does not trust endpoints or keys supplied by callback/JWT headers.

Authlib builds the code request using state, nonce, S256 and the exact registered
redirect. The token exchange reconstructs the callback URL from configuration,
uses the saved state/verifier, public `none` or confidential `client_secret_basic`
client authentication, a total timeout and no automatic retry/redirect. It does
not request offline_access or retain tokens for background refresh. Local session
renewal remains separate from IdP token refresh.

The transport rejects unexpected status/media/encoding, duplicate or non-object
JSON and bodies above 64 KiB before Authlib receives them. JWT input is capped at
16 KiB. Only configured asymmetric algorithms are allowed, and token-selected
key URLs, embedded keys and access-token JWT profiles are rejected. joserfc
verifies signatures using only the supplied public key set; Authlib validates
issuer/audience/azp/nonce/timestamps and at_hash when present. A fresh-login request
also requires a suitable auth_time. Verified-email/provisioning remain downstream
AccountResolver policy, not an implicit owner or email-based account link.

The library seam uses narrow internal protocols around Authlib's untyped API.
No OAuth/JOSE implementation is copied into the application. Parsing a JWT header
for unsupported inputs is not used as proof of identity; signature/claim validation
must succeed before producing ValidatedIdentity. Provider text and token diagnostics
are replaced by fixed safe exceptions. Application/proxy/telemetry logging still
requires separate qualification.

## Evidence this group is intended to produce

Tests in `test_k03_oidc_library.py` require real library imports and synthetic
signatures; missing dependencies fail collection rather than yielding skipped
qualification. They use generated ephemeral private keys only inside the test
process and HTTPX2 MockTransport, without sockets, discovery or real IdP traffic.

Cases include request PKCE/nonce/state/redirect, public/confidential exchange,
wrong signature/issuer/audience/azp/nonce, missing claims, algorithm/profile/key-source
rejection, clock skew, expiry, malformed/oversized/streamed/compressed responses,
outage/redirect/timeout/cancellation and fresh auth_time. The failed first run is
recorded below together with the subsequent passing focused rerun.

## Operator-supplied library verification

The import check confirmed the API worktree's OIDC source and the explicit
temporary dependency directory for Authlib 1.9.5, joserfc 1.8.0, HTTPX2 2.16.0
and cryptography 50.0.2. The combined first-slice/adapter/OIDC/related regression
run reported **276 passed, 2 failed in 9.82s**, pytest exit 1. Ruff passed, exit 0.
Mypy checked seven source files and reported two auth_time comparison errors,
exit 1. These counts overlap earlier verification and must not be added.

Both runtime failures were at authorization URL generation. The installed
Authlib `create_authorization_url(url, state=None, ...)` generates a fresh state
when the method's state argument is omitted; it does not reuse constructor state
for this method. K-03 now explicitly supplies the saved transaction state and
preserves the returned-state consistency check. The existing PKCE/state URL test
continues to require the exact saved value, rather than accepting a new state.

The auth_time numeric guard was split to make type narrowing explicit while
preserving boolean/string/null rejection and freshness bounds. Additional signed
token tests cover those types and a future auth_time. No installed dependency,
K-02/J-01 source or shared fixture was changed. Kiên supplied the subsequent
focused run: **54 passed in 2.32s**, pytest exit 0; Ruff including C901 exit 0;
strict mypy reported no issues in seven source files, exit 0. This qualifies
the corrected crypto/protocol adapter in the synthetic scope. Counts overlap
prior runs and the earlier combined run remains recorded as failed.

## Explicit missing pieces

- The runtime must source material from approved configuration or an issuer-pinned
  bounded discovery/JWKS loader, refresh it under a bounded single-flight policy
  and integrate readiness/resource closing. No such loader is supplied in this
  group; expired material fails closed rather than fetching attacker-selected URLs.
- The LoginCoordinator/internal atomic ports in `login.py` passed the subsequent
  combined offline run (328 tests; pytest/Ruff/strict mypy exits 0), recorded in
  `k03-login-transaction-handoff.md`. K-05 must implement/qualify the actual shared
  transaction store. It must
  atomically claim state, validate browser/expiry/intent and prevent replay.
  AuthorizationAttempt alone is not evidence of consumption.
- Login commit orchestration is now written but its real provisioning/epoch/
  account-switch transaction still requires K-05 qualification. No production
  session is issued here and no route exposes the library adapter directly.
- K-02/J-01 must review routing, Problems and response semantics. The subsequent
  CSRF/origin wrapper is now verified offline, with experimental J-01 callers;
  real HTTP/browser acceptance remains open. See
  `k03-csrf-handoff.md`. Shared rate limit/admission is still required.
- IdP selection, client registration, real HTTPS, recovery/MFA, shared PostgreSQL,
  browser scenarios and the measured revoke target remain unverified inputs/work.

The current adapter must not be treated as a completed login deployment. No
K-03.01–06 or J-02 acceptance is closed by this draft.

## Operator-only dependency installation

Use the existing confirmed K-02 interpreter but install the direct pins and their
dependencies into a fresh GUID-named directory under the operator's TEMP. Pip
must use `--target`, no upgrades of the shared venv, no source/repo installation
and no mutation of `pyproject.toml`. Download wheels from the official PyPI index;
save an installation report in that new directory. This action requires network
downloads and writes temporary package/report files; it does not configure an IdP.

Retain the directory path and report for the subsequent verification command.
Supply package-install success/errors first; test commands depend on that result.
Do not paste index credentials, tokens or private configuration. Tests will later
prepend that explicit directory and this API worktree's src to PYTHONPATH for
that PowerShell session, then run offline. Do not set PYTHONPATH globally or reuse
these packages as an approved production lock without qualification.
