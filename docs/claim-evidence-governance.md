# Public claim and evidence governance

This contract prevents implementation progress from being reported as a
scientific result. The machine-readable authority is
`configs/governance/public_claim_registry_v1.json`.

## Required fields

Every current public claim binds all of the following:

- claim class and evidence state;
- unit of analysis and exact denominator;
- minimum evidence and content-addressed primary artifacts;
- scope boundary and a concrete falsifier or downgrade rule;
- allowed wording and explicit wording that the evidence cannot support.

Engineering, scientific and positioning claims are separate classes. A merge,
passing CI run or implemented mechanism may support an engineering claim, but
none of those states constitutes confirmatory admission.

## Current mechanism boundary

The terminal mechanism inventory contains three mechanisms. The admitted
causal-diagnosis track is empty; label noise is assumption-limited; data drift
and preprocessing mismatch are rejected instruments. Therefore the diagnostic
ground-truth denominator is zero. Downstream evaluation must not replace that denominator with
the implementation inventory or pool the three terminal tracks.

The instrument-validity v1.2 result is a complete registered negative study, not a technical
failure. It established that the registered manipulations reached their dose
while the target-effect, direction and paired-instrument gates did not pass.
It does not establish that the mechanisms are universally ineffective.

## Fail-closed audit

Run:

```bash
PYTHONPATH=src python scripts/audit_public_claims.py
```

The audit recomputes the registry identity, reconciles mechanism denominators
and the instrument-validity terminal-store hash, resolves every current primary artifact and
scans the declared public surfaces for unsupported assertive wording. A missing
artifact, denominator mismatch, hash mismatch or forbidden assertion blocks the
audit. The audit does not rewrite public text or infer a stronger claim.

The evidence snapshot from the 2026-09-19 closeout is preserved at
`reports/governance/public-claim-audit-v1.json`. It covers only the declared
repository public surfaces. The local-only RQ0 paper package is not silently
treated as published; a bounded RQ0 registry entry is required at the later
publication-review boundary.

Pending claims stay in the planning registry and are not copied into this
current-public registry until their minimum evidence exists. If a falsifier is
triggered, the claim is narrowed, downgraded or withdrawn before publication;
its denominator is never repaired retrospectively from downstream outcomes.
