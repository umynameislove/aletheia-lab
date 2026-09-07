# Claim-support normalization recovery

## Status

The response-contract recovery is frozen and outcome-blind. It is not an
execution authorization. No provider call, automatic label, corpus entry,
blind human packet, human annotation, main outcome or sealed outcome is
created by this change.

The first development diagnosis attempt remains terminal and immutable:

- 360/360 scheduled requests have terminal records;
- 258 responses reached the parsed gateway state;
- 102 requests ended as technical provider failures;
- 49 parsed responses satisfied the downstream normalization contract;
- 209 parsed responses were rejected by that contract;
- 152 candidate claims were available, below the fixed target of 200.

The preparation artifact and all failures remain denominator-visible. The
first attempt is retired and cannot be rerun, edited, manually repaired,
silently filtered or combined with a later attempt.

## Failure characterization

The failure was an interface defect, not evidence that the scientific
mechanisms were absent. The original provider schema accepted arbitrary local
claim and material-part identifiers and did not impose the downstream array
bounds. The downstream `diagnosis-output/2` contract correctly rejected those
incompatible envelopes. Most rejections involved noncanonical identifiers;
some outputs also exceeded the five-claim ceiling.

Because this defect was observed after execution, the old responses are not
retrofitted. A recovery is permitted only through a new prospective contract,
a new authorization and a new create-only attempt store.

## Version 2 response boundary

The recovery envelope uses `diagnosis-provider-output/2` and has one nested
terminal union:

- a completed result contains one to five atomic claims;
- an abstained result contains one nonblank reason and no claims.

For completed results:

- each claim contains one to eight material parts;
- each claim cites one to 32 evidence IDs;
- citation values are restricted to the IDs visible in that exact context;
- claim text is nonblank and bounded to 2,048 characters;
- material-part text is nonblank and bounded to 1,024 characters;
- the provider does not author claim or part identifiers.

Claim text, material-part text and abstention reasons must be trimmed and
control-free. The gateway rejects leading/trailing whitespace, embedded control
characters (including tabs and newlines), and lone surrogates before declaring
the response parsed. Text-boundary tests exercise both gateway and normalizer,
including the exact length limit and a trailing newline (which a plain `$`
regular-expression anchor would otherwise permit).

The normalizer assigns `claim-1` through `claim-5` by claim order and assigns
`part-1` through `part-8` within each claim. Repeated citations are reduced to
their first occurrence without changing their semantic target. These are
prospectively declared structural canonicalizations, not recovery of content
from the retired attempt.

The gateway now validates the required Structured Outputs subset locally:
`pattern`, `minItems`, `maxItems` and nested `anyOf`, in addition to the
existing closed-object, type, enum and const rules. The local interpreter retains
the full schema. The current transport uses the separate
[structured-output compatibility projection](claim-support-structured-output-compatibility.md)
without string regexes; this does not relax local acceptance. OpenAI documents these
constraints as supported for Structured Outputs, with the root remaining an
object and nested `anyOf` permitted:
<https://developers.openai.com/api/docs/guides/structured-outputs>.

## Frozen scientific invariants

Only the provider response contract changes. The following remain unchanged:

- the 15 primary families and 45 observed evidence contexts;
- the 360-request primary census;
- the eight-variant matrix;
- the GPT-4.1 model and `gpt-4.1-2025-04-14` snapshot;
- context, tool, evidence and retry budgets;
- the target of 200 claims and every later selection rule;
- the separation between automatic relations and independent human ratings.

The original recovery registration held the 600-token output ceiling fixed.
After all three synthetic compatibility probes terminated at that ceiling,
the separately hashed [CSR-03R technical amendment](claim-support-recovery-output-budget-amendment.md)
raises only the recovery output ceiling to 2,048 tokens. It applies uniformly
to every model-backed variant and requires new request identities and authority.

The recovery runs the complete matrix as 315 model-backed requests and 45
deterministic requests. It does not reuse any predecessor output. Request
semantics remain fixed, while gateway request identities must change because
the prompt and response-schema hashes change.

The added instruction governs serialization only; it must not require supported
claims or override a variant's reasoning or abstention policy. This amendment
is informed by observed development failures, not blind to those failures.
It remains prospective with respect to recovery outputs and human evaluation.
The 102 provider failures are a separate unresolved failure class: this contract
does not establish their cause or guarantee their removal. Nor does structural
validity establish claim correctness or guarantee 200 eligible claims.

## Verification

The tracked registration is independently rebuilt from the request census,
variant fairness freeze and observed-evidence census:

```bash
PYTHONPATH=src python scripts/claim_support_normalization_recovery.py verify
```

Verification fails closed if any frozen input, predecessor reference, schema
set, semantic instruction, count, outcome flag or protocol hash differs. The
test suite covers all eight variants, both terminal branches, context-specific
citation binding, bad structural fields, excessive claims, missing parts,
blank text, ambiguous terminal shapes, hash-seed stability and preservation of
the zero-outcome boundary.

## Recovery execution boundary

The recovery entrypoint now implements two strictly ordered, separately
authorized phases:

1. `compatibility` sends three synthetic requests, one for each distinct frozen
   response schema. It contains no observed family evidence and cannot create a
   scientific claim corpus.
2. `diagnosis` schedules the complete 360-request recovery only after an
   independently regenerated compatibility receipt proves all three synthetic
   responses parsed and normalized.

Each phase has its own immutable authorization, lease, attempt store and
receipt. Their directory must be outside both the repository and the preserved
predecessor run. A lease can be created once; any partial state blocks restart.
The independent verifier rebuilds terminal counts, failure categories,
normalization counts, candidates and the store identity from immutable shards.
Provider failures remain in the denominator.

```bash
PYTHONPATH=src python scripts/claim_support_recovery.py rehearse
PYTHONPATH=src python scripts/claim_support_recovery.py audit-predecessor \
  --predecessor-store /absolute/path/to/preserved/attempt-store
```

After this implementation is merged and CI is green, the operator must run the
phases in order: authorize, `require-live-ready`, execute once, then verify.
The authorization command requires the current rehearsal SHA and an explicit
cost ceiling; execution requires the resulting authorization SHA. The CLI
checks the pinned SDK and credential without printing the credential. Exact
operator commands must use a clean synchronized `main` checkout and private
absolute destinations.

The command sequence is intentionally manual at each paid boundary:

```bash
export CLAIM_RECOVERY_DIR=/absolute/private/path/claim-support-recovery-run-v3
export CLAIM_PREDECESSOR_STORE=/absolute/private/path/predecessor/attempt-store
export CLAIM_RETIRED_COMPATIBILITY_RUN=/absolute/private/path/claim-support-recovery-run
export CLAIM_RETIRED_STRUCTURED_OUTPUT_RUN=/absolute/private/path/claim-support-recovery-run-v2

# Copy the displayed rehearsal SHA into the authorization confirmation.
PYTHONPATH=src python scripts/claim_support_recovery.py rehearse
PYTHONPATH=src python scripts/claim_support_recovery.py authorize \
  --phase compatibility --run-dir "$CLAIM_RECOVERY_DIR" \
  --predecessor-store "$CLAIM_PREDECESSOR_STORE" \
  --retired-compatibility-run "$CLAIM_RETIRED_COMPATIBILITY_RUN" \
  --cost-ceiling-usd COMPATIBILITY_CEILING \
  --retired-structured-output-run "$CLAIM_RETIRED_STRUCTURED_OUTPUT_RUN" \
  --confirm-rehearsal-sha256 REHEARSAL_SHA
PYTHONPATH=src python scripts/claim_support_recovery.py require-live-ready \
  --phase compatibility --run-dir "$CLAIM_RECOVERY_DIR" \
  --predecessor-store "$CLAIM_PREDECESSOR_STORE" \
  --retired-compatibility-run "$CLAIM_RETIRED_COMPATIBILITY_RUN" \
  --retired-structured-output-run "$CLAIM_RETIRED_STRUCTURED_OUTPUT_RUN" &&
PYTHONPATH=src python scripts/claim_support_recovery.py execute \
  --phase compatibility --run-dir "$CLAIM_RECOVERY_DIR" \
  --predecessor-store "$CLAIM_PREDECESSOR_STORE" \
  --retired-compatibility-run "$CLAIM_RETIRED_COMPATIBILITY_RUN" \
  --retired-structured-output-run "$CLAIM_RETIRED_STRUCTURED_OUTPUT_RUN" \
  --confirm-authorization-sha256 COMPATIBILITY_AUTHORIZATION_SHA
PYTHONPATH=src python scripts/claim_support_recovery.py verify \
  --phase compatibility --run-dir "$CLAIM_RECOVERY_DIR"
```

Only a verified `recovery_compatibility_pass` permits repeating that four-step
authorize/preflight/execute/verify sequence with `--phase diagnosis`, a new
diagnosis cost ceiling and the diagnosis authorization SHA. Placeholder values
must be replaced explicitly; no command infers, reuses or auto-confirms them.

Offline tests still do not prove live provider acceptance. The synthetic phase
is therefore a real paid compatibility check, but it is not a scientific
attempt. A failed compatibility phase remains recorded and blocks diagnosis;
it is never reclassified as success. Diagnosis completion likewise reports all
technical failures and candidate counts without guaranteeing 200 eligible
claims or constituting scientific admission.
