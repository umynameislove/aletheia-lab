# Diagnosis main freeze candidate V3

## Current decision

V3 passes every integrity and traceability check but deliberately remains
**not ready for main execution**. Its canonical manifest self-hash is
`4a08fdc58141159971dc1a64dc565375c31f7611de2aa6d8bd6cf14ad65e967c`.
It authorizes no execution, opens no protected main outcome, and records zero
consumed registered attempts.

On 2026-09-21, the current candidate was re-sealed after an outcome-blind
module-boundary and hash-ownership refactor. The scientific contracts and
frozen census did not change; no protected outcome was opened and no
registered attempt was consumed.

V3 closes two of V2's four blockers:

1. The exact census is now 32 families, six superfamilies, 128 sibling
   contexts, and 1,024 controlled logical requests. The deterministic Qwen
   subset contains 12 families and 72 exact request IDs.
2. The outcome-eligible runtime covers the eight controlled paths, including
   create-only per-turn persistence and exact offline preflight for 896
   provider-backed requests, 128 deterministic B0 requests, and 1,408 provider
   turns. B3 is correctly retained as a separate 35-case external-transfer
   stratum rather than forced into the controlled matrix.

Two blockers remain:

1. The user-run Q8 download/build/template check and seven-call synthetic
   development calibration have not run.
2. Independent outcome-blind methods approval cannot occur until that
   calibration receipt exists.

## Scientific position frozen in V3

- The design is a finite frozen benchmark policy comparison, not a causal
  trial or a superpopulation sample.
- The primary estimand is equal-family paired `B1 - A3`
  evidence-accountability loss across `full`, `missing_key`, and `noisy`.
- `B1-A3` is a bundle contrast. Only the sequential B1-A1, A1-A2, and A2-A3
  comparisons receive component language, and all remain benchmark-local.
- The BCa family-resampling interval is a descriptive stability summary. No
  p-value or formal null test is registered.
- Claim harm, coverage, abstention, citation validity, failure, latency, token,
  and cost outcomes remain separate to prevent abstention or non-response from
  masquerading as improved groundedness.
- Qwen, LogDx-CI, CodeGraph, FULL, B0, and RQ6b retain their non-pooling and
  claim boundaries.
- EviScope narrows novelty: paired evidence perturbation itself is adjacent
  prior art. Aletheia's defensible contribution is the diagnosis-specific,
  content-addressed combination of provenance, runtime paths, claim
  accountability, failure retention, and explicit transfer boundaries.

## Verification

From the repository root:

```bash
source .venv/bin/activate
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py
```

Expected: integrity `pass`, readiness `blocked`, and exactly these blockers:

- `qwen_local_artifact_build_and_calibration_receipt_missing`;
- `independent_methods_approval_missing`.

The strict audit must currently return 2:

```bash
set +e
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py --require-ready
test "$?" -eq 2
```

Exit 2 is evidence that fail-closed behavior works; it is not a failed
integrity audit.

## Required readiness transitions

1. Run the exact Q8 preparation and frozen development calibration in
   `docs/qwen-local-sensitivity-preparation.md`.
2. Preserve the create-only receipt and log outside Git; audit them without
   inspecting synthetic answer quality.
3. Issue a forward candidate closing only the Qwen blocker.
4. Send that candidate and review packet to an outcome-blind independent
   methods reviewer.
5. Bind the review receipt in a final forward manifest and run
   `--require-ready`.
6. Request a separate fresh authorization before any main provider call or
   protected-outcome opening.
