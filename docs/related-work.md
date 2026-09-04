# Related Work and Research Positioning

## Positioning question

Aletheia Lab is positioned through a focused research question:

> Which existing systems diagnose failures, which evaluate evidence-grounded behavior, and what remains untested when these constructs are combined for ML-system failure diagnosis?

The comparison below summarizes the closest research families used to define the evaluation boundary.

## Comparison matrix

| Work/family | Primary target | Explicit evidence boundary | Atomic claim support | Bounded causal claims | Abstention under missing evidence | Paired evidence sensitivity | Provenance/lineage | Relationship to Aletheia |
|---|---|---:|---:|---:|---:|---:|---:|---|
| MLDebugger | ML-pipeline root-cause search through provenance and reruns | Partial | No | Partial through tested configurations | No | Partial intervention | Yes | deterministic/provenance reference, not an LLM claim audit |
| D-Bot | database anomaly RCA and solution generation | Partial | Partial | Partial | Limited | No | Partial | mature LLM diagnosis prior art; different domain and evaluation boundary |
| HG-InsightLog | temporal-hypergraph log QA and context prioritization | Yes | No | No formal contract | Partial | No | Yes | retrieval/context baseline for log-heavy cases |
| LogDx-CI | CI-log root-cause diagnosis | Yes | Partial | forbidden-claim rubric | Partial | Partial/matched reducers | Partial | closest public external transfer benchmark; directly overlaps required signals and evidence spans |
| DQA | conversational IT-support diagnosis with persistent state | Yes | No | No formal atomic policy | Question gathering | No | Partial | closest conversational/RAG family; informs B2 versus FULL |
| AttributionBench/ALCE | claim–reference attribution and citation | Yes | Yes | Not a diagnosis target | No | No | No | evaluator precedent for citation validity and atomic support |
| FaithEval | contextual faithfulness under complete/incomplete/inconsistent evidence | Yes | Partial | Context-conditioned | Yes | Yes | No | direct precedent for evidence-condition intervention |
| MedEinst/RFEval | paired diagnostic/reasoning faithfulness | Yes/Partial | Partial | Task-specific | Limited | Yes | No | direct paired-intervention precedent outside ML failure diagnosis |
| Abstention literature | unanswerable/underspecified questions and selective answering | Yes | Usually no | Uncertainty-focused | Yes | Partial | No | supplies abstention, false-abstention and risk–coverage constructs |
| Provenance/observability systems | artifact/run/version traceability | Partial | No | No | No | No | Yes | supplies lineage and reproducibility architecture, not diagnosis faithfulness |
| **Aletheia Lab** | controlled ML-failure diagnosis plus real-project audit | **Yes** | **Yes** | **Yes, condition-specific** | **Yes, with missing-evidence request** | **Yes, within family** | **Yes** | tests the bounded combination under one immutable protocol |

Having more checkmarks is not itself novelty. The contribution is the controlled integration and evaluation below.

## Contribution 1 — Evidence contract

An Aletheia `EvidenceBundle` specifies stable IDs, visibility, provenance, allowed/forbidden claim strength and separate diagnosis/evaluator projections. Required-signal and evidence-span concepts already exist, especially in LogDx-CI; citation and attribution contracts also exist.

The contribution is their strict adaptation to heterogeneous ML failure artifacts and their binding to immutable case, diagnosis and evaluation records—not the invention of evidence bundles or citations in general.

## Contribution 2 — Bounded causal claim policy

Aletheia separates:

- an observed metric or distribution change;
- a comparison/association;
- a bounded causal hypothesis; and
- a strong causal conclusion.

Evidence sufficiency determines the maximum permitted claim strength. Full/noisy evidence in the current protocol supports, at most, a bounded hypothesis; missing-key evidence requires explicit uncertainty or abstention.

This is an evaluation boundary, not a causal-discovery claim. Aletheia does not claim that PSI plus metric degradation proves root cause.

## Contribution 3 — Evidence-conditioned abstention

Prior abstention work establishes that answerability and refusal require explicit evaluation. Aletheia adapts this to diagnosis by withholding decisive evidence while preserving symptoms, then measuring:

- appropriate cause non-assertion;
- overclaim;
- false abstention when evidence is sufficient; and
- whether the model asks for the missing evidence needed to proceed.

The contribution is diagnosis-specific operationalization and integration with evidence visibility, not the invention of abstention.

## Contribution 4 — Paired evidence sensitivity

Within one `case_family_id`, Aletheia changes evidence while holding the underlying incident and matched model settings fixed. It tests whether diagnosis behavior moves in the declared direction between `full`, `missing_key` and `noisy` siblings.

Counterfactual and paired faithfulness designs already exist. Aletheia's extension is to ML-system failure diagnosis with explicit family dependence, neutral secondary evidence, claim support, controls and immutable lineage.

## Contribution 5 — Joint trustworthiness analysis

A diagnosis can be correct but unsupported, supported but incorrect, both or neither. Aletheia therefore reports correctness, citation/support, abstention/overclaim and paired sensitivity separately before studying their joint behavior.

The joint protocol studies these properties without equating plausible text, a valid citation or task accuracy with faithful diagnosis.

## Contribution 6 — Intervention support before scientific admission

Experimental and causal-inference methods distinguish a failed or unsupported
manipulation from a negative outcome under a valid manipulation. Positivity and
overlap formalize the requirement that the declared intervention be supported
by the study population; manipulation-validation work likewise treats evidence
that an intervention changed its intended construct as part of construct
validity.

P2R adapts that prior logic to deterministic fault injection. Before a
registered attempt can be authorized, each mechanism direction must have enough
susceptible sealed rows to deliver the declared dose, plus a prospectively
frozen reserve. This is an instrument-validity gate, not a causal estimator and
not a claim that passing the gate proves dominant cause. The contribution is
the content-addressed, outcome-blind integration of intervention support with
mechanism admission, failure receipts and immutable benchmark lineage.

## Comparison requirements

The related work requires the following comparison structure:

- B0 deterministic rule/statistical reference;
- B1 matched plain LLM;
- B2 generic multi-turn RAG;
- B3 pinned LogDx-CI external transfer;
- A1/A2/A3 Aletheia ablations; and
- CodeGraph as a registered graph-index component result; and
- FULL provenance-aware retrieval/conversation, reported separately when its information path differs.

LogDx-CI results remain separate from the controlled ML benchmark. The prospective Projmem case is a real-project ecological-validity study, not a replacement baseline and not part of the controlled denominator.

## Model-family and version robustness

The frozen primary study uses `gpt-4.1-2025-04-14`. Provider-neutral transport
does not make model substitution scientifically neutral: model family, dated
version, endpoint behavior and runtime policy are experimental factors.

A future GPT-5.6 Terra study is therefore a separate prospective replication,
not a repair or extension of the current attempt. Its minimum registered matrix
is B1/A1/A2/A3 across the same 45 evidence contexts (180 provider-backed
requests). Effects are estimated within each model and cross-model
heterogeneity is reported without pooling calls, outputs or repeated case
families as independent observations. Confirmatory wording requires an
immutable provider snapshot; if only a moving alias is available, the result is
time-bounded exploratory. B2, CodeGraph and FULL are added only for a separate
preregistered research question.

## Research contribution boundary

> Aletheia Lab contributes a controlled ML-failure benchmark and audit protocol that jointly binds diagnosis-visible evidence, condition-specific causal claim limits, evidence-conditioned abstention, atomic claim support, paired evidence interventions and immutable lineage. These individual constructs have prior art; the contribution is their strict integration and empirical evaluation for ML-system failure diagnosis, plus a local-first audit workflow that preserves uncertainty when hidden ground truth is unavailable.

The current extension makes that boundary more falsifiable: competing mechanisms
must be symptom-matched on development data yet distinguishable by
diagnosis-visible evidence. Injection labels are candidates rather than automatic
ground truth; a mechanism requires manipulation, dominant-cause, control,
discriminability, provenance and reproduction gates. If visible evidence cannot
separate two causes, the valid target is a bounded set-valued hypothesis or
abstention rather than a forced single label.

## Claims to avoid

- “the first LLM debugging system”;
- “the first evidence-grounded diagnosis method”;
- “citations prove faithfulness”;
- “paired intervention or abstention is new”;
- “lineage edges prove causality”;
- “one pilot proves Aletheia is superior”; or
- “the dashboard or 3D graph is a scientific contribution without a dedicated evaluation.”

## Representative sources

- [Debugging Machine Learning Pipelines](https://arxiv.org/abs/2002.04640)
- [D-Bot](https://www.vldb.org/pvldb/vol17/p2514-li.pdf)
- [AttributionBench](https://aclanthology.org/2024.findings-acl.886/)
- [FaithEval](https://proceedings.iclr.cc/paper_files/paper/2025/hash/48404cd9ce03946c6b7177691f3267a1-Abstract-Conference.html)
- [Do LLMs Know When to NOT Answer?](https://aclanthology.org/2025.coling-main.627/)
- [DQA](https://aclanthology.org/2026.acl-industry.79/)
- [OpenRCA](https://openreview.net/pdf?id=M4qNIzQYpd)
- [OpenRCA 2.0](https://arxiv.org/abs/2606.27154)
- [ORCA-bench](https://arxiv.org/abs/2607.28545)
- [Who & When](https://arxiv.org/abs/2505.00212)
- [Who&When Pro](https://arxiv.org/abs/2607.09996)
- [AgentRx](https://arxiv.org/abs/2602.02475)
- [TRAJDEBUG](https://arxiv.org/abs/2608.06346)
- [AgentDebugX](https://arxiv.org/abs/2607.18754)
- [Beyond Fault Localization](https://arxiv.org/abs/2608.21310)
- [Causal Inference: What If](https://miguelhernan.org/whatifbook)
- [Diagnosing and responding to violations in the positivity assumption](https://doi.org/10.1177/0962280210386207)
- [Construct Validation of Experimental Manipulations in Social Psychology](https://pmc.ncbi.nlm.nih.gov/articles/PMC7954782/)
- [CodeGraph](https://github.com/codegraph-ai/CodeGraph) — project/system
  documentation only; self-reported capabilities are not scientific results.
- [HELM](https://crfm.stanford.edu/helm/)
- [How Is ChatGPT's Behavior Changing over Time?](https://arxiv.org/abs/2307.09009)
- [Reproducible Evaluation of Large Language Models](https://arxiv.org/abs/2405.14782)
- [The Reproducibility Crisis in LLM-based Software Engineering](https://arxiv.org/abs/2512.00651)
- [GPT-5.6 Terra model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) — product documentation used only for prospective feasibility and runtime policy.

The exact LogDx-CI version and adapter must be pinned before comparative evaluation.

## 2026 refresh and bounded novelty

Recent work already evaluates trajectory-level RCA, agent/step failure
attribution, evidence-based validation, causal graph tracing and recovery/rerun.
Therefore Aletheia does not claim to invent evidence-grounded debugging,
trajectory attribution, code graphs or closed-loop repair.

The bounded position is that the prespecified reviewed corpus did not reveal one
evaluated system that jointly combines controlled ML-failure eligibility,
diagnosis-visible evidence siblings, atomic support, evidence-conditioned
abstention, correctness-groundedness divergence, immutable project lineage and
reproducible local audit. This wording describes a scoped review result, not a
universal first.

CodeGraph may be evaluated as an optional evidence-index component. It cannot
replace matched generic RAG, claim-support evaluation, abstention gates or
provenance and reproduction checks.
