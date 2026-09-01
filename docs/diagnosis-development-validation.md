# Diagnosis development validation

This document defines the offline validation boundary for the nine diagnosis
configurations. The validation exercises implementation wiring, request
authority, artifact integrity and frozen fairness constraints before any
protected evaluation outcome is opened.

It is an engineering validation result. It is not a model comparison, a
scientific estimate, an admission decision or evidence that one configuration
outperforms another.

## Authoritative inputs

The runner accepts two versioned authorities:

- [`diagnosis_development_pilot_plan.json`](../configs/evaluation/diagnosis_development_pilot_plan.json)
  defines three synthetic cases and the exact nine-configuration matrix;
- [`diagnosis_variant_fairness_freeze.json`](../configs/evaluation/diagnosis_variant_fairness_freeze.json)
  defines implementations, model policies, information budgets, tool policies,
  evidence policies, prompt policies and reporting strata.

The plan binds the complete registry hash and fairness-freeze hash. A missing,
duplicated, reordered or rebound configuration invalidates the run. The
canonical order is A1, A2, A3, B0, B1, B2, B3, CodeGraph and FULL.

The fixtures deliberately cover three contract states:

1. sufficient visible evidence;
2. conflicting visible evidence;
3. insufficient visible evidence requiring configured abstention behavior.

All fixture content is synthetic. Evaluator metadata, hidden answers and
protected outcomes are absent by construction.

## Execution boundary

The implementation boundaries and permitted dependency direction are recorded
in [ADR 0002](adr/0002-diagnosis-development-boundaries.md). The stable public
API remains `aletheia_lab.diagnosis.development`; execution, independent
validation and artifact publication are separate internal authorities.

[`diagnosis_development_pilot.py`](../scripts/diagnosis_development_pilot.py)
runs a deterministic local executor. It does not invoke the production model
gateway, make a provider call, access the network, execute a project, consume a
registered attempt or authorize interpretation.

Run and immediately audit a fresh store:

```bash
PYTHONPATH=src python scripts/diagnosis_development_pilot.py all \
  --store experiments/evaluation/outputs/diagnosis-development
```

Re-audit an existing content-addressed run without executing it:

```bash
PYTHONPATH=src python scripts/diagnosis_development_pilot.py validate \
  --store experiments/evaluation/outputs/diagnosis-development \
  --run-id devrun-<manifest-sha256>
```

When a store contains exactly one run, `--run-id` may be omitted. Multiple runs
require an explicit identifier so selection cannot depend on filesystem order
or recency.

## Artifact lifecycle

Each terminal run is published as one immutable directory:

```text
store/
  runs/
    devrun-<manifest-sha256>/
      objects/sha256/<prefix>/<suffix>
      terminal.json
  failures/
    devfail-<failure-sha256>.json
```

Requests, responses, tool ledgers, case definitions, run records and the
manifest are canonical JSON objects addressed by SHA-256. The terminal is
published only after the complete case-by-configuration matrix validates. The
store rejects missing objects, orphan objects, changed bytes, symlinks,
non-canonical object names and untracked files.

A failure publishes only a safe receipt containing the stage, exception class
and message hash. It never promotes partial output to a terminal run and never
copies fixture or response text into the receipt.

Repeated execution with identical authorities is idempotent: it resolves to
the same run identifier and cannot overwrite a conflicting directory.

## Fairness audit

The independent offline audit re-reads stored bytes and recomputes twelve
checks:

| Check | Required invariant |
|---|---|
| `implementation_artifacts_resolve` | all nine frozen factories resolve uniquely |
| `complete_variant_matrix` | every synthetic case has exactly nine terminal records |
| `request_authority_binding` | every request reconciles with plan, case, freeze and registry |
| `content_addressed_artifacts` | stored bytes and exact object membership match their hashes |
| `response_contracts` | each response satisfies its registered capability envelope |
| `matched_model_parity` | A1, A2, A3, B1 and B2 share the frozen model policy |
| `matched_information_parity` | matched configurations share the frozen information budget |
| `matched_context_parity` | matched requests share case-level context and evidence identities |
| `resource_budget_compliance` | observed retrieval, turn and context bounds do not exceed policy |
| `tool_ledger_and_fallback_policy` | required ledgers are exact; ambient tools and fallback are absent |
| `reporting_strata_preserved` | reference, external and system configurations remain separately pooled |
| `development_only_boundary` | no live call, outcome opening, registered attempt or interpretation occurs |

Any blocked finding makes the audit receipt terminally
`development_pilot_blocked`. Later registration code must call
`require_development_pilot_ready` and reject any receipt carrying a blocker.

The context bound uses the canonical UTF-8 byte count as a deliberately
conservative token upper bound. It does not use a characters-per-token
heuristic that could understate consumption.

## Tracked validation result

The versioned receipt is
[`diagnosis_development_pilot_receipt.json`](../configs/evaluation/diagnosis_development_pilot_receipt.json).
It records:

- 3 synthetic cases;
- 9 configurations;
- 27 of 27 terminal records;
- 12 of 12 passing audit findings;
- zero provider calls;
- zero registered attempts;
- no protected outcome access;
- no permission for scientific interpretation.

CI recreates this receipt in independent processes with distinct
`PYTHONHASHSEED` values and requires byte-identical terminal and audit
identities. The tracked receipt is therefore evidence that the implementation
and fairness contracts are executable and reproducible, not evidence about
diagnostic accuracy.

## Remaining authorization boundary

This validation does not authorize the main evaluation. Before protected
outcomes can be opened, the project must still freeze the real claim census,
complete evaluator onboarding and blind human instrument validation, resolve
all prespecified human-validation gates, and publish a separate immutable
registration and main-run manifest.

Development fixtures and their receipt must never be pooled into scientific
results or counted in a main-study denominator.
