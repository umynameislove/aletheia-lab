# P2 downstream mechanism disposition policy

## Purpose

This policy freezes the distinction between engineering completion and scientific
admission before P4/P5 outcomes exist. A merged implementation, green CI, an alpha
family, or an eligible candidate is not confirmatory admission.

The machine-readable source of truth is
`configs/benchmark/provenance/p2_downstream_disposition_policy.json`. It is bound
to evidence snapshot commit `8155a31a10a8749fb0ea2c299e823eb10c4f3760`.

## Status at freeze time

| Mechanism | Engineering status | Scientific status | Confirmatory basis |
|---|---|---|---|
| Data drift | merged | `pending_confirmatory` | no registered terminal study |
| Preprocessing mismatch | merged | `pending_confirmatory` | no registered terminal study |
| Label noise | merged | `assumption_limited` | registered v3.3 completed with `ABSTAIN` |

Therefore the mechanism inventory is three and the admitted denominator is zero.
This is not a negative judgment about the two pending implementations; it prevents
implementation validity from being reported as confirmatory scientific evidence.

## Non-interchangeable denominators

Every downstream table and claim must identify one of these tracks:

1. **Mechanism inventory:** all three implemented mechanisms.
2. **Primary admitted track:** only mechanisms that later complete a registered
   confirmatory study and pass every prespecified gate. This set is empty at freeze.
3. **Assumption-limited track:** label noise, reported separately.
4. **Pending-confirmatory track:** data drift and preprocessing mismatch.

Counts from different tracks must never be pooled or silently substituted. If the
primary track remains empty, downstream reports must state that fact rather than
manufacturing a primary aggregate.

## Prospective admission rule

A mechanism changes to `admitted` only after its own registered confirmatory study
produces content-addressed terminal evidence and passes every prespecified outcome,
control, provenance, and cross-environment assumption gate. P4/P5 answers, scores,
or dashboard behavior cannot change a mechanism status. Thresholds, denominators,
and gate meanings cannot be relaxed after those outcomes are observed.

This version is immutable after merge. A later registered study may update a
mechanism's status only through a new, superseding, versioned status artifact that
keeps this policy and its evidence snapshot available for audit.

## Frozen label-noise abstention rubric

A complete downstream answer must:

- state `ABSTAIN` or `assumption_limited`;
- acknowledge the strong registered directional signal rather than claiming no
  observed effect;
- identify the failed extreme-prior assumption environments at odds multipliers
  0.25 and 4.0, while distinguishing the passing 1.0 environment;
- deny confirmatory admission and cross-dataset generalization; and
- bind claims to protocol SHA-256
  `5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456`
  and terminal-store SHA-256
  `d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152`.

Claiming that label noise is admitted, claiming three-of-three admission, allowing
cross-dataset generalization, denying the observed signal, or using hidden evidence
is a hard evaluation failure. Missing a required fact is incomplete. Evaluation
never retroactively changes the scientific status.

## Verification

```bash
PYTHONPATH=src python scripts/p2_downstream_disposition.py
```

The verifier fails closed on status inflation, denominator drift, evidence rebinding,
or an invalid abstention reference policy.
