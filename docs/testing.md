# Test execution profiles

The repository keeps one authoritative full test gate while offering smaller
feedback loops for development. Profiles change execution cost, not test
semantics. Plain `pytest` and the `full` profile continue to collect the entire
suite.

## Recommended workflow

Run the profile nearest to the code being changed while iterating:

```bash
python scripts/run_test_profile.py fast
python scripts/run_test_profile.py project
python scripts/run_test_profile.py research
```

- `fast` runs ordinary unit tests and excludes integration, property, frozen
  research-regression, and large local-artifact checks.
- `project` runs tests whose node IDs concern the project import and identity
  boundary across unit, integration, and property directories.
- `research` runs frozen benchmark/scientific regression tests without adding
  coverage instrumentation.

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

## Continuous integration

Pull requests run the full suite on Python 3.11 and an uninstrumented full
compatibility run on Python 3.12. Coverage is measured once on Python 3.11,
where the 88 percent threshold remains blocking. Pushes to feature branches do
not duplicate an open pull request's matrix; pushes to `main` remain fully
validated. New commits cancel stale in-progress runs for the same pull request.

## Runtime interpretation

Coverage roughly doubles local wall time because it instruments the complete
package. Research-regression tests also intentionally pay for subprocess,
dataset, reproducibility, and byte-level artifact checks. Those costs protect
scientific claims and should be moved between profiles rather than deleted.
