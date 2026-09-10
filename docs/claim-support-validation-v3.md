# Prospective V3 measurement calibration

V3 is a separately versioned development calibration, designed after observing
the V1 label shortfall and V2 extraction failure. Neither historical cohort is
repaired, relabeled, pooled into V3, or reused as V3 model output. Existing
protocols, terminal stores and failed attempts remain authoritative history.

## Question and limits

The bounded question is whether a part-coverage instrument agrees with human
judgments on claims about **authentic measurements in the displayed report**.
It is not a test of causal diagnosis, free-form explanation quality, variant
superiority or production readiness. Reusing seven prompt profiles does not
make this a valid seven-way ablation: the explicit calibration instruction
overrides free-form diagnosis, and assigned targets vary across schedule cells.

A claim such as `In the displayed measurement report, ev-a#/payload/x = 0.73`
has an explicit displayed-report scope. A different authenticated report can
contradict that scoped assertion, but cannot refute a historical assertion
about an absent original experiment. The rater instructions must retain this
distinction. Scope review is required before any V3 cohort authorization.

The 15 source families remain the dependence units. Pairing measurements
increases canonical-text diversity, not independent sample size. Two textually
different claims can share material parts, outputs and families. Any later
human analysis must report these dependencies and use family-aware uncertainty.
No claim about the natural prevalence of errors follows from a balanced
challenge set. Numeric lookup calibration does not validate general NLI or
the original diagnosis research question.

A V3 pass does not automatically satisfy the historical AH-02B acceptance
criteria or authorize the main-run freeze. Methods review must explicitly
decide whether this narrower corpus supports the intended instrument-validation
claim; qualification cannot make that scientific decision.

## Frozen source and relation design

`configs/evaluation/claim_support_validation_v3_protocol.json` binds the design
and executable implementation. `verify-protocol` rebuilds it from authenticated
source artifacts without provider access.

- Keep the 15 primary families, 45 contexts and balanced 360-cell source
  schedule: 315 model-backed cells and 45 deterministic B0 cells.
- Assign exactly two source targets per cell before V3 generation. Pair an
  authentic key measurement with an observed/delta performance measurement.
  Missing-key contexts use visible performance measurements only.
- Use exact decimal values and RFC 6901 pointers, including categorical keys.
  No invented numbers, paraphrase padding, rounding or output repair.
- Share the allocation cursor between full/noisy cells of one family so they
  do not all repeat the first witness. This produces 720 prospective claim
  instances and 328 distinct canonical target texts, not 720 observations.
- Freeze 240 relation assignments: 60 disjoint claim texts per intended frame,
  at most five per family/frame and two per output/frame. This gives at least
  12 families and 30 output cells per frame. The assignment is chosen before
  V3 outputs or labels; there is no post-label substitution or expansion.
- The desired later human sets remain disjoint 20-case real onboarding and
  200-case validation, with 50 validation claims per actual automatic label.
  The 240 assignments are only structural headroom, not a guaranteed sample.

| Frame intent | Actual visible material | Structural check, not a label |
|---|---|---|
| Natural | Authenticated source context | Every material part has exact support |
| Withdrawal | Source-provenance item only | All measurement support is absent |
| Partial | Source key-measurement item only | One part supported, performance part absent |
| Counter | Prespecified authentic same-mechanism report | At least one asserted displayed field differs |

Copied items retain their content and source hashes. Evaluator-side family,
condition, frame and expected values never enter the provider projection.
Withdrawal must remove **all** supporting measurement items: removing only the
key can leave a partially supported compound claim.

## Identity and acceptance

Content hash identifies content, not an observation. A V3 source instance binds
protocol + scheduled slot + output-content hash + claim ordinal. A relation
instance additionally binds source instance + frame + visible-context hash.
Identical outputs in different cells therefore remain separate provenance
records. Repetition is retained in the execution denominator; repeated text
cannot be padded into the human sample.

Source acceptance requires the full local schema, exact target text, material
parts, citations and order, followed by the existing real normalizer. A parsed
but altered claim fails qualification; normalization is not a rescue step.

Relation output is a complete matrix of material parts × visible evidence IDs.
Duplicate, missing, foreign and malformed cells fail. Support is the union
across evidence items: separate items may jointly support all parts. Any
explicit contradiction takes precedence; otherwise full/partial/no support
maps to fully_supported/partially_supported/unsupported. Frame intent is never
copied into the observed automatic label. The historical V1 reducer is not
changed retrospectively.

## Qualification and execution safety

The CLI `scripts/claim_support_validation_v3.py` implements **qualification
only**, not the 360-cell cohort executor:

1. `verify-protocol`, `plan`, `rehearse`: provider-free reconstruction, exact
   local token counts, frozen-rate cost bound and full request projection.
2. After merge to clean synchronized main, `authorize` requires the exact plan
   and rehearsal hashes, a fresh private directory and an explicit cost ceiling.
3. `require-live-ready` checks the authority, source commit, SDK, credential
   presence and unused attempt. It never sends a provider request.
4. `execute` requires the exact authorization hash and reserves an exclusive
   create-only lease before the first call. A second start is refused.
5. `verify` independently rebuilds the receipt from all terminal shards and
   checks identities, raw/parsed artifacts, authorities and failure counts.

There are 33 synthetic-only requests: 21 source requests (seven profiles ×
three conditions), then 12 relation requests (three synthetic examples × four
frames). Every source must parse, match its targets and normalize. Every
relation cell must match the synthetic numeric oracle, not merely its final
label. All 33 must pass. Synthetic outputs never enter the corpus or human sets.

The pinned model remains `gpt-4.1-2025-04-14`, with 2,048 output tokens, at most
two attempts per request, disabled SDK retries, one-second global pacing and
the existing bounded Retry-After-aware policy. Costs include wire schemas,
wrapper allowance and maximum output on both attempts, at the frozen planning
rates (USD 2/8 per million input/output tokens). This is not a provider bill or
a claim that those rates are current. Read the freshly generated plan before
authorizing. Qualification is approximately USD 1.27 under these assumptions.

Provider failures preserve allowlisted categories and available usage/finish
diagnostics, never credentials or arbitrary provider error bodies. Operational
errors expose safe blocker codes. After interruption or failure, retain the
lease/store and audit; do not delete them to restart. Qualification success
unlocks **cohort planning only**, not a paid cohort, relation run or blind packet.

## Verification coverage

The unit suite exercises authentic source capacity, all 360 source-normalizer
round trips, disjoint assignment limits, source/instance identity, frame
semantics, complete part matrices, claim tampering, real adapter serialization
with a mocked HTTP response, one-use execution, failure denominators and
independent read-only terminal replay. Integration tests verify cross-process
hash stability and credential-safe CLI refusal. These are offline tests,
not evidence of successful live qualification or a guarantee of zero future
provider failures. No timeout budget or historical acceptance rule is relaxed.
