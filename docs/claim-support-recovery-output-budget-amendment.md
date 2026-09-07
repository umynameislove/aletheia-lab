# Claim-support recovery output-budget amendment

Historical amendment: CSR-03R subsequently failed all three probes at 2,048
configured tokens. Keep its registration and artifacts unchanged. Current work
uses the [separate CSR-03S transport correction](claim-support-structured-output-compatibility.md),
not another invocation of the failed run.

## Registered technical finding

CSR-03 remains an immutable failed compatibility attempt. Its authorization,
lease, three request shards and terminal receipt are not edited, deleted or
reused. Independent verification records:

- three of three synthetic schema probes ended as `provider_failed`;
- all three carry the allowlisted issue `provider_output_truncated`;
- the registered maximum was 600 output tokens;
- no response content, scientific evidence, claim, label or human judgment was
  materialized by that attempt.

This proves that the synthetic requests reached the registered output cap
before producing complete structured responses. It does not reveal the
discarded partial text and does not establish that the 102 failures from the
earlier diagnosis run had the same cause.

## CSR-03R amendment

The recovery-only output ceiling is prospectively increased from 600 to 2,048
tokens. The value is 3.413 times the observed failed ceiling and stays far below
the pinned model snapshot's platform limit. It is bounded headroom, not a
guarantee that a request will complete.

The following remain identical to the prior recovery contract:

- GPT-4.1 and snapshot `gpt-4.1-2025-04-14`;
- all 45 observed-evidence contexts and the 360-request census;
- response schemas and normalization semantics;
- prompts, except for no new text in this amendment;
- variant implementations, evidence/tool access and selection policy;
- two-attempt transport ceiling and no fallback model.

All seven model-backed variants (`A1`, `A2`, `A3`, `B1`, `B2`, `CodeGraph`,
`FULL`) receive exactly the same 2,048-token ceiling. Deterministic `B0` remains
local and makes no provider call. The amended model-policy hash changes every
model-backed request identity; predecessor identities remain retired.

The tracked amendment is
`configs/evaluation/claim_support_recovery_output_budget_amendment.json`. Its
canonical SHA is bound into recovery protocol v2, the rehearsal, each new
authorization and the resulting request identities. Cost estimates are
recomputed using 2,048 output tokens per model call and both allowed attempts;
they remain conservative ceilings rather than billing predictions.

## Execution gate

CSR-03R uses a new private directory. Before authorizing it, the CLI verifies
the retired CSR-03 directory byte-for-byte against its registered
authorization, receipt and terminal-store identities. A new directory may not
overlap either the predecessor diagnosis store or the retired CSR-03 run.

The three synthetic probes must pass in CSR-03R before a full diagnosis
authorization can be created. If they fail, that new compatibility attempt is
also terminal and must not be restarted. No full 360-request call is implicit
in compatibility execution.

```bash
PYTHONPATH=src python scripts/claim_support_recovery.py \
  audit-retired-compatibility \
  --retired-compatibility-run /absolute/private/path/claim-support-recovery-run

PYTHONPATH=src python scripts/claim_support_recovery.py rehearse
```

The operator must run authorization and paid execution only from a clean,
synchronized `main` after merge and green CI. The authorization SHA and cost
ceiling must be copied explicitly; the CLI never auto-confirms them.
