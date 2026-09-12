# Prospective V3.1 measurement calibration

V3 is a separately versioned development calibration, designed after observing
the V1 label shortfall and V2 extraction failure. Neither historical cohort is
repaired, relabeled, pooled into V3, or reused as V3 model output. Existing
protocols, terminal stores and failed attempts remain authoritative history.

The first V3 qualification is terminal and failed: all 33 requests parsed with
zero technical failures, but only 19 were accepted. All 12 relation probes
passed. Fourteen of 21 source probes omitted the required displayed-report
scope from at least one provider-authored material part; 12 also omitted the
repeated scope in compound claim text. Their target values, evidence paths,
citations and claim order were otherwise correct. The immutable closeout at
`configs/evaluation/claim_support_validation_v3_qualification_failure.json`
binds its source commit, protocol, plan, receipt and terminal-store hashes. That
attempt remains failed and cannot be reinterpreted or rerun.

V3.1 is a prospective source-representation amendment. It keeps the question,
model, evidence, target allocation, relation instrument, budgets and all cohort
quotas fixed. It removes provider-authored claim prose from the source boundary:
the provider returns ordered target/value readings, exact local validation
checks every value, and deterministic code renders canonical claim text,
material parts and citations. This prevents formatting variance from being
confounded with measurement correctness without accepting or repairing any
wrong value.

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

Source acceptance requires the `claim-source-measurement-output/1` envelope,
the complete ordered target census and exact byte-for-byte measurement values.
Missing, duplicate, reordered, mistyped or foreign targets and changed values
fail before claim construction. After acceptance, a deterministic renderer
constructs the displayed-report scope, material parts and citations, and the
existing real normalizer must still accept that rendered output. Provider prose
from V3 is not accepted by the V3.1 boundary and no historical output is rescued.

Relation output is a complete matrix of material parts × visible evidence IDs.
Duplicate, missing, foreign and malformed cells fail. Support is the union
across evidence items: separate items may jointly support all parts. Any
explicit contradiction takes precedence; otherwise full/partial/no support
maps to fully_supported/partially_supported/unsupported. Frame intent is never
copied into the observed automatic label. The historical V1 reducer is not
changed retrospectively.

## Qualification and execution safety

The CLI `scripts/claim_support_validation_v3.py` implements **V3.1 qualification
only**, not the 360-cell cohort executor. Before planning, the retired run can
be audited read-only against every terminal shard:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v3.py \
  audit-failed-qualification \
  --retired-run /absolute/private/path/claim-support-validation-v3-qualification
```

This command requires the registered receipt/store hashes and proves the 14/12
scope-omission classification. It never rewrites the run or reclassifies an
old failure as accepted.

The prospective workflow is:

1. `verify-failure-closeout`, `verify-protocol`, `plan`, `rehearse`:
   provider-free reconstruction, exact
   local token counts, frozen-rate cost bound and full request projection.
2. After merge to clean synchronized main, `authorize` requires the exact plan
   and rehearsal hashes, a fresh private directory and an explicit cost ceiling.
3. `require-live-ready` checks the authority, source commit, SDK, credential
   presence and unused attempt. It never sends a provider request.
4. `execute` requires the exact authorization hash and reserves an exclusive
   create-only lease before the first call. A second start is refused.
5. `verify` independently rebuilds the receipt from all terminal shards and
   checks identities, raw/parsed artifacts, authorities and failure counts.

V3.1 uses a fresh private destination and fresh authorization. There are 33
synthetic-only requests: 21 source requests (seven profiles ×
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
authorizing; do not copy plan or rehearsal hashes from this document.

Provider failures preserve allowlisted categories and available usage/finish
diagnostics, never credentials or arbitrary provider error bodies. Operational
errors expose safe blocker codes. After interruption or failure, retain the
lease/store and audit; do not delete them to restart. Qualification success
unlocks **cohort planning only**, not a paid cohort, relation run or blind packet.

## Verification coverage

The unit suite exercises the bound V3 failure closeout, authentic source
capacity, all 360 source-normalizer
round trips, disjoint assignment limits, source/instance identity, frame
semantics, complete part matrices, reading/value tampering, deterministic
scope/citation rendering, rejection of the old prose envelope, real adapter serialization
with a mocked HTTP response, one-use execution, failure denominators and
independent read-only terminal replay. Integration tests verify cross-process
hash stability and credential-safe CLI refusal. These are offline tests,
not evidence of successful live qualification or a guarantee of zero future
provider failures. No timeout budget or historical acceptance rule is relaxed.

## V3.1 authentic source-cohort boundary

The successful V3.1 qualification is an immutable 33/33 calibration result:
all 33 requests parsed and were accepted, with zero technical or semantic
failure. That result removes the calibration blocker but does not itself create
study claims. `scripts/claim_support_validation_v3_cohort.py` is the separately
versioned source-cohort boundary. Its modules deliberately do not match the
qualification implementation-binding glob, so adding the executor cannot
retroactively change the protocol under which qualification passed.

The source phase freezes exactly 360 scheduled cells before execution: 315
provider-backed cells across the seven model variants and 45 deterministic B0
cells. Every cell has two prespecified measurement targets, for 720 source
instances. Request identity binds the V3.1 protocol, scheduled slot and phase;
content identity is never used as observation identity. The plan binds the
successful qualification receipt/store, all request projections and the cohort
implementation files. Token counts and a conservative two-attempt cost ceiling
are rebuilt locally.

This authority intentionally excludes the 240 prospective relation cells.
Relation requests are not constructed until an independently replayed source
receipt proves 360/360 exact acceptance. Any technical or semantic failure is
kept in the denominator and blocks relation planning; there is no adaptive
replacement, post-result balancing or silent rerun. A completed source receipt
still has all automatic-label, corpus, blind-packet and human-outcome flags
false.

The operator sequence is `plan`, `rehearse`, merge to clean synchronized main,
then `authorize`, `require-live-ready`, `execute` and `verify`. Plan and
rehearsal never load a provider adapter. Authorization requires their exact
fresh hashes, a private empty destination and an explicit cost ceiling.
Preflight validates the credential only by presence and never prints it.
Execution reserves a create-only lease before the first call, permits only
sealed-terminal resume, and refuses a partial unsealed request state. Repeating
`execute` after closeout performs read-only verification rather than spending a
second attempt.

## V3.1 source-cohort failure closeout

The registered source cohort is now closed as a development failure, not as a
partial success. All 360 scheduled requests reached authenticated parsed
terminals, but only 355 passed the exact source-reading contract. The five
failed sequences are 26, 53, 103, 232 and 307, leaving 710 rather than the
required 720 source instances. There were no technical terminal failures. The
original receipt and store remain immutable, the failed cells remain in the
denominator, and rerun, relation construction, corpus admission and blind-packet
generation remain forbidden.

The public-safe terminal replay classifies eleven mismatched readings without
publishing their expected or observed numeric values. Eight readings contain a
target-ordinal token in the value field. Three readings copy a different
visible numeric leaf: the observed branch was used where the frozen target
pointed to the delta branch. There are no numeric-reformat-only cases. The
stored structured payloads, request/slot identities and hashes all reproduce,
so the evidence does not support a parser, serializer or evaluator mapping
defect. It supports exact-reading noncompliance by the provider under the V3.1
source-measurement role.

The 70 rate-limited attempts are a separate transport observation. They belong
to 70 requests that all succeeded on retry and all passed semantic acceptance;
none overlaps the five failed requests. Provider usage is incomplete only
because those rejected rate-limit attempts have no provider usage metadata.
The 315 successful provider responses retain complete usage, totalling 382,931
input and 17,737 output tokens (400,668 total). These are observed SDK records,
not a reconstructed bill.

The successful 33/33 synthetic qualification is not reinterpreted: it showed
that the contract could be followed on the calibration set, not that every
authentic cohort request was guaranteed to pass. Five failures are too few and
too post-selected to support variant, condition or mechanism comparisons. The
355 accepted results cannot be padded, repaired or admitted retrospectively;
substituting evaluator-known values would change the measured behavior.

The only next authorized action is a prospective V3.2 source-measurement role
review and fresh qualification. That amendment must decide explicitly whether
provider transcription is part of the scientific measurement or should be
replaced by deterministic extraction. Either choice creates a new protocol and
new request identities; it is not a repair or replay of V3.1. The immutable
closeout is tracked at
`configs/evaluation/claim_support_validation_v3_source_cohort_failure.json`.
`scripts/claim_support_validation_v3_failure.py` independently verifies the
historical receipt/store and then reproduces that closeout without provider
access or write authority.
