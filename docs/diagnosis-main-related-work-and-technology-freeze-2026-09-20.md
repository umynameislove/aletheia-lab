# Diagnosis main study: related-work and technology freeze

## Scope and evidence standard

This review was refreshed on 2026-09-20 from primary paper pages, official
repositories, model cards, immutable revisions, release notes, and license
files. It supports the diagnosis main-study design; it is not an outcome analysis and it does not
establish novelty or state of the art. Preprints are identified as such and no
score is compared across unlike datasets, tasks, models, or truth standards.

## Closest recent work

| Work | Verified design signal | Consequence for Aletheia |
|---|---|---|
| [EviScope v1.1](https://arxiv.org/abs/2609.17081) | September 2026 preprint/accepted workshop short paper with 40 paired four-condition quartets, span-level support labels, and explicit add/remove/distract/contradict evidence interventions. It directly shows that answer accuracy can hide unsupported answering, conflict blindness, and wrong non-answer actions. | Treat paired evidence perturbation as established adjacent design space, not Aletheia's novelty. Aletheia must differentiate itself through diagnosis-specific provenance, frozen multi-path runtime comparisons, atomic claim accountability, terminal-failure retention, and the controlled/external-transfer boundary. Run a contamination/provenance audit before making novelty claims. |
| [LogDx-CI v1.2](https://github.com/eyuansu62/LogDx/releases/tag/v1.2) and its [paper](https://arxiv.org/abs/2605.28876) | Public preprint/release with 35 real CI failures and a frozen cached-evaluation path for studying how log reduction affects downstream diagnosis. | Keep it as the separate RQ6a external-transfer benchmark. Preserve its native metrics and denominator. Do not pool it with controlled ML families or describe cached evaluator replay as a fresh model rerun. |
| [OpenRCA 2.0](https://arxiv.org/abs/2606.27154) | Public preprint with 500 instances and step-wise causal-process supervision. Its reported gap between root-cause identification and verified propagation-path grounding shows that endpoint accuracy can hide evidentiary failure. | Report claim support and evidence/path grounding separately from cause correctness. Preserve the distinction between a correct answer and a supported diagnostic process. |
| [ORCA-bench](https://arxiv.org/abs/2607.28545) | Public preprint evaluating agents in production-fidelity on-call tasks with difficulty-stratified results and explicit implausible-cause behavior. | Report difficulty, confident unsupported claims, failure, and abstention rather than one undifferentiated RCA score. Do not generalize from one benchmark to broad on-call capability. |
| [DQA](https://aclanthology.org/2026.acl-industry.79/) | ACL 2026 Industry Track paper on 150 anonymized enterprise-support scenarios. Persistent diagnostic state and root-cause-level retrieval aggregation outperform its multi-turn RAG comparator under a replay protocol. | B2 and FULL must record retrieval/state/turn paths. Any memory benefit claim requires matched observable information and budget, while acknowledging the different enterprise-support domain and replay design. |
| [DiagChain](https://arxiv.org/abs/2608.03591) | Public preprint evaluating evidence-grounded attack-chain reconstruction over ordered reference steps; it reports substantial evidence-incorporation and ordering failures. | Track evidence incorporation, citation validity, contradiction, and missing-evidence behavior. Retrieval availability alone is not evidence of grounded reasoning. |
| [Beyond Fault Localization](https://arxiv.org/abs/2608.21310) | Public preprint evaluating diagnostic trajectories against curated propagation paths, with failure modes for omitted, misused, and unsupported evidence. | Preserve observable diagnostic-process evidence and avoid treating final localization as sufficient. Aletheia's atomic support analysis is complementary, not directly score-comparable. |
| [Cloud RCA failure analysis](https://arxiv.org/abs/2602.09937) | Public preprint analyzing 1,675 agent runs and multiple failure types; it reports that prompt/communication interventions reduce some errors but do not eliminate investigation failures. | Keep instrumentation, request identity, evidence closure, and terminal failures as first-class outputs. Do not present prompt design alone as a complete reliability intervention. |

## Bounded research position

Aletheia's current defensible position is narrower than “better root-cause
analysis.” It tests whether a frozen evidence contract changes atomic-claim
support and calibrated behavior under controlled evidence availability. The
primary comparison is paired within family (`B1 - A3`) over `full`,
`missing_key`, and `noisy` conditions. Human-adjudicated claim-support labels
validate the measurement instrument; they do not establish cause correctness.

The study adds a useful combination of controls—content-addressed requests,
matched information budgets, atomic support labels, retained terminal failures,
family-clustered analysis, and explicit non-pooling boundaries. The literature
above independently motivates those choices. It does not prove that the
combination is novel, superior, or generally valid.

No main-study superiority, effectiveness, or generalization claim is currently
authorized. The complete 32-family/128-context/1,024-request census and its
runtime preflight are now frozen, but the local Qwen runtime has not been
calibrated and independent methods approval is outstanding. RQ6b is excluded
from the current registration and requires a new prospective acquisition seal
before any protected case data are opened.

## Statistical and reproducibility boundary

The 32 families are a finite source census, not 32 independent draws from a
population of software failures. Repeated evidence conditions and variants are
paired within family, and the families themselves belong to six
dataset×mechanism superfamilies. Small-cluster methods can improve inference in
some regression settings ([MacKinnon, Nielsen, and Webb](https://arxiv.org/abs/2301.04527)),
but they do not manufacture a defensible superpopulation or causal estimand
from this benchmark. Accordingly, Aletheia uses a family-resampling BCa
interval only as a descriptive stability summary, reports leave-one-family and
leave-one-superfamily-out sensitivity, and makes no p-value, causal-effect, or
superpopulation-coverage claim. This follows the broader warning that cluster
structure and cluster count must be matched to the empirical design rather
than handled by a generic standard error
([cluster-robust practice guide](https://arxiv.org/abs/2205.03285)).

LLM evaluation can vary even under low-temperature or fixed-seed settings
([Blackwell et al.](https://arxiv.org/abs/2410.03492)). Therefore the fixed seed
is recorded as a repeatability control, every request and terminal result is
content-addressed, and same-seed divergence in Qwen calibration is disclosed.
The main registered run remains a one-attempt finite benchmark execution; it
does not reinterpret one sampled output per cell as the model's complete
stochastic distribution.

Publicly available benchmarks can also be represented in model training data.
Published contamination audits show that both contamination prevalence and
its metric effect vary materially across benchmark/model combinations
([Li et al., Findings of EMNLP 2024](https://aclanthology.org/2024.findings-emnlp.30/)).
Aletheia therefore records provenance and source dates, performs exact and
normalized duplicate checks across its own families, and must report possible
training-data familiarity as a validity limitation. The current checks do not
prove absence from GPT-4.1 or Qwen training data; no such claim is permitted.

## Qwen local sensitivity boundary

The secondary local sensitivity uses
[Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)
at revision `b2cff646eb4bb1d68355c01b18ae02e7cf42d120`: Apache-2.0,
30.5B total/3.3B active MoE parameters, 128 experts with eight active per token,
48 layers, a 262,144-token native context, and non-thinking-only operation. The
study caps visible context at 12,000 tokens and server context at 32,768; the
native maximum is not the experimental budget.

The primary GGUF is the official ggml-org Q8_0 artifact, pinned by repository,
revision, filename, byte count, Xet identity, and SHA-256. The Unsloth Q6_K
artifact is a predeclared operational fallback only. Its converter/source and
quantization differ, so it is not interchangeable evidence for Q8_0 and must be
identified separately if activated.

The runner is [llama.cpp v0.4.1](https://github.com/ggml-org/llama.cpp/releases/tag/v0.4.1),
commit `b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`, built with Metal explicitly
enabled. The run uses the embedded Qwen Jinja template, no supplied tools, no
context shifting, fixed resource limits, and Qwen's documented sampling
settings with a frozen seed. A seed is a repeatability control, not a guarantee
of deterministic output; development calibration must report any observed
same-seed divergence.

The Qwen comparison is only `B1` versus `A3` within Qwen over 12 families ×
three conditions = 72 requests. It is reported separately from GPT-4.1, never
pooled, and never increases the independent-family count of the primary study.
Before those 72 requests may run, the frozen calibration entrypoint must verify
the Q8 bytes, llama.cpp commit/tag/build, source and embedded template identity,
then execute six synthetic B1/A3 cells plus one same-seed replicate. Only
schema/runtime feasibility, crash/OOM/truncation, and repeatability are eligible
calibration evidence; synthetic answer quality may not select Q8 versus Q6.

## LogDx-CI reproduction boundary

The pinned LogDx-CI artifact is release v1.2 at commit
`99591c1471118c95155976346df72f520a05f100`. Code is Apache-2.0; the release
data/reports/protocol materials are CC-BY-4.0. Aletheia's completed check ran
the release evaluator over its cached diagnoses and reproduced the published
`v2/dev` result bytes exactly. Four last-decimal rounding differences on two
supplementary splits are retained explicitly rather than hidden by a post-hoc
tolerance.

The allowed wording is: “We reproduced the published v1.2 evaluator scores and
result manifest from the frozen release cached diagnoses.” This is not a fresh
provider/model run, an end-to-end independent reproduction, or independent
validation of the ground truth. The 35-case size, AI-drafted/single-author-
verified truth, historical provider-error handling, and public-preprint status
remain limitations.

## Registered downstream reporting rules

- Report final cause correctness, claim support, evidence/path grounding,
  abstention, contradiction/confident error, terminal failure, latency, tokens,
  and cost as distinct outcomes.
- Preserve raw evidence identities and citation validity; do not equate
  retrieved evidence with evidence actually used by the answer.
- Keep B0, B3, CodeGraph, FULL, Qwen, LogDx-CI, and any later prospective case
  in their declared reporting strata.
- Use component-effect language only for clean matched ablations. Describe
  composite information-path differences as composite.
- Retain null, negative, infeasible, and precision-limited outcomes.
- Describe `B1 - A3` as a registered bundle contrast. Component language is
  reserved for `B1-A1`, `A1-A2`, and `A2-A3`; even those are benchmark-local
  policy contrasts, not causal effects on a user population.
- Report possible benchmark familiarity/contamination as unresolved unless a
  model-specific audit supports a narrower statement.
- Do not use “state of the art,” “novel,” “native reproduction,” or “general
  RCA superiority” without a new like-for-like evidence review and matching
  execution record.

The safe current positioning is: **Aletheia evaluates evidence-grounded,
process-aware diagnosis under frozen information and runtime contracts, with
explicit artifact, provenance, and reproduction boundaries.**
