# Claim-support validation V2 protocol

This document closes the first claim-support cohort without changing it and
freezes a separate prospective V2 design. It is a protocol and audit boundary,
not permission to call a provider, construct the 200-claim sample, or deliver
human packets.

## V1 disposition

The V1 artifacts are valid historical evidence but cannot supply the frozen
balanced validation sample. Independent closeout found:

- 360/360 diagnosis requests reached a terminal state;
- 282 parsed and normalized outputs produced 962 request-local claim instances;
- 78 requests ended after two transient attempts;
- the full pool contained 842 `fully_supported`, 112 `partially_supported`, six
  `unsupported`, and two `contradicted` instances;
- the two rare strata failed the frozen requirement of 50 distinct claims from
  at least ten families and 25 outputs.

The cohort therefore terminates as
`closed_v1_inadequate_for_balanced_validation_v2_required`. It must not be
rerun, relabeled, padded, or pooled into V2. No V1 blind validation packet was
created.

## What the failure evidence establishes

The public-safe audit is reconstructed from the immutable private diagnosis
store and the two closeout receipts. It records 460 provider attempts: 282
responses and 178 transient errors. One hundred requests used a second attempt;
22 recovered and 78 failed again. The first terminal failure was request 144,
after a prefix of 143 parsed requests. Stored retry gaps were approximately
1.6--6.4 milliseconds, so the old recovery execution did not provide meaningful
retry backoff.

The failure subtype is not recoverable. The old transport collapsed connection,
timeout, rate-limit and eligible HTTP server conditions into one public-safe
`transient_provider_error` category before persistence. It retained neither a
safe HTTP subtype nor a failed response body. Consequently:

- absence of pacing and effective backoff is established from code and timing;
- an external transient-service or quota effect is plausible;
- a specific rate-limit, connection, timeout, or server-error root cause is not
  established and must not be reported as fact.

All data-drift requests happened before the failure-heavy part of the schedule,
while preprocessing and label-noise requests happened later. Missingness is
therefore confounded with execution order. Their observed 0, 37, and 41 terminal
failures cannot be interpreted as a scientific difference between mechanisms.

The structured-output correction itself passed its relevant boundary: all 282
parsed outputs normalized, with zero downstream schema rejection. The V2 design
must preserve that schema while replacing the weak transport diagnostics and
execution schedule.

## V2 scientific scope

The V2 question is the construct validity of automatic claim-evidence relation
labels against two blinded human raters and adjudication. Its population is a
prospectively constructed development challenge set made from new diagnosis
outputs and authentic visible evidence, conditional on the registered technical
admission gates. That conditioning is part of the estimand, not an assumption
that failed requests are exchangeable with successful requests.

This design does **not** estimate label prevalence in naturally occurring model
outputs. It does not authorize a diagnosis-variant superiority claim, a causal
mechanism claim, or a main-evaluation result. Metrics and thresholds apply only
to the frozen V2 challenge population. A prespecified sensitivity report must
show technical failures by mechanism, family, condition, variant and execution
position. Neither availability nor failure rate may be interpreted as relative
variant or mechanism performance.

V1 informed the need for V2, but V1 outputs, automatic labels and human
annotations are ineligible for the V2 sample. Every V2 provider request receives
a new identity and belongs to a separately authorized cohort.

## Prospective source frame

V2 retains the comparable diagnosis matrix:

- 15 primary development families across three mechanisms;
- `full`, `missing_key`, and `noisy` observed-evidence conditions;
- `A1`, `A2`, `A3`, `B0`, `B1`, `B2`, `CodeGraph`, and `FULL`;
- 360 diagnosis requests: 315 provider-backed and 45 deterministic `B0`.

The schedule changes prospectively to a content-hash-bound balanced interleave.
It must distribute mechanisms, families, evidence conditions and variants
throughout the execution rather than running one mechanism block at a time.

At most two atomic source claims may be selected from each completed output by
the frozen `canonical-claim-hash/v1` rule. Each source claim receives its natural
visible context and at most one prospectively assigned challenge context. The
challenge is assigned with a balanced hash over the authentic frames for which
that source claim is structurally eligible. Selection and assignment happen
before any automatic relation result exists. An ineligible assignment is logged
and cannot be replaced after observing an outcome. The four registered evidence
frames are:

1. `natural_context`: the original authentic visible evidence;
2. `support_withdrawal`: authentic visible evidence with decisive support
   removed and no contradictory item introduced;
3. `partial_support_projection`: authentic evidence covering only a proper
   subset of the claim's material parts;
4. `direct_counterevidence`: authentic measured evidence that explicitly
   conflicts with a material part of the claim.

Frame construction is evaluator-side and fixed before relation outcomes exist.
A frame records design intent only: it is neither ground truth, an answer key nor
a human label, and its identity is hidden from raters. Textual negation, invented
evidence, synthetic padding, and main or sealed evidence are forbidden. Before
execution, the source inventory must demonstrate authentic capacity across at
least ten families and 25 scheduled diagnosis cells for every frame. The
resulting ceiling is 1,440 relation requests: 360 outputs times two source claims
times two contexts.

## Transport and missingness controls

V2 retains GPT-4.1 snapshot `gpt-4.1-2025-04-14`, schema-native responses, a
2,048-token diagnosis output ceiling, a 600-token relation output ceiling, a
60-second timeout and at most two attempts. Model, context, visible evidence and
applicable budgets remain equal across matched model-backed variants.

The implementation gate must add:

- at least one second between provider-call starts globally;
- five-second initial retry backoff, bounded exponential growth and a
  60-second ceiling;
- bounded use of provider `Retry-After` metadata;
- safe persisted categories for rate limit, timeout, connection, eligible HTTP
  status, server error, request rejection, refusal, truncation, invalid envelope
  and local schema incompatibility;
- no provider exception text, response body, prompt, credential, or secret in a
  technical issue artifact;
- retry only for the prospectively registered transient categories;
- immutable checkpoints and denominator preservation for every terminal.

Before the full cohort, a separate seven-request synthetic qualification run
must exercise all provider-backed variants. All seven requests must parse and
none may enter the corpus. The diagnosis cohort must reach at least 95% parsed
globally and 90% within every mechanism, evidence condition and model-backed
variant. `B0` must be terminal for all 45 requests. These are technical
admission gates, not a license to discard failures or claim missingness is
exchangeable.

## Frozen sampling and human validation

The final V2 sample remains exactly 200 claims, 50 per automatic label. Each
label must span at least ten families and 25 diagnosis outputs, with no more
than five claims per family and two per output. A canonical claim text and a
source claim may appear at most once in the selected sample.

Two independent raters remain blind to the automatic label, source frame,
variant, mechanism, evidence condition and provenance identities. Disagreements
are adjudicated, as is every case where either rater marks `contradicted`. A model
cannot act as a human rater. Packets expose only blind ID, claim text and visible
evidence. The ordered scale remains `contradicted`, `unsupported`,
`partially_supported`, `fully_supported`. The registered gates remain:

- quadratic weighted kappa at least 0.70;
- automatic-label macro-F1 at least 0.80;
- false-supported rate at most 10%;
- contradicted-to-supported rate at most 5%.

False-supported uses automatic supported predictions as its denominator;
contradicted-to-supported uses adjudicated contradicted claims. Uncertainty uses
2,000 deterministic bootstrap replicates, seed 73021, clustered by case family.
Empty registered denominators or an invalid clustered-bootstrap census fail
closed.

If exact balanced selection is infeasible, V2 closes as insufficient. It must
not reduce a quota, duplicate a claim, add synthetic evidence, or adapt the
frame after observing labels.

## Cost and authorization boundary

The planning estimate is USD 36.075393: USD 13.230724 for diagnosis based on the
registered recovery authorization, USD 22.344669 from scaling the V1 relation
estimate to the 1,440-request ceiling, and a USD 0.50 qualification allowance.
The combined operator ceiling is USD 36.09.

These figures are planning bounds, not an authorization. Exact input tokens,
the final request census, current pinned prices, destination, clean synchronized
commit and an explicit operator ceiling must be recomputed and confirmed before
each live phase.

## Verification and next gate

Verify the tracked historical audit and prospective freeze without network
access:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v2.py verify
```

That historical protocol receipt retains the status
`claim_support_validation_v2_protocol_frozen_implementation_pending`; it is not
rewritten to represent later implementation progress. The separate offline
runtime artifacts are reproduced with:

```bash
PYTHONPATH=src python scripts/claim_support_validation_v2_runtime.py verify
```

The runtime census binds 15 rounds of 24 unique diagnosis cells. Each round
contains eight cells per mechanism, eight per evidence condition and three per
variant; each family appears once or twice per round. The seven qualification
projections bind synthetic context, prompt, exact response schema, snapshot and
2,048-token ceiling. They contain no scientific evidence and are not executable
authorizations. The CLI intentionally exposes only offline materialization and
verification, not a provider execution command.

The V2 adapter retains the registered recovery wire-schema projection and adds
allowlisted failure categories. A single paced adapter instance must be shared
by the cohort: starts are serialized with a monotonic one-second interval,
including retries. The gateway's explicit V2 retry controller applies the
five-second initial delay and bounded Retry-After. Its absence preserves legacy
runtime behavior and legacy serialized records omit the new optional fields.
The prospective feasibility plan's current adapter-file checksum is refreshed
for the additive translation hook; historical run stores and receipts are not
rewritten.

### Structural frame eligibility is not a label guarantee

The source inventory resolves only authenticated items from the 45 development
contexts. A withdrawal removes the key-measurement item. A partial projection
retains one prospectively selected item. Counterevidence candidates come from a
different development family of the same mechanism and condition, with a
different value at the same JSON measurement path, evidence ID, title and kind.
Different hashes or incidental numbers do not establish conflict.

Actual challenge eligibility is stricter than source capacity. Every material
part must be an explicit numeric equality such as `observed.macro_f1 = 0.7`,
and the claim text must be exactly those parts joined by `; `. All parts must
be witnessed in the cited original evidence. This conservative implementation
does not infer or erase a subject, qualifier or causal assertion from prose.
Arbitrary prose remains eligible for its natural context but not automatically
for a challenge. Withdrawal must remove a witnessed part while retaining cited
evidence; a partial projection must cover a nonempty proper subset. Conflicting
measurements are candidate design evidence, never assigned ground truth.

Consequently, the inventory's family/cell counts are **structural capacity**,
not a forecast that generated claims will satisfy this restricted witness rule,
nor that 50 claims per label will exist. No output prompt is silently rewritten
to force this grammar or a desired label. Ineligibility is recorded before any
relation result, and no post-outcome replacement is allowed. Before any live
qualification or full-cohort authorization, the operator must review this
expressiveness limitation against the registered challenge-population objective.
An amendment, if necessary, must precede new scientific outcomes rather than
follow a failed label quota.

The technical-admission reducer consumes independently audited terminal and
parsed request IDs, keeps all 360 scheduled requests in its denominators, and
checks the registered global and stratum thresholds. It does not authenticate a
store, establish exchangeable missingness, or authorize sample selection. Those
remain separate evidence and execution boundaries.

The private V1 aggregate can be reproduced by additionally supplying the
immutable recovery closeout, pool feasibility closeout and diagnosis store to
`audit-v1`. That command only reads existing artifacts and never calls a
provider.
