# Hosted producer/consumer CI without runtime services

`.github/workflows/hosted.yml` has an independent `contracts` job. It exports
current J01/K04/K07/project-operation models, validates their schemas, runs the
actual dashboard consumer against the fresh J01/K04 directories, runs the
offline hosted unit tests, self-checks the unified OpenAPI, and builds demo,
live and hosted assets. The existing live bundle budgets remain blocking.
The final build is hosted. The former `ci.yml` dashboard job moved here so the
consumer has the Python backend required by its parity tests and is not run
twice against different fixture defaults.
The job ID is `contracts`, while its check-run label remains `dashboard`.

The contract job has no PostgreSQL service, database URL, Docker command, IdP
credential or dependency on the backend job. Manual workflow dispatch defaults
`run_postgres` to **false**. Leave it false for this path. The separate `backend`
job retains the PostgreSQL/HTTPS integration gate on pull requests and pushes
to main; manual dispatch must explicitly opt in to its service container.
No remote run is implied by a local pass or by this workflow definition.

The PostgreSQL job selects every current `tests/integration/test_k0[2-7]_*.py`
module, including session renewal/logout races and API shutdown/effect races.
K03–K07 database aliases point to that job's isolated K02 database. Explicit
large-load, provider or destructive recovery opt-ins still retain their own
guards; selecting a module does not qualify a skipped case. Pytest adds
`tests/unit` to its configured helper path so an integration module can also be
collected alone, without depending on another test being imported first or on
an inherited shell `PYTHONPATH`.

## Local equivalent

Use the API source root in a disposable working copy, Python 3.12 (the package
supports 3.11+), Node 24 (package minimum 22.12), and npm. Install the declared
dependencies before running the commands:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,hosted]" "httpx==0.28.1" "psycopg[binary]==3.3.8"
npm --prefix dashboard/web ci --ignore-scripts
export PYTHON="$PWD/.venv/bin/python"
export PYTHONDONTWRITEBYTECODE=1
CONTRACT_QA_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aletheia-contracts.XXXXXX")"
python -B scripts/check_j01_schema.py fixtures/j01/producer-review-v7
python -B scripts/export_j01_review.py "$CONTRACT_QA_ROOT/j01-producer-current"
python -B scripts/export_k04_contract.py "$CONTRACT_QA_ROOT/k04-producer-current"
python -B scripts/export_k07_contract.py "$CONTRACT_QA_ROOT/k07-producer-current"
python -B scripts/export_k02_project_operations.py --output "$CONTRACT_QA_ROOT/k02-project-operations-current"
export J01_FIXTURES="$CONTRACT_QA_ROOT/j01-producer-current"
export K04_FIXTURES="$CONTRACT_QA_ROOT/k04-producer-current"
npm --prefix dashboard/web test
python -B scripts/check_j01_schema.py "$J01_FIXTURES"
python -B -m pytest -o addopts= -q -p no:cacheprovider -m "not integration" \
  tests/unit/test_k02_*.py tests/unit/test_j01_*.py tests/unit/test_k03_*.py \
  tests/unit/test_k04_*.py tests/unit/test_k05_*.py tests/unit/test_k06_*.py \
  tests/unit/test_k07_*.py tests/unit/test_security_ci_contract.py
python -B scripts/export_hosted_openapi.py --repo . \
  --inventory docs/k02-original-operation-inventory.csv \
  --output "$CONTRACT_QA_ROOT/hosted-openapi-current"
npm --prefix dashboard/web run build
npm --prefix dashboard/web run build:live
npm --prefix dashboard/web run budget
npm --prefix dashboard/web run build:hosted
```

Check `hosted-openapi-current/verification.json`: `source_stable` must be true;
the workflow asserts this after the exporter succeeds. Keep the new output
directory and logs as evidence. J01 refuses an existing output directory;
create a new root for each run instead of overwriting an earlier export.

Without the two fixture environment variables, `npm test` uses historical
review fixtures and does not reproduce the current producer check. Its P1
tests invoke the actual Python backend and the Unicode code generator; Node
dependencies alone are insufficient. Missing Python/npm dependencies must be
installed, not turned into skips. Platform-specific Windows unit skips on a
POSIX runner remain explicit and do not qualify Windows runtime behavior.
The three PostgreSQL query-probe cases located under `tests/unit/` are marked
`integration`: the contract job deliberately deselects them, and the separate
PostgreSQL job runs that marked group with its isolated database URL.

This path does not run Chrome, PostgreSQL integration, real IdP login, staging,
deployment or load tests. `npm run e2e:hosted` is a separate controlled Chrome
check when a local Chrome executable is available; it is not IdP acceptance.
