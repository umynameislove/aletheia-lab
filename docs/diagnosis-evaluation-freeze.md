# Diagnosis evaluation feasibility and variant-fairness freeze

This document records the outcome-blind protocol feasibility lint and the
variant-fairness policy freeze. It is a preparation artifact, not an execution
authorization and not a scientific result.

At this freeze point:

- protected outcomes have not been opened;
- no registered diagnosis evaluation attempt has been consumed;
- no missing implementation is replaced by a default;
- a structural or CI pass cannot be promoted to scientific admission; and
- the command exits successfully when the freeze is internally valid, while
  `--require-ready` remains blocking until every execution prerequisite exists.

## Protocol feasibility lint

The strict source of truth is
[`configs/evaluation/diagnosis_protocol_feasibility_plan.json`](../configs/evaluation/diagnosis_protocol_feasibility_plan.json).
Its self-consistent receipt is derived by
[`scripts/diagnosis_evaluation_freeze.py`](../scripts/diagnosis_evaluation_freeze.py), rather than
being edited by hand.

The lint covers five independent surfaces.

| Surface | Frozen check | Failure behavior |
|---|---|---|
| Data and governance | Required archives, protocol, mechanism filter, runtime and closeout files exist as ordinary files and match their SHA-256 bindings | Block before registration |
| Runtime | Python 3.11/3.12, deterministic fixture adapter, immutable attempt store, production gateway adapter and structural reducer resolve | Block before registration |
| Artifact publication | Staging, content-addressed object and terminal roots are repository-relative, mutually disjoint, create-only and atomic | Reject the plan |
| Attempt semantics | Exactly one registered execution is permitted; bounded provider retries preserve the original request identity and are not new registered runs | Reject the plan or block closeout |
| Closeout | Complete request census, current authorization, terminal inventory and technical/scientific status separation are mandatory | Fail closed; never infer a scientific pass |

When both registered dataset archives are present and hash-valid, every
feasibility check now passes. The production gateway artifact is hash-bound and
its import resolves, but that state means only that the transport contract is
ready for registration. It does not authorize execution or open an outcome. A
source-only checkout, including CI, reports the archive `present` and `hash`
checks as blockers. These are valid environment-readiness failures; they are not
suppressed or replaced by network downloads.

### One execution versus provider retry

The registered study is allowed one immutable execution. A provider retry is a
bounded transport operation inside the same request identity. It may not change
the provider, model snapshot, prompt, visible context, tools, seed or output
schema. An identical replay is idempotent; a conflicting replay fails closed.
This distinction prevents a transient API failure from becoming an undisclosed
second scientific attempt.

## Variant-fairness freeze

The strict source of truth is
[`configs/evaluation/diagnosis_variant_fairness_freeze.json`](../configs/evaluation/diagnosis_variant_fairness_freeze.json).
The freeze contains exactly the canonical nine variants; removing, duplicating
or silently rebinding any variant invalidates it.

| Variant | Role | Comparison treatment |
|---|---|---|
| B0 | Deterministic non-LLM reference | Separate reference; no matched-LLM pooling |
| B1 | Plain matched LLM | Matched primary |
| B2 | Generic multi-turn RAG | Matched primary |
| B3 | Pinned LogDx-CI native transfer | External-only; native metrics remain separate |
| A1 | Structured evidence only | Matched primary |
| A2 | Structured evidence plus required citation | Matched primary |
| A3 | Citation plus abstention | Matched primary |
| CodeGraph | Registered graph retrieval/index path | Separate component/configuration result |
| FULL | Provenance-aware full Aletheia path | Separate whole-system result |

### What “fair” means

B1, B2, A1, A2 and A3 share the exact provider, immutable model snapshot,
generation settings, observable corpus, context ceiling, retrieval-item ceiling,
turn ceiling, question contract and hidden-field exclusions. Their registered
tool and evidence policies differ only where that difference defines the
ablation. Every query, selected item, omitted item, turn and tool call must be
preserved when a variant enables retrieval.

B0 and B3 cannot honestly share the main LLM policy. FULL and CodeGraph expose
different information paths. They are still frozen under explicit model,
context, tool and evidence budgets, but they are reported separately. Their
results must not be pooled into the matched-primary effect or used to attribute
a whole-system gain to one component.

All variants forbid web access, shell access, project execution and silent
fallback. The main LLM variants freeze OpenAI `gpt-4.1-2025-04-14`, temperature
0, top-p 1, seed 17, 600 output tokens, a 60-second request deadline and at most
two provider attempts for the same immutable request.

### Resolved implementation registry

Every frozen variant now resolves to a distinct, package-local implementation
factory. The canonical registry verifies the exact nine-variant census, rejects
factory aliases and wrong variant identities, and reconciles each implementation
against its frozen model, information, tool, evidence, prompt and response-schema
policies. Each resolved variant carries an implementation version, source-file
SHA-256 and a derived content hash.

The registry is still outcome-blind and execution-free. It describes the only
permitted strategy and capability envelope; it does not call a provider, perform
retrieval, open a protected outcome or authorize a registered attempt. A future
request must carry a content-addressed binding for its variant, implementation,
context, evidence and, when required, tool ledger. Missing or ambient tool access
fails closed.

## Verification

Run the outcome-blind audit:

```bash
PYTHONPATH=src python scripts/diagnosis_evaluation_freeze.py all
```

With both registered dataset archives present and hash-valid, expected status is
`diagnosis_freezes_verified_ready_for_registration`: the fairness receipt has no
implementation blocker and the feasibility receipt has no environment blocker.

In a source-only checkout, the receipt also includes the `present` and `hash`
blockers for each absent registered archive. The blocker count is therefore
environment-sensitive by design, while the blocker identities and reconciliation
remain deterministic for a given checkout.

To use the same command as a registration gate:

```bash
PYTHONPATH=src python scripts/diagnosis_evaluation_freeze.py all --require-ready
```

That command returns a blocking exit code until all implementation and
environment-readiness blockers are removed.
It still does not authorize execution: registration, immutable release and the
separate outcome-opening authority remain later steps.

## Next implementation order

1. Build the development-only runner and append-only artifact lifecycle around
   these immutable request bindings.
2. Exercise every variant with deterministic or synthetic development fixtures;
   do not open the main outcome.
3. Re-run the fairness audit and evaluation profile under distinct process hash
   seeds after runner integration.
4. Audit execution-path parity before preparing any immutable diagnosis
   evaluation registration or outcome-opening authorization.
