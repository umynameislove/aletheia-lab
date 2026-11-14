# K-03 installed migration and artifact rollout — 2026-11-10

This continues the local migration/rollout work. Kiên explicitly approved adding
only the canonical admission_schema.sql package-data rule in pyproject.toml.
Existing dependency/version changes from other chats were retained. No commit,
push, clone, branch switch or shared-environment migration occurred.

## Change and read-only review

Migration previously read its sibling SQL correctly from the checkout, but
package configuration did not explicitly include that untracked file. The new
`[tool.setuptools.package-data]` entry includes only admission_schema.sql under
aletheia_lab.hosted_auth. MANIFEST.in, dependencies, versions and scripts were
not edited. The canonical v1 SQL and migration implementation were unchanged.

Two read-only review subtasks inspected the migration and packaging. They found
no additional concrete quota-loss/transaction defect in the reviewed code and
identified this artifact gap. This is not a whole-repository/security audit.
Preserved boundary: runtime readiness checks counter table/policy/time, not the
operator-only ledger. Explicit migration still precedes a reviewed rollout.

## Wheel and source artifact verification

Manual test harness: tests/unit/test_k03_migration_package.py. It runs setuptools
already installed in the confirmed interpreter, with egg-info, build, bdist and
dist paths explicitly under a new TEMP directory. It builds from the current
local source and WIP without creating a checkout/worktree or copying WIP to
another working tree. No network/dependency installation is used by the harness.

The harness checks the SQL appears exactly once in the wheel, appears in the
sdist and matches the checkout SHA-256. It safely extracts its own generated
sdist into TEMP, rebuilds another wheel from it and verifies the same checksum.
Archive path traversal and symlink rejection are covered by offline tests.

That rebuilt wheel is installed with --no-index/--no-deps/--no-compile into its
own TEMP target. A child process starts outside the checkout, removes the API
src directory from PYTHONPATH, prepends only the installed target and keeps the
existing isolated dependency paths. Import assertions verify the migration comes
from that installed target; `_ddl()` returns SQL with the expected checksum.
The installed migration CLI's --help also exits successfully.

The first harness invocation encountered Windows cp1258 subprocess reader errors
while decoding UTF-8 build diagnostics; artifact assertions nevertheless reached
success. The harness was corrected to decode logs explicitly as UTF-8. A complete
clean rerun passed without those reader errors:

```text
K03_WHEEL_AND_SDIST_SQL_CHECKSUM=PASS
K03_SDIST_REBUILT_WHEEL_SQL=PASS
K03_INSTALLED_MIGRATION_SQL_AND_CLI=PASS
```

Final installed target:

```text
C:\Users\TRUNGK~1\AppData\Local\Temp\aletheia-k03-package-omrtb5h0\installed
```

The target is a local verification artifact, not a published release. Installation
used existing dependencies; it does not prove a complete fresh install or a
transitive production lock. The artifact contains current local source, including
other chats' WIP, so this is not permission to distribute it as an accepted release.

## Installed CLI on isolated PostgreSQL

The rollout harness can now point migration child processes at the installed
package target. Each such process runs outside the checkout, excludes src from
PYTHONPATH and asserts the CLI file is under that installed target before running.
It uses real `migrate` and `change-policy` CLI commands, including the no-op policy
change and reversed policy rollback. The source harness still drives the rest of
the repository/fixture checks; this does not claim every runtime module executed
from the wheel or that an HTTPS application listener was deployed.

The installed artifact passed the same dedicated PostgreSQL 16 container cases:

- Fresh install, concurrent idempotent reruns and adoption of legacy rows with
  byte-for-byte/equality preservation of counters.
- Version/checksum/table/data/trigger/RLS/index/inheritance drift rejection and
  transaction rollback without a partially installed migration.
- Active-window policy transition rejected; later CLI change/no-op accepted;
  old-policy adapter denied; safe later rollback accepted.
- A separate API LOGIN role could use the quota table with DML grants while DDL
  and ledger access remained denied.
- Actual restart of the dedicated DB, followed by installed CLI UNCHANGED,
  preserved exhausted quotas and preserved API grants.

Final outcomes included:

```text
K03_MIGRATION_AND_LOCAL_ROLLOUT=PASS
K03_ROLLOUT_DATABASE_RESTART_PRESERVES_LEDGER_QUOTA_AND_API_GRANTS=PASS
K03_OWN_ROLLOUT_CONTAINER_AND_VOLUME_REMOVED=PASS
K03_INSTALLED_ROLLOUT_EXIT=0
```

This DB run used the first successful installed target. The clean rebuild's
admission_migration.py, admission_migrate_cli.py and admission_schema.sql were
then compared against that tested target and the checkout; all three hashes
matched. The explicit assertion printed
`K03_FINAL_ARTIFACT_MATCHES_ROLLOUT_MIGRATION_BYTES=PASS`.

Only an aletheia-k03-rollout-UUID container was
created/restarted/removed. Credentials were random child environment values,
never printed. Existing J-01 containers/schemas/configs were not modified.

## Final code checks

| Check | Result |
| --- | --- |
| Current test_k03_*, test_j01_* and test_k02_* unit files | **821 passed in 47.84s**, exit 0; no skip |
| Ruff including C901, 17 auth source files and all K-03 tests | All checks passed, exit 0 |
| Strict mypy on those 17 auth source files | No issues, exit 0 |

The three new K-03 unit cases check the package-data declaration, archive path
traversal and symlink rejection. The rollout smoke files run external services
only when invoked directly; pytest does not run Docker/DB/build CLIs. Counts
overlap the earlier 813/786 results, and the combined selection includes newly
added tests from concurrent chats. Do not add these counts or attribute all of
the increase solely to this packaging change.

The same K-02 Python interpreter, isolated OIDC/CSRF/jsonschema/psycopg targets,
-B/PYTHONDONTWRITEBYTECODE, no pytest cache and fresh TEMP bases were used. Ruff
used --no-cache without fixes; mypy used a separate TEMP cache. Shared venv and
existing dependency targets were unchanged.

## Remaining limits

The migration artifact gap is closed for the tested wheel/sdist/source build.
Still open: K-05 production adoption, reviewed release/transitive lock and fresh
environment installation, deployment performance/failover/restore, coordinated
namespace/key operations, real HTTPS/browser/multi-tab/MFA and J-02 acceptance.
No authoritative external tracker was changed by these local checks.

## Workspace and concurrent changes

The starting snapshot covered 355 WIP paths. Final comparison found five
intentional existing-path edits (the approved package-data rule, the installed
CLI rollout harness and three K-03 documentation updates), three new K-03 files,
and additional runtime/worker/pool/server/auth/test/workflow changes made by other
chats. Those other changes were neither reverted nor incorporated into a claim
of a frozen release. The other 336 baseline hashes matched at the final snapshot.

Branch/HEAD remained feat/product-api at
2bbcdc2a7637c840babd8b71910306c64072bc4b, with no staged paths. The v1 SQL checksum
remained 3bc7d160abdc1ba325f8348e436cbb57b0b39c6913fbc820b325e95877090070. Docker's
final label-filtered inventory showed no remaining K-03 rollout container.

The 821-test result describes the shared source at its execution time. Other
chats continued editing afterwards, including runtime integration tests. The
installed migration evidence is anchored separately by exact artifact/source
migration+CLI+SQL hashes; no production migration code changed in this turn.
