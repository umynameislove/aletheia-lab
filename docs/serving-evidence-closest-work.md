# Closest work and the defensible contribution boundary

Six comparison axes are: question, evidence/observation boundary, sufficiency or
error criterion, intervention, empirical substrate, and cost/assumptions.
This matrix is targeted source comparison, not an exhaustive novelty proof.
Reported results of other papers are not experiments reproduced in this project.

| Work | Question | Observation boundary | Criterion | Intervention | Substrate | Cost and assumptions |
| --- | --- | --- | --- | --- | --- | --- |
| [Log20](https://www.eecg.utoronto.ca/~yuan/papers/log20.pdf) | Which logging sites distinguish execution paths? | Selected program locations | Path ambiguity/information objective | Budgeted log placement | Software executions | Logging budget; execution-path identity is not request-model enrollment |
| [Errlog, OSDI 2012](https://www.eecg.toronto.edu/~yuan/papers/osdi12-errlog.pdf) | Capture diagnostic failure manifestations missed by logs | Exception/error checks | Failure diagnosis, controlled programmer study | Proactive inserted logging | Five-system failure corpus and software tests | Practical logging overhead; does not provide a model-state authenticity service |
| [LogGC, CCS 2013](https://www.cs.purdue.edu/homes/xyzhang/Comp/ccs13.pdf) | Remove audit events without losing forensic causality | OS events, instrumented execution/data units | Retain forensic graph utility | Reachability GC and dependency refinement | Real software audit workloads | Trusted OS/initial clean state; compaction and causal preservation are prior art |
| [Hossain et al., USENIX Security 2018](https://www.usenix.org/conference/usenixsecurity18/presentation/hossain) | Reduce forensic data while preserving dependency analysis | System audit records and dependence graph | Provably preserved backtracking/impact-analysis accuracy | Dependence-preserving event reduction | System audit data | Defined forensic tasks; do not call its guarantees merely approximate |
| [Hindsight, NSDI 2023](https://www.usenix.org/system/files/nsdi23-zhang-lei.pdf) | Obtain detailed traces after relevant events trigger collection | Buffered distributed tracepoints | Triggered retrospective tracing | Buffer plus trigger/collection | Distributed service tracing | Finite buffer/retrieval horizon; buffering is not novel here |
| [model_signing 1.1.1](https://github.com/sigstore/model-transparency) | Authenticate signed model-file content | Declared file serialization/closure | Manifest/signature verification | Signing and verification | Official implementation, exercised locally here | Local-key mode; actual-use linkage must be represented separately |
| [in-toto predicate specification](https://github.com/in-toto/attestation/blob/main/spec/v1/predicate.md) | Represent signed claims about subjects/processes | Extensible predicates | Valid signed statements plus verifier policy | Attestations and policy | Generic provenance ecosystem | Can express runtime binding; do not misrepresent it as intrinsically incapable |
| [Modelstamp, 2026 preprint](https://arxiv.org/abs/2609.01781) | Detect artifact or represented environment drift before deserialization | Artifact hash and bounded package metadata | Reference-state agreement | Preload verification, optional shared-key HMAC | Controlled drift/trust cases; 10 MiB–1 GiB benchmark | Trusted represented environment; complementary control, not publisher/actual-use proof |
| [DEMM-Bench, 2026 preprint](https://arxiv.org/abs/2606.20634) | Are records sufficient for decision-level governance properties? | Normalized adapters across evidence regimes | Property sufficiency/overclaim | Deterministic evidence degradation | Construction-oracle agent-runtime cases | Schema/trace-present baselines are weaker than a fully integrated baseline |
| [Cai et al., 2025 v2](https://arxiv.org/html/2504.04715v2) | Audit model substitution under adversarial API behavior | Text/log probabilities, activations and hardware attestation | Detection robustness and model-integrity guarantees | Compare software tests and evaluate TEE protection | API model-substitution experiments | Stronger hardware trust boundary than our honest-host hooks; not a claim that metadata alone settles identity |
| This study | Which evidence answers each serving lifecycle query? | Artifact + actual resident/use + association + persistence | Correct/unknown/conflict/incorrect and timely fulfilled service, separately | Boundary-specific future repair | Bounded source corpus with42/44 primary human labels and11 paired ratings; two MLServer source boundaries plus current-stable native CLI/API helper probe; supporting protocol and Bento ownership transfer; 169.799 MiB ORT,50 workers and1,920 fulfilled audits;30-repo public ONNX packaging sample | Honest host/hook, source-informed outcome transfer and authored service; boundary taxonomy unresolved (paired kappa0.346535); no natural/general enrollment transfer or new retention algorithm |

## What is not new

More logs need not disambiguate the relevant history; diagnostic logging, causal
joins, query-specific evidence, dependency-safe compaction, signed provenance,
buffering, and trusted-capture assumptions all have prior art. Neither a list of
frameworks nor a hash-plus-checker architecture is a contribution by itself.

## Contribution candidate supported so far

The empirical/system package makes **artifact authenticity, actual resident
enrollment, numerical behavior, and fulfilled audit service separate measurable
predicates** in serving lifecycle cases. A source-pinned reload fix can restore
the top module but leave its cached helper state unchanged; a signed updated
directory does not repair that resident dependency. The strong ordinary baseline
is credited whenever its capture/association is sufficient, rather than defeated
by withholding fields. Large ONNX execution closes the tiny-artifact feasibility
gap and supplies a finite matched-service frontier, not global optimization.

Completed transfer is limited to eligible declared queries and disclosed source
exposure. Ordinary sufficient baselines tie; retain the empirical boundary/repair
result and defer a separate algorithm paper without a demonstrated residual gap.
No claim that no one has studied evidence sufficiency, no new theorem claim,
no guaranteed journal quartile, and no baseline victory inferred from a toy.

Additive execution now supplies one new actual-use ownership mechanism:
BentoML5223 full causal modules, sixteen native predictions and six named repair
predicates. Source exposure is disclosed; ordinary native/history and actual
official-signing-integrated baseline tie the candidate. This closes one concrete
identity test, not general or natural-deployment validation. Matched large-model
cost and paired native-floor contrasts are now measured; ordinary deduplication
still does not establish a separate optimizer contribution. Family counts remain
mechanism/source units, not the number of requests or authored evidence views.

The final operational supplement demonstrates the helper-refresh contrast through
unmodified normal MLServer CLI/HTTP and includes a standalone reproducer. Public
ONNX graphs in10/30 fixed sampled repositories declare external locations;18 remain
unverified and positive repositories cluster by publisher. This is source-informed
repair-scope evidence and bounded packaging motivation, not new logging/closure
theory, signing-omission prevalence or an algorithm beating ordinary baselines.
