# Claim-support V2 diagnosis-cohort authorization

## Purpose and boundary

CV2-08 freezes the exact 360-request V2 diagnosis cohort after the separate
seven-request qualification has passed. It implements planning, offline
rehearsal, one-use authorization and live preflight only. It does not execute a
provider call, run deterministic `B0`, create a claim, assign a relation label,
materialize a sample or create a human packet.

The authority is prospective and source-commit bound. CV2-09 may consume it to
execute the registered cohort, but relation execution remains separately gated.

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

Do not create the authorization immediately after merging this planning
boundary. CV2-09 must first implement, test and merge the cohort executor
without provider access. Then synchronize `main`, rerun both commands and
review their hashes and estimated upper cost. This ensures the authorization's
`source_commit_ref` identifies the exact code that will execute the cohort,
rather than the earlier planning-only commit. Authorization is an explicit
operator action:

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

There is intentionally no `execute` command in this boundary. Merging it
unlocks CV2-09 implementation. Only after that executor is merged may a fresh
plan, rehearsal, authorization and successful preflight authorize the live
execution. None of those states is evidence that a balanced 200-claim sample
exists.
