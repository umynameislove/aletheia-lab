# Claim-support V2 seven-request qualification

## Purpose and boundary

V2-3 implements the prospective transport-and-expressiveness qualification that
must pass before any new 360-request scientific cohort is authorized. It runs
exactly one synthetic request for each provider-backed variant: `A1`, `A2`,
`A3`, `B1`, `B2`, `CodeGraph` and `FULL`.

The qualification is calibration, not study data. Every request and result is
excluded from the corpus, relation labels, estimands, sampling and human
packets. It reads neither V1 relation frequencies nor protected outcomes. A
failure remains a terminal qualification result; it cannot be silently retried
as a new registered attempt.

## Frozen acceptance rule

Every variant must return a schema-valid completed response. Its first atomic
claim must exactly equal the deterministic numeric witness recomputed locally
from the authenticated synthetic evidence. A changed value, changed path,
unknown evidence ID, missing material part or abstention fails the qualification.

Passing therefore requires all of the following:

- exactly seven terminal requests and seven distinct gateway identities;
- 7/7 parsed responses;
- 7/7 locally accepted first witnesses;
- zero technical and zero semantic failures;
- an independently rebuilt receipt equal to the published receipt.

The gate validates transport compatibility and the amended source-claim grammar.
It does not establish support-label prevalence or guarantee that a balanced
200-claim corpus will be feasible.

## Execution safety

Planning and rehearsal are offline. The plan binds the current source commit,
the amendment and review hashes, exact request census, GPT-4.1 snapshot,
2,048-token output ceiling, two-attempt limit, global one-second pacing and
bounded retry policy. Input tokens and a conservative output allowance are
priced before authorization. At the current frozen inputs the seven-request
upper estimate is about USD 0.28; the authoritative value is always the value
recomputed on synchronized `main` immediately before authorization.

Authorization requires clean synchronized `main`, explicit confirmation of the
plan and rehearsal hashes, a private destination outside the repository and an
operator cost ceiling. Execution consumes a create-only lease, stores every
attempt in immutable request shards and refuses a second execution in the same
directory. The OpenAI credential is read only from the environment and is never
written to an artifact or rendered by the CLI.

## Offline verification

Use a private directory under the project memory area:

```bash
export CLAIM_V2_QUAL_DIR="/absolute/path/to/project/memory/claim-support-validation-v2-qualification"

PYTHONPATH=src python scripts/claim_support_validation_v2_qualification.py plan \
  --run-dir "$CLAIM_V2_QUAL_DIR"

PYTHONPATH=src python scripts/claim_support_validation_v2_qualification.py rehearse \
  --run-dir "$CLAIM_V2_QUAL_DIR"
```

After this implementation is merged, recompute both outputs on clean synchronized
`main`. Authorization, preflight and execution are deliberate operator actions;
they must use the hashes printed by that exact checkout. Do not copy hashes from
documentation or from a feature branch.

After the one authorized run, independently rebuild the result with:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v2_qualification.py verify \
  --run-dir "$CLAIM_V2_QUAL_DIR"
```

Only a verified status of
`claim_support_validation_v2_qualification_passed` with
`full_cohort_authorization_unlocked: true` permits V2-4 exact cohort planning.
Any other terminal status preserves the failure and requires a separately
versioned prospective decision; it does not permit the 360-request run.

## Registered outcome

The one authorized qualification run completed with 7/7 parsed responses, 7/7
locally accepted first witnesses, zero technical failures and zero semantic
validation failures. Independent verification reproduced receipt SHA-256
`ab0b82b59a392c2dc460fdf47b55b0e67292d641f43d7078f4bb6add4dbb6ae2`
and terminal-store SHA-256
`03e7f2d6cdb397b9ba57e739a44d7d94fbb1b868d69aa4317d3a308dce2f2f95`.

This pass unlocks the separately versioned
[diagnosis-cohort authorization boundary](claim-support-validation-v2-authorization.md).
It does not admit the seven synthetic results to the corpus and does not by
itself establish label balance or the feasibility of a 200-claim sample.
