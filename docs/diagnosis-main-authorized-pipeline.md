# Diagnosis main-study authorized pipeline

This entrypoint joins the frozen 1,024-request census to terminal persistence,
blind claim-to-evidence scoring, analysis-input materialization, and the frozen
analysis. It does not change the registered census, GPT-4.1 snapshot, prompts,
metrics, aggregation, missingness, or failure policy.

## Safety boundary

- `preflight` requires clean synchronized `main` and runs the complete pipeline
  with network-incapable adapters. Its
  output is not a scientific result and consumes zero registered attempts.
- `authorize` requires the exact preflight hash, a clean synchronized `main`, a
  private destination outside the repository, an explicit UTC timestamp, and
  an operator-approved USD cap. It performs no provider call.
- `execute` requires the exact authorization hash again at action time. This is
  the only subcommand that reads `OPENAI_API_KEY` or can contact OpenAI.
- Main and relation stores retain the raw response alongside parsed records. Sealed
  terminals are replayed read-only; any incomplete request stops the whole
  resume before another provider call is dispatched.
- One shared byte-conservative budget guard covers both provider stages. A
  request is rejected locally before dispatch if its maximum call reservation
  would exceed the approved cap. Budget exhaustion stops result publication.
  The guard uses GPT-4.1 standard token rates checked on 2026-09-22
  ([official model page](https://developers.openai.com/api/docs/models/gpt-4.1));
  an operator must recheck the rates before authorizing a later run.
- Raw requests, responses, item-level relation labels, analysis input, and the
  analysis report remain in the private run directory. Git receives no private
  path or item-level outcome.

## Registered semantics

The single lease is created only after local SDK, credential-format, and
persisted-budget checks pass, immediately before the first authorized execution
attempt. A process restart resumes the same lease and the same immutable
stores; it does not create a new registered attempt. A crash after a request is
persisted but before its result is persisted is intentionally not replayed
automatically, because doing so could duplicate billed or outcome-bearing work.
An atomic execution-directory lock rejects concurrent writers. An abrupt process
termination can leave this lock in place; inspect the owner and persisted requests
before removing a stale lock. Normal exit releases the lock but preserves the lease.

Technical provider, parse, and relation failures remain in the frozen
denominator through the existing worst-case-loss policy. Relation scoring uses
only `claim_text`, `claim_type`, and `visible_evidence`; mechanism, condition,
variant, hidden truth, human judgment, and main outcomes remain withheld.

## Operator sequence

1. On the exact merged commit, run `preflight` against the sealed private
   packet and a fresh private rehearsal workspace.
2. Review the emitted counts and hashes. Keep both the workspace and receipt
   outside the repository.
3. Present the exact private destination, model/snapshot, maximum request
   counts, approved budget, and preflight hash for fresh approval.
4. Run `authorize`; then present its authorization hash immediately before
   `execute`.
5. If execution is interrupted only between terminal requests, rerun the same
   `execute` command. Never delete or edit the lease or either attempt store.
6. Run `verify` after completion. Publish only a separately reviewed sanitized
   summary; never copy the private run directory into Git.

No live provider execution is part of this implementation change.
