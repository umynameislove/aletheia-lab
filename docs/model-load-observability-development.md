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

## Prospective validation design lock (INT-04, 2026-10-04)

The prevalidation code above was merged in PR #168. The remaining INT-04 slice
now seals a **controlled loader-format and lifecycle-composition transfer**
design, not a new outcome. The sole public design is
`configs/evaluation/model_load_validation_protocol.json`; its canonical SHA-256 is
`62fa6545781b22311aaaa6a84fc6611868e98f92f20e6f16fde07b8601cf64e0`.
`model_load_validation.py` only prepares or reconstructs a private design plan.
It has no execute action and does not import the new loaders, fit models or read
the previous private reports. Protocol completion is **not execution readiness**.

### Question, selection and scope

Can the offered-buffer, request-policy-relative result transfer beyond the exposed
Joblib/MLflow workflows to **ONNX Runtime 1.23.2** and **SKOPS 0.15.0**, including
deferred handoff, interleaved retry, native reentry and warm-cache reload? These
are two unused loader APIs/formats under one authored scheduler, **not two
independent deployments**, natural incidents or a population sample. Primitive
ideas such as retries and cache reuse were already used in development; only the
selected API/lifecycle compositions are prospective. Schedule, branch and slot
identities stay outside candidate evidence. No loader/source substitution is
allowed after outcomes.

The trust model is an honest instrumented local process and scheduler. The
reference independently joins caller/root policy and snapshots the complete
immutable bytes offered at each actual native entry, before observer delivery
loss. It does not call S/T/P or reuse an authored expected-status field. It does
not attest every byte consumed, all model dependencies, later inference or a
malicious host. Failed native entries count as entries, while completed loads
and technical errors remain separate.

### Fixed workload and comparisons

| Item | Frozen choice |
| --- | --- |
| Census | 2 loader strata × 12 schedules × observation/prevention branches = **48 planned slots**. Four lifecycle families have three schedules each. |
| Observation | 22 planned load slots and 2 cache-only slots. S/T/P share the same admitted policy, binding, occurrence and closure evidence. |
| Prevention | 24 separate opportunities; blocking changes execution and is not a matched diagnostic verdict. |
| Native budget | At most 48 target plus 24 auxiliary entries, **72 total**. Reentry is two actual native calls; repeated delivery of one record is one call. |
| Cutoffs | After attempt close before delayed delivery, then after delayed delivery before cleanup. Expired binding stays unavailable; reference cannot fill candidate gaps. |
| Reference | Root/attempt binding, selected artifact and actual native-entry buffer retained independently of observer spool. |
| S/T/P | Existing receipt S, bounded completion reference T and adapted provenance P with the actual signed in-toto verifier. P can carry scoped buffer evidence; path-only capture is an explicit ablation. |
| Prevention baseline | Read once, check immutable bytes, pass that same buffer; an atomic root/attempt-bound token permits at most one native entry. Digest checking alone is insufficient for double loads. |
| Resources | 256 KiB/artifact, 2 local fits, 30 s/slot, 60 s/setup, 1,800 s total, 32 MiB private result cap, no runtime network or provider calls. |

ONNX uses two embedded float32 MatMul/Add graphs (one input tensor with two
features), CPU provider only, one thread and constructor fallback disabled.
External tensor data, custom operators and sidecars are excluded recursively.
Every native `C.InferenceSession` entry is counted, including recreation through
`set_providers`; counting outer Python constructors would miss this lifecycle.
This behavior is checked against the [pinned ORT source](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.23.2/onnxruntime/python/onnxruntime_inference_collection.py)
and [ONNX 1.19.1 protobuf](https://raw.githubusercontent.com/onnx/onnx/v1.19.1/onnx/onnx-ml.proto).

SKOPS uses fixed tiny `LinearRegression` fits, without custom attributes, and
loads the whole archive through `loads(bytes, trusted=[])`. Default trusted types
remain; unknown types are never automatically approved. Preparation must check
`get_untrusted_types(data=payload) == []` and fail closed otherwise. The selected
[0.15.0 release provenance](https://pypi.org/project/skops/0.15.0/)
binds the inspected [implementation](https://github.com/skops-dev/skops/blob/f426e9e3ebee6685327534d4b922679b44d4225f/skops/io/_persist.py).
Serialization recipes do not promise byte-stable ZIP archives. Actual generated
artifact hashes must be sealed before target loads. Runtime/ABI compatibility
and these roundtrips **have not been executed** in INT-04.

### Fair capture and analysis

The strong adapted collector may hash the actual constructor bytes, because
those bytes are available at the selected APIs. The study must not hide this
capability and call a path collector an inherent SDK/provenance limitation.
The path ablation hashes the real spool before its scheduled mutation and after
the final entry/block and close. The A→B→A control changes that file, lets the
worker read B, then restores A; it is not an in-memory choice mislabeled a race.
Caller manifest content, native message settings, explicit absent registry
fields and full frame equality are fixed in the JSON. Only opaque nonce IDs can
be consistently renamed. Artifact selectors, versions, digests, relationships
and messages cannot be dropped after results to manufacture an equal-frame pair.

Primary coverage is correct identified compliant/violation decisions divided
by **all 22 planned observation load slots**, separately per method/cutoff.
Setup failures, reference-unavailable and unexecuted slots stay in that coverage
denominator. False-compliance/false-violation counts and their reference-assessable
denominators are reported separately, alongside unknown/conflict. Cache-only
slots are not credited as successful loads. Prevention reports blocks, false
blocks, legal completions, residual violations and failures against its planned
opportunities; a block is not compliant deserialization. Unweighted/per-family
descriptive tables are planned. Repeated schedules, branches and cutoffs do not
become independent N or population confidence intervals.

The theory is inherited: partial/lost observations require a semantics that
accounts for compatible executions, as in [Basin et al.](https://arxiv.org/abs/1909.11593).
The conditional sufficiency test groups equal evidence and asks whether a group
contains opposite contract statuses; this is not a new theorem. [in-toto](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias)
is the strong integrity comparator, while [Modelstamp](https://arxiv.org/abs/2609.01781)
is close pre-deserialization prior art. This design does not reproduce Modelstamp
or allege a defect in it. If S/P or the prevention baseline solves all cases,
report that result and reject a new-checker advantage claim. A meaningful
benchmark contribution still requires useful, systematic empirical findings;
the design lock alone proves neither novelty nor publishability.

### Prepare, preflight and next execution boundary

```sh
PYTHONPATH=src python scripts/model_load_validation.py prepare --root . --plan PRIVATE_NEW_PLAN.json
PYTHONPATH=src python scripts/model_load_validation.py preflight --root . --plan PRIVATE_NEW_PLAN.json
```

The plan must be outside Git checkouts in an existing private directory, with
no symlink components or overwrite. Its full design, deterministic slot census,
code hashes and creation commit are replayed. Duplicate JSON fields, non-finite
numbers, extra/tampered fields and type-confused rehashed plans fail closed.
Merge commits descending from the creation commit do not invalidate unchanged
code. Self-hashes provide consistency, not authentication against a malicious
plan producer. Stdout contains aggregate identities/counts only.

### Validation runner and bounded execution

The runner is now implemented separately in `scripts/run_model_load_validation.py`.
The original design module, its CLI and protocol stay unchanged, so a retained
design plan remains replayable. Its old `runner_implemented=false` field is the
historical design snapshot, not the new runner's current status. The new entry
point adds preparation, execution preflight, execute and hash-only verify.
Implementation and synthetic tests alone do not establish real runtime compatibility
or validation findings. The completed controlled validation and its limitations
are described below; these execution instructions are historical reproduction
guidance, not permission to rerun a consumed study.

The adapters import selected SDKs only on explicitly invoked preparation/loading.
ORT intercepts actual `C.InferenceSession` calls, including same-object provider
recreation; automatic constructor fallback is disabled. SKOPS offers the complete
immutable archive and retains `trusted=[]`. Neither adapter accepts arbitrary
downloaded artifacts. The fixed two local fits occur only during authorized
preparation, before the actual generated buffers are sealed. Installed package
versions, Python build and platform are recorded. This inventory is not a
complete transitive ABI attestation; loading compatibility is an execution result.

The lifecycle performs real spool replacement/restoration, file-backed alias
resolution, SQLite delayed/misrouted delivery and root-record expiry. Reference
snapshots are copied before observer routing; missing candidate records are never
filled from them. Native constructor completion and successful outer SDK loading
have separate counters. Closed cache reuse is not a new load. Equal-evidence
grouping retains all frozen fields, exact messages and artifact selectors; only
opaque request/root/URI identity is consistently renamed. Candidate S/T/P share
the same observer facts; signed provenance verifies those facts without acquiring
private reference access. Ephemeral signing keys are never saved.
Path-evidence equality and complete scoped-evidence equality are reported
separately; the scoped comparison retains cutoff, selection and parent binding.

Each fixed slot uses a separate process. The 30-second worker limit covers
loading and S/T/P evaluation; timeout kills and reaps the process. Its raw
lifecycle row is retained before scoring. A missing native census consumes the
entire slot allowance and stops subsequent slots; the worker is never retried.
Known technical failures retain captured entries and stay in the census. Output
capture uses bounded OS pipes rather than unbounded temporary log files. Native
stdout/stderr retain at most 256 KiB each; outer worker stdout retains 4 KiB and
stderr 256 KiB. Overflow or reader failure is terminal, not truncated evidence.
Descriptor restoration and both capture readers are checked on failure paths.
Storage for the next slot and the terminal census is reserved before entry, and private
publication checks the 32 MiB cap before writing. All 48 planned rows remain,
including unexecuted rows. Full independent signed replay is a separate `verify`
operation and performs no deserialization, fit or native loading.

Preparation and native execution are separate scoped actions. Preparation needs
the exact original plan hash and a clean committed checkout; it consumes a new
private directory before making the two fits. The seal binds actual generated
A/B buffers, code, environment, platform and creation commit. Execution requires
that exact seal hash and creates one immutable lease. Existing preparation or
lease cannot be overwritten, resumed or rerun. Code/environment/artifact drift
fails closed; the worker rehashes the actual bytes it read before offering them
to the SDK. Python socket creation/resolution is blocked in workers. This is a
trusted local-process guard, not an OS firewall or malicious-host attestation;
the controlled runtime must also remain offline. There are no provider calls.

After the implementation is committed and merged, use Python 3.12 with the
seven dependency versions in the frozen JSON. A dedicated ignored `.venv` can
avoid altering historical experiment environments. These commands require no
OpenAI credential:

```sh
../working-baseline/.venv/bin/python -m venv .venv
.venv/bin/python -m pip install -e . \
  'onnxruntime==1.23.2' 'onnx==1.19.1' 'skops==0.15.0' \
  'scikit-learn==1.9.0' 'numpy==2.5.1' \
  'in-toto==3.0.0' 'securesystemslib[crypto]==1.5.1'
```

Run this setup once in a new dedicated environment, not an existing historical
experiment environment. Installation success is not native compatibility
evidence. If platform wheels are unavailable, stop rather than substituting
versions or interpreting the unavailable runtime as a scientific result.

```sh
PYTHONPATH=src .venv/bin/python scripts/run_model_load_validation.py prepare \
  --root . --plan ../memory/model-load-validation-plan-v1.json \
  --study-dir ../memory/model-load-validation-v1 \
  --confirm-plan-sha256 7c9a3ae4d2078128c8d3df0e35f640b58b8aefc81d55b8c9cf0a89c4ea24743e

PYTHONPATH=src .venv/bin/python scripts/run_model_load_validation.py preflight \
  --root . --plan ../memory/model-load-validation-plan-v1.json \
  --study-dir ../memory/model-load-validation-v1
```

Preparation makes exactly the fixed two fits but does not load either SDK's
model. Inspect its returned `seal_sha256`, then use that exact value for the
single native execution:

```sh
PYTHONPATH=src .venv/bin/python scripts/run_model_load_validation.py execute \
  --root . --plan ../memory/model-load-validation-plan-v1.json \
  --study-dir ../memory/model-load-validation-v1 \
  --confirm-seal-sha256 EXACT_SHA_FROM_PREPARATION

PYTHONPATH=src .venv/bin/python scripts/run_model_load_validation.py verify \
  --root . --plan ../memory/model-load-validation-plan-v1.json \
  --study-dir ../memory/model-load-validation-v1
```

Stdout is aggregate only; exact buffers, SDK messages and references remain in
the one private study directory outside all Git checkouts. `terminalized` is not
`verification=pass`, scientific admission or a new-checker advantage. The frozen
22-load denominator, separate prevention branch, no-substitution rule and
contribution stop remain in force. No result-based source, threshold or exclusion
changes are permitted; an affected exposed unit becomes development and further
design is forward. This bounded loader-format transfer does not require a new
method frontier, and cannot demonstrate one by itself.

### Completed controlled validation and evidence closeout

The fixed two-loader census completed all 48 slots: 44 planned-load and four
cache-only slots. Actual native entries totaled 62 (24 auxiliary and 38 target),
within the frozen 72-entry reservation. Independent hash-only replay passed
without repeating native loading. S, T and actual signed P returned the same
verdict for every observation slot at both cutoffs. On the 22 planned observation
loads, correct identification was 16 before delayed delivery and 18 afterwards.
There were no observed false compliance or false violation commitments. All four
remaining unknown cases were reference violations: two missing offered-buffer
witnesses after wrong-mailbox delivery and two missing root selections after
expiry. Thus compliant cases were identified in 12/12 and violations in 6/10;
81.8% identification is not complete violation detection.

Path-only evidence had two opposite-status groups covering ten slots. Exact full
scoped evidence had zero opposite-status groups. The path ablation must not be
presented as unavoidable ambiguity under the complete scoped input. Prevention
is a separate endpoint: ten blocked proposals, eight opportunities blocked without
a target load, 14 completed compliant loads and two cache-only opportunities;
zero observed false blocks or residual violations. This does not establish host,
prediction or serving attestation. Both loader strata share one authored scheduler,
not two independent deployments. The supported disposition remains bounded
transfer with no new-checker advantage.

`model_load_evidence_development.py closeout` verifies the original authority and
reference, then analyzes only actually retained observations. It never restores
missing evidence from the private producer ledger. Removing current selection or
load evidence reduced after-cutoff identification to 2/22; removing closure gave
6/22. Removing parent selection did not change 18/22 on this particular census.
A separate finite witness establishes why that last observation is not a general
permission to drop roots: the same child selection/load/closure can be compliant
under root A and violating under root B. Without the authoritative root both
histories share the child-only view.

The static sufficient selector retains every current-scope record and all root
selection variants referenced by any current selection. It preserves conflicts,
distinct load occurrences and no-new-load eligibility. It preserved S/T decisions
on all 44 before/after load views while removing 44 irrelevant parent load/closure
records. Enumerating the 32 subsets of five evidence groups is exposed descriptive
analysis, not byte-optimal acquisition or an online-safe adaptive policy.

### Collector retention development

The companion `collector` command exercises actual SQLite transactions, routing,
deduplication, delayed delivery, root expiry and garbage collection using inert
producer buffers. It executes no model fit, native model load or provider request.
Ten authored episodes include healthy/wrong/extra loads, healthy inheritance,
reselection, cache reuse, duplicate delivery and truly foreign-scope evidence.
The upstream producer hashes 16 buffers (557,056 bytes) once into a common tape;
each configuration sees the same 50 receipt deliveries (13,238 serialized bytes).
The 16 counted hashes are producer-boundary witness hashes; common domain
initialization is not included in that counter or its timing.
Policies receive trusted records and caller lifecycle, not the reference outcome,
fault label or future schedule. Raw buffer archives and native logs are not added
to full retention merely to inflate its cost.

A two-by-two comparison isolates mailbox versus trusted-scope retrieval and
attempt expiry versus request-lifetime root retention. Correct identification of
nine load episodes was 6, 7, 7 and 8, respectively, with zero false commitments;
all configurations also correctly classified the one cache-only episode. The
truly foreign-scope witness remained inadmissible. Wrong mailbox with correct
payload scope can be retrieved only because the same store still contains it;
that is a delivery capability, not stronger inference or restored absent capture.
These are newly constructed collector episodes, not a repaired 20/22 or 22/22
result on the immutable validation.

At matched scope retrieval, root lifetime and audit horizon two, static sufficient
and full retention preserved the same immediate and retrospective decisions.
Static receipt writes were 36 rows / 9,716 payload bytes versus 49 / 12,963 for full
retention (25.05% fewer logical payload bytes). Payload peak was 3,384 versus 4,878
bytes. Across 118 common event steps, payload occupancy was 275,456 versus 366,269
byte-steps; serialized bookkeeping contributed another 30,077 byte-steps to both.
These are logical payload/state measures, not Python allocator or physical write
volume. Database and rollback-journal sizes are reported separately. SQLite
DELETE can free rows without shrinking the main database file, as documented in
[SQLite auto-vacuum](https://www.sqlite.org/pragma.html#pragma_auto_vacuum).

Audit horizons zero, two and eight provided 0/27, 19/27 and 27/27 of the same
requested retrospective queries at ages zero/one/two drained requests. Every
available query preserved its earlier decision. A shorter horizon is therefore
a different service contract, not free cost reduction at equal audit coverage.
Only a trusted request-drained barrier closes future delivery and descendants;
attempt closure alone does not allow deletion. Conflicting duplicate variants
remain separate. An undelayed redelivery makes an existing delayed receipt visible;
a correct-mailbox redelivery is not discarded because an identical payload was
previously routed elsewhere. Request-owned foreign records are also reclaimed
when their owner's horizon ends, without becoming target witnesses.

Three rotated configuration runs completed 300 episode-runs. Repeat counts are
engineering reproducibility, not independent experimental N. Local operation
timing includes resource instrumentation and supports no production latency or
throughput claim. The pilot permits one declared query attempt per request,
bounded input and no crashes/concurrent descendants. Technical configuration
failures remain in an incomplete report instead of being excluded from the census.
The reported executed episode count includes only fully completed configuration
runs; partial work in a failed run is not an observed completed episode.
No adaptive optimizer is promoted: a request/root-aware sufficient baseline is
already adequate on these controls, and no residual adaptive advantage has been
demonstrated. Signing costs from P are not included in collector timing.

### Prior methods and scope

[Hindsight (NSDI 2023)](https://www.usenix.org/system/files/nsdi23-zhang-lei.pdf)
already separates local capture from contingent retrospective retrieval and
retention within a finite buffer horizon. It cannot recover history outside that
horizon. [Pivot Tracing (SOSP 2015)](https://jonathanmace.github.io/papers/mace2015pivot.pdf)
established dynamic instrumentation and causal joins. [Runtime Verification over
Out-of-order Streams](https://people.inf.ethz.ch/basin/pubs/tocl19.pdf) supplies
earlier partial-observation semantics and state-relevance principles, not a
general cheap-completeness guarantee. The present contribution candidate is a
bounded contract-specific evidence/cost characterization; adaptive tracing,
three-valued verdicts and causal indexing are not new inventions here.

```sh
PYTHONPATH=src python scripts/model_load_evidence_development.py closeout \
  --root . --plan PRIVATE_ORIGINAL_PLAN.json --study-dir PRIVATE_COMPLETED_STUDY \
  --output PRIVATE_NEW_CLOSEOUT.json

PYTHONPATH=src python scripts/model_load_evidence_development.py collector \
  --root . --output PRIVATE_NEW_COLLECTOR_REPORT.json --repeats 3
```

## Native-load cost and multi-attempt retention development — 2026-10-05

`model_load_runtime_development.py` extends exposed development, not the consumed
validation. It constructs fresh trusted local artifacts using the unchanged
ORT/SKOPS adapter factory. No historical artifact is deserialized, no protected
study is rerun, and no provider request occurs. The fixed census is two SDKs ×
four observer arms × three fresh worker processes × three loads = **72 native
loads in 24 processes**, plus two local preparation fits. Every planned load and
failed worker remains in the report. An unresponsive worker has an **unknown**
entry count, not an assumed zero. The final source-bound report completed 72/72
loads and passed a read-only verifier that does not enter either loader.

### Timing boundaries and findings

The four arms are observer-work-disabled, hash-only, full receipt retention, and
static sufficient retention. All use the same adapter shell and hooks; the
disabled arm is **not an untouched native SDK baseline**. SDK imports, artifact
preparation, database creation/registration, common control-hash verification
and resource sampling are outside the measured load endpoint and are separately
reported where applicable. A first load after import is not OS-cold storage.
The two later samples deserialize fresh objects, not cache hits. Configuration
order rotates across three process blocks; this is not a fully balanced Latin
square or a population performance study.

`sdk_load_ns` surrounds the public adapter load and includes observer callbacks
and SDK initialization. `native_entry_ns` surrounds its nested native entry.
`object_release_ns` is separate. `load_and_observer_ns` includes the SDK load,
object release, closure receipt, query and checker; `read_to_decision_ns` adds the
artifact read. Hash, capture/encoding, SQLite write/commit, query and checker
phases are retained individually. Nested phases and independently calculated
medians **must not be added** to reconstruct a total. Write timing includes
deduplication and serialization, not a pure fsync microbenchmark. These endpoints
do not measure signing costs for P, RSS, physical write volume or native throughput.

Final-run medians of `load_and_observer_ns`, in milliseconds:

| SDK / sample condition | Disabled | Hash-only | Full | Static sufficient |
| --- | ---: | ---: | ---: | ---: |
| ORT / first use after import | 1.512 | 1.602 | 2.881 | 3.016 |
| ORT / later fresh object | 0.165 | 0.176 | 1.134 | 1.259 |
| SKOPS / first use after import | 0.745 | 0.700 | 2.295 | 2.028 |
| SKOPS / later fresh object | 0.219 | 0.239 | 1.248 | 1.309 |

The two constructed artifact sizes are only 148 and 5,366 bytes. Later-sample
hash medians are approximately 1–4 microseconds; durable receipt-write medians
are approximately 0.82–0.90 milliseconds in this SQLite DELETE/FULL setting.
Thus receipt persistence dominates the small-model observer cost **on this
machine and configuration**, not necessarily for large artifacts or a long-lived
collector. Full and static receive the same three receipts in this isolated root
query; their timing differences do not establish a retention optimization.
The negative SKOPS first-use hash-only delta is retained as observed noise, not
a claim that hashing accelerates loading. Three process replicates and their
within-process loads are not independent deployment samples. Full ranges and
all samples remain in the private aggregate; no confidence interval, p95,
steady-state convergence or production speedup is claimed.

[Kalibera and Jones (ISMM 2013)](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf)
distinguish variation at iteration and execution levels, motivating fresh-worker
replicates and limited interpretation of small differences here. This pilot does
not implement their precision-driven statistical procedure.

### Query-service-dependent retention and concurrent safety

`AttemptReceiptStore` declares the complete producer-attempt census and query
service **before capture**. Two primary requests each have a root and two child
attempts. Two producer threads generate a coordinated 26-event tape containing
20 deliveries, parent closure before child completion, delayed parent conflict,
child load, duplicate delivery and true foreign scope. Two auxiliary requests
advance audit age and add six receipts. The twelve comparisons are full/static
× children-only/root-and-children queries × horizons 0/2/8. Collector replay is
serialized to match event order: this is **not a concurrent-native throughput
benchmark**. Separate tests execute actual concurrent SQLite submit/snapshot
operations and a snapshot-copy versus retirement race.

All six matched full/static pairs preserve complete decisions, admitted evidence
frames, and retrospective availability. At horizon two:

| Primary query service | Full payload written | Static payload written | Reduction | Payload byte-steps, full / static |
| --- | ---: | ---: | ---: | ---: |
| Children only | 6,569 B | 5,341 B | 18.69% | 230,817 / 173,186 |
| Root and children | 6,569 B | 6,295 B | 4.17% | 273,762 / 261,432 |

The matched sampling counts are 65 and 75 respectively; serialized bookkeeping
adds 50,787 and 64,545 byte-steps to **both** policies in the corresponding service.
Physical main-database peaks are identical at 28,672 bytes, so logical savings
do not imply a smaller disk file. When root queries are required, all root
load/closure receipts must remain; the remaining 274-byte difference removes
only a foreign-scope receipt. The older single-query pilot's 25.05% is therefore
not a transferable guarantee. Sufficiency depends on the declared query service,
not merely on current child verdicts.

For the same requested audit ages zero/one/two, horizons 0/2/8 serve 0/12, 8/12,
12/12 child-only queries or 0/18, 12/18, 18/18 root-expanded queries. Every available
query preserves its earlier admitted frame and decision. Reduced retention
changes audit service, not free efficiency at equal coverage. A copied early-root
deletion control turns a previously compliant child into unknown; it does not
mutate the real collector or repair immutable validation results.

One lock covers lifecycle admission, SQLite writes, fetch-all snapshot copies,
release and retirement; no live cursor escapes to the checker. This follows the
relevant [SQLite isolation constraint](https://www.sqlite.org/isolation.html):
modifying a shared connection while stepping a query is not a safe snapshot
contract. Attempt settlement alone never permits retirement. Request draining
requires all declared attempts settled and no pending delivery, including
filtered or already-visible duplicate deliveries. The caller must still provide
a truthful no-future-descendant/no-future-delivery barrier; this is not a
distributed watermark or crash-recovery protocol. Conflict variants and distinct
occurrences survive deduplication. Expired audits fail explicitly rather than
returning a cached guess. The implementation is bounded to 64 requests, 128
attempts, 4,096 deliveries and 8,192 bytes per receipt; retained tombstones are
counted, not an unbounded production-memory guarantee.

### Disposition and reproducibility

**Development slice complete; static sufficient/S remains the default.** This
adds measured native endpoints and multi-attempt safety evidence, not a new
checker, adaptive optimizer, production frontier or automatic Paper B/U unlock.
The immutable validation remains S = T = P, 18/22 after delivery with four unknown
violations. Preserve that result and its limitations.

The aggregate binds all directly used code and contains samples/resources but
no raw model bytes, credentials or local paths. Verification rebuilds fixed
censuses, endpoint keys, sample/worker failure consistency, summary and matched
retention queries; it does not independently remeasure wall-clock timings or
authenticate a maliciously rewritten report. Earlier engineering runs remain
private immutable records: the final run follows endpoint/verifier hardening,
with the same fixed census and no outcome-based arm or replicate selection.
Three worker contexts covered twelve logical research/prototype/assurance roles;
the lead integrated and tested the final code, not twelve independent reviewers.

```sh
PYTHONPATH=src python scripts/model_load_runtime_development.py run \
  --root . --report PRIVATE_NEW_RUNTIME_DEVELOPMENT_REPORT.json

PYTHONPATH=src python scripts/model_load_runtime_development.py verify \
  --root . --report PRIVATE_COMPLETED_RUNTIME_DEVELOPMENT_REPORT.json
```

Run requires the pinned local ORT/SKOPS development environment. Verification
only reads the aggregate and code. Both refuse the repository and historical
validation store as report destinations; run refuses replacing an existing report.

Outputs are aggregate, immutable, capped at four MiB and outside the repository.
Closeout output must also be outside the original study. No new execution seal,
protected attempt or API credential is needed for these read-only/development
commands. An original native validation execution must not be repeated.

## External application development: MLServer

This is a bounded transfer into an externally authored application, not another
author-written SDK scheduler or a deployed-incident benchmark. The application is
[MLServer 1.7.1 at commit 1d1f3ee42f96744d809aca941ed2925347d198e9](https://github.com/SeldonIO/MLServer/tree/1d1f3ee42f96744d809aca941ed2925347d198e9),
Apache-2.0. Six installed source files are checked byte-for-byte against that
commit: registry, repository, repository handlers, DataPlane, REST app and sklearn
runtime. The release-wheel digest is public provenance metadata, not a claim that
this runner independently verified the downloaded wheel. The source was selected
for its actual load/reload/resident-object/cache lifecycle, not for a positive
checker gap.

The runner invokes real ASGI REST handlers, with `parallel_workers=0` and native
response caching enabled. It follows repository load through the application
registry to `SKLearnModel.load` and Joblib, and inference through DataPlane.
It does not replace these handlers with a fixture. There is no bound socket
server, gRPC, Kafka, tracing collector or multiprocess serving deployment.
Outbound socket connections are denied and counted in each worker.

### Native policy, research overlay and capture boundary

Native MLServer uses mutable model name/version/URI settings. A successful
explicit reload replaces the resident object; changing a file alone does not
reload it, and a failed reload can retain the prior object. Native inference can
reuse a cached response. The runner verifies these relations, including original
prediction-computation counts: equal responses alone do not prove a cache hit.
See the pinned [registry](https://raw.githubusercontent.com/SeldonIO/MLServer/1d1f3ee42f96744d809aca941ed2925347d198e9/mlserver/registry.py)
and [DataPlane](https://raw.githubusercontent.com/SeldonIO/MLServer/1d1f3ee42f96744d809aca941ed2925347d198e9/mlserver/handlers/dataplane.py).

The **operator byte-pin is a declared research overlay**, not an authorization
policy promised by MLServer. At a successful explicit load, a snapshot of the
opened descriptor must match that pin. Cache/resident inference without a new
load is `no_new_load`, not newly established whole-serving compliance.

Only fresh trusted, bounded, uncompressed, single-file artifacts are admitted;
the opened inode must remain immutable from first open through reconstruction.
The observer snapshots the descriptor without replacing the original unpickler's
stream or arguments. It rejects unsupported descriptors, offsets and mmap use.
Normal native/captured lifecycles agree on all observed responses, object
relations and counts. Both have the common diagnostic profiler, so this checks
observed lifecycle transparency, **not zero observer overhead**.

The immutable-inode condition is substantive. A regression counterexample
overwrites the same inode after Joblib has buffered bytes: a descriptor snapshot
can then see B while the original stream reconstructs A. Stat checks around the
snapshot cannot certify absence of an earlier completed write. Consequently
this observer does **not** prove every consumed byte or support arbitrary
concurrent in-place writers. The admitted critical control uses atomic pathname
replacement while the already-open inode remains unchanged.

The private raw sink and reference predicate are separate from receipt/checker
decisions, but share trusted instrumentation and the local host. S and adapted
in-toto receive the same pin, descriptor digest and occurrence census. Real
in-toto signatures/rules are verified during execution; ephemeral signing files
are discarded. Replay checks report integrity and represented rule-result
consistency, not independent re-verification of retained signatures or hostile
host attestation. Modelstamp is not reproduced by this experiment.

### Complete census and bounded result

Two fresh local fits prepare A/B before the comparative outcomes. The fixed four
arms are native, captured, path-legal and path-replaced. Each normal arm has twelve
REST operations; each critical-pair arm has seven. The completed development
aggregate has:

| Endpoint | Observed result |
| --- | --- |
| Application/source clusters | 1; dependent authored controls |
| REST operations | 38/38; six HTTP failures retained |
| Actual Joblib loads / reconstruction entries | 6 / 6; declared upper allowance 8 |
| Snapshot bytes | 2,268; not peak memory or total IO |
| Matched instrumented operation decisions | S = P = reference on 26/26 |
| Successful explicit loads within that comparison | 4: three compliant, one overlay violation |
| Other operations | 22 `no_new_load`; includes one failed explicit reload |
| Actual in-toto verifier calls | 4 |
| Provider calls / socket connection attempts | 0 / 0 |

The missing-file reload returns 422 **before** entering Joblib. It is retained
as an application failure and zero native loads, not counted as an unpickler
failure or silently dropped. Four post-unload inference failures return 404.
Incomplete worker execution retains the planned 38/26 denominators and reports
an unknown partial native census rather than claiming zero calls.

The legal and replaced controls share the declared path/metadata frame: settings,
native metadata, readiness/load status and pre/post pathname hashes. They have
different opened-descriptor snapshots. Their predictions also differ (`[0,0]`
versus `[1,1]`) and are excluded from that explicit ablation. Therefore the finding
is **not** indistinguishability of all application evidence or a native MLServer
policy violation. S and strong adapted provenance close this ablation equally.

**Disposition: bounded application capture transfer, no new checker advantage.**
This extends the earlier controlled loader evidence into real application
handlers and exposes a capture-assumption counterexample. It does not establish
natural-incident prevalence, an independent deployment benchmark, a new theorem,
an algorithmic advantage, a production cost frontier or a second-paper result.
The byte/count endpoints above are not wall-clock overhead measurements; earlier
ORT/SKOPS cost estimates are not transferred to MLServer.

### Reproduction and verification

The optional `application` extra pins MLServer, sklearn runtime, Joblib, httpx,
in-toto and securesystemslib separately from the historical MLflow environment.
The public CLI accepts only `run` and `verify`; workers are internal and receive
the artifacts freshly generated by the parent, not caller-supplied pickle files.
The interface is not a security boundary against someone controlling Python or
the host. Run refuses an existing report or a repository destination. Verify
does not deserialize snapshots or modify the report; it binds source, contract,
versions, code, occurrences and complete decisions.

```sh
PYTHONPATH=src python scripts/model_load_application.py run \
  --root . --report PRIVATE_NEW_APPLICATION_REPORT.json

PYTHONPATH=src python scripts/model_load_application.py verify \
  --root . --report PRIVATE_COMPLETED_APPLICATION_REPORT.json
```

The dedicated Linux application CI job requires exact runtime/source preflight
and subprocess-aware coverage; the existing Linux/Windows gates remain intact.
Local application tests: **52 passed, 92.31% coverage** across the three modules,
including actual native-worker lines; **68 contract tests** passed. These local
checks do not assert remote CI or native Windows qualification. Engineering
checkpoints remain private; the final aggregate follows CLI/optional-runtime
hardening with the same fixed source, arms and census, not favorable outcome
selection. Historical
validation, model artifacts and results are unchanged.

## Read-only research acceptance and synthetic handoff checks

The acceptance command pins a historical document to a **caller-supplied accepted
canonical identity**. A document that merely recomputes its own hash is not an
accepted replacement. It reads bounded JSON only and rejects duplicate members,
nonfinite numbers, symlink inputs and repository-local research receipts. The
output contains allowlisted aggregate endpoints, opaque hashes and the original
scientific disposition, not retained rows, buffers, SDK messages, local paths or
private keys. No new receipt, model fit, native load or provider request is made.

Two operations are deliberately distinct:

- Default replay invokes the existing artifact-specific verifier. Validation
  requires the original plan and sealed study; closeout additionally binds the
  accepted parent result and independently rebuilds its descriptive ablations.
  Missing dependencies, changed historical source bindings or changed endpoints
  fail closed. Replay does not silently degrade to identity-only inspection.
- `--identity-only` checks the accepted document identity and typed summary. Its
  status is `pinned_summary_only_not_replayed`, never a historical replay pass.
  This can inspect a prior artifact without its optional SDK environment, but
  does not reproduce its original runtime or source checks.

Each rate keeps its own unit and denominator. Observability coverage uses
load-eligible attempts, while its verdict histogram includes cache episodes;
both denominators are explicit. Validation slots, observation-load comparisons,
native entries and prevention opportunities are separate. Application HTTP
outcomes, definite load decisions and `no_new_load` remain separate; native
totals are labelled **known** and retain incomplete-census flags. Unknown
retention comparisons and failed samples remain visible. No cross-study pooling
or inference of an independent deployment count is performed.

Validation and historical provenance replay perform fresh local in-toto rule
checks with ephemeral signatures. This is not independent verification of
retained historical signatures. Application replay instead reconstructs the
represented rule-result consistency. These boundaries follow the declared
layout and artifact-flow scope of [in-toto, §3.2–4](https://www.usenix.org/system/files/sec19-torres-arias.pdf);
they do not authenticate a hostile capture host or observe absent buffers.

```sh
PYTHONPATH=src python scripts/accept_model_load_research.py receipt \
  --root . --kind validation --receipt PRIVATE_RESULTS.json \
  --expected-sha256 ACCEPTED_CANONICAL_SHA256 \
  --plan PRIVATE_ORIGINAL_PLAN.json --study PRIVATE_ORIGINAL_STUDY
```

Use each artifact's original compatible environment, not an arbitrary current
interpreter. The supported kinds are observability, provenance, validation,
closeout, runtime and application. The accepted identity must come from the
prior research checkpoint, not from a new candidate file. This command does
not install dependencies, repair old source bindings, rerun protected outcomes
or admit a scientific mechanism.

### Selected canonical product-view acceptance

The repository fixture `tests/fixtures/research_acceptance/synthetic_product_view.json`
is the existing public synthetic project-audit handoff, not a research result.
Its family denominator stays `null/not_estimated`; contexts, outputs and claims
are distinct dimensions. Selected checks reject causal promotion, non-mock
external execution, hidden/evaluator fields, unauthorized or dangling citations,
foreign snapshot/turn references and invalid graph endpoint types. Noncausal
graph relations and relative evidence references are checked against the same
visible facts, consistent with the typed provenance relationships in
[W3C PROV-DM, §5.2 and §7](https://www.w3.org/TR/prov-dm/).

```sh
PYTHONPATH=src python scripts/accept_model_load_research.py synthetic-view \
  --reference tests/fixtures/research_acceptance/synthetic_product_view.json \
  --candidate CANDIDATE_CANONICAL_PRODUCT_VIEW.json
```

The caller supplies the trusted reference. A candidate must match its full
canonical JSON, including disposition, missing evidence and scope. Matching
only hashes of evidence or aggregate counts is insufficient: synthetic tests
keep those fingerprints unchanged while altering selection policy, citation
authority or causal wording. Missing closure stays unknown; closed cache reuse
stays `no_new_load`; HTTP failure is not automatically an artifact violation.
The command rejects unsupported changes rather than repairing them.

This is a bounded synthetic compatibility check, **not** a general DTO/privacy
validator, a ResultEnvelope implementation, a scientific-report ingest adapter,
or an executed ProductService/table/graph/export integration. Screenshots and
arbitrary export formats are not canonical ProductViews. Real consumers must
later supply their canonical projections through the existing product contract;
successful fixture tests cannot mark those consumers accepted.

## Persistent-collector serving cost and delayed audit

`scripts/model_load_serving_study.py` is an opt-in development experiment,
not another execution of the protected loader-format validation. It runs real
MLServer ASGI REST calls through the pinned application source, in fresh serial
processes. No external socket, provider request or downloaded model is needed.
Optional serving dependencies must already pass the application runtime/source
checks; the command does not install or substitute them.

The fixed design in `configs/evaluation/model_load_serving_protocol.json` uses
three genuinely different fresh decision-tree sizes (depths 2, 6 and 10), not
padding. Each has complementary-label A/B controls. The artifact size is bounded
by the existing 256 KiB descriptor-read capability; these are not large production
models. Each process runs twelve explicit load/reload slots, with either zero
or sixteen real inferences after each slot. Request IDs are distinct so resident
inferences are not silently replaced by the response-cache path. Valid replacement,
contract-violating replacement and failed reload are explicit separate controls.

Four arms use the same service stream:

- Native: no capture hook or profiler; the REST duration is the application-cost
  anchor. Its successful reloads are reference-checked, not instrumented loader
  entries. A native request cannot win a sufficient-evidence audit frontier.
- Hash-only: one digest per actual reconstruction, no durable lineage or audit
  service. This is an ablation, not a sufficient alternative.
- Static sufficient: hash at actual load, reuse resident generation identity,
  retain scoped frame, dependency and closure. It does not serialize discarded
  application detail. It uses the existing receipt checker.
- Full: the same scoped frame, dependency, checker and indexed query, plus
  naturally observed request/response/event detail. It does not deserialize unused
  detail on the query path. Additional data are not treated as stronger proof.

The two sufficient arms share one persistent SQLite collector per process,
WAL with `synchronous=FULL`, the same schema/indexes and the same commit cadence:
one completed operation per append transaction and one retirement per load slot.
The commit is after the native ASGI response but before the **observer wrapper**
completes; this is not a claim that the native HTTP response itself waits for
durable evidence. SQLite documents the WAL sync distinction between FULL and
NORMAL in [WAL, §2.3](https://sqlite.org/wal.html) and
[the synchronous pragma](https://sqlite.org/pragma.html#pragma_synchronous).
No asymmetric batching is used to manufacture a baseline disadvantage.

Audit ages and retention horizons are 0, 2 and 8 **completed load slots**, inclusive;
measured elapsed time is separate. Queries occur before that slot's retirement.
An active resident parent and the ancestors of retained inference rows remain
pinned even after nominal expiry. A failed reload advances the schedule but does
not replace the resident generation. Later queries beyond the twelve-slot end
are right-censored, not generated by fake ticks or counted as failed audits.
Each horizon must report its full eligible audit denominator. Cheap short
retention is not a matched-service saving when it loses a required later audit.

### Reference, capture and measurement boundaries

Capture hashes the bytes of the actual original Joblib descriptor and preserves
its position; it does not give the reference recorder a hidden raw-buffer copy.
The reference uses the owned immutable delivered artifact and the pinned loader
path, post-request fresh-object/generation checks, a semantic tree-field
fingerprint, and a fixed prediction probe. Structured-array padding is not part
of that semantic fingerprint. Exact-byte compliance remains a different claim
from predictor equivalence. The reference is not used to repair checker decisions.

These assumptions require a stable opened file, trusted local collector and serial
dispatch. Generation bookkeeping and post-request resident checks do not establish
hostile-host authentication or concurrent inference-boundary attestation. The hook
rejects nested/concurrent capture and unsupported mmap; separate worker processes
are repetitions, not a throughput benchmark.

Timing keeps native REST, observer-wrapper end-to-end, descriptor read, digest,
SQLite append, query, checker, retirement and close separate. The fixed
service sum includes collector initialization and close exactly once. Fixture
setup, reference checks and statistics sampling are reported separately, outside
that endpoint; they can still perturb caches. First-use loads stay in the main
endpoint. Any warm/cold decomposition after observation is descriptive sensitivity,
not replacement of the fixed endpoint. Nine successful loads per process make a
nearest-rank p95 equal its maximum; it is not a production tail-latency estimate.

Resource output distinguishes frame/detail logical bytes, key/index bookkeeping,
sampled database/WAL/SHM peaks and whole-process peak RSS. WAL file size is not
device I/O volume, and the research recorder/model/runtime included in RSS cannot
be attributed solely to the collector. Logical field-component peaks must not be
confused with physical database allocation or an independently measured memory
saving. Performance comparisons use three process repetitions on one machine;
the hierarchy of repeated measurements is explicit, following the concerns in
[Kalibera and Jones, §3–5](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf).

### Execution and immutable replay

```sh
PYTHONPATH=src python scripts/model_load_serving_study.py preflight --root .
PYTHONPATH=src python scripts/model_load_serving_study.py run \
  --root . --study-dir /path/to/private/fresh-study
PYTHONPATH=src python scripts/model_load_serving_study.py verify \
  --root . --study-dir /path/to/private/completed-study
```

Run requires a new directory outside the repository and seals its design/code
before fitting or timing. Worker failures and timeouts remain in the planned
census. Models are created locally; never supply untrusted Joblib/pickle files.
The verifier performs no fit, native load or provider call. It binds the original
code/config, models, per-worker records and aggregate, independently replays
retention with a set/ancestor model, and rebuilds the descriptive service frontier.
Original-version source is necessary for exact replay after a code correction;
do not rewrite an old result to make it pass current source bindings.

Only complete sufficient arms satisfying every planned audit age with no false
decision enter the matched-service frontier. A measured minimum is not a
statistical superiority or new method admission. Do not introduce a selector or
buffer policy merely because it sounds novel: retroactive triggered buffering
already has prior art such as [Hindsight, §3](https://www.usenix.org/system/files/nsdi23-zhang-lei.pdf).
Retain a valid conventional baseline when no residual method advantage is shown.
Raw research records, models, timing results and plots remain outside the public
repository. Focused synthetic tests are included in both the application and
evaluation test profiles; passing them is distinct from executing the workload.

## Completed-revision audit archive development screen

`audit_bundle_archive.py` adds an opt-in, single-tier service over completed
serial serving frames. It preserves each full closed operation revision and its
resident-generation dependency using lossless compression and shared atoms.
Scope/target/parent seals prevent compaction from silently dropping a conflicting
observation. The trusted native census supplies each scope exactly once; this is
not a global duplicate ledger or hostile-host attestation.

The common admission layer pins the current resident and accepted post-completion
audit leases through an inclusive load-slot expiry. Optional whole-bundle
selection cannot weaken these pins. A lease may be refused before acceptance;
future mandatory growth or failed persistence can still cause a reported service
failure after acceptance. In-flight reservation/backpressure, arbitrary future
queries, late revisions and crash recovery are not implemented guarantees.

The budget charges the complete canonical persisted archive BLOB, including
compressed/base64 frames, manifests, dependency/lease metadata and counters.
SQLite/WAL/SHM peaks are separate diagnostics, not that logical cap or device I/O.
Creation and access use a monotone event clock; TTL and lease expiry use load-slot
age. Internal safety checks do not create artificial popularity observations.
Audit reads resolve durable retained bytes only. No policy sees the source truth,
future query targets or a mechanism for refetching discarded evidence.

Seven arms share this representation and admission layer: compressed static
newest-admission retention, TTL age two, LRU, LFU, standalone size/cost priorities,
incremental union density and bounded exchange. The latter starts with density
and permits at most four improving 1-out/1-or-2-in rounds over twelve-entry
shortlists. Its declared surrogate is `sum(1 + past hits)`, not future audit
coverage. No generic submodular, approximation or competitive guarantee follows.

[Dependency-Aware Online Caching, §3.1](https://arxiv.org/html/2401.17146) is close
prior art but includes refetching from a slow tier. Its guarantees do not transfer
to irreversible evidence loss. Cost/size priorities are also established in
[GreedyDual-Size](https://www.usenix.org/conference/usits-97/cost-aware-www-proxy-caching-algorithms).
Dependency bundles or density scoring alone are not claimed new algorithms.

```sh
PYTHONPATH=src python scripts/audit_bundle_screen.py run \
  --root . --study-dir /path/to/private/fresh-development
PYTHONPATH=src python scripts/audit_bundle_screen.py verify \
  --root . --study-dir /path/to/private/completed-development
```

Run requires the same pinned optional MLServer environment as the serving study.
It seals a development design before capture/replay and writes only to a fresh
directory outside the repository. Two fanouts, four logical budgets and two
authored schedules yield 112 paired replay configurations on one source.
Same-slot audits occur after the whole operation batch; delayed audits include
the entire tail. Refusals, aborted configurations and unprocessed offered queries
remain in the denominator. Replay metrics are not policy-interposed serving
latency or independent deployment measurements.

`--capture-dir /path/to/existing/development` optionally makes a separately sealed
technical revision using the existing native captures. It records source hashes
and does no fitting or runtime load; it is not fresh replication. Preserve earlier
directories and use their original code for exact historical source binding.

The verifier checks the native operation/reference census, complete configuration
census, query/failure prefixes, hard-lease decisions, raw accounting and aggregate.
It independently reconstructs snapshot union byte costs and enumerates finite
current-pool surrogate optima. This uses a shared crypto primitive but not the
online selector's cost/enumeration logic. It does not certify all selector history,
latency superiority or future optimality. Raw models, captures, databases and
results remain private; public tests are synthetic and require no provider calls.

## Native application transfer and independently drainable audit obligations

`scripts/application_audit_development.py` runs two opt-in local applications:
BentoML 1.4.39's real `Service.to_asgi()` with its model store and sklearn loader,
and MLflow 3.9.0's native scoring application with a local SQL model registry.
MLflow is a new serving workflow here, not a previously unseen framework. These
are externally authored application implementations, not sampled deployments or
naturally occurring incidents. Only newly fitted, trusted local estimators are
loaded; no external pickle or historical artifact is accepted.

Source selection precedes the development comparison. The inclusion criteria
are an actual native application handler, inspectable selection/object caching,
a local executable startup/use/failure boundary, and supported owned artifacts.
A positive checker gap is not a source inclusion criterion. The fixed census
contains initial startup, lawful version change, wrong B artifact, failed B
startup, restoration, reverse substitution, and final restoration. Additional
controls keep the old application across a registry/latest change, replace a
path after startup, and try a failed new application while retaining the prior
application. Keeping the previous app is an operator action; neither framework
is claimed to provide an automatic rollback or live reload endpoint.
BentoML new generations use a fresh Service/reference definition. Reinitializing
the same already resolved descriptor in one process is not tested as a fresh
latest lookup; native descriptor caching can outlive an individual app instance.

Version/alias selection and cached-model behavior are native contracts. The
released-byte digest is an explicit operator policy overlay. File corruption
under an unchanged tag/version is a controlled stressor, not proof of a native
framework bug. Native readiness, a current pathname hash, or a package version
alone must not be labeled a cryptographic commitment to the resident object.
Separate raw descriptor snapshots supply the load reference. Fingerprints and
actual prediction responses check the post-request object sink; they do not
enter the tested receipt checker. This serial source-local association is not
distributed request attestation or an end-to-end inference integrity proof.

The sufficient baseline uses the same admitted selection/closure/descriptor
evidence as signed in-toto provenance. Full package signature/rule verification
is executed during the census, not replaced by Python digest equality. Signing
trusted local capture does not attest a hostile host. Read-only replay checks
raw reference consistency and recorded decisions, not fresh signature execution.

The admission experiment compares ordinary `drain_static`, `drain_lru`,
`reserve_static`, and `reserve_lru` at 2, 4 and 8 KiB, two serial passes per
source. Each arm invokes native startup and HTTP prediction/health handlers;
refused operations are skipped **before** native dispatch. All arms use one
durable collector, the same compressed closed frames, exact known dependency
deduplication, fixed-width ledger fields, and protected accepted leases.
These passes are nested runtime instances inside two worker processes, not
independent process replications or concurrent throughput measurements.
Hard-query scheduling is independent of later dispatch. Optional previous-load
queries are conditional on a dispatched current operation and an observed prior
scope; their counts are not a common fixed offered-query utility benchmark for
all policies. Every offered operation and before/after-completion refusal is
reported separately. Prediction and health HTTP statuses are checked in replay.

Two promises must be distinguished:

- Drain arms promise only already accepted, immutable completed-revision audits.
  New work may run and its new audit lease may be refused at completion.
- Reserve arms additionally reserve a bounded new load frame before dispatch.
  Conservative unused reservation can reduce admission. This stronger promise
  must not be compared as if it were the same capability as post-completion
  admission, or credited as a new algorithm by itself.

For supported serial load frames, canonical frame bytes are capped at 1,024,
scope IDs at 64 ASCII characters, and the incremental evidence/manifest reserve
at 2,048 bytes. Completion checks the realized bound. Overrun, unknown evidence
and native failure remain explicit; they do not silently overcommit or erase an
old lease. Previously accepted audits are serviced independently even when new
ingress is refused. Deadlines are inclusive logical events, not wall-clock SLAs.

The conditional capacity invariant is exact charged durable state plus remaining
conservative reservations at most the budget. Admission preserves it; completion
converts a reservation to charged evidence; deletion affects only unpinned
revisions. This establishes retained availability under bounded conforming input
and successful durability, not crash recovery, scheduling fairness, authenticity,
arbitrary future demand or physical DB/RSS bounds. Reservation and demand envelopes
are established ideas: see [Banker's algorithm](https://www.cs.utexas.edu/~EWD/transcriptions/EWD06xx/EWD623.html)
and [RFC 2212](https://www.rfc-editor.org/rfc/rfc2212). These analogies do not transfer
a network delay guarantee to this storage service.

Run and verify write/read one explicitly chosen private study directory. Use
isolated dependencies rather than modifying a historical research environment.
The local MLflow scoring adapter requires FastAPI 0.115.14 / Starlette 0.46.2;
newer FastAPI without `route` is incompatible with this MLflow startup path.
BentoML is loaded separately so its dependency set is not forced onto MLflow.
Disable telemetry before importing either SDK; worker network sockets are denied
after asyncio bootstrap and through shutdown. No provider API calls are used.
The design records selected source/package/code identities before the full run.
Preserve its implementation if later internal refactors change code identities;
`verify --root /path/to/frozen/implementation` binds historical executable files
while reading the existing results. Native work is never repeated by verification.

## Ordered native inference and compact audit certificates

`scripts/joint_inference_development.py` adds a bounded development comparison
on BentoML 1.4.39's real local `depends` path. A native encoder service transforms
the request; a native classifier service consumes that output. Their model
descriptors resolve and retain locally fitted sklearn objects. The ASGI handler,
dependency invocation, startup failure and prediction/health requests execute;
no network server, remote dependency or production deployment is simulated as
having been measured. The source includes a simpler architectural control: the
same fitted encoder/classifier packaged as one native sklearn Pipeline artifact.
Service splitting needs a reason such as independent component management; two
inference stages alone do not require two independent artifact obligations.
[Bento's composition documentation](https://docs.bentoml.com/en/latest/build-with-bentoml/distributed-services.html)
supports the native workflow, not the operator's release policy or deployment demand.

The fixed question is whether a completed request used the authorized ordered
encoder/classifier tuple. The operator pins that tuple **before dispatch**.
Individually valid component loads are necessary but not sufficient: a request
can use two correctly loaded components whose combination was not authorized.
Conversely, a deliberately authorized mixed pair is not a violation merely
because it differs from another release or changes a prediction. This is
contract-relative authorization, not task accuracy or proof of performance harm.

Twelve authored request slots include authorized retained generations, changed
request authorization without a new load, lawful and forbidden mixes, an invalid
input, corrupt owned startup and restoration. `attempt_one` checks an attempt
annotation; it is not a native retry after an earlier failed attempt. All component
artifacts exist before requests, so `authorized_old` is not a publication race,
automatic rollback or observed alias-update experiment. Fresh service definitions
are used for new retained generations; requests within each application are serial.

The capture contract supplies request/attempt/revision, root authorization,
ordered uses, operand/result identities, resident generation, load/object binding,
declared occurrence census, closure and failure. The independent reference retains
original descriptor bytes, actual object fingerprints and operands/results, then
compares the actual byte tuple with the pre-dispatch authorization. It does not use
the tested verdict resolver. It still shares the trusted serial invocation boundary;
neither sink proves a hostile host or every consumed byte. Installed native package
versions and consequential module hashes accompany each capture.

The sufficient resolver and real in-toto role/artifact verification receive the
same complete bindings. Signing eligibility uses visible completeness, not the
reference verdict. The trusted adapter supplies order/dataflow/completeness;
in-toto itself checks the two role/artifact rules. Executed signatures therefore
are a conditional rule comparator, not an independent request attestation.
[in-toto](https://www.usenix.org/system/files/sec19-torres-arias.pdf) already provides
cross-step supply-chain artifact rules; this comparison does not invent signed provenance.

`joint_inference_audit.py` implements two ordinary representations. Full frames
retain named fields. Compact certificates positionally pack **all** admitted
fields; `expand(materialize(frame)) == frame` for the supported exact schema.
This reversible mapping preserves conflict and unknown basis as well as positive
evidence. It proves no minimal-cardinality certificate or novel inference theorem.
For the declared completed-revision query family, equality of materialized evidence
implies equality of the resolver result. That statement does not extend to
unrecorded facts, arbitrary future queries, late revisions or open histories.
[Runtime verification](https://havelund.com/Publications/rv-2023-tutorial.pdf)
already maintains verdict summaries; ordinary materialization is a required baseline.

Full and compact arms share lossless compression, exact load-capsule deduplication,
complete manifest/counter accounting, SQLite WAL/FULL durability and inclusive
accepted lease pins. Static oldest-optional eviction, access-based LRU and optional
TTL age two are compared at 2, 4 and 8 KiB. The cap measures the complete canonical
persisted BLOB, not physical pages, WAL, process memory or device I/O. Physical
SQLite/WAL/SHM bytes are sampled separately. Smaller logical representation does
not necessarily save physical storage or latency.

Two fresh native processes supply 36 matched durable retention replays. Every arm
offers the same three optional query ages (0, 2, 6) for every slot, including native
failures and lease refusals. Eligible completed audits may receive a hard lease
through inclusive age four. Hard queries drain independently of later admission;
native unknowns, capacity refusals, missing optional evidence, false decisions
and unserved accepted leases are distinct counters. Native capture happens once
per process: these are paired archive replays, **not policy-interposed native
backpressure, concurrent throughput or wall-clock service measurements**.

The conditional invariant is that durable charged state is at most the logical
cap and successful admission retains the complete certificate/dependency union
through its hard deadline. Eviction removes only unpinned entries; failure to fit
refuses a new lease without deleting an old one. This assumes unique source-census
ingress, fixed revisions, bounded schema and successful durability. It is not
crash recovery, arbitrary late evidence, a global duplicate ledger or authenticity.
Unknown frames may be retained without a conclusive hard promise.

```sh
PYTHONPATH=src python scripts/joint_inference_development.py run \
  --root . --study-dir /path/to/private/fresh-development \
  --dependencies /path/to/pinned/bento-dependencies \
  --dependencies /path/to/pinned/provenance-dependencies
PYTHONPATH=src python scripts/joint_inference_development.py verify \
  --root /path/to/executed/implementation \
  --study-dir /path/to/private/completed-development
```

Run seals source/code decisions before native work. It requires a fresh owned
directory and explicit isolated dependencies. Only locally created trusted models
are loaded; do not supply external pickle/Joblib artifacts. Child sockets are
denied after asyncio bootstrap and through shutdown; telemetry is disabled.
Verification performs no native load, fitting, signature re-execution or provider
call. It rebuilds raw-reference verdicts, common query/lease counts and canonical
durable byte/basis checks. Storage decoding is independently reconstructed but
codec expansion and resolution are shared, not a wholly independent formal checker.
Preserve executed source identities before later code refactors.

Method development is conditional on a residual gap beyond ordinary compact static
and lease-safe alternatives under equal promises. Representation packing, signed
ties or a result on one authored schedule are not adaptive method novelty.
If a conventional representation meets the declared service, close that candidate
with a bounded negative method decision. Raw native captures, models, databases
and aggregate outcomes remain outside the repository.

## Source-informed request/model serving replication

`scripts/request_model_validation.py` executes a bounded development study of
Ray Serve's multiplexed model selection across asynchronous batches. The source
is the externally reported [Ray issue #56633](https://github.com/ray-project/ray/issues/56633);
the upstream [repair #59334](https://github.com/ray-project/ray/pull/59334) captures
per-item context, separates batches by model and restores batch-aware selection.
The issue and repair are prior work, not a newly discovered fault or method.
[PEP 567](https://peps.python.org/pep-0567/) explains task-local context inheritance;
the relevant native batch worker is a different context boundary from the
incoming HTTP request. More log text does not repair a missing correspondence.

The fixed question is whether a completed request uses the model it requested.
This is the application's multiplexing contract, not predictive accuracy or an
operator rule forbidding all legitimate version changes. The study runs pinned
Ray 2.48.0 and 2.54.0 in separate explicit Python environments. Their other
recorded numerical and HTTP dependencies match. Comparing these releases is
consistent with the upstream repair but does not isolate that commit from all
other changes. The recorded Python/platform differ from the original issue;
this is a source-informed replication, not an exact environment reproduction
or unseen validation.

Two modes must remain separate. Original mode retains the issue's object-naming
responses and sequential singleton request order: output can already reveal a
wrong model. The controlled ML extension uses two locally fitted linear models,
both first-model orders, repeated warm batches, nonbatched controls, same-model
and mixed-model concurrent requests, equal-output inputs and invalid inputs.
Mixed-model co-membership alone is not labelled an old native violation: the
test checks each member's actual requested/used model. The old API did not
promise homogeneous multiplexed batches. Prediction agreement at a particular
input does not establish model identity.

The trusted adapter observes request tokens, actual batch membership, operand
and result, model object/process identity, generation, and the same owned bytes
loaded by that generation. HTTP response IDs, public selection context and
available per-item batch contexts are retained rather than artificially hidden.
Original mode admits ordinal correspondence only for a complete successful
sequential singleton census. It is not a general concurrency join. The raw
reference checks observed object/load consistency and recomputes numeric
predictions from the recorded release coefficients without the tested resolver.
Neither reference nor capture attests a hostile process or deployment.

Comparators include output-only inference, native context with available item
IDs, ordinary explicit item/object joins and executed in-toto artifact rules
with the same complete trusted capture. An old scalar context is not assumed
to carry each request's identity; fixed batch context is given its full available
capability. Load identity alone is an ablation, not a competitive request-use
baseline. Ordinary joins already address correspondence: see
[OpenTelemetry batch links](https://opentelemetry.io/docs/specs/otel/overview/)
and [Pivot Tracing](https://cs.brown.edu/people/jcmace/papers/mace15pivot.pdf).
An agreement with adapted provenance is not checker superiority. The read-only
replay validates recorded comparator outcomes and source bindings; it does not
re-execute signatures or independently cryptographically verify the saved links.

Retention uses the realized ML capture once, then matched durable SQLite replays.
All policies share lossless compression, dependency deduplication, charged
manifest/counter fields, WAL/FULL durability and inclusive accepted lease pins.
Full and reversible compact frames are compared with static newest-first,
LRU, LFU, size-cost, TTL and the existing union-density/exchange heuristics.
The four logical budgets and two declared audit-delay schedules are fixed
before measurement. All optional queries are offered, including failed native
requests and requests whose hard lease was refused. Hard obligations arise
only for eligible completed requests; refusal is distinct from losing an
accepted obligation. No policy may recover discarded evidence from raw captures
or inspect future queries during selection.

The logical cap includes the canonical durable evidence BLOB and its complete
dependency/manifest union. Sampled database, WAL and SHM bytes are separate;
the cap is not a physical storage or RSS guarantee. Snapshots bind the durable
basis of every answer. Failed writes preserve old accepted state or expose a
failure; successful bounded admission retains its basis through the inclusive
deadline. This is a conditional fixed-revision service invariant, not crash
recovery, a global duplicate ledger, host authenticity or a wall-clock SLA.
Selection/write/query timing is descriptive local replay cost, not randomized
serving overhead or concurrent throughput. Compact packing is ordinary lossless
materialization, not a minimum-certificate theorem. A heuristic that fails to
beat tuned compact static is not admitted as a new retention contribution.

```sh
PYTHONPATH=src python scripts/request_model_validation.py run \
  --root . --study-dir /path/to/private/fresh-development \
  --before-python /path/to/isolated-ray-2.48/bin/python \
  --fixed-python /path/to/isolated-ray-2.54/bin/python
PYTHONPATH=src python scripts/request_model_validation.py verify \
  --root /path/to/executed/implementation \
  --study-dir /path/to/private/completed-development
```

The run requires fresh owned storage and the exact explicit Ray versions;
the orchestrator also needs the pinned provenance dependencies above. Do not
resolve a virtual-environment Python symlink to the underlying base executable.
Only locally created hash-checked Joblib bytes are deserialized. Ray uses owned
local clusters and loopback HTTP with telemetry disabled; no provider or public
deployment is involved. Runs retain startup failures, timeouts and every planned
request. Verification is read-only and does not rerun serving, fit/load models,
sign new records or make provider calls. Preserve executed source copies before
later refactors. Synthetic tests require no Ray installation; optional native
execution remains outside the default CI profile. Private source observations,
signed records, database snapshots and empirical aggregates are not repo assets.

## Response-origin transfer and standard cache repairs

`scripts/response_origin_validation.py` studies an additional native boundary:
the response may come from an earlier computation rather than a new invocation.
A request-to-predict-only certificate therefore loses lawful cache hits as well
as stale ones. The bounded origin graph is request → computation, or request →
cache entry → producing computation → actual resident generation. It uses ordinary
provenance joins and contract-relative identification, not a new tracing theorem,
minimal-certificate proof or attestation against a hostile serving host.

The [MLServer 1.7.1 DataPlane source](https://raw.githubusercontent.com/SeldonIO/MLServer/1.7.1/mlserver/handlers/dataplane.py)
keys the response cache by payload JSON before generating a missing request ID.
The cache is shared; route/version and resident generation are not separately
included. A hit reconstructs the stored response without calling predict.
The [native registry](https://raw.githubusercontent.com/SeldonIO/MLServer/1.7.1/mlserver/registry.py)
loads a replacement before publishing it. Failed loading preserves the prior
resident; an already selected in-flight object can complete lawfully after reload.
These are source-informed mechanisms, not claims of newly discovered incidents.

The route contract checks requested model/version, retaining full native response
body and headers. The reload contract additionally requires the generation selected
at request entry. This is an explicit service assumption, **not an OIP guarantee
of immutable artifact bytes or cache freshness**. Generation refers to a realized
resident object. A newly selected request differs from an old in-flight request;
neither current registry state nor output agreement alone is reference truth.

The fixed matrix has version routes, serial replacement and delayed old cache
fill; each has six arms and two fresh process replicates in a fixed shuffled order.
Arms are unchanged cache, cache disabled, model/version namespace, successful-load
flush, namespace plus epoch-fenced insertion/flush, and selected-generation key.
These repairs are established baselines. [Triton caching](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/response_cache.html)
already includes model/version/inputs. [Scaling Memcache at Facebook, §3.2.1](https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170.pdf)
already treats stale sets after invalidation with leases. The fence arm implements
a local epoch rejection, not that distributed lease protocol. It must not be called
a novel optimizer or an executed Triton comparison.

Models are three locally fitted one-feature CPU predictors. Zero inputs give
equal outputs despite different model identity. Native REST ASGI handlers,
repository, registry, sklearn predictor and LocalCache run unchanged outside
explicit repair hooks. A barrier delays the old native predict but does not replace
its numerical result. Observer identity is a ContextVar only; it never enters the
HTTP body, headers or cache key. Generation is bound inside the original selection
context and reused consistently for lookup and insertion. The run is same-process
ASGI with concurrent tasks, not TCP throughput, worker-pool or distributed serving.

Reference replay reconstructs accepted insertions and hits from raw cache strings,
checks actual object/load/owned-byte identity and recalculates numeric outputs from
captured coefficients. The resolver and a separate ordinary graph join get exactly
the same capture capability; equality is expected. The native-body comparator is
only a name/version-consistency check, not an optimal checker using every native
output, chronology and registry fact. It can detect a visible wrong version.
The equal-output collision projection contains labels and input/output tensors;
it omits response IDs, timing and registry history and is not global observational
equivalence. Header-only checks are limited by actual available
fields, and neither is falsely treated as a generation witness. Missing dependencies,
open responses and failed/unattempted work abstain; cycles and incompatible operands
conflict. All planned requests remain in the denominator, including startup failure,
transport error, non-JSON response, timeout and failed reload. Raw logs stay private.
Descriptive timing does not establish a comparative production latency claim.

```sh
PYTHONPATH=src python scripts/response_origin_validation.py run \
  --root . --study-dir /path/to/private/fresh-development \
  --native-python /path/to/isolated-mlserver-1.7.1/bin/python
PYTHONPATH=src python scripts/response_origin_validation.py verify \
  --root /path/to/executed/implementation \
  --study-dir /path/to/private/completed-development
```

Do not resolve the virtualenv executable symlink out of its environment. Only
owned locally created artifacts are loaded. Child network entry points are denied
after asyncio self-pipe bootstrap; no provider or deployment is invoked. Run locks
its code/design before native execution. Replay is read-only, with no native fitting,
load, prediction or signature execution. Preserve executed modules before refactoring.
Two process repeats are robustness checks nested in one cache implementation, not
independent incidents. This prospective lifecycle development is not untouched
external validation and does not reopen historical protected studies.

Raw replay also checks structured responses against retained native response
text, selected-generation/object equality with the load census, producer labels,
and repair keys against independently recorded request inputs and the selected
namespace. Headers were captured within the cache key, not as independent ingress
wire bytes; their shape is checked without claiming full-wire attestation.
Stronger analysis-only validation can replay a completed study against its
preserved executed implementation; it must not alter the original plan, source
observations or result aggregates, nor rerun the native lifecycle to repair a checker.

An additional source-informed [BentoML timeout report #5642](https://github.com/bentoml/BentoML/issues/5642)
motivates a different endpoint: HTTP completion versus actual synchronous worker
exit. Offline closure replay keeps error responses unknown for response-origin
compliance even if later model computation becomes visible. A worker may finish
or fail after the client has received 504. The [proposed repair #5671](https://github.com/bentoml/BentoML/pull/5671)
was open at source review; this comparison does not evaluate or claim an upstream
fix. A finite local TCP prototype with normal-level log observation is supporting
development evidence, not another set of MLServer repair trials. Absence from the
captured logger set is not absence from every possible native telemetry source.

The resulting architecture separates response-origin compliance, worker/attempt
closure and retention of their respective dependencies. Signed roots and log
volume cannot reconstruct a computation/cache edge that was never captured.
Existing explicit joins or source-native repairs may solve the declared problem;
their success does not establish a new checker or adaptive retention advantage.
Raw observations, models, logs, plan/result aggregates and prototype artifacts
remain outside the repository. Writing and held-out evaluation are separate work.

## Source-conditioned transfer on LitServe

The optional pinned [LitServe 0.2.19 source](https://raw.githubusercontent.com/Lightning-AI/LitServe/v0.2.19/src/litserve/server.py)
provides a previously unused serving implementation, selected for its CPU,
queue and ordered-batching architecture before controlled failure outcomes.
The eligible frame has three families: immutable endpoint batching, queue-age
expiry and client abandonment after actual prediction entry. This is one
implementation with authored workloads and nested process repeats, not a sample
of independent deployments or a naturally occurring incident corpus. Existing
cache/reload development evidence is not relabelled as fresh LitServe validation.

The fixed design is two application arms (native and cooperative), two live
SQLite persistence delays (zero and 0.5 seconds) and two fresh process repeats:
eight cells with eleven offered requests each. Two owned bounded JSON affine
models avoid untrusted model deserialization. All native UID allocation,
default batching/unbatching and response transport remain in the actual SDK.
The cooperative arm is explicitly application-owned deadline refusal before
designated computation, not a claimed native cancellation guarantee. A start
callback is not an actual prediction-entry witness. Queue-age expiry does not
cancel an already running prediction.

The live collector physically delays persistence while a separate trusted
producer journal retains execution-boundary reference facts. Producer clocks,
object and batch-slot identities, arithmetic, original client response bytes,
native UID transport, per-PID sequence and SQLite census are rebuilt independently.
This checks the representation and resolver against a second implementation of
arithmetic/interval reasoning; it cannot independently attest the host or prove
that the hooks observed every possible operation. Receipt time is recorded after
commit. Neither missing evidence nor eventual worker exit proves closure at an
earlier client cutoff.

An initial adapter attempt failed before prediction because postponed HTTP
annotations were not resolved by the SDK signature inspection. All attempted
HTTP 422 responses and unattempted slots remain in a separate immutable report.
A technical annotation correction retained the same design and full census;
the corrected execution completed 88/88 offered requests. Both executed code
snapshots and original reports are retained privately.

| Actual application outcome | Native, 44 offers | Cooperative, 44 offers |
| --- | ---: | ---: |
| Delivered HTTP 200 | 28 | 31 |
| Delivered HTTP 504 | 8 | 13 |
| Client ReadTimeout | 8 | 0 |
| Attempt closed at client cutoff, raw reference | 36 | 44 |
| Attempt still open at client cutoff, raw reference | 8 | 0 |
| Lawful batching successes / offers | 24/24 | 23/24 |

The cooperative gate refuses all eight slow started attempts before numerical
computation. Refusing four blockers also lets queued requests run, so the success
count difference is queue redistribution, not a general success improvement.
One lawful cooperative request expires in the queue and remains in the census.
The policy does not establish an HTTP latency SLA or preemption of indivisible work.

### Strong native controls and distinct evidence tiers

The frozen `native_uid` comparator uses **persisted native UID evidence**. Its
first-query count is not a test of every native capability: asynchronous receipt
usually follows client completion even at zero artificial delay. An additive
post-result analysis strengthens this control on exactly the same immutable raw
data, without rerunning serving or changing the original forecasts or aggregates.

Under the explicit trusted deployment contract (direct synchronous nonstreaming
HTTP, immutable correct endpoint setup, default order/cardinality checks, no
hidden background computation, proxy, cache or dispatch mutation), a successful
native response supports conditional correspondence before collector receipt.
Delivered known queue-expiry or cooperative-refusal responses also establish
attempt closure. The source-conditioned response control classifies all 59
delivered numerical origins correctly (29 unavailable) and 80 closed attempts
correctly (eight unknown), with zero false conclusive answers against reference.
At either persistence delay, it has 40 closed and four unknown. This explicitly
rejects a claim that delayed collection causes a general native diagnostic gap.

These source-contract implications are **not materialized per-request actual-use
certificates**. Equal numbers do not identify a model without the source/setup
premises, and these premises fail in several prior cache/context studies.
ReadTimeout cannot imply closure: retained entry/terminal chronology later
establishes all eight native attempts were open at the actual client cutoff.
Unidentified HTTP 500 also remains unknown because handler errors can precede
worker submission. No delivered HTTP 500 occurs in this frame.

After collector drainage, ordinary same-access joins correctly reconstruct all
88 closure classifications and all 59 available numerical origins, with zero
false conclusive answers. Actual [in-toto](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias)
signature/rule verification covers the same captured event bundle; eight public
layouts, keys and links replay successfully. Signatures add integrity, not
missing observations or new inference accuracy. The frozen 36 nested forecast
checks are supported with zero contradictions, not 36 independent source trials.

### Measured cost and replay boundary

Across eight cells, recorded capture encoding/journal IO is 163.322 ms; repeated
collector scanning is 7.544 s; SQLite persistence is 442.263 ms; final archival IO
plus journal reading is 14.508 ms; analysis is 9.454 ms; signing plus verification
is 71.782 ms. These instrumented components may overlap and must not be added
as an estimate of end-to-end overhead. Payload is 194,780 bytes, physical SQLite
327,680 bytes, raw/log archive 559,507 bytes and provenance 20,072 bytes. The last
stats write for each of 32 PIDs is explicitly unmeasured. No observer-free matched
run, cheapest sufficient implementation, production speedup or storage optimizer
advantage is established here.

The optional runtime is isolated; the normal test profile has SDK-free contracts
and does not install or start LitServe. Running needs a fresh caller-owned private
directory, an explicit interpreter and native site directory. Dependencies and
native source bytes are bound before serving. No provider, historical protected
study or user model is invoked. Python audit hooks deny external networking but
are not a kernel sandbox; the local serving child permits loopback/AF_UNIX only.

```sh
PYTHONPATH=src python scripts/litserve_evidence_validation.py prepare \
  --root . --study-dir /private/fresh-study \
  --native-python /isolated/bin/python --native-site /isolated/site-packages
PYTHONPATH=src python scripts/litserve_evidence_validation.py run \
  --root . --study-dir /private/fresh-study \
  --native-python /isolated/bin/python --native-site /isolated/site-packages
PYTHONPATH=src python scripts/litserve_evidence_validation.py verify \
  --root /private/executed-code-snapshot --study-dir /private/completed-study
PYTHONPATH=src python scripts/litserve_evidence_validation.py closeout \
  --root /private/executed-code-snapshot --study-dir /private/completed-study
```

`prepare` reads metadata/design only and does not authorize or execute serving.
`verify` rebuilds raw/reference, physical collector and actual public signature
verification read-only. `closeout` additionally creates the separately labelled
strong-native-control analysis once, or checks byte identity on repetition.
Historical replay uses the retained executed implementation, not resealed current
code. The supported finding is bounded evidence/repair transfer and native
adequacy under stated contracts, **not a new checker, optimizer or universal
minimal-evidence theorem**.

## Cache-producer lifecycle transfer and sufficient audit cost

A forward, source-informed experiment uses previously unused **cachetools 6.2.6**
memoization inside an application-owned **aiohttp 3.14.3** HTTP service. This is a
controlled new implementation, not a naturally occurring incident or a claim
that either library supplies hot-reload semantics. It runs immutable affine JSON
models created locally; no user model, provider or historical protected study is
loaded. Candidate parsing precedes atomic publication. A request's contract is
the resident snapshot selected under lock after JSON decoding, not client arrival.
An old request finishing after a reload is therefore not automatically wrong.

### Mechanism and evidence requirements

The locked [cachetools implementation](https://raw.githubusercontent.com/tkem/cachetools/v6.2.6/src/cachetools/_cached.py)
computes a miss outside its cache lock and then uses `setdefault` to prefer an
already inserted value. Clearing the cache does not cancel an earlier running
miss. This predicts **two directions** of incorrect return:

- Old computation finishes first: an old value refills the shared input-only key
  and is served to a new-generation request.
- New computation finishes first: the old request computes with its selected old
  object, but the wrapper returns the new producer's already cached value.

The latter shows why a computation witness alone does not establish response
origin. For the declared retrospective service, the ordinary evidence join needs
the actual wrapper-return producer linked to its computation and loaded model,
plus the selected generation and scoped handler terminal. Client numeric equality
alone does not identify that producer; equal-zero controls preserve this limit.
Source-conditioned generation compliance and a named, retained per-request
producer certificate are **different services**, not interchangeable metrics.
The frozen resolver's concrete dependency checks reject missing or conflicting
load, computation, return and terminal witnesses. These are dependencies of that
linked-witness service, not universal information requirements. In particular,
the forward refinement below removes a redundant wrapper record when the trusted
handler terminal already carries the actual returned producer.

Versioned keys and isolated per-generation wrapper/cache lifetimes are ordinary
repairs, consistent with established cache-coherence principles such as
[leases](https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170.pdf).
They are not presented as a new algorithm. Native successful inference, lawful
same-generation cache reuse and failed-reload rollback are retained as controls.

### Executed results

The design fixes four cache arms, two completion orders, three materialized-evidence
modes and two fresh-process repetitions: **48 cells**, **3,456 HTTP inferences**
and **96 HTTP reload offers**, with 216 numerical computations. Every cell
completed. All source-informed violation-count predictions, rollback checks and
materialized-join predictions match. Requests and repetitions are nested in one
authored application; they are not independent incident samples.

| Cache arm | Wrong-generation returns / 864 inferences |
| --- | ---: |
| Input-only key | 30 |
| Clear shared cache on publication | 18 |
| Generation included in key | 0 |
| Isolated generation cache/wrapper | 0 |

Twelve reverse-order cases independently confirm that an old request computes A
but returns B's producer. Both ordinary generation controls prevent observed
violations without disabling lawful cache reuse. This is evidence for a bounded
repair footprint, not a general thread-safety or production-fault theorem.

Persisted-only ordinary joins on sufficient and full records each correctly audit
**1,152/1,152** requests, with zero false or unknown answers. The no-additional-store
arm has 1,152 unknown materialized answers: it still collects the common raw
reference and is **not an uninstrumented application baseline**. All 32 actual
public in-toto bundles independently verify and bind their closed SQLite files;
signatures protect retained evidence, not a hostile host or missing capture.

The original secondary native comparison improperly used raw reference selection
for unsafe shared-key arms. The execution/results snapshot is unchanged. An
additive analysis-only closeout removes that uncharged premise: the conservative
tier has 576 source-conditioned correct and 576 unknown answers per evidence mode.
It is **not an exhaustive body/client-order comparator** and is excluded from any
strongest-native gap claim. All primary materialized answers, forecasts, truth,
timing and storage measurements are checked identical to the original report.

### Matched cost and limits

Both materialized modes use the same persistent collector and synchronous FULL
SQLite transaction policy throughout a cell, with one commit per admitted record.
Reported times include actual HTTP inference and instrumented hash, capture,
write, query and signing/verification components. No component-time sum is called
end-to-end overhead. All four nested samples per row are retained.

| Strong repair / evidence | Steady inference median, ms (range of cell medians) | Write median, ms | Closed DB, KiB | Live DB + WAL + SHM median, bytes |
| --- | ---: | ---: | ---: | ---: |
| Generation key / sufficient | 0.330 (0.316–0.355) | 15.297 | 96 | 1,157,536 |
| Generation key / full | 0.422 (0.384–0.441) | 21.327 | 108 | 1,511,856 |
| Isolated cache / sufficient | 0.348 (0.319–0.357) | 15.657 | 96 | 1,157,536 |
| Isolated cache / full | 0.394 (0.378–0.430) | 19.079 | 108 | 1,511,856 |

Sufficient reduces closed database bytes by 11.11%; including the equal 2,509-byte
public-provenance bundle gives about 10.865%. The generation-key/sufficient arm
has the lowest measured strong-repair steady median in this candidate set. This
does **not** establish global minimum, statistically robust latency superiority
or uniform total-cost dominance: signing/total elapsed costs do not consistently
favor sufficient. Live WAL footprint is not the final archive footprint. Native
cache object bookkeeping is separately measured with identity-deduplicated
`sys.getsizeof`, excluding wrappers, thread resources and RSS. Common raw journal,
setup, trusted observer and reference costs remain present in every arm.

### Replay and forward code

The opt-in CLI exposes `prepare`, `run`, `verify`, `closeout` and an internal
worker. Preparation locks runtime source bytes, code, design and predictions;
starting a run prohibits silent retry/resume. Launch failures, timeouts and failed
signature verification retain their planned denominator. A failed replica cannot
make its surviving cost group qualify as sufficient. Offline tests need no SDK.

Exact historical replay must execute the archived analysis/study, because forward
maintenance and the labelled secondary analysis are not byte-identical to
executed code. Snapshot directories omit package initializers; plain `PYTHONPATH`
can accidentally select the live editable package. The current CLI explicitly
loads the bound archived modules and first rejects changed shared helpers:

```sh
PYTHONPATH=src:/isolated/native-site /isolated/bin/python scripts/cache_lifecycle_validation.py \
  verify --root /private/completed-study/code-snapshot --study-dir /private/completed-study
PYTHONPATH=src:/isolated/native-site /isolated/bin/python \
  scripts/cache_lifecycle_validation.py closeout \
  --root /private/completed-study/code-snapshot --study-dir /private/completed-study
```

The latter writes/checks `analysis-closeout-v2.json` once, binding its analysis
implementation and the unchanged original result. It performs no serving replay.
The supported conclusion is **bounded evidence-requirement and ordinary-repair
transfer with measured same-service sufficient cost**. It closes the declared
experiment, not production coverage, native framework reload guarantees, a novel
retention method or journal acceptance.

### Post-result adequacy refinement: assumptions, query and materialization

A second development pass uses the **same immutable 48-cell trace**. It neither
reruns nor reseals the original native validation; its choices are post-result,
not new held-out predictions. The additive output contains 96 real SQLite
materializations/signature bundles over the 32 nonempty sufficient/full stores,
plus analysis of all 48 client transcripts. Source/config/code bindings, exact
planned census, physical payload/binding, actual public database signatures and
dependency replays are verified independently of the stored summary.

The organizing model is established
[query determinacy](https://dbucsd.github.io/paperpdfs/2010_3.pdf) and
[monitoring under partial observations](https://arxiv.org/pdf/2207.05678):
for declared operational premises M and projection O, consider all compatible
histories H_M(o) and the possible answers A_q(o) to a specified query q. A singleton
answer is sound **if** the actual history satisfies M, the projection is faithful,
and the analysis overapproximates compatible histories. Empty compatibility is a
conflict, not compliance. The implemented rule comparator is conservative and
tested against this bounded corpus; it is not a complete enumerator, a universal
new theorem or proof for arbitrary cache deployments.

Five queries remain distinct: generation compliance, named producer occurrence,
handler closure, client delivery, and failed-reload resident preservation.
Numerical equality and generation compliance do not establish a named actual-use
certificate or counterfactual numerical harm. A trusted terminal can itself permit
thinner conditional inferences. Requiring separate load/computation cross-links
is an explicit trace-consistency service requirement, not semantic minimality.

The stronger native comparator receives only a strict projection: HTTP route,
operand, status/body, invocation/completion interval, known affine parameters and
the declared cache arm. It receives no reference selection, producer, token,
event index, completion-order label or barrier observation. Its three capabilities
are separated rather than credited as evidence-free:

| Conditional native capability | Correct generation verdicts / 3,456 | Unknown | False | Correctly recognized violations / 48 |
| --- | ---: | ---: | ---: | ---: |
| Client intervals/numeric output + immutable source-generation contract | 3,360 | 96 | 0 | 24 |
| Also complete client census, initially empty LRU8, no hidden callers/eviction | 3,444 | 12 | 0 | 36 |
| Also observer-assisted driver premise: sole old computation entered A before reload dispatch | 3,456 | 0 | 0 | 48 |

The 12 residual unknowns concern an overlapping old request returning the new
producer in unsafe `new_first` arms: client intervals alone do not locate its
server selection. Adding the actual controlled driver premise resolves them.
Thus the experiment does **not** prove universal native-telemetry inadequacy.
Source assumptions, observer-assisted control and retained actual-use witnesses
have different authority/cost and must not be silently exchanged.

Offline deletion on 2,304 physically retained inference certificates shows that
removing the separate wrapper keeps all answers; terminal-carried return is enough
for this linked service. Removing load, computation or terminal leaves this
resolver unknown. Removing client responses preserves producer/handler answers
but removes delivery proof. Publication/failure records do not affect that
inference tuple, yet are retained and charged for the separate two-offer reload
ledger. These are resolver/service ablations, **not operational capture-loss
experiments or a universal necessity/minimality proof**. Contradictory bindings,
future computations, mismatched object/body and inconsistent publication/failure
chronology are rejected; cross-request producer references remain lawful cache hits.

Ordinary field projection removes wrapper duplication and unused fields while
keeping load/computation/terminal, all HTTP responses and publication/failure
records. Every candidate produces 2,304/2,304 correct linked certificates with
observed client delivery, zero false/unknown, and the same reload ledger:

| Offline candidate, median over 32 nested cells | Records | Commits | Logical bytes | Closed DB | Live DB/WAL/SHM bytes | Persistence, ms | Reconstruction, ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Source sufficient records | 227 | 228 | 82,235 | 100 KiB | 1,169,896 | 25.369 | 2.009 |
| Projected, FULL per admitted record | 155 | 156 | 38,122 | 56 KiB | 782,616 | 17.678 | 1.271 |
| Same projection, FULL after complete drain | 155 | 2 | 38,122 | 56 KiB | 111,056 | 1.465 | 1.209 |

The replay schema charges its own scoped binding/ordinal bookkeeping; its 100 KiB
baseline is not the original collector's 96 KiB. Projection saves **44% closed DB**,
or **42.948%** including each equal 2,509-byte public-provenance bundle. Per-record
projection writes faster in 30/32 paired cells; its persistence range is
9.771–38.939 ms. Signing/verification medians are 4.244/4.259/4.269 ms and favor
projection in only 18/32 pairs: no uniform total-cost dominance is claimed.
After-drain writes take 1.249–5.017 ms but make **no earlier prefix-survival
promise**. No crash or matched serving-overhead experiment was run. These are
actual offline encoding/write/query/reconstruction/hash/sign costs, not HTTP
speedups or global minima. The lowest observed archive footprint among these
ordinary linked-service candidates is 56 KiB; a terminal-only conditional service
has a different assurance contract and is not compared as equivalent.

A separate exploratory cachetools 6.2.6 prototype ran three arms/nine native calls:
an old `x=0` miss blocks, clear/publication B occurs, then A refills. New/repeated
requests return producer A with the same zero value B would produce. Generation
keys and isolated caches return B. This combines stale refill with noninjective
output and tests the comparator's conservative guard; it is not another unseen
validation or a newly discovered upstream bug. Source and result are archived
inside the existing additive study output, never imported as executable replay.

The opt-in `adequacy` action materializes this forward analysis once;
`verify-adequacy` rebuilds it read-only, without timing/native rerun. Both take the
original executed snapshot as `--root` and the completed study as `--study-dir`.
Verification additionally rejects an unbound live WAL on a claimed closed store.
This refinement strengthens **query-specific, capability-explicit evidence and
repair limits**, while retaining ordinary baselines. It admits no new optimizer,
Paper B method, host attestation, production guarantee or Q1 acceptance claim.

Local verification: 96 focused tests and 68 contract tests pass; strict typing
for Python 3.11/3.12, Ruff/format, maintainability and focused security checks pass.
Independent realized-output checks cover raw arithmetic, all 96 physical stores,
public signatures and archived probe hashes. Windows and the complete remote CI
matrix have not been executed for this uncommitted change.

## Source-informed dependency realization, capture loss and sufficient cost

This additional experiment adapts the known MLflow `code_paths` limitation:
two packaged models can contain differently implemented modules with the same
name, while a shared process reuses the first imported implementation.
[The upstream discussion](https://github.com/mlflow/mlflow/discussions/8905),
[reported issue](https://github.com/mlflow/mlflow/issues/12377) and
[documented limitation](https://mlflow.org/docs/latest/ml/model/dependencies/#limitation-of-code-paths-in-loading-multiple-models-with-the-same-module-name-but-different-implementations)
motivate the mechanism; this is an exposed, source-informed adaptation on
MLflow 3.9.0, not discovery of a new bug, reproduction of an original deployment,
a blind holdout or a natural incident prevalence estimate. All artifacts are
created locally. Only their trusted owned Python implementation is loaded.

The query is which delivered requests after deployment used a mismatched resident
dependency. Packaged artifact identity, callable dependency realization, numerical
output, observed client delivery and complete attempt closure are separate facts.
The service is serial; synchronized eviction of owned modules is not concurrent
Python import isolation. Namespace separation and eviction are ordinary repairs.

### Actual serving and operational capture interventions

The complete 54-cell matrix executes 1,998 real HTTP prediction requests and
54 deliberately invalid loads, including two load orders, three ordinary repair
arms, native/compact/full evidence, five delivery interventions and eight kills
of the SQLite-owning child. There are 360 dependency-contract violations;
only 240 change the numerical output. Numerical equality at zero does not prove
dependency identity. Body-only inference resolves 1,350 requests and leaves 648
unknown, with zero false conclusions. A stronger comparator with the complete
serial client history and known immutable source semantics resolves **all 1,998**
correctly. The experiment therefore does not establish universal insufficiency
of native information or superiority of a new checker.

Captured actual-use evidence gives 1,454 correct answers, 544 unknown and zero
false conclusions over all arms; this denominator includes the intentionally
unobserved native arms and lossy stores. Missing load or predict witnesses remain
unknown. Duplicate sequence delivery is deduplicated; delayed in-process delivery
is unavailable before flush. The latter is a controlled buffer, not an external
network outage. Dropped closure never becomes a complete-attempt certificate.

All **1,672 acknowledged payloads** are recovered byte-for-byte across the
non-native stores. Per-event acknowledgement is published only after SQLite
WAL/FULL commit succeeds. Eight DB-owning process kills distinguish acknowledged
event persistence from uncommitted drain buffering: after a drain-before-flush kill,
all 37 queries remain unknown; after actual drain, all 37 are supported. This is
process-crash evidence, not OS/power-loss durability. All 42 public in-toto bundles,
including two empty databases, verify independently. Signatures bind retained
bytes, not completeness or honesty of a compromised host/hook.

The first full startup census failed because the sandbox prohibited loopback
binding. It is retained separately, with zero serving calls. Technical execution
then ran the same complete matrix. Original results and snapshots remain immutable;
an additive closeout corrects empty-WAL state binding and removes a secondary
raw-load-ID shortcut from the client-history comparator. Neither change selects
favorable native outcomes.

### Cost floor, real audit queries and limited retention premise

Twelve additional fresh processes provide a no-observer floor: no child reference
journal, binding hashes, collector or database. A common parent HTTP ledger is
still charged as experiment control. Two further cells actually query the audit
endpoint before and after delayed delivery: 37 unknown/no closure becomes
25 compliant plus 12 violations/complete closure. Together these controls execute
518 additional predictions. Median prediction latency is 4.022/4.081/3.858 ms for
the collision/eviction/namespace floor arms and 4.214/4.223 ms for early/late
compact/full audit cells. Separate nonrandomized epochs do **not** support causal
observer-overhead subtraction, production throughput or a global minimum.

An offline incident-window retention premise rematerializes all 42 store cells
as 84 real SQLite databases. Ordinary shared-load interval union/pins retains
944 records/498,080 payload bytes/1,064,960 closed-database bytes, versus full
compact retention's 1,672/877,756/1,490,944. Complete scope/closure roots are retained;
unrequested predictions are explicitly unknown, and missing input evidence is not
dropped from the failure census. The 32 originally adequate stores satisfy the
scoped demand. This measures projection/physical archive cost under a smaller
declared query service, not equal-service compression, online capacity/admission,
optimal retention or a distinct new method. The full retention research question
remains open.

### Second development pass: qualify a binding cache before amortizing capture

The bounded architecture becomes **qualify footprint → obtain/reuse binding →
link each request → persist → acknowledge → reconstruct**. Caching just at load
is permitted under an immutable dependency premise, but not silently reused after
that premise is relaxed. Qualified guarded memoization is ordinary prior-aware
optimization, not a new general Python dependency algorithm:
[precise-dependency function caching](https://grosskurth.ca/bib/2000/heydon-caching.pdf),
[Build Systems à la Carte](https://simon.peytonjones.org/assets/pdfs/build-systems-original.pdf)
and [guarded specialization in PEP 659](https://peps.python.org/pep-0659/)
already motivate these ideas. Private dictionary version tags are not used;
[PEP 699](https://peps.python.org/pep-0699/) rescinds their former contract.

The fixed footprint accepts owned affine code shapes and pinned MLflow dispatch,
holding strong references to the selected method/closure/global/module chain and
checking coefficients and metadata. Dynamic module subclasses, PEP 562 lookup,
instance/descriptor routes, changed wrapper closures, global `float` shadowing,
unsupported bytecode/defaults and cycles are refused. Supported coefficient or
same-shaped function changes cause a fresh binding scan. Files, builtins, SDK
metadata/type hints/input conversion remain immutable and trusted; mutation
during a call (including ABA), arbitrary Python dependencies and hostile-host
attestation are outside the claim. Fresh qualification scans bypass the cache
but use the same bounded introspection; they are not an independent oracle for
arbitrary dynamic Python.

A separate 48-process development matrix offers 1,440 predictions and executes
1,428, with 12 unsupported calls refused before native execution. All 48 raw
workloads and physical SQLite stores verify. The load-only ablation yields six
stale certificates under supported coefficient/function mutation and six
unqualified certifications under dynamic lookup/global shadowing. Quasi-identical
fresh bindings in the latter case are not counted as sound: byte/object equality
misses the changed execution footprint. Both qualified scan and guarded cache
have zero stale/unqualified certificates in this finite matrix. Restoration and
failed-load resident preservation are included.

For the 36 immutable cells, all arms serve the same calls, per-request linkage and
WAL/FULL service. Full scans fall from **924 to 36** for both ordinary caches;
the guarded cache reuses 888 binding checks. Median of cell-level prediction
component medians is 0.262188 ms for scan, 0.134292 ms for load-only and 0.168584 ms
for guarded reuse: a 35.70% descriptive component reduction versus scan, not a
35.70% HTTP speedup. Fresh pre/post reference scans are excluded from this component
timer and reported separately. Fixed mode order/two nested repetitions do not
justify population inference. Unsupported mutation cells have unequal executed
censuses and are not used for speed ratios. A first worker's canonical JSON
integer-key serialization failure is preserved separately, followed by the
complete technically corrected matrix; neither failed receipt was resealed.

The opt-in `binding`/`verify-binding` and `amortization`/`verify-amortization`
actions use fresh private directories, archive executed code and refuse failed
or altered censuses. Verification is read-only: snapshots, offered inputs/phases,
actual responses/load linkage, recovered payloads, acknowledged digests and
aggregates are checked. Results are development evidence, not retroactive changes
to earlier registered studies or admission of a new retention algorithm.

A further **36 fresh HTTP-serving processes**, with seed-fixed randomized mode
order, execute 1,332 predictions and recover all 1,512 acknowledged payloads.
All 1,332 actual-use audit answers are correct; ordinary repairs remove the
dependency mismatch. These cells use the same compact WAL/FULL service and
fresh reference journal in every arm. Reference hashing/journaling is measured
separately but remains inside the HTTP endpoint, so this is an instrumented
end-to-end comparison, not bare production overhead.

| Immutable serving arm, median HTTP prediction ms | Qualified scan | Load-only cache | Guarded cache |
| --- | ---: | ---: | ---: |
| Shared names, no repair | 4.720 | 4.526 | 4.709 |
| Shared names, owned serial eviction | 4.613 | 4.488 | 4.725 |
| Distinct names | 4.764 | 4.581 | 4.638 |

Guarded reuse is **not uniformly faster** end-to-end: cheaper binding scans do
not dominate HTTP/journaling/persistence, and the eviction arm is slower than
qualified scan. Load-only is the cheaper ordinary option when immutability can
actually be guaranteed; guarded reuse buys a stricter between-call change
contract. No inferential significance or global optimality follows from four
nested cells per arm. The supported contribution is an experimentally qualified
trade-off among footprint assumptions, binding validity, audit service and cost,
not a prior-free cache algorithm or assured publication tier.

Executed implementations are archived before measuring. The publication candidate
removes the administrative task field from the protocol, decomposes
census/application/shutdown helpers for the frozen complexity budget, constructs
the constant-compiled rebinding function without `exec`, and propagates failed
verification as a nonzero CLI exit. Archived protocol/code remains authoritative
for exact original reproduction. Raw replay and focused semantic tests check
the equivalent analysis/refactor paths; the rebinding equivalence is independently
checked against archived function bytecode/globals/output.
Full Windows/current remote CI has not been run; scientific completion does not
imply the repository changes have been committed, pushed or merged.

Final local checks: 191 focused tests and 68 contract tests pass. Strict typing
for Python 3.11/3.12, Ruff/format, the unchanged maintainability budgets, repository
hygiene and focused Bandit CI severity/confidence checks pass. The original
54-cell additive closeout, 14 controls, 42-cell retention premise, 48-cell binding
study and 36-cell HTTP study all replay read-only in the declared native dependency
environment; public signatures require its optional provenance dependencies.

## Incident audit obligations: runtime development and inference transfer

Audit service concerns the lifecycle of evidence, not just a correct live
resolver. This extension executes prospective reservations and later incident
queries through a persistent SQLite WAL/FULL archive. It reuses the request-model
resolver and ordinary shared-dependency compaction; it introduces no new checker,
LLM, cryptographic attestation or admitted retention algorithm.

### Source boundary and predictions

The development source is ONNX Runtime 1.23.2's maintainer-authored
[session initializer override test](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/test/python/onnxruntime_test_python.py#L1162-L1185).
Two legal CPU sessions share the same 130-byte graph but have different effective
initializers. A graph-file digest alone therefore does not select an effective
session. Complete native session/caller history already answers the model-use
question under the fixed trusted source assumptions and receives full credit.
The study's request routing, omissions, incident timetable and budgets are
authored controls, not observed operator incidents.

Predictions separate repairs: routing to the requested session corrects model-use
violations, including an equal-zero-output case; capturing a missing association
changes audit availability but not execution; retention/refetch of incomplete
capture cannot invent the association. Pinning later cannot resurrect discarded
evidence without a declared recovery store. Failed native calls remain unknown,
not fulfilled conclusive audits.

The archive compares compact static, incremental union, dependency-safe LRU and
size/cost selection with the same leases, bounded future-growth reservations,
dependency unions and independent drain operation. Logical quotas are 4/16/64 KiB,
with a provisioned reference and an explicitly charged 256 KiB secondary tier.
These are development allocations, not operator resource limits. Four KiB cannot
fit a 4 KiB future-frame reservation plus its bookkeeping; resulting refusals are
structural feasibility, not evidence of a new algorithm opportunity.

The quota bounds the complete compressed state and pending growth, not physical
SQLite pages, WAL/SHM, RSS or arbitrary future demand. Every offered audit is
counted, including refused, missing, failed, late and admitted-but-unserved cases.
Recovery restores the whole requested union atomically; it does not read the
private ground-truth journal or install optional records one at a time and evict
them before accepting the obligation. Post-commit telemetry failure does not
retroactively roll back a durable acknowledgement.

### Executed development results

The original development pass and a separate post-result refinement are retained.
The refinement separates capture omission from native failure and adds an ordinary
no-eviction fast path; it is not held-out validation. Each pass offers nine fresh
observer processes and three native-only controls: 288 instrumented calls plus
96 control calls. The refined census has no failed process, nine native failures,
273 correct captured conclusions, six capture-unknown conclusions and zero false
conclusions. Requests and process repetitions are nested within one source family.

| Refined source arm, 96 calls each | Model-use violations | Correct captured conclusions | Capture unknown | Native failures |
| --- | ---: | ---: | ---: | ---: |
| Observed routing | 12 | 90 | 3 | 3 |
| Route repair | 0 | 90 | 3 | 3 |
| Complete capture | 12 | 93 | 0 | 3 |

Native source/history identifies all 93 conclusive calls in each arm. Its
verdict history is not automatically the same as recovering a retained linked
input/output/session/closure witness after an incident.

Across the refined full-reference archives, 90 distinct offered audits are
admitted: **57 are complete correct and 33 admitted-but-unserved**, with zero
wrong answers. Twenty-seven unserved windows contain the native failed call;
six additional windows contain only a missing capture. Complete capture recovers
three complete audits, but does not repair native failure. Admission here promises
bounded retention, not that a producer will successfully execute and capture every
call. Reporting only accepted counts or treating unknown as fulfilled would hide
this distinction.

At 16 KiB, static/LRU serve the same 57 complete-correct audits, but refuse nine
offered obligations and do not retain the full reference frontier. Equal correct
coverage is not equal service. At 64 KiB, ordinary candidates match every reference
query answer, admission and measured response-budget disposition. The lowest
measured closed database allocation among these candidates is **49,152 bytes**,
with median audit-query times about 7.82–7.86 ms. This is a closed-storage minimum
over tested candidates, not a global cost optimum or a guarantee that all audits
are conclusive. Secondary recovery is charged its full measured store allocation
and per-query retrieval; it does not establish a same-budget method advantage.

The 50 ms query-response cutoff and 1/10/50/100 ms sensitivity values are declared
development budgets. Producer ticks define arrival ordering and are not elapsed
wall time. Repetitions do not establish production SLOs, concurrent throughput or
population confidence intervals. The common 37-archive observer workload cannot
be used to estimate each policy's causal serving overhead. Independent replay
checks 3,330 query frontiers and 342 closed databases. Eight abrupt process-exit
controls recover the expected pre-commit or post-acknowledgement state; this is not
a power-loss, hostile-host or distributed-concurrency guarantee.

### Transfer to an externally authored inference dependency workflow

ONNX 1.19.1's original
[ModelContainer reference-evaluator tests](https://github.com/onnx/onnx/blob/b751946c3d59a3c8358abcc0569b59e6ddb08cdd/onnx/test/model_container_refeval_test.py)
provide a distinct source-informed execution family. The installed release-wheel
test is pinned by SHA-256; its Apache-2.0 source, helpers, assertions and method
bodies are unchanged. One-file and multi-file layouts are related comparators,
not two independent deployments. Source bodies/expected values were inspected
before inference; this is not untouched source validation.

Eight cells compare lawful behavior, replacement of the owned external A tensor,
restoration before load, and omission of one captured association. The initial
adapter incorrectly required an explicit external-data length; multi-file ONNX
legally omits it. All four resulting failed workers and the initial successful
one-file cells remain preserved. A separate technical correction uses the full
file span when the length is omitted; it does not regain unused-outcome status.
Each failed multi-file worker had already executed its first upstream inference,
but its partial per-call rows were not retained. The initial eight one-file call
receipts are not the total original native-call census; failed-process streams
cannot reconstruct the missing outputs or timings. This limitation is retained,
not filled with guessed records.
The correction's inherited `unused` metadata is explicitly superseded by an
additive closeout; no original receipt is resealed.

The corrected eight-cell census executes **19 of 20 planned native inferences**.
The two dependency-replacement cells retain the original upstream AssertionError;
the multi-file method stops before its third inference, which remains unexecuted.
Replacement changes the loaded dependency while the saved graph file remains
unchanged. Restoring the dependency restores the original assertions in both
layouts. Missing capture leaves native assertions correct but two linked-use
audits unknown. Static and union archives recover the same 17 correct and two
unknown answers, with zero false conclusions.

Owned native-file receipts preserve the original weights and separately record
the replacement `.changed` bytes with their before/changed/final hashes. Replaying
the original filename alone reproduces lawful behavior, not the fault. A fault
replay must apply the recorded replacement at the recorded post-save boundary;
read-only verification checks that mutation mapping without re-executing it.

Saved external-data graphs lack A/C payloads, but the effective post-load
ModelProto embeds those tensors. Capturing that effective native object is already
a sufficient strong baseline for the fixed numerical question. Source history can
also reconstruct this deterministic fixture. This distinction rules out claiming
that all graph-only observation is insufficient or that a new provenance method
outperforms ordinary native evidence. The earlier four external-data serialization
qualification cells have no inference outputs and are not counted as this transfer.

### Separate component cost probe

A further seed-ordered 12-process probe uses the identical native call path in
all four modes, 16 calls each: native-only, hash-only, uncompressed per-request
capture and compact static. All 192 calls are retained, including 12 invalid-shape
failures. Capture and compact each retain 45 correct conclusive answers and three
native-failure unknowns; hash-only does not claim linked-use witness service.

| Component median, microseconds per request | Hash-only | Per-request capture | Compact static |
| --- | ---: | ---: | ---: |
| Input/output hashing | 4.813 | 6.209 | 7.334 |
| Frame construction/resolve | — | 30.084 | 32.042 |
| Write and acknowledgement | — | 108.792 | 1,639.250 |
| Read-only query | — | 33.479 | 160.063 |
| Reconstruction verification | — | 7.729 | 69.709 |
| Closed database bytes | 0 | 28,672 | 24,576 |
| Peak database + WAL + SHM bytes | 0 | 209,936 | 267,616 |

Ordinary compaction saves 4 KiB of closed storage here, but is slower and has a
higher measured persistence peak. Whole-state rewriting is a cost of this bounded
implementation, not inherent to all compact storage. The measurements do not
support efficiency dominance or a new retention algorithm. Native setup, shared
numerical checking and raw receipts are disclosed; component stages are distinct
and not added to the combined-workload timers. Tiny native fixtures, three nested
process repeats, no operational arrival distribution, no IO-operation counters
and no operator SLO forbid a production/minimum-cost claim.

The result strengthens conditional evidence/repair boundaries and exposes the
difference between live correctness, retained witnesses and promised audit
service. It narrows the method claim: **ordinary baselines suffice for the tested
full captured service; no separate algorithmic contribution is demonstrated**.
Natural deployment/incident evidence remains unavailable. Independently justified
operational demand/resource/deadline evaluation remains open; adding framework
counts or tuning budgets until a policy wins does not close it.

The opt-in incident development, transfer and cost commands create private stores
outside the public repository. Exact execution implementations are archived before
running. Read-only reproduction uses those `code-snapshot` roots; publication
helper refactors, historical nomination-label updates and additive analysis do
not substitute for the executed bytes. Current
local checks do not imply commit, push, merge or remote Windows CI success.
