# K-03 migration artifact packaging: proposed narrow change

Kiên approved the exact package-data change on 2026-11-10. It is implemented;
wheel/sdist/rebuilt-wheel, installed SQL/CLI and installed PostgreSQL rollout
checks passed. See `k03-migration-package-verification-20261110.md` for scope.

The migration loads its canonical v1 SQL from the installed hosted_auth package.
Source-checkout tests do not prove that setuptools includes this untracked SQL
file in a distributable wheel. The current pyproject has no explicit package-data
entry for it. Missing SQL causes a safe migration failure after installation.

Proposed exact change to the shared pyproject.toml:

```toml
[tool.setuptools.package-data]
"aletheia_lab.hosted_auth" = ["admission_schema.sql"]
```

No dependency/version/script or another package setting changes. This declares
only the canonical K-03 SQL; other local hosted schemas remain outside this task.

Verification will build only the current local source (including WIP), inspect
the wheel/SQL checksum, install that artifact with --no-deps into a separate TEMP
target and import migration from the installed path with source imports excluded.
Then the real migration CLI will be exercised against a dedicated local UUID
PostgreSQL container; source/dependency targets and the shared venv are preserved.
The wheel is a local verification artifact, not a release or permission to
commit/push/publish. No Git operation or existing DB migration is required.
