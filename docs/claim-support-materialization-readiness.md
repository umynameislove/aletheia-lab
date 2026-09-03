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

The canonical primary schedule contains 360 requests, but only 315 are
model-backed. The 45 `B0` requests are deterministic local executions. The
frozen two-attempt ceiling therefore permits at most 315 provider calls on a
one-attempt pass and 630 only if every model-backed request consumes its retry.
No reserve request is scheduled.

The offline rehearsal proves the complete census, route split, terminal replay
skip and fail-closed treatment of a partial request. It does not construct a
diagnosis input, call a provider, parse an output or consume the one registered
execution.

The live gate currently remains closed for three substantive reasons: an
executable visible-evidence boundary has not been implemented, the automatic
relation instrument has no pre-outcome relation-assignment implementation, and
the fairness freeze still records `execution_authorized=false`. Exact input
tokens and cost also cannot be calculated before the real visible-evidence
payloads exist. These findings block an external send; they must not be bypassed
with synthetic evidence, intervention labels, arbitrary relation metadata or a
post-output patch.
