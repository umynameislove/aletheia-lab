# Claim-support V2 source-claim expressiveness amendment

## Decision

EXP-01 is complete as an offline, outcome-blind amendment. The original V2
runtime established authentic evidence-frame capacity but did not require model
outputs to use the exact numeric grammar consumed by the conservative challenge
witness. Its synthetic probes also used prose rather than JSON measurements.
Running those probes would therefore have tested transport without testing the
scientific source-claim boundary.

The amendment fixes that prospective mismatch before any V2 provider outcome.
It does not rewrite the parent protocol or runtime artifacts. It binds their
hashes and supersedes only the seven ambiguous qualification projections.

## Shared source-output contract

Every completed V2 response starts with one `evidence_statement` whose material
parts are exact visible JSON equalities:

```text
payload.changed_target_count = 246; payload.observed.accuracy = 0.79186377
```

The claim text is exactly the material parts joined by `; `. Paths and base-10
values are copied without calculation, rounding, renaming or inference, and all
source evidence IDs are cited. Selection is deterministic from visible evidence
alone: `payload.observed.*` precedes `payload.delta.*`, then other `payload.*`
paths, with full-path lexical tie-breaking. Where both key measurement and
performance summary exist, the key part must not be identically duplicated in
the summary; a performance-summary part follows it. A missing-key context uses
one performance-summary part.

This first claim is selected for relation-frame construction before at most one
canonical-hash-selected diagnostic claim. Additional diagnosis claims remain
governed by each variant's original prompt, so the amendment does not erase the
variant intervention. Required abstention remains valid and must not be replaced
with an invented witness. The deterministic `B0` route uses the same witness
algorithm. Local acceptance recomputes the expected witness from the authenticated
context instead of trusting provider-authored text.

## Offline evidence

The review builds one non-corpus witness in memory for each of the 45 authentic
development contexts and submits it to the same measurement and challenge
eligibility functions used by the V2 relation runtime. The effective capacity is:

| Frame | Contexts | Families | Scheduled diagnosis cells |
| --- | ---: | ---: | ---: |
| Natural context | 45 | 15 | 360 |
| Support withdrawal | 30 | 15 | 240 |
| Partial-support projection | 30 | 15 | 240 |
| Direct counterevidence | 45 | 15 | 360 |

Withdrawal and partial projection correctly exclude all 15 `missing_key`
contexts. Direct counterevidence remains available because the neutral observed-
path priority produces an authenticated same-path value conflict in all 45
registered source/counter pairs. Every frame still exceeds the protocol's ten-
family and 25-cell minimums.

These counts establish expressiveness, not relation-label prevalence. The review
did not read V1 relation outcomes, V1 label frequencies, human annotations, main
or sealed outcomes. It did not make provider calls, materialize source claims,
generate labels, sample 200 claims or create human packets.

The standardized first claim is an instrument-calibration stratum inside a
prospectively constructed challenge set. Agreement on it must not be generalized
to arbitrary free-form causal diagnosis prose or reported as the natural
prevalence of any support label. The optional second, variant-specific claim
preserves a bounded prose stratum, but it receives a challenge frame only when
the same conservative witness independently establishes eligibility.

## Qualification replacement and next gate

Seven new synthetic probes bind the original per-variant prompt, the shared
amendment, exact response schema, GPT-4.1 snapshot, 2,048-token ceiling and a
JSON-only synthetic context. Probe outputs remain excluded from every corpus and
estimand. All seven must parse and satisfy the exact first-witness contract.

Reproduce the amendment and review offline:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v2_expressiveness.py verify
```

The tracked amendment SHA-256 is recorded in
`configs/evaluation/claim_support_validation_v2_expressiveness_amendment.json`;
the 45-context receipt is recorded in
`configs/evaluation/claim_support_validation_v2_expressiveness_review.json`.
Neither file is a live authorization. The next allowed action is to implement
and separately authorize the seven-request V2 qualification. Only 7/7 parsed
and locally accepted responses may open exact planning for the new 360-request
cohort. Label balance and final 200-claim feasibility remain unknown until their
registered downstream gates.
