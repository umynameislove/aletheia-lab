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

## Comparison requirements

The related work requires the following comparison structure:

- B0 deterministic rule/statistical reference;
- B1 matched plain LLM;
- B2 generic multi-turn RAG;
- B3 pinned LogDx-CI external transfer;
- A1/A2/A3 Aletheia ablations; and
- FULL provenance-aware retrieval/conversation, reported separately when its information path differs.

LogDx-CI results remain separate from the controlled ML benchmark. The prospective Projmem case is a real-project ecological-validity study, not a replacement baseline and not part of the controlled denominator.

## Research contribution boundary

> Aletheia Lab contributes a controlled ML-failure benchmark and audit protocol that jointly binds diagnosis-visible evidence, condition-specific causal claim limits, evidence-conditioned abstention, atomic claim support, paired evidence interventions and immutable lineage. These individual constructs have prior art; the contribution is their strict integration and empirical evaluation for ML-system failure diagnosis, plus a local-first audit workflow that preserves uncertainty when hidden ground truth is unavailable.

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

The exact LogDx-CI version and adapter must be pinned before comparative evaluation.
