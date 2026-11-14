# K-03 local verification — 2026-11-10

This records the earlier 453-test slice. Subsequent approved runtime fixes,
725-test regression and local Keycloak/PostgreSQL checks are in
`k03-runtime-verification-20261110.md`; do not interpret the earlier open bugs
below as a claim that those later fixes are absent.

Kiên explicitly authorized the assistant to run commands and continue coding,
while withholding commit/push. This supersedes the earlier operator-only command
rule for this chat. No branch/worktree changes, WIP copying, commit or push were
performed. All edits remain inside the previously approved K-03 paths.

## Observed checkout and runtime

- Existing API worktree: `aletheia-j01-api`, `feat/product-api`, HEAD
  `2bbcdc2a7637c840babd8b71910306c64072bc4b`.
- Local J-01/K-02/dashboard WIP has grown since the previous handoff. A new hash
  snapshot of 322 modified/untracked paths was taken before this group's edits;
  it is a comparison of this turn's starting point, not a previous-day baseline.
- Interpreter: the already confirmed K-02 `metric-review-venv`, Python 3.12.15.
- Existing isolated OIDC target was reused with Authlib 1.9.5, joserfc 1.8.0 and
  httpx2 2.16.0. The shared venv and pyproject were not modified.
- asgi-csrf 0.11, itsdangerous 2.2.0 and python-multipart 0.0.33 were installed
  as wheels into a separate target with pip's installation report:

```text
C:\Users\TRUNGK~1\AppData\Local\Temp\aletheia-k03-csrf-deps-9f13922825994d1191ee231068e11d5a
```

The first sandbox download failed DNS resolution; the rerun outside the sandbox
succeeded, exit 0. Sandboxed pytest had fixture/temp-directory errors and a
stalled retry; those runs were stopped and do not count as passing checks. The
successful test runs used explicit new GUID-named TEMP bases outside the sandbox.
No existing test directory was supplied as pytest basetemp.
An import assertion subsequently confirmed csrf/abuse/oidc source in this API
worktree and asgi_csrf/itsdangerous/python_multipart/Authlib/joserfc/httpx2 in their
intended isolated targets. It passed, with no credential/config values printed.

The final hash comparison found five intended existing K-03 documentation edits
and four new K-03 files (abuse source/test and outage/verification notes). Two
other paths changed concurrently: pyproject.toml and J-01's progress report.
This group neither edited nor reverted them. All other 315 baseline path hashes,
including the existing auth implementations and their tests, matched. Git still
had no staged paths and the same branch/HEAD at the final check. This is a
point-in-time comparison; the other chats can continue editing local WIP.

## Passing checks on the final code

| Check | Result |
| --- | --- |
| Focused CSRF | 83 passed in 4.31s, exit 0 |
| CSRF plus original K-03 and selected callers, before abuse group | 420 passed in 15.74s, exit 0 |
| Final combined K-03 plus selected K-02/J-01 | **453 passed in 7.69s, exit 0** |
| Ruff including C901, K-03-owned source and all test_k03 files | All checks passed, exit 0 |
| Strict mypy, K-03-owned source | No issues in 10 source files, exit 0 |

Counts overlap; they must not be added. The 31-test initial abuse run preceded
the final URL-recovery guard adjustment and actual discovery-storm test; the
453-test result covers those final changes.

The final pytest selection was all eight `tests/unit/test_k03_*.py` files plus:

- test_k02_read_backend.py
- test_k02_hosted_application.py
- test_j01_session_capabilities_contract.py
- test_j01_callback_http.py
- test_j01_login_start_http.py
- test_j01_source_http.py
- test_j01_runtime_routes.py
- test_j01_oidc_discovery.py

It used `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-o addopts=''`, `-q`,
`-p no:cacheprovider` and an explicit new basetemp. PYTHONPATH prepended the two
isolated dependency targets and this API worktree's src for each command.
Ruff used `--no-cache` without --fix; mypy used a separate GUID-named TEMP cache.

The ten source files checked were __init__, boundary, csrf, identity, login,
oidc, repositories, sessions, settings and abuse. Newly present J-01-owned
experimental auth modules were exercised only through the selected tests; this
report does not claim to have linted/typed all of J-01 or audited the whole repo.

## Scope and outstanding coordination

The final tests use synthetic authority, controlled schedules, generated signing
keys and mock HTTP. They start no IdP/database/server and call no public service.
They do not establish real shared DB atomicity, restart, real browser cookie
ordering, account recovery/MFA, multi-tab cleanup, audit redaction or the measured
five-second revoke/membership target.

Existing J-01 code now includes experimental HTTP, discovery, PostgreSQL and
managed runtime components. Its progress report describes local Keycloak/DB
experiments; those historical claims were read, not independently rerun here.
Their existence replaces the earlier assumption that no local experiment exists,
but does not establish approved staging, production migrations or joint acceptance.

The concurrently updated J-01 report now also records a real loopback HTTPS
Keycloak/PostgreSQL check with two synthetic accounts. Its attempted in-session
switch from A to B failed: password-only reauthentication returned A again,
while revoking the old app session. Real browser automation was unavailable.
These are J-01-reported results, not executions by this K-03 group. The desired
account-selection/reauth/IdP logout policy needs joint review and a corrective
end-to-end test; prompt=login alone is not evidence of successful account switch.

An integration discrepancy needs J-01/Quân review: `callback_http.py` clears the
login binding cookie on success, while the coordinator handoff retains it across
intents. A late successful callback can clear the cookie required by a newer
intent; no browser response-order test closes this case. This turn does not edit
that concurrently developed file or claim browser-bound supersession is accepted.

See `k03-csrf-handoff.md` and `k03-auth-outage-handoff.md` for precise remaining
composition/store/contract work. Authoritative assignment trackers outside this
repo are unchanged; no K-03.01–06 or J-02 joint acceptance is marked passed.
