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
bindings, unactivated reserves and duplicated source claims.

The corpus store publishes canonical entry objects, a manifest and a terminal
receipt through a create-only same-volume staging directory. Identical replay is
idempotent and non-identical replay is rejected. The independent auditor reads
persisted bytes directly and does not import or trust the writer. It detects
missing or corrupt objects, partial publication, untracked files, duplicate
entries, duplicate source claims, cross-source binding and visibility leakage.

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
