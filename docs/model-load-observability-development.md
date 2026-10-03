# Model-load observability on controlled local lifecycles

This development tool tests whether retained evidence identifies compliance with
an explicit model-load contract. It does not assess prediction quality, causal
diagnosis, malicious-host security, or whole-inference integrity.

## Contract and supported boundary

An attempt is scoped by request and retry number. The caller declares one policy:

- `pin_at_acceptance`: the actual selection returned at acceptance is binding.
- `resolve_at_load`: the actual selection returned at the designated load boundary
  is binding. A queued authorized alias update can therefore be legal.

Retry `inherit` requires an authoritative root selection and explicit parent
scope/token. Intermediate inheritance chains are unsupported and remain unknown.
Retry `reselect` selects again **at the retry load**, not when it is enqueued.
The root selection must match the declared root policy boundary.

The operational model has two to four possible artifact digests and zero to two
actual deserializer invocations. The normative rule permits at most one new load,
whose bytes must match the required selection. Wrong and extra loads remain in
the possible histories; the rule does not filter violations out of the reference.

Artifacts are compressed single-file Joblib objects created by this tool inside
an owned temporary directory. The same `BytesIO` buffer is independently retained
for replay, hashed for the tested witness, and passed to `joblib.load` with
`mmap_mode=None`. No external or historical pickle is loaded. This restriction is
necessary because [Joblib loading can execute code](https://joblib.readthedocs.io/en/stable/generated/joblib.load.html).
Multi-file models, memory mapping, subsequent object mutation and serving-use
contracts are outside scope. Cache-only attempts have no new load and are excluded
from the load denominator, even when a serving/reload policy would need a separate
assessment. They are not automatically called compliant inference.

## Baselines and verdicts

`S` is a simple receipt/correlation checker: it admits trusted scoped selection,
same-buffer load and closure records. A witnessed wrong/extra load can establish a
violation immediately. Matching visible bytes cannot establish completed-attempt
compliance without closure. Missing policy or required bindings remain unknown.

`T` exhaustively enumerates the declared finite completion set on **the same
admitted evidence as S**. Unanimous completions establish compliance or violation;
mixed statuses yield unknown; inconsistent observations yield conflict.
It is a specification/reference, not a new general temporal logic solver.
Shared admission is tested separately using independent finite-world predicates,
delivery permutations, truthful refinement and invalid parent-binding controls.

The native view is only a **current-alias/trace proxy** from these local producers.
Its registry declaration is not silently treated as an acceptance receipt or
consumed-buffer witness. Raw native messages are retained, but no general native
log parser, MLflow integration, in-toto verifier or Modelstamp implementation is
evaluated. Native versus scoped views compare capture capability; S versus T
compares checkers with matched evidence. Neither difference establishes that all
registry or provenance tools are deficient.

## Runtime experiment and independent replay

The fixed authored census has six episodes per implementation:

| Episode | Actual mechanism and control |
|---|---|
| Queued pin violation | Alias A becomes B before the worker; required A, actual B. |
| Queued late selection | Same update; load-time selection B and actual B are legal. |
| Overlap with delayed selection | Reader snapshot selects A while a separate writer commits B; the observer selection is held in its spool, then released. |
| Cache reuse | Object A is warmed, alias becomes B; target attempt does not deserialize again. |
| Retry with missing evidence | Actual initial A load, post-load retry and alias update; target attempt consumes B. SQLite workflow drops its load record; file workflow expires its selection. |
| Retry scope control | SQLite workflow routes the target load to the wrong mailbox; file workflow legally reselects B at retry load. |

Both implementations use a real queue/thread and persistent observer spool.
One registry uses SQLite WAL snapshots; the other uses an atomically replaced
file alias. Delay, drop, expiry and misrouting occur in the observer transport,
not by masking a completed gold ledger. Inherited parent access retrieves only
the root selection, not loads accidentally delivered to that mailbox.
[SQLite's documented snapshot behavior](https://www.sqlite.org/isolation.html)
supports the overlap control. Log arrival order is never taken as selection order.

The private reference reconstructs required identity from separate control
snapshots/policy and consumed identity by hashing the actual retained input bytes.
It does not consult candidate receipts, S or T. Replay checks the fixed census,
scope/policy/selection bindings, code identity, all decisions and aggregates without
deserializing retained bytes. This is algorithm/channel separation on a trusted
local host, not independent-host attestation. A report hash detects changes; it is
not an authenticated signature or evidence that its author is trustworthy.

## Run and replay

Run locally without retaining raw control bytes:

```sh
PYTHONPATH=src python scripts/model_load_observability.py run --root .
```

To retain and independently replay a report, choose a **new file in an existing
private directory outside the repository**:

```sh
PYTHONPATH=src python scripts/model_load_observability.py run --root . --report PRIVATE_REPORT
PYTHONPATH=src python scripts/model_load_observability.py verify --root . --report PRIVATE_REPORT
```

Existing files are not overwritten; symlink destinations and oversized replay
inputs are rejected. Stdout contains aggregates, not loader bytes or private
paths. There are no paid calls, downloads, historical experiment reads or protected
executions. Temporary models/spools are removed after this local run.

## Scientific interpretation

The development census produced 12 authored episodes: 10 eligible load attempts
and two cache-only attempts. Both S and T identified five eligible statuses before
delayed delivery and seven after it; the remaining three retained gaps stayed
unknown. Both methods made zero false compliance/violation commitments in this
census and agreed on every compared view. The current-alias proxy identified none.

The disposition is `narrow_controlled_feasibility_no_new_method_evidence`.
This shows useful lifecycle/capture failure boundaries in these controls, but
**no incremental benefit of T over S**, no deployed-incident population estimate,
no measured capture-cost frontier and no novel algorithm result. Twelve authored
episodes on two implementations are not twelve independent systems or sources.

Partial/out-of-order monitoring already has substantial prior work, including
[Basin, Klaedtke and Zalinescu](https://arxiv.org/abs/1909.11593). Artifact-flow
integrity also predates this tool, including [in-toto](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias).
These sources inform the assumptions and comparison boundary; their systems were
not reproduced here. Do not advance a complex monitor, capture optimizer or broad
benchmark claim solely because this controlled census or its tests passed.
