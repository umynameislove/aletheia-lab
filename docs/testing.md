# Test execution profiles

The repository keeps one authoritative full test gate while offering smaller
feedback loops for development. Profiles change execution cost, not test
semantics. Plain `pytest` and the `full` profile continue to collect the entire
suite.

## Recommended workflow

Run the profile nearest to the code being changed while iterating:

```bash
python scripts/run_test_profile.py fast
python scripts/run_test_profile.py contract
python scripts/run_test_profile.py project
python scripts/run_test_profile.py research
python scripts/run_test_profile.py evaluation
```

- `fast` runs ordinary unit tests and excludes integration, property, frozen
  research-regression, and large local-artifact checks.
- `contract` runs architecture direction, filesystem publication,
  maintainability, CI and security contracts as an early fail-fast gate.
- `project` runs tests whose node IDs concern the project import and identity
  boundary across unit, integration, and property directories.
- `research` runs frozen benchmark/scientific regression tests without adding
  coverage instrumentation.
- `evaluation` runs the provider-neutral execution, visibility, immutable-store,
  structural-closeout, leakage, project/evidence, reproducibility, and CI
  contract tests. It always reports the 20 slowest tests and has a five-minute
  POSIX timeout. Windows receives a twelve-minute ceiling for the same
  undeselected suite because durable immutable-file operations are substantially
  slower there.
- `windows-publication` exercises the shared filesystem primitive and every
  immutable store whose durability behavior differs across Windows and POSIX.

Additional pytest arguments may follow `--`, for example:

```bash
python scripts/run_test_profile.py project -- -x --durations=20
```

P3 closeout changes must exercise the persisted end-to-end generation directly:

```bash
pytest tests/integration/test_project_snapshot_regression_pipeline.py -q
python scripts/run_test_profile.py project -- --durations=20
```

The first command checks import, mapping, snapshot, regression, evidence,
persistence, restart recovery, typed lineage and closeout reconciliation as one
pipeline. The project profile remains the portable blocking gate and also runs in
the Windows CI job.

Before opening or updating a pull request, run the authoritative local gate:

```bash
python scripts/run_test_profile.py full
```

The full profile enforces the 88 percent coverage floor. Large-artifact tests
remain part of this profile when their ignored local evidence stores are
available; they are never silently removed from the default suite.

Before changing evaluation infrastructure, run its reproducibility gate three
times under distinct process hash seeds:

```bash
python scripts/run_test_profile.py evaluation --repeat 3
```

The intended budget is at most five minutes per evaluation run on a reasonable
POSIX development or CI machine and twelve minutes on Windows CI. Ordinary
unit/property tests target two seconds, and deterministic fixture-provider
integration paths target 15 seconds. The profile output is the runtime report;
investigate its top 20 entries rather than reducing property examples or
removing regression tests.

Record the exact resolved environment and run the controlled guard audit with:

```bash
python scripts/report_dependency_inventory.py
python scripts/run_evaluation_mutation_audit.py
python scripts/check_maintainability.py
```

The inventory is canonical, path-free, and self-hashing. The mutation audit uses
temporary source copies and requires the mapped regression test to detect every
mutation; it never edits tracked source. The maintainability audit blocks growth
in C901 complexity, unreviewed modules over 800 lines, direct hash duplication,
and publication logic outside the shared filesystem core.

## Continuous integration

Pull requests run the full suite on Python 3.11 and an uninstrumented full
compatibility run on Python 3.12. Coverage is measured once on Python 3.11,
where the 88 percent threshold remains blocking. Pushes to feature branches do
not duplicate an open pull request's matrix; pushes to `main` remain fully
validated. New commits cancel stale in-progress runs for the same pull request.
The evaluation profile repeats under three hash seeds on Python 3.11, runs once
for Python 3.12 compatibility, and runs once in the blocking Windows job. The
contract profile runs before dataset acquisition on both Linux interpreters.
The dedicated Windows publication profile replaces an unversioned list of test
paths. Pip caches are keyed from `pyproject.toml`; dependency-audit logs include
the exact resolved inventory digest.

## Runtime interpretation

Coverage roughly doubles local wall time because it instruments the complete
package. Research-regression tests also intentionally pay for subprocess,
dataset, reproducibility, and byte-level artifact checks. Those costs protect
scientific claims and should be moved between profiles rather than deleted.
