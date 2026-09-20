# Release-profile stability and dependency-drift audit

The release-profile audit is closed locally against source commit
`85cd1453dab54b0383d849f8331934d0143bead4`. The machine-readable receipt is
[`reports/release/release-profile-stability-audit-v1.json`](../reports/release/release-profile-stability-audit-v1.json).

## Decision

Status: **PASS**.

- The evaluation profile passed 897 tests in each of three isolated runs using
  `PYTHONHASHSEED` values 1, 104729 and 209759. There were zero inconsistent
  outcomes and every run remained below its 600-second timeout.
- The project profile passed 391 tests in 22.66 seconds against the 120-second
  budget.
- The full profile passed 3,562 tests with six platform-dependent skips and
  88.72% line coverage against the 88.00% floor.
- No tracked or untracked production module under `src/` differs from the
  audited source commit. The policy's 90% changed-source floor for new modules
  is therefore not applicable; it has not been waived for a changed module.
- Ruff, strict mypy, repository hygiene, maintainability, the 16-case mutation
  audit, Bandit at the CI severity/confidence threshold, `pip check`, and the
  live vulnerability audit all passed.
- The resolved environment contains 87 distributions on Python 3.12.14. Its
  canonical inventory SHA-256 is
  `ad8e97493e8b8e49992dd6286303b330a19219ac8794cd1a8f4149d1ab0536b3`.
  The complete name/version inventory is preserved in
  [`reports/release/resolved-dependency-inventory-v1.json`](../reports/release/resolved-dependency-inventory-v1.json).

The 32 full-profile warnings are one understood upstream Joblib/NumPy
deprecation class; they were not inconsistent across repeats and did not hide a
test failure. The vulnerability audit skipped only the editable local
`aletheia-lab` distribution, whose source was covered by the static and test
gates above.

## Drift rule

This receipt records the resolved environment actually audited; it is not a
claim that dependency ranges form a universal cross-platform lock. A change to
the Python version, `pyproject.toml`, resolved inventory hash, CI profile, or
bound audit scripts reopens this audit. This engineering PASS does not authorize
a main diagnosis run and does not alter any frozen RQ0 result.
