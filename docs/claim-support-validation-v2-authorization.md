# Claim-support V2 diagnosis-cohort authorization

## Purpose and boundary

CV2-08 freezes the exact 360-request V2 diagnosis cohort after the separate
seven-request qualification has passed. CV2-09 adds the one-use cohort
executor, immutable terminal store, independent verifier and technical
admission reducer. The merged executor may consume a fresh prospective,
source-commit-bound authorization; this implementation boundary itself sends
no provider request.

Execution closes all 360 diagnosis terminals, including deterministic `B0`
requests and explicit technical failures. It does not assign relation labels,
select or materialize 200 claims, open protected outcomes, or create a human
packet. Relation execution remains a separately authorized downstream stage.

## Admission evidence

Planning independently rebuilds the immutable private qualification store and
requires all of the following:

- status `claim_support_validation_v2_qualification_passed`;
- exactly 7/7 parsed terminal requests;
- exactly 7/7 locally accepted first witnesses;
- zero technical and zero semantic-validation failures;
- `full_cohort_authorization_unlocked: true`;
- synthetic-only results that were not admitted to the corpus.

The registered qualification receipt is
`ab0b82b59a392c2dc460fdf47b55b0e67292d641f43d7078f4bb6add4dbb6ae2`
and its terminal-store hash is
`03e7f2d6cdb397b9ba57e739a44d7d94fbb1b868d69aa4317d3a308dce2f2f95`.
These results establish transport and source-claim expressiveness only. They do
not establish label balance, corpus feasibility or scientific performance.

## Frozen cohort

The planner rebuilds every request projection from authenticated repository
inputs:

- 360 diagnosis cells in 15 balanced rounds;
- 315 `gpt-4.1-2025-04-14` model requests;
- 45 deterministic-local `B0` requests;
- the same 2,048-token output ceiling and at most two provider attempts for
  every model-backed variant;
- one-second global pacing and bounded Retry-After-aware backoff;
- a response schema bound to the evidence IDs visible in each context;
- the frozen expressiveness amendment appended to every model prompt.

Each projection has its own content hash. The plan also binds the complete
request census, projection census, runtime manifest, runtime readiness,
amendment, expressiveness review and verified qualification receipt.

## Token and cost accounting

Local `tiktoken==0.14.0` accounting records exact tokens for the canonical
messages and wire response schemas. Provider-billed input is explicitly marked
unknown because provider-side framing is not observable before execution.

The conservative input ceiling prices two attempts for every model request and
adds a registered 1,024-token response-format overhead allowance per attempt.
The output ceiling prices two complete 2,048-token outputs per model request.
The authoritative estimate must be recomputed on the exact synchronized `main`
commit used for authorization; feature-branch hashes are not reusable.

## Operator procedure

Use private directories under the project memory area. The completed
qualification directory is read-only input; the cohort directory must be new or
contain only its immutable authorization.

```bash
export CLAIM_V2_QUAL_DIR="/absolute/path/to/project/memory/claim-support-validation-v2-qualification"
export CLAIM_V2_COHORT_DIR="/absolute/path/to/project/memory/claim-support-validation-v2-cohort"

PYTHONPATH=src python scripts/claim_support_validation_v2_authorization.py plan \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR"

PYTHONPATH=src python scripts/claim_support_validation_v2_authorization.py rehearse \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR"
```

Do not reuse the planning-only hashes or any feature-branch authorization.
First merge CV2-09, synchronize a clean `main`, create a new empty private
cohort directory, rerun both commands and review their hashes and estimated
upper cost. This ensures the authorization's `source_commit_ref` identifies the
exact code that will execute the cohort. Authorization is an explicit operator
action:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v2_authorization.py authorize \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR" \
  --cost-ceiling-usd <reviewed-ceiling> \
  --confirm-plan-sha256 <plan-sha256> \
  --confirm-rehearsal-sha256 <rehearsal-sha256>

PYTHONPATH=src python scripts/claim_support_validation_v2_authorization.py require-live-ready \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR"
```

Authorization requires a clean synchronized `main`, an available environment
credential, a sufficient operator ceiling and exact hash confirmations. The
credential is never persisted or printed. The authorization is create-only and
binds one registered V2 diagnosis-cohort attempt to its private destination.

Only after a successful preflight may the operator explicitly consume that
authorization:

```bash
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/claim_support_validation_v2_execution.py execute \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR" \
  --confirm-authorization-sha256 <authorization-sha256>

PYTHONPATH=src python scripts/claim_support_validation_v2_execution.py verify \
  --qualification-run-dir "$CLAIM_V2_QUAL_DIR" \
  --run-dir "$CLAIM_V2_COHORT_DIR"
```

The executor rebuilds the exact registered request census before taking the
create-only lease. It uses the same provider binding, response schema, output
budget, retry policy and global pacing across all model-backed variants. A
sealed terminal may be replayed but never called again. After interruption,
execution can continue only when completed shards are terminal and all
remaining shards are untouched; a mid-shard partial state fails closed under
the one-attempt contract. The verifier independently rebuilds the
qualification, plan, authorization, lease, 360 terminal shards and receipt
without provider access.

Technical admission requires at least 95% parsed globally, at least 90% parsed
within every mechanism, evidence condition and provider-backed variant, and a
terminal result for every deterministic `B0` request. Failures remain in their
original denominators and retain public-safe categories. Passing this gate only
unlocks prospective relation execution. It is not evidence that a balanced
200-claim sample exists.
