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
log parser, MLflow integration, in-toto verifier or Modelstamp implementation was
evaluated in that first census. The MLflow/in-toto follow-up below is a separate
development study, not a replacement for its results. Native versus scoped views compare capture capability; S versus T
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
not reproduced in the first census. Do not advance a complex monitor, capture optimizer or broad
benchmark claim solely because this controlled census or its tests passed.

## Prevalidation: independent SDK workflow and strong provenance

This follow-up uses **MLflow 3.9.0's actual local SQLite model registry and public
sklearn flavor loader**, rather than reimplementing a registry facade. It executes
**in-toto 3.0.0 full verification** with a signed layout, authorized signed links,
required artifacts, `MATCH` and terminal `DISALLOW`. It is exposed development:
`validation_locked=false`, not a held-out validation protocol or natural incident
corpus. One MLflow workflow stack is one cluster; eight authored schedules are
not eight independent systems. No general temporal-monitor method is admitted.

### Selection policy and capture boundary

The [MLflow workflow documentation](https://mlflow.org/docs/latest/ml/model-registry/workflow/)
explicitly permits mutable aliases: a later alias load can select a newly assigned
version. A version URI pins a different contract. This study uses actual returned
`ModelVersion.version` as a version witness, **not an alias epoch or proof of
registry linearizability**. Both version-pinned and legitimately reselected loads
are included. See the pinned [sklearn loader source](https://github.com/mlflow/mlflow/blob/v3.9.0/mlflow/sklearn/__init__.py)
and [model artifact repository](https://github.com/mlflow/mlflow/blob/v3.9.0/mlflow/store/artifact/models_artifact_repo.py).

Two small fitted models A and B are saved by the SDK in an owned temporary
directory and registered as versions 1 and 2. Only their known, freshly generated
`model.pkl` bytes may enter the native unpickler. No downloaded, submitted or
historical pickle is accepted. The instrumented native `pickle.load` boundary
reads the actual file object, separately retains the offered bytes, hashes them
for the observer and passes **that same `BytesIO`** to the original unpickler.
The observer cannot inspect the separate control ledger.

This instrumentation changes streaming semantics. It observes the full buffer
**offered to the unpickler**, not a proof that the unpickler reads every byte,
the whole MLflow package, subsequent object mutation or actual serving use.
The temporary global hook is supported for this sequential local study, not
arbitrary concurrent application workloads. SDK telemetry is disabled and socket
connections denied; tracking/registry settings and the hook are restored. Only
owned SQLite engines are disposed. Native Windows behavior must be checked by CI;
the local research execution was on macOS/Python 3.12.

### Fair comparison: capture and verification are different questions

Three scoped methods receive exactly the same admitted selection, scope/token,
retry-parent, offered-buffer and closure evidence:

- `S`: existing strong receipt/correlation checker.
- `T`: existing bounded compatible-completion reference.
- `P`: independent lifecycle/policy adapter with actual signed in-toto verification.

P does not call S, T or shared admission to decide its result. Scope, phase,
inherited identity and closure are represented as required hashed artifacts.
Duplicate delivery, foreign scope, conflicting admissions, absent parent/closure,
wrong phase, excess loads and unavailable signatures have explicit tests. An
observed violation can be conclusive before closure; a matching prefix cannot
establish completed-attempt compliance.

The [in-toto API](https://in-toto.readthedocs.io/en/latest/api.html) and
[specification](https://github.com/in-toto/specification/blob/master/in-toto-spec.md)
define signatures and artifact rules. `REQUIRE` and terminal `DISALLOW` prevent
missing or unmatched artifacts from silently passing a `MATCH`. No inspection
commands execute. Expected-command/byproduct fields are not used as evidence.
Signing is a **local trusted-record transcode**; it adds no new independent-host
capture or authentication capability to the observer.

A second in-toto comparator verifies pre/post path hashes. This is an **adapted
path-evidence artifact-chain comparator**, not an out-of-box `in-toto-run`
collection benchmark. Its artifact-rule `pass` is reported without mapping it
to authorized-load compliance. Native metadata and path evidence are retained
separately from the same-buffer view; richer capture is not called a better
checker algorithm.

### Fixed development census and result

| Schedule | Reference | S / T / P |
|---|---|---|
| Pin version A, load A | compliant | compliant |
| Resolve updated alias B, load B | compliant | compliant |
| Acceptance pin A, load alias B | violation | violation |
| Path A before/after, transient B offered to unpickler | violation | violation |
| Retry inherits root A but alias loads B | violation | violation |
| Retry legitimately reselects B and loads B | compliant | compliant |
| Retry loads B, target witness dropped in the spool | violation | unknown |
| Warmed A reused, no target deserialization | no new load | excluded |

All **7/7 planned load attempts completed**, with no technical failure. Each
scoped method identifies **6/7** (3 compliant, 3 violation), leaves **1 unknown**,
and makes **zero false compliance/violation commitments** in this fixed census.
S, T and P agree on all eight slots. Seven actual scoped in-toto verifications
execute; the missing-buffer slot verifies a valid partial prefix but remains
unknown. Four retained auxiliary deserializations (three parent loads and one
cache warmup) are separate from the target denominator. Failed slots remain in
the planned denominator; their partial auxiliary state is not counted as retained
successful capture. Setup failure aborts with no claimed complete report.

The **critical pair** is a legal pinned-A load versus the authored A→B→A
same-path control. Both have byte-identical native fact frames comprising SDK
selection/current snapshots, actual version URI, registered-model metadata,
MLmodel hash and pre/post model-file hashes. Their separately retained offered
buffers differ and give opposite reference statuses. All seven path-only chain
checks pass, including this pair; this is not an in-toto verifier defect. It
shows that those path/metadata observations do not identify this input-buffer
property under the declared control and trust model.

Disposition: `bounded_capture_finding_no_new_checker_advantage`. This provides
a concrete executable capture-adequacy boundary on an independent SDK stack.
It does **not** establish that all provenance tools fail, that an uninstrumented
MLflow deployment has this bug, that T outperforms S/P, or that 0 observed errors
is a universal guarantee. This is not a measured capture-cost frontier, a new
natural source population, adversarial-host attestation or a validated theory
paper. The earlier 12-control census remains unchanged and is not pooled with
this one into an independent sample-size claim.

### Run, replay and next boundary

Optional dependencies are pinned in `.[provenance]`. The normal runtime does not
auto-install them. Linux and Windows test jobs install the extra so the real SDK
and signature tests run rather than silently skipping them. Without the extra,
those explicitly marked optional tests skip and the CLI fails closed.

```sh
PYTHONPATH=src python scripts/model_load_provenance.py run --root . --report PRIVATE_NEW_REPORT
PYTHONPATH=src python scripts/model_load_provenance.py verify --root . --report PRIVATE_NEW_REPORT
```

The destination must be a new bounded report outside the repository in an
existing private directory. No overwrite or symlink destination is accepted.
Raw generated buffers are private; stdout is aggregate only. Replay hashes but
never deserializes retained buffers. It validates code identity before/after
execution, fixed census, SDK/native frame, observer joins/closure, independent
reference, signed verifier outcomes and aggregate interpretation. Self-hashing
protects consistency, not authorship or trust in a malicious report producer.

**The prevalidation check is complete; final validation remains unlocked.**
The next scope is a prospective narrowed validation design: explicitly
hold out workflow/lifecycle units, define capture-adequacy endpoints and unavailable
evidence, preserve legal alias/retry/cache controls, compare fairly adapted S/P
and separate checker efficacy from capture capability. These exposed controls
cannot become held-out cases. A capture-cost study requires a measured useful
cost/coverage gap; final validation requires a separately sealed design and scoped
execution authority.
