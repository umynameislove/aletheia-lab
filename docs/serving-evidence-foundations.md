# Serving evidence foundations: development results and remaining validation

This is a methods/result checkpoint, not a manuscript or a claim of journal
acceptance. Historical studies and their receipts are unchanged. Model weights,
raw receipts, human labels and local research packets are not repository inputs.

## Question and evidence model

For a declared audit query, distinguish artifact authenticity, resident state,
request-to-use association, dependency closure, numerical behavior, persistence,
and timely fulfilled service. Evidence can be adequate for one and unresolved
for another. A correct scalar output is not a general state fingerprint.

Adequate means a conclusive correct answer against the declared reference under
the stated assumptions. Unknown, conflict, incorrect, and late answers remain
distinct. An evaluator returning unknown does not establish information-theoretic
insufficiency; an observation-equivalent pair with different query answers can.
This determinacy principle is established observability reasoning, not a new theorem.

## Bounded public source collection

Before the main retrieval, fix five projects, the 2019-01-01 to 2026-10-07 window,
title terms, ordering, and one-page caps: 20 merged PRs and 10 issues per project.
The 150 returned records produce 40 provisional systematic families. Four
previously exposed purposive supplements give 44 provisional families total:
31 classified as confirmed mechanisms and 13 unresolved symptoms. Followups and
backports of a common cause are grouped, not counted as additional incidents.

| Project | Provisional families | Provisionally confirmed |
| --- | ---: | ---: |
| MLServer | 11 | 5 |
| KServe | 9 | 8 |
| Ray Serve | 8 | 6 |
| BentoML | 9 | 6 |
| TorchServe | 7 | 6 |
| Total | 44 | 31 |

These are source-screening drafts, not a human-validated taxonomy, representative
sample or production prevalence estimate. Subsequent supplied human coding covers
42/44 population rows and all 11 preassigned second-coder rows. Two unpaired rows
remain missing, not imputed as unresolved. Pre-adjudication primary-boundary
agreement is 5/11 (45.45%), Cohen's kappa 0.346535; source-tier agreement is 10/11
(90.91%), kappa 0.792453. Joint agreement is 4/11, with seven disputed families.
The full-population completion gate remains unmet; paired reliability is available
without dropping any assigned pair. Human/qualification/independence are declared,
not independently authenticated. This does not yet establish a stable boundary
taxonomy; unresolved adjudication and missingness remain explicit. Raw submissions
and derived private provenance are not repository inputs.
Selection uses canonical JSON `[salt, family_id]` digests; source IDs remain
opaque and unchanged. The search is deliberately bounded/recent-title biased.

## Official signing and integrated ordinary baseline

The optional development dependency is **model-signing 1.1.1**, from
[model-transparency](https://github.com/sigstore/model-transparency). Local EC
keys exercise its actual file-serialization signing and verification API.
Sign the declared full directory closure with SHA-256, no symlinks, one worker;
reject unsigned extra files and instantiate a fresh verifier per control.
Disable default Git-path exclusions when signing; after official verification,
require the signed resource census and digests to equal the declared closure.
A cryptographically valid partial signature is not a full-closure success.
Private keys are ephemeral; public verification material is retained outside
the signed directory. This is not a keyless OIDC/Fulcio/Rekor experiment.

Eleven actual toy-ONNX crypto controls matched their declared expectations,
including valid updates, tampering, missing signatures, wrong keys and unsigned
files. A valid signature for B does not satisfy an expected-A signature check.
Signing the graph alone misses changes to an external weight file; signing the
full closure catches them. A valid full B closure on disk can coexist with an
already resident A session. Artifact authenticity then remains true while a
request-specific resident-B enrollment query is false. This is a bounded
counterexample, not an impossibility claim about all provenance/signing systems.

The integrated ordinary baseline includes the expected state, actual resident
witness, association and closure for the scalar query. It and the candidate use
the same resolver and answer identically. Do not claim checker superiority or
that generic in-toto cannot express request binding.

## Two source-pinned native development families

Execute unchanged source at the causal boundary with a documented compatibility
bridge: MLServer 1.7.1, legacy Pydantic settings, in-process worker transport.
This is not the complete historical deployment or concurrent throughput.

| Source/condition | Affected | Fixed | Repair observation |
| --- | --- | --- | --- |
| [MLServer #581](https://github.com/SeldonIO/MLServer/pull/581), registry reload | Load/load/unload leaves next worker request `ModelNotFound` | Load/load serves updated value 10 | Native worker load restores future request |
| [MLServer #705](https://github.com/SeldonIO/MLServer/pull/705), top module | Disk update to 10 still serves resident 1 | Updated top module serves 10 | Fixed parser reload restores top module |
| Same #705, authored helper-dependency probe | Resident helper remains 1 | Resident helper still remains 1 | Reloading only top module fails; clearing/reloading the relevant generated module closure restores 10 |

Six conditions completed in the integrated run. Four updated disk closures
verify with the official signer, including the three conditions still serving
old scalar state. The integrated baseline identifies those three violations;
the remaining fixed top-module condition is compliant. No additional public
family is counted for the authored helper variation.

Component repair does not always restore the predicate and single development
timings do not establish speed superiority. These outcomes challenge the broad
claim that a smaller reload always fixes a smaller dependency or is faster.
Future repair does not retrospectively certify a past request.

## Large native ONNX qualification and mini workload

[TorchVision ResNet-101 V2](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.resnet101.html)
is a preparation/reference model, exported with Torch 2.9.1 / TorchVision 0.24.1
to ONNX opset 17 and served by ORT 1.23.2 CPU InferenceSession. A pinned official
checkpoint is loaded with `weights_only=True`; no untrusted pickle is accepted.
The ONNX graph and referenced external weights total **178,047,016 B (169.799 MiB)**.
Every serialized initializer is reachable from graph outputs; no padding is counted.

Three declared inputs compare entire finite 1x1000 Torch/ORT vectors with locked
`rtol=1e-4`, `atol=1e-5`: 3/3 pass, maximum absolute errors 8.881e-6, 2.384e-6 and
3.934e-6. This is finite export consistency, not universal numerical correctness.
The full graph/weight closure is signed and subsequently reverified read-only.

The mini workload performs two actual session loads and four actual inference
calls (cold, two reuse calls, fresh reload), retained SQLite WAL/FULL receipts
and delayed queries. Four offered/accepted audits complete correctly and on time;
no unknown, incorrect, late or accepted-but-unserved case in this small workload.
The 100 ms delay and 30 s deadline are authored sensitivities, not operator SLOs.

| Measured stage | Development observation |
| --- | --- |
| Two session loads | 14.620 / 14.206 ms |
| Identity hashing, once per load | 83.954 / 86.711 ms |
| Signature verification including its hash | 84.264 / 85.380 ms |
| Four inference calls | 193.014 / 189.427 / 187.140 / 186.088 ms |
| Receipt commit acknowledgement | 0.168–0.195 ms per call |
| Delayed query plus output-digest check | 0.163–0.198 ms per call |

Hash is nested in signature verification: do not add them to claim overhead.
The process RSS high-water mark is about 1.27 GB and includes preparation/export;
it is not instantaneous serving RSS. Open DB/WAL/SHM and closed DB differ. All
model copies, journals and recovery material must be charged in a final comparison.
This is one development process, not a matched frontier or a 44% serving speedup.
Pretrained-weight redistribution rights remain unresolved: do not publish weights.

## Reproducibility and verification boundary

`scripts/serving_native_development.py run --sources SOURCE_CACHE --output NEW_DIR`
requires exact upstream file pins and refuses overwrite. `verify` rechecks report
census, retained closure and actual public-key signatures without native execution.
It is a consistency/crypto check, not independent authentication of historic capture.
Source execution rechecks the fixed pins at the executable boundary. These checks
assume an owned source cache without a concurrent malicious writer, not hostile-host
attestation. Post-result scope/pin guards are engineering changes, not a rerun of
the original development outcomes.

`scripts/review_serving_large_model.py --study-dir RETAINED_DIR` reads the original
full numerical arrays, checkpoint/closure hashes, public signature, closed SQLite
joins and service census. It also checks mini-workload outputs against saved Torch
reference vectors. It downloads nothing, loads no model and creates no SQLite
sidecars. Exact executed preparation scripts/specs remain with private artifacts.

`scripts/serving_corpus_review.py` accepts two locked human submissions and writes
agreement/confusion/disagreements exclusively to a new file. It never generates
human labels or performs adjudication. Ordinary CI uses dependency-light contract
tests; actual native/crypto/model executions are explicitly separate evidence.

For the separated full-population/paired packets, supply `--population-packet`
for the 44-family source packet and `--packet` for the locked 11-family packet.
Each original submission is validated against its own hash/complete assignment;
sources and ontology must agree on the subset. Agreement uses only the 11 paired
families, retains original submission hashes and does not adjudicate differences.
The remaining 33 population labels never inflate the paired denominator.

## Source-qualified supporting transfer

Two reserved source families have now executed under fixed code/reference seals:
[BentoML #2469](https://github.com/bentoml/BentoML/pull/2469) and
[TorchServe #2566](https://github.com/pytorch/serve/pull/2566). Their exact accepted
commits and affected parents have been checked. Their scope is narrower than a
natural deployment: Bento's fix is on the runners-1.0 development branch;
TorchServe's fix changes the KServe gRPC response conversion, not model binding.
The comparison selects unchanged causal methods and pinned native converters,
with local transport. Complete historical servers were not deployed. Inputs,
query contracts and fault-free transport are authored and source-informed.
These sources must not be used to claim unseen model-identity validation.

| Family | Conditions per version | Affected conformance | Fixed conformance |
| --- | ---: | ---: | ---: |
| BentoML #2469 | 10 | 2 | 8 |
| TorchServe #2566 | 48 | 12 | 48 |

Bento's eight mapping/batch-member predicates all pass in the fixed source;
the fixed sync and async local-runner controls both raise TypeError. Affected
sync passes, affected async fails. The source-visible bound-method/self behavior
is retained without an adapter correction. This is not a newly established
production bug. TorchServe's affected gRPC postprocess route already conforms
in all 12 conditions; direct, JSON-dict and no-header routes do not. Fixed source
conforms in all 48. REST checks concern the returned dictionary before JSON wire
serialization, not an HTTP server. Every required core tensor field is checked.

The complete-history ordinary reference answers all 116 declared protocol
queries, including failures. Signature-only answers to these runtime predicates
are unresolved by the declared evaluator; this is not an impossibility proof
about all signing integrations. The candidate receives no exclusive witness.
Native component repair was not qualified; changing an AST is not a repair API.

## Matched large-model audit cost

The qualified 169.799 MiB ONNX closure is reused without a new export. Fifty
fixed fresh-process workers complete: two patterns, five rotating paired blocks,
five arms. There are **3,200 serving inference calls plus 90 recovery calls**.
The 30 audit workers offer, accept and fulfill **1,920/1,920** obligations;
unknown, conflict, incorrect, late, refusal and accepted-but-unserved are zero
in this declared envelope. Native and hash-only offer no audit service and are
not admitted to the same-service frontier.

All audit stores use binary vectors, SQLite WAL/FULL, batching eight requests,
the same fsynced acknowledgement journal and a common signed recovery tier.
Static amortizes generation identity; compact additionally deduplicates immutable
vectors; full retains generation information with each request. Recovery reloads
the signed model and executes the three retained unique inputs before audit
fulfillment. Caller/load evidence assumes an honest owned host, not attestation.

| Pattern / arm | Median workload through final ACK (s) | Median complete service (s) | Closed collector + caller files (MiB) | Including common recovery (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Reuse / static sufficient | 12.122 | 14.013 | 37.134 | 208.669 |
| Reuse / incremental compact | 12.275 | 14.174 | 1.821 | 173.356 |
| Reuse / full retention | 12.343 | 14.247 | 37.173 | 208.708 |
| Reload / static sufficient | 13.819 | 15.703 | 37.145 | 208.680 |
| Reload / incremental compact | 13.683 | 15.591 | 1.832 | 173.368 |
| Reload / full retention | 13.871 | 15.759 | 37.176 | 208.711 |

Complete service includes the authored one-second audit delay, retrieval and
native recovery. It is not serving latency per request. The operative cost
forecast used the median of five paired full-minus-compact workload differences,
not a subtraction of marginal medians: reuse +6.549 ms; reload +91.001 ms.
Both contradict the fixed forecast that full is no slower. Reuse has mixed signs
and a tiny difference; no robust or statistically significant speed advantage
is claimed. Static is fastest in reuse; compact has the lowest measured workload
and storage in reload. Full has the lowest median RSS in reload. No arm dominates
all reported dimensions. Ordinary deduplication is not a new retention algorithm.

Compact saves about 95.1% of collector/caller bytes versus full, but only about
16.9% including the 179,867,865 B shared recovery tier. All copies, inputs,
numerical-reference vectors, public signature/key and caller journals are charged.
Storage includes closed files, not only DB size. Per-file DB/WAL/SHM samples,
allocated peaks, load/inference/capture/write/ACK/retrieval/query/verify timings
and fresh-child RSS are retained. These are sampled peaks, not exhaustive physical
device accounting. RSS is process high-water, not instantaneous serving memory.

Signature timing includes closure hashing and crypto verification. The planned
nested hashing timer was not instrumented in the audit arms: only the separate
hash-only floor is available. Do not subtract that floor as an exact crypto-only
cost or add it to signature timing. This limits decomposition, not the measured
matched-service elapsed comparison. No sealed outcome or analysis was amended.

## Scientific disposition and remaining inputs

Keep the conditional distinction between artifact authenticity, actual-use
association, complete declared closure and fulfilled timely service. Keep the
source-specific repair limits and the finite large-model frontier. Narrow transfer
to the actual batching/response predicates; exclude general deployment validity,
arbitrary dependency completeness, hostile-host capture, power-loss guarantees
and global optimality. Component-repair superiority is unidentified in the final
source slice, not supported by its fixed-version conformance.

Both sealed result sets pass read-only reconstruction/consistency checks without
rerunning native outcomes. These checks are not independent authentication of
historical capture. Human coding has since supplied42/44 first labels and all11
paired labels; the reliability figures and unresolved taxonomy are stated above.
The technical comparison is complete within the stated domain; it does not
fulfill a natural-deployment or operator-grounded demand claim. A separate novel
retention/admission contribution is not established; retain ordinary baselines.
No manuscript, paid API, publication or historical study rerun was performed.

## New store-ownership identity transfer

[BentoML #5223](https://github.com/bentoml/BentoML/pull/5223) fixes restoration of
the prior global model store after successful packaged-service import. Full
unchanged affected/fixed loader modules run with native Bento1.4.0 stores,
deserialization, SDK service construction and sklearn predictor use. Installed
loader bytes match the accepted source; other dependencies are qualified
compatibility versions, not a full historical deployment.

This ownership mechanism was not used to build the earlier resolver. The
framework, corpus mechanism and patch were exposed. It is one source-informed,
outcome-held-out implementation/lifecycle transfer, not blind discovery or a
natural deployment. Two correlated version trajectories contain eight requests
each. Small owned A/B models share a store-scoped tag and equal probe output,
while actual state and immutable signed model closures differ.

All sixteen locked identity predictions and six named repair predicates match.
Affected has three compliant/five violation uses; fixed has six/two. Actual native
Model-A loading restores the held consumer but not future tag routing. Package-A
reimport with the fixed loader reinitializes store context; Python service module
remains cached, so this is not module hot reload/full restart. Later wrong-context
tag reload remains wrong even with fixed source. No repair-speed gain is claimed.

Reference independently reads caller state immediately before invocation and
owned retained pickle state, joined to actual load/use object IDs. The trusted
serial consumer then invokes that backend; it is not hardware/backend-entry
attestation. Both full model-directory closures are actually verified with the
official signer. Complete ordinary native/history, signing-integrated history
and candidate all answer16/16 correctly on the same evidence. Missing use/load
gives unknown; conflicting use gives conflict, separately authored interventions.
Two expected import failures retain native errors and store rollback.

The exact expected signed model-directory closure/tag/input/output projection
matches while actual-use truth differs. This proves insufficiency only of that
projection, not all signing integrations or richer selected-artifact/history
views. Code/source/model/environment/forecast seals preceded selected execution;
raw-pickle/generation/read-only/crypto checks pass without inference replay.

## Paired overhead relative to the native application floor

Read-only supplementary analysis uses all fifty retained large-model workers,
without new cost execution. It pairs arm−native within pattern/block, then takes
the median of five ratios, not a ratio of marginal medians.

| Pattern | Static sufficient | Incremental compact | Full retention |
| --- | ---: | ---: | ---: |
| Reuse | +4.474% | +4.282% | +4.339% |
| Reload | +18.598% | +17.706% | +19.132% |

These are workload-through-final-ACK contrasts. Native supplies no audit service;
this is added-service cost, not a same-service winner or causal production
estimate. Capture/write around one percent is not total overhead. Delay/recovery,
request medians and nested signature/hash timing stay distinct; five same-host
blocks are descriptive and fresh process does not clear OS page cache.

## Current-stable native operational reload supplement

The unchanged **MLServer1.7.1** package now also executes through its normal CLI,
file-based settings and real loopback HTTP repository/inference endpoints, not
the earlier selected source-method transport. Current stable release tag is
`1d1f3ee42f96744d809aca941ed2925347d198e9`; this is not a claim about newer
prereleases. The supported configured serial path uses `parallel_workers=0`.
All101 installed Python source files match distribution RECORD before/after.

Six inference responses and two public repository reloads return HTTP200.
The two processes exit cleanly. Source revisions and per-load UUID/PID witnesses
distinguish load completion, replacement initialization, and dependency freshness:

| Condition | Runtime offset | Loaded helper | Output for x=0 |
| --- | ---: | ---: | ---: |
| Initial | 0 | 1 | 1 |
| Helper edited to10, public reload | 0 | 1 | 1 |
| Runtime edited to100, public reload | 100 | 1 | 101 |
| Fresh process, identical final source files | 100 | 10 | 110 |

Each public reload creates a new per-load UUID within the same PID. Restart
changes PID and reads both updated sources. Bytecode writes are disabled and
edit sizes differ. Original socket-denied startup and readiness failures remain
in separate attempt records. The predecessor source archive was reconstructed
after execution and hash-matched to original snapshots; this retention recovery
does not turn capture into independent attestation.

This supports a configured **repair-scope boundary**: public model reload did
not refresh the imported helper; restart did. It is source-informed development,
not a new incident family, blind held-out discovery or a proven MLServer bug.
It does not establish a minimum repair or concurrent-worker behavior. The
[standalone example](../examples/mlserver-reload-boundary/README.md) requires no
project evaluator or observer. The [upstream reload implementation](https://github.com/SeldonIO/MLServer/blob/1d1f3ee42f96744d809aca941ed2925347d198e9/mlserver/settings.py#L60-L66)
and [Python reload contract](https://docs.python.org/3/library/importlib.html#importlib.reload)
are consistent with the mechanism; a recursive refresh promise was not established.

## Bounded public ONNX packaging exposure

Before collection, lock one recent-createdAt descending ONNX-tagged API page
(limit200), a deterministic digest-ranked30-repository sample, commit revisions,
lexicographic first2 graphs/repo,8MiB per graph and32MiB graph-attempt reservation
per execution. Use anonymous public GETs, no credentials, no graph execution and
no referenced weight downloads. Graph files themselves may contain embedded
weights and are included in the byte cap. Failure/unknown repositories stay in
the denominator; sample units are repositories, not independent deployments.

All30 selected metadata records were retrieved. Of50 selected graphs,23 were
size/budget-qualified and statically parsed;27 remained unverified. The first
attempt's23 downloads failed before retained bytes because the allowlist missed
the public HF CDN. A separately sealed technical correction reused byte-identical
frame/sample/metadata/revisions and the exact same23 choices/caps, preserving all
original failures. It is not an additional independent sample or blind study.

| Evidence category | Repository count |
| --- | ---: |
| Filename candidate: ONNX plus `.data`/`.onnx_data` sibling | 11/30 |
| Parsed graph declares EXTERNAL tensor with safe single location | 10/30 |
| Every graph parsed, no declared EXTERNAL tensor | 2/30 |
| External-dependency status still unverified | 18/30 |

Parsed files contain5,903 EXTERNAL tensor declarations with syntactically safe
single relative locations. This establishes declared dependencies, not verified
weight contents, executable validity or complete model closure. Ten positive
repositories span5 publisher namespaces;6 are from one namespace. Repeated graph
digests also occur. No population prevalence, independence-based test or confidence
interval is claimed. The11 filename candidates are10 graph-confirmed plus1 unknown;
absence of a marker is not a negative without a complete graph census.
An additive read-only metadata plausibility check resolves all5,903 locations
to17 distinct listed `(repository,path)` targets, with declared offset/length
within listed sizes and matching known tensor byte requirements. This is a
post-hoc supporting check, not a new prospective endpoint or verification of
external file content, availability or executability; no weights were fetched.

No declared signature filename marker was found in the30 listings. This means
only absence under the fixed naming convention, **not unsigned models** or
signing omission. Dependency declarations follow the [ONNX external-data specification](https://onnx.ai/onnx/repo-docs/ExternalData.html).
This supplement supports naturally published closure exposure, not resident
identity, incident frequency or operator audit demand.

`scripts/onnx_packaging_exposure.py prepare/run/verify` implements the fixed
census and read-only byte/hash/selection/tensor/resource/summary reconstruction.
Raw public snapshots and downloaded graphs stay outside the repository. The
separate reviewer makes no network call or inference. Post-result retry guards
do not change the classifier, sealed outcome or original code archive.

## Final method decision from the supplement

Retain the ordinary sufficient resolver, integrated official signer and compact
storage baseline: these supplements provide no reason to replace them with a new
checker or optimizer. Strengthen native execution fidelity, dependency classification
and claim precision instead. Paper A's defensible contribution remains empirical
query-specific evidence/repair boundaries and finite matched-service cost.
Operator-grounded demand, natural deployments, hostile-host completeness and
global optimality remain unestablished; taxonomy remains descriptive pending
human disagreement resolution. These are not silently closed by more runners.
