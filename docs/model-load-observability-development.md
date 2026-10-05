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
