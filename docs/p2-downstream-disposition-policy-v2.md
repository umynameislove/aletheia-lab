# Downstream mechanism disposition policy v2

## Purpose and precedence

This policy reconciles the frozen mechanism inventory after the registered
instrument-validity v1.2 terminal result and before downstream outcomes. It supersedes the operational
status table in v1 without deleting or rewriting that historical policy.

The machine-readable sources of truth are:

- `configs/benchmark/provenance/p2_downstream_disposition_policy_v2.json`;
- `configs/benchmark/provenance/p4_p5_mechanism_filter_manifest.json`;
- `configs/benchmark/provenance/p2r_v1_2_publication_summary.json`.

The v2 policy is bound to predecessor policy SHA-256
`ddd5fdabff327ba42c4a4e175954c1e82df2ca2d6ec0029bcfd6403eac8ca32b`
and instrument-validity publication-summary SHA-256
`e8595547c26f73f81d20fd53d00e5e876bb3a3c4f4044027bf8c9c6205fedd2e`.

## Terminal inventory

| Mechanism | Engineering state | Scientific state | Positive diagnosis ground truth |
|---|---|---|---:|
| Data drift | merged | `rejected` | no |
| Preprocessing mismatch | merged | `rejected` | no |
| Label noise | merged | `assumption_limited` | no |

Consequently:

- mechanism inventory: 3;
- primary admitted denominator: 0;
- assumption-limited denominator: 1;
- rejected-instrument denominator: 2;
- pending-confirmatory denominator: 0;
- diagnostic-ground-truth denominator: 0.

These counts measure different constructs and cannot be pooled, renamed, or
substituted. The empty admitted track is itself a required result.

## Downstream evaluation routing

The primary causal-diagnosis track is empty. Family-clustered primary causal
statistics require a non-empty admitted track and therefore must not be
manufactured for the current inventory.

Label noise may be evaluated only for prespecified assumption-limited behavior:
abstention correctness, evidence sufficiency, faithfulness, provenance
sufficiency, leakage, and reproducibility.

Data drift and preprocessing mismatch may be evaluated only for instrument-
rejection and validity behavior: false-admission rate, rejection correctness,
evidence sufficiency, faithfulness, provenance sufficiency, leakage, and
reproducibility.

All three remain in the evidence-accountability inventory. No non-admitted
mechanism may be scored as positive causal-diagnosis ground truth.

## Governance

Downstream outcomes cannot modify a mechanism state, threshold, or denominator.
Implementation or CI success cannot substitute for admission. The registered v1.2 study cannot
be rerun. A later status change requires a new independent protocol and new
content-addressed evidence, followed by another superseding policy that keeps
v1 and v2 available for audit.

## Verification

```bash
PYTHONPATH=src python scripts/p2r_v1_2_closeout.py verify
```

The verifier fails closed on predecessor-policy erasure, status inflation,
evidence rebinding, denominator drift, endpoint erosion, or routing a
non-admitted mechanism into causal-diagnosis scoring.
