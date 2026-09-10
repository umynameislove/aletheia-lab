# Claim-support materialization readiness

This document records the zero-outcome technical and methodological closeout
that precedes development-corpus execution. The closeout does not contain a
model response, extracted claim, automatic support label, human judgment, main
outcome or sealed outcome. It authorizes only a later, separately reviewed
development materialization stage.

## Prospective source census

The registered inventory contains 15 primary families and six ordered reserve
families: five primary and two reserve families for each of data drift,
preprocessing mismatch and label noise. Each identity binds its mechanism,
registered order, seed, intervention parameters, invariants, three visible
evidence conditions and a repository-local source artifact.

The request census is the exact Cartesian product of each family with `full`,
`missing_key` and `noisy` evidence and the eight eligible variants `A1`, `A2`,
`A3`, `B0`, `B1`, `B2`, `CodeGraph` and `FULL`:

- 15 primary families × 3 conditions × 8 variants = 360 primary requests;
- 6 reserve families × 3 conditions × 8 variants = 144 reserve requests.

Every request records `provider_call_authorized=false`. Reserve activation is
restricted to mechanism-local, pre-execution technical ineligibility. Output
quality, automatic labels, human judgments and quota scarcity cannot activate a
reserve.

## Atomic output and adapters

`diagnosis-output/2` provides a concrete structured boundary for completed,
abstained and parse-failed responses. A completed output carries at most five
schema-native atomic claims. Every claim has a type, bounded text, declared
material parts and visible evidence identifiers. Sentence splitting and
free-text fallback are prohibited.

Eight hash-bound adapters normalize the eligible variants. Seven consume
`diagnosis-output/2` directly. The deterministic `B0` adapter performs only a
registered field rename from structured rule claims; it cannot interpret prose.
`B3` has no adapter and fails closed because its external native output is not
comparable to the atomic-claim contract.

## Automatic relation instrument

The frozen instrument receives only claim text, claim type and visible evidence.
Its precedence is contradiction, no support, partial support and complete
support. Mechanism, evidence condition, variant, hidden truth, human judgment
and protected outcomes are absent from the function boundary. Six synthetic
semantic fixtures cover contradiction precedence, polarity, neutral evidence,
partial scope and complete scope. Fixtures test behavior only and are ineligible
for the 200-claim validation corpus.

## Observed evidence and blind semantic assignment

Every primary family-condition pair must have one observed, content-addressed
evidence binding before live execution: 15 families x three conditions = 45
contexts. A binding records evaluator-side family and condition provenance, but
its model-visible projection contains only canonical evidence IDs, kinds,
titles, content and hashes. Explicit answer keys, condition labels, automatic
labels, human judgments and protected outcomes fail closed at this boundary.

The relation-assignment request is built separately for each schema-native
atomic claim and includes exactly three provider fields: claim text, claim type
and the evidence IDs cited by that claim with their immutable visible content.
The provider returns only support polarity and scope for those IDs. Local code
requires every cited ID exactly once and in request order, binds the response to
the source output and claim, then joins the relation back to the stored evidence
text. A response from another claim, output, family or condition cannot be
replayed as a valid label.

The relation rubric, structured response schema, exact model snapshot,
temperature, seed, retry policy and implementation bytes are frozen in
`claim_support_evidence_semantics_policy.json`. No provider call or automatic
label was produced while creating this policy. Human raters remain independent;
the semantic model is part of the automatic instrument, not a human rater.

## Materializer, store and independent audit

The materializer consumes already persisted outputs; it has no model or provider
dependency. It rejects B3, free text, non-atomic claims, missing evidence
bindings, unactivated reserves and duplicated request-local claims. Identical
output content observed under distinct frozen requests remains separate in the
pool because its family, evidence condition and relation assignment can differ;
the later 200-claim sampler still forbids repeated canonical claim text.

The corpus store publishes canonical entry objects, a manifest and a terminal
receipt through a create-only same-volume staging directory. Identical replay is
idempotent and non-identical replay is rejected. The independent auditor reads
persisted bytes directly and does not import or trust the writer. It detects
missing or corrupt objects, partial publication, untracked files, duplicate
entries, duplicate request-local claims, cross-source binding and visibility
leakage.

## Verification

Run:

```bash
PYTHONPATH=src python scripts/claim_support_corpus_readiness.py verify
```

The expected terminal state is
`claim_corpus_materialization_ready_zero_outcome`, with 15 primary families,
six reserves, 360 primary requests, 144 reserve requests and eight adapters.
All outcome flags remain false.

The earlier `corpus_protocol_frozen_source_expansion_required` receipt remains
immutable historical evidence of the insufficient five-family starting point.
It is not rewritten as if the additional sources had existed at the first
freeze. The new family inventory, request census, manifests, plan and readiness
receipt form a separate forward-linked identity chain.

Readiness is not scientific admission and is not permission to run the main
evaluation. Development provider execution, real claim materialization, human
validation and the main-run manifest remain separate gates.

## Development execution preflight

The next boundary is inspected without a provider call:

```bash
PYTHONPATH=src python scripts/claim_support_corpus_execution.py preflight
PYTHONPATH=src python scripts/claim_support_corpus_execution.py rehearse
```

The canonical primary schedule contains 360 diagnosis requests, but only 315
are model-backed. The 45 `B0` requests are deterministic local executions. A
completed output may contain up to five atomic claims, and relation assignment
is one request per claim. The prospective upper bound is therefore 1,800
relation requests, 2,115 total provider calls on a no-retry pass, and 4,230 if
every eligible call consumes the two-attempt ceiling. These are safety ceilings,
not expected usage; exact input tokens and cost must be computed from the 45
observed contexts before authorization. No reserve request is scheduled.

The offline rehearsal proves the complete census, route split, terminal replay
skip and fail-closed treatment of a partial request. It does not construct a
diagnosis input, call a provider, parse an output or consume the one registered
execution.

The visible-evidence and automatic-relation implementations are complete and
bound prospectively. The measured census now contains exactly 45 contexts: 15
primary families under `full`, `missing_key` and `noisy` conditions. Every
context is rebuilt from the registered Telco development partition and fitted
baseline, is bound to an immutable source-projection SHA-256, remains below 32
items and 12,000 canonical UTF-8 bytes, and excludes evaluator-side family,
mechanism, condition and outcome fields from its model-visible payload.

Verify the independently reconstructable census and accounting receipt with:

```bash
PYTHONPATH=src python scripts/claim_support_observed_evidence.py verify
```

The tracked receipt records 45 contexts, 15 source projections, 315
model-backed diagnosis requests and 296,071 input tokens. This token count is
exact for the pinned `tiktoken==0.14.0`, `o200k_base` and two-message local chat
serialization contract. It is not claimed to be the provider-billed token
count, which is unavailable until a provider call occurs. At the frozen rates,
the measured diagnosis-input estimate is USD 0.592142. The USD 53.944142
one-attempt total is a deliberately conservative safety ceiling that also
assumes maximum diagnosis and per-claim relation outputs; it is not expected
spend. The receipt pins the USD 2.00 input and USD 8.00 output rates observed
for GPT-4.1 on 2026-09-03 from the
[official model page](https://developers.openai.com/api/docs/models/gpt-4.1),
so a later price change cannot silently rewrite this estimate.

Supply both immutable artifacts to execution preflight:

```bash
PYTHONPATH=src python scripts/claim_support_corpus_execution.py preflight \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --evidence-receipt configs/evaluation/claim_support_observed_evidence_receipt.json
```

Exact reconciliation removes `observed_evidence_census_pending` and exposes the
frozen-input count and cost ceiling. It does not authorize an external send.
The fairness freeze still records `execution_authorized=false`; explicit
authorization and a clean synchronized `main` remain mandatory. Synthetic
fixtures, intervention labels, arbitrary relation metadata and post-output
patches cannot clear the gate.

## Authorized diagnosis execution

The live boundary is deliberately split into authorization and execution.
Authorization is permitted only from a clean checkout of synchronized `main`
and binds the commit, 360-request plan, 45-context census, token receipt,
pinned GPT-4.1 snapshot, retry ceiling and conservative cost ceiling. The
authorization file, lease, attempt store and terminal receipt must remain in a
private directory outside the repository.

First create the immutable authorization without making a provider call. Copy
the exact `execution_plan_sha256` printed by preflight into the confirmation
argument:

```bash
CLAIM_RUN_DIR="/absolute/private/claim-support-live-run"
CLAIM_PLAN_SHA256="replace-with-exact-preflight-value"
PYTHONPATH=src python scripts/claim_support_corpus_execution.py authorize \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --evidence-receipt configs/evaluation/claim_support_observed_evidence_receipt.json \
  --confirm-plan-sha256 "$CLAIM_PLAN_SHA256" \
  --output "$CLAIM_RUN_DIR/authorization.json"
```

After setting `OPENAI_API_KEY`, require the complete gate to pass:

```bash
PYTHONPATH=src python scripts/claim_support_corpus_execution.py require-live-ready \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --evidence-receipt configs/evaluation/claim_support_observed_evidence_receipt.json \
  --authorization "$CLAIM_RUN_DIR/authorization.json"
```

Only then execute the registered diagnosis run, confirming the exact
`authorization_sha256` printed by the authorization command:

```bash
CLAIM_AUTHORIZATION_SHA256="replace-with-exact-authorization-value"
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/claim_support_corpus_execution.py execute \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --evidence-receipt configs/evaluation/claim_support_observed_evidence_receipt.json \
  --authorization "$CLAIM_RUN_DIR/authorization.json" \
  --confirm-authorization-sha256 "$CLAIM_AUTHORIZATION_SHA256" \
  --store "$CLAIM_RUN_DIR/attempt-store" \
  --lease "$CLAIM_RUN_DIR/execution-lease.json" \
  --output "$CLAIM_RUN_DIR/diagnosis-execution-receipt.json"
```

The runner constructs exactly 315 provider-backed requests and 45 local B0
requests. Provider-visible prompt and context bytes are exactly those used by
the frozen token receipt. Every raw response, parsed technical result, retry
record and terminal state is content-addressed. A terminal replay is an
idempotent no-op; any partial request blocks continuation. Per-request shards
retain the existing immutable-store verifier while avoiding a quadratic scan
of all 360 ledgers after every state transition.

Completion of this command is technical diagnosis closeout only. It does not
assign claim/evidence relations, materialize or select 200 claims, create
automatic support labels, consume human annotations, or open main/sealed
evaluation outcomes.

## Post-execution reserve decision and reconciliation

Reserve eligibility is decided from execution timing, not output quality. A
reserve family may replace a primary family only when that primary family is
technically ineligible before any request starts. Provider failures observed
after a request starts remain terminal records in the authorized denominator;
they cannot activate a reserve, be silently excluded, or be repaired by a new
request.

Run the independent read-only reconciliation after the live receipt exists:

```bash
PYTHONPATH=src python scripts/claim_support_execution_reconciliation.py \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --authorization "$CLAIM_RUN_DIR/authorization.json" \
  --lease "$CLAIM_RUN_DIR/execution-lease.json" \
  --live-receipt "$CLAIM_RUN_DIR/diagnosis-execution-receipt.json" \
  --store "$CLAIM_RUN_DIR/attempt-store" \
  --reserve-output "$CLAIM_RUN_DIR/reserve-activation-receipt.json" \
  --reconciliation-output "$CLAIM_RUN_DIR/request-reconciliation-receipt.json"
```

The verifier has no provider adapter, credential access, materializer, relation
instrument, or human-workflow dependency. It independently checks all 360
authority files and request shards, the immutable object and ledger chains,
terminal coverage, evidence and schedule bindings, attempt counts, issue
census, and the aggregate store hash. Both output receipts are canonical,
content-addressed, create-only private artifacts; exact replay is idempotent and
different bytes conflict.

The registered GPT-4.1 execution passed this reconciliation with 360/360
terminal requests: 258 parsed and 102 `provider_failed`. All 315 provider-backed
requests and all 45 deterministic requests started, so no family met the
pre-execution reserve rule and zero reserve requests were activated. The
provider failures remain denominator-visible. Output normalization is the next
separate gate. At this point `outputs_normalized`, `claims_materialized`,
`automatic_labels_generated`, `blind_packets_generated`,
`human_annotations_collected`, and `main_or_sealed_outcomes_opened` all remain
false.

## Post-reconciliation claim-pool construction boundary

The construction implementation reads only independently verified terminal
objects. It cannot access raw provider bytes, credentials or a provider
adapter. It accepts the registered `diagnosis-provider-output/1` envelope only,
normalizes schema-valid terminal outputs to `diagnosis-output/2`, and preserves
every technical or schema failure in the immutable 360-request census. It does
not recover claims from prose or split sentences and punctuation into claims.

The read-only preflight reproduces the reserve decision and request
reconciliation before it exposes any parsed payload:

```bash
PYTHONPATH=src python scripts/claim_support_pool_construction.py preflight \
  --evidence-census configs/evaluation/claim_support_observed_evidence_census.json \
  --authorization "$CLAIM_RUN_DIR/authorization.json" \
  --lease "$CLAIM_RUN_DIR/execution-lease.json" \
  --live-receipt "$CLAIM_RUN_DIR/diagnosis-execution-receipt.json" \
  --reserve-receipt "$CLAIM_RUN_DIR/reserve-activation-receipt.json" \
  --reconciliation-receipt "$CLAIM_RUN_DIR/request-reconciliation-receipt.json" \
  --attempt-store "$CLAIM_RUN_DIR/attempt-store"
```

After preflight, `prepare` can publish a create-only private preparation
artifact outside the repository. That artifact binds one normalization record
to every authorized request and an exact relation-assignment request to every
schema-native claim. The provider-visible relation context contains exactly
`claim_text`, `claim_type` and `visible_evidence`; mechanism, evidence
condition, variant, hidden truth, human judgment and outcomes cannot cross the
gateway contract.

Full-pool publication is a later, separate action. It requires a result for
every frozen relation request, exact preparation and policy hashes, and zero
unresolved relation terminals. The deterministic support instrument is then
applied once and the resulting entries, manifest and receipt are published to
the existing content-addressed create-only store. Identical replay is
idempotent; missing, reordered, forged, conflicting or tampered inputs fail
closed. This boundary never selects the 200-claim validation sample or creates
human packets.

The construction code and its synthetic conformance tests are present, but no
real output normalization, relation-assignment run, automatic label or corpus
entry is published by that implementation change. Those actions require the
merged source, private artifacts and their separately reviewed execution gate.

## Normalization recovery boundary

The two failed synthetic attempts remain retired. The corrected structured
transport subsequently passed its separately authorized compatibility gate and
the registered diagnosis recovery reached terminal state on source commit
`dcec23655f3234b01b54c1c3b3c2e50d7b09f513`. Its immutable 360-request store
contains 282 parsed outputs and 78
`retry_exhausted` terminals. Every parsed output independently normalizes under
`diagnosis-output/2`, yielding 962 relation candidates without prose repair.

Availability is not exchangeable across the registered mechanisms. Data drift
ran first and retained 120/120 parsed requests; preprocessing mismatch retained
83/120 and label noise retained 79/120. This is an execution-order confound, not
evidence of relative mechanism performance. The 78 failures remain in the
denominator, cannot activate reserves after execution, and cannot be retried as
the same registered attempt.

The recovery closeout and preparation are provider-free, create-only private
operations:

```bash
PYTHONPATH=src python scripts/claim_support_pool_construction.py recovery-closeout \
  --recovery-run-dir "$CLAIM_RECOVERY_DIR" \
  --output "$CLAIM_RECOVERY_DIR/recovery-closeout.json"

PYTHONPATH=src python scripts/claim_support_pool_construction.py recovery-prepare \
  --recovery-run-dir "$CLAIM_RECOVERY_DIR" \
  --recovery-closeout "$CLAIM_RECOVERY_DIR/recovery-closeout.json" \
  --output "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json"
```

Closeout binds all 360 dispositions, the missingness tables, the execution
receipt and the terminal-store hash. Preparation binds the 282 normalized
outputs to exactly 962 blind relation requests and records 748 distinct
canonical claim texts and 214 repeated instances. It does not discard, repair,
label or sample any candidate.

Relation assignment remains a separately authorized paid action. The final
200-claim selection remains blocked until relation results reconcile and the
four 50-claim label strata satisfy the frozen family/output caps. The selector
also prevents the same canonical claim text from entering the human sample
more than once; it fails closed rather than padding when 200 distinct eligible
claims are unavailable. No blind packet is created by either recovery command.

## Blind relation execution boundary

The relation executor consumes the immutable recovery preparation as an exact
962-request census. It sends only `claim_text`, `claim_type` and the cited
`visible_evidence` to the frozen GPT-4.1 snapshot. A single global one-second
minimum start interval applies to initial calls and retries. There is no model
fallback, silent provider switch or repair of a failed terminal.

Planning and rehearsal are read-only and provider-free:

```bash
PYTHONPATH=src python scripts/claim_support_relation_execution.py plan \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --run-dir "$CLAIM_RELATION_DIR"

PYTHONPATH=src python scripts/claim_support_relation_execution.py rehearse \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --run-dir "$CLAIM_RELATION_DIR"
```

The observed preparation contains 962 unique assignment identities and
437,982 exact message-input tokens. Its conservative two-attempt ceiling is
2,846,140 input tokens plus 1,154,400 output tokens, or USD 14.927480 under the
frozen pricing assumptions. This is a ceiling rather than an expected charge.
Authorization must bind both the plan and rehearsal hashes on a clean,
synchronized `main` checkout. `require-live-ready` then constructs all 962
gateway requests and validates the pinned SDK, endpoint, credential shape,
model policy and destination without making a provider call.

Execution consumes a create-only one-attempt lease and writes an isolated
sharded attempt store, relation-result bundle and receipt. Verification
rebuilds the complete request census, lease, terminal-store hash, semantic
relations and receipt from the immutable objects. Provider-terminal failures
and locally rejected semantic responses are reported separately and both stay
in the denominator. The run does not materialize automatic labels, publish a
corpus, select the final 200 claims or create human packets; those remain later
gates and require zero unresolved relation terminals.

The registered run reached 962/962 terminal requests with 961 parsed relation
responses and one `retry_exhausted` terminal. The sole unresolved request used
both registered attempts and both attempts ended in transient provider errors;
there was no semantic-validation failure. Re-executing the original run is
forbidden. Its receipt, result bundle, terminal-store hash and failed terminal
remain immutable historical evidence.

## Targeted relation-terminal recovery

The one unresolved transient terminal may be addressed only through the
separate targeted-recovery boundary. Closeout independently verifies the full
predecessor store, identifies exactly one eligible failure and binds its two
transient attempts. Planning then freezes one request, the original GPT-4.1
snapshot, prompt, visible payload, schema, 600-token output budget, two-attempt
ceiling and one-second global pacing. The provider cannot see predecessor
results, mechanism identity, evidence condition, hidden truth, human judgments
or protected outcomes.

The recovery has its own clean-main authorization, destination and create-only
lease. Success replaces only the failed relation in a reconciled bundle; all
961 predecessor successes must remain byte-equivalent and in their original
order. The original failure remains linked in the closeout and recovery receipt,
and total provider attempts include both predecessor and recovery histories.
Failure of the recovery remains terminal and cannot be retried again.

Provider-free preparation is:

```bash
PYTHONPATH=src python scripts/claim_support_relation_recovery.py closeout \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --predecessor-run "$CLAIM_RELATION_DIR" \
  --run-dir "$CLAIM_RELATION_RECOVERY_DIR"

PYTHONPATH=src python scripts/claim_support_relation_recovery.py plan \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --predecessor-run "$CLAIM_RELATION_DIR" \
  --run-dir "$CLAIM_RELATION_RECOVERY_DIR"

PYTHONPATH=src python scripts/claim_support_relation_recovery.py rehearse \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --predecessor-run "$CLAIM_RELATION_DIR" \
  --run-dir "$CLAIM_RELATION_RECOVERY_DIR"
```

Authorization must confirm the exact plan and rehearsal hashes printed by those
commands. `require-live-ready` verifies the credential and frozen OpenAI SDK
without sending a request. `execute` consumes the lease and can send only the
single authorized request. `verify` reconstructs the target, terminal store,
result, reconciled bundle and receipt without provider access. If a process
stops after the terminal store is complete but before all closeout files are
published, `finalize` safely reconstructs those files from the terminal store;
it never invokes the provider and requires the same clean synchronized commit.

A successful recovery is still not a 200-claim sample and not a human packet.
The reconciled 962-result bundle merely clears the technical prerequisite for
provider-free full-pool publication and the subsequent frozen feasibility and
sample-selection audits.

## Independent full-pool feasibility closeout

Full-pool publication and closeout are provider-free operations. Both require
a clean checkout whose `main` commit equals `origin/main`, and every input and
output path must remain outside the repository. Publication applies the frozen
automatic instrument to all 962 reconciled request-local claims and writes a
content-addressed, create-only run. Closeout independently reads the persisted
bytes without trusting writer state, reconstructs every request, output, claim,
relation, evidence and automatic-label binding, and then tests the frozen
balanced selector without publishing a sample.

```bash
PYTHONPATH=src python scripts/claim_support_pool_construction.py publish \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --relation-results "$CLAIM_RELATION_RECOVERY_DIR/reconciled-results.json" \
  --pool-store "$CLAIM_POOL_DIR/store" \
  --output "$CLAIM_POOL_DIR/publication-closeout.json"

PYTHONPATH=src python scripts/claim_support_pool_construction.py closeout \
  --preparation "$CLAIM_RECOVERY_DIR/claim-pool-recovery-preparation.json" \
  --recovery-closeout "$CLAIM_RECOVERY_DIR/recovery-closeout.json" \
  --relation-results "$CLAIM_RELATION_RECOVERY_DIR/reconciled-results.json" \
  --pool-store "$CLAIM_POOL_DIR/store" \
  --publication-closeout "$CLAIM_POOL_DIR/publication-closeout.json" \
  --output "$CLAIM_POOL_DIR/feasibility-closeout.json"
```

The receipt carries both the diagnosis-source commit and the closeout-code
commit, preserves the 78 technical diagnosis failures and the execution-order
missingness warning, and reports all four label strata with distinct text,
family and output coverage. Repeated request-local instances remain auditable
in the full pool, while the selector excludes repeated canonical claim text and
enforces the frozen per-family and per-output caps. If any label cannot supply
50 distinct eligible claims from at least ten families and 25 outputs, closeout
terminates as insufficient. It does not reduce the sample, pad a label, open a
reserve, create a blind packet or authorize human validation.

The registered closeout terminated as `claim_pool_insufficient_label_stratum`.
It observed 842 fully supported, 112 partially supported, six unsupported and
two contradicted instances; unsupported and contradicted both failed the frozen
quota and diversity requirements. V1 is therefore closed, non-poolable
historical evidence. The separately versioned
[`claim-support-validation-v2-protocol.md`](claim-support-validation-v2-protocol.md)
records the exact failure audit and the prospective V2 design. No V1 output is
eligible for the V2 sample, and neither the closeout nor the V2 protocol creates
a blind packet or authorizes another provider execution.

## V2 diagnosis-cohort execution boundary

CV2-09 implements, without executing, the registered 360-request V2 cohort.
The executor rebuilds all 315 model-backed requests and 45 deterministic `B0`
requests from authenticated frozen inputs. It binds the source commit,
qualification, schedule, prompt, schema, visible evidence, provider snapshot,
2,048-token output ceiling, two-attempt policy and global pacing before any
terminal can be written.

The private run uses a create-only lease and authenticated per-request terminal
store. Completion requires exactly 360 terminal shards. Independent
verification reconstructs the entire store and receipt without trusting writer
state or invoking a provider. Completed terminal shards can be replayed, while
a mid-shard partial state fails closed rather than risking a duplicate provider
call. Provider failures retain public-safe categories, remain in all applicable
denominators and cannot silently disappear through replay.

The technical gate requires at least 95% parsed globally and at least 90%
within each mechanism, evidence condition and provider-backed variant, while
all deterministic `B0` requests must terminate. A pass unlocks only a separately
authorized relation stage. It does not establish label balance, materialize a
sample, or create blind human packets. At this code-boundary state, no V2 cohort
provider calls or outcomes exist; execution requires a fresh plan, rehearsal,
authorization and preflight on the exact clean synchronized `main` commit after
merge.
