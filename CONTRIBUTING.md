# Contributing to Aletheia Lab

Thank you for helping improve Aletheia Lab. Contributions are welcome across
code, tests, documentation, benchmark design, reproducibility, and research
methodology.

Aletheia is an evidence-grounded evaluation project. Changes should be small
enough to review, explicit about their assumptions, and supported by tests or
reproducible artifacts.

## Ways to contribute

You can help by:

- fixing bugs or improving error messages;
- adding tests for leakage, determinism, and failure boundaries;
- improving documentation and examples;
- proposing benchmark cases or evidence conditions;
- strengthening evaluation metrics and statistical reporting;
- reporting security, privacy, licensing, or reproducibility concerns.

For substantial changes, open an issue before implementation so the scope and
evaluation criteria can be agreed upon early.

## Development setup

Fork and clone the repository, then create an isolated environment:

```bash
git clone https://github.com/<your-username>/aletheia-lab.git
cd aletheia-lab
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

Run the quality checks before making changes:

```bash
make check
```

## Branches and commits

Create a focused branch from the latest `main`:

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description
```

Use a clear prefix that reflects the change:

- `feat/` for new capabilities;
- `fix/` for bug fixes;
- `docs/` for documentation;
- `test/` for test-only changes;
- `refactor/` for behavior-preserving restructuring;
- `exp/` for bounded research experiments.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(benchmark): add a deterministic drift case
fix(evidence): reject answer-key leakage in visible notes
test(baseline): verify split membership across repeated runs
docs: clarify dataset provenance
```

Keep commits coherent. A commit should explain one meaningful change and remain
safe to review or revert independently.

## Engineering standards

### Reproducibility

- Pass random seeds explicitly; do not rely on global random state.
- Record relevant configuration, package versions, checksums, and provenance.
- Demonstrate reproducibility with independent runs where practical.
- Keep generated artifacts out of Git unless they are intentionally small,
  reviewed fixtures.

### Benchmark integrity

- Keep hidden ground truth separate from diagnosis-visible evidence.
- Treat leakage as a blocking defect.
- Validate manifests and cross-artifact references before evaluation.
- Do not label an outcome as a regression, improvement, or stable result without
  a measured comparison and a documented threshold.
- Preserve negative and null results; do not select cases only because they
  support a preferred claim.

### Tests

- Add a regression test for every bug fix.
- Cover success paths, fail-closed behavior, and important boundary conditions.
- Do not make unit tests depend on network access.
- Avoid importing helper functions from another test module; place shared
  fixtures in `tests/conftest.py`.
- Keep assertions focused on behavior rather than implementation details.

### Code quality

- Target Python 3.11 or newer.
- Add type annotations to public interfaces.
- Keep modules focused and prefer existing project abstractions.
- Do not suppress lint or type errors without a documented reason.
- Avoid unrelated formatting or refactoring in a functional change.

## Local verification

Run the same checks used by CI:

```bash
make lint
make hygiene
make test
```

Before committing, also run:

```bash
git diff --check
git status --short
```

If your change affects deterministic data, baseline, or benchmark generation,
include the exact reproduction command and a concise result summary in the pull
request.

## Pull requests

A pull request should include:

- the problem and why it matters;
- the chosen approach and relevant trade-offs;
- the files or components affected;
- test and reproduction evidence;
- known limitations or follow-up work;
- confirmation that no secrets, private data, or generated artifacts were added.

Keep a pull request focused on one concern. Draft pull requests are encouraged
for early design feedback. Do not merge while required CI checks or review
threads are unresolved.

## Data, privacy, and secrets

Never commit:

- `.env` files, API keys, tokens, or credentials;
- raw or derived datasets that the repository is not authorized to redistribute;
- personal, confidential, or proprietary records;
- generated models, experiment runs, caches, or large binary outputs.

Use `.env.example` for configuration names without real values. Respect the
license and usage terms of every external dataset, model, and dependency.

If you discover a sensitive-data exposure or credential leak, do not open a
public issue containing the affected material. Contact the repository owner
privately and rotate exposed credentials immediately.

## Documentation

Public documentation should describe current behavior, supported commands, and
verified limitations. Keep internal schedules, private notes, task assignments,
and administrative tracking outside the repository.

When behavior changes, update the relevant documentation in the same pull
request. Examples must be runnable and must not claim results that have not been
measured.

## Review principles

Reviews prioritize, in order:

1. ground-truth and data integrity;
2. correctness and fail-closed behavior;
3. reproducibility and test evidence;
4. privacy, licensing, and security;
5. maintainability and clarity.

A green CI run is necessary but does not by itself prove that a research claim
or benchmark design is valid. Reviewers may request additional evidence when a
change affects experimental conclusions.
