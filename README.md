# Aletheia Lab

[![CI](https://github.com/umynameislove/aletheia-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/umynameislove/aletheia-lab/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/umynameislove/aletheia-lab)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Aletheia Lab is a research framework for **evidence-grounded failure diagnosis
and meta-faithfulness evaluation of ML/LLM systems**. It injects controlled
faults into a model pipeline, records the observable evidence, asks a diagnosis
model to name the root cause, and then measures whether that explanation is
actually grounded in the evidence, correct about the true cause, and willing to
abstain when the evidence is thin.

The framework is built **eval-first**: a single narrow slice runs end to end
before the benchmark, memory layer, agent, or dashboard are widened. Ground-truth
causes are held strictly apart from the evidence a diagnoser is allowed to see,
so a case tests inference rather than recall of an answer key.

---

## The question

When an ML/LLM system fails and an assistant explains why, three things can come
apart:

- whether the explanation is **grounded** in the shown evidence,
- whether it is **correct** about the true cause,
- whether the model **abstains** instead of guessing when evidence is insufficient.

A diagnosis can be faithfully grounded in the evidence and still wrong about the
cause, or right for the wrong reasons. Aletheia measures that gap directly.

```text
injected failure case
  -> observable evidence bundle (metrics, configs, distribution signals)
  -> diagnosis under one fixed variant (plain LLM / RAG / evidence-bound / full)
  -> scored for correctness, faithfulness, and abstention
  -> divergence between groundedness and correctness
```

The primary signal is the **groundedness–correctness divergence**: correctness
alone rewards lucky guesses, and faithfulness alone rewards confident,
well-cited errors. Reporting the two together — and their divergence — is what
keeps the evaluation from being gamed by either.

---

## Design commitments

- No platform before the loop runs. The eval-first slice comes first; benchmark,
  memory, and UI are widened only after it measures something real.
- The LLM judge is never the only measure of truth. A human-audited subset with
  Cohen's kappa guards against a circular "LLM grades LLM" result.
- Ground-truth causes never enter the evidence a diagnoser can see. Injectors
  separate the hidden answer key from the observable signals; a leakage guard
  enforces it.
- "Grounded in the evidence" and "correct about the cause" are kept distinct and
  scored separately.
- CrossRoute stays a separate method. Aletheia reuses its faithfulness-audit
  ideas without absorbing the paper.

---

## Status

Eval-first skeleton, `0.1.0`. The P1 `data_drift` slice is implemented and
tested; the data contracts for later phases are defined; the diagnosis and
evaluation runtimes are contract-first scaffolding, not yet a full end-to-end
loop over real cases.

| Phase | Goal | Required output | Status |
|---|---|---|---|
| P1 | Eval-first vertical slice | 15 `data_drift` cases running end to end | In progress |
| P2 | Benchmark + evidence layer | 120–200 cases with a clean schema | Contracts drafted |
| P3 | Memory + lineage | failure objects, run lineage, reproducibility | Not started |
| P4 | Diagnosis variants | 4 variants running over the benchmark | Not started |
| P5 | Meta-faithfulness evaluation | correctness / faithfulness / abstention + ablation | Not started |
| P6 | Human study + release | Cohen's kappa, report, artifact release | Not started |

**Implemented and covered by tests**

- `benchmark/injectors.py` — `CategoricalDriftInjector`: deterministic given a
  seed, one-factor, with the hidden `ground_truth` kept separate from the
  evidence-safe `signals`.
- `benchmark/signals.py` — categorical distribution and Population Stability
  Index (PSI); pure and deterministic.
- `benchmark/manifest.py` — `BenchmarkCase` / `GroundTruth` schema and loader.
- `evidence/schema.py` — `EvidenceBundle` with `allowed` / `withheld` /
  `counterfactual` evidence and a `leakage_check_passed` flag.
- `evidence/leakage.py` — answer-key leakage scan over visible evidence.
- `evaluation/` — metric helpers for correctness, faithfulness (claim-support
  ratio), abstention, agreement, and small stats utilities; a `JudgeResult`
  contract shared by LLM, rule-based, and human judges.
- `cli.py` — `plan`, `validate-case`, `leakage-check`, `score-example`.

**Contract-first scaffolding (intentional placeholders)**

- `diagnosis/runner.py` ships a conservative placeholder that abstains; it is
  replaced with concrete LLM/local-model integrations once the benchmark and
  evaluator are stable. `DiagnosisOutput`, the variant enum, and the prompt
  contracts are already fixed so later work only fills in the runtime.

---

## Install

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Optional extras: `.[llm]` (OpenAI / LiteLLM diagnosis backends),
`.[dashboard]` (Streamlit + Plotly). Copy `.env.example` to `.env` for local
config (`ALETHEIA_CONFIG`, optional provider keys); real secrets are never
committed.

## Quickstart

```bash
pytest                    # 13 tests: injector, leakage guard, schema, metrics, smoke
aletheia --help
aletheia plan             # summarize the active project config
aletheia validate-case tests/fixtures/minimal_case.json
make check                # ruff lint + repo-hygiene guard + tests
```

`make slice` runs the P1 vertical-slice entrypoint against
`configs/project.yaml`.

---

## Repository layout

```text
src/aletheia_lab/
  benchmark/     fault injectors, distribution signals (PSI), case manifest
  evidence/      evidence schema, JSON store, answer-key leakage guard
  diagnosis/     variant enum, prompt + output contracts, placeholder runner
  evaluation/    correctness / faithfulness / abstention / agreement / stats + judge contract
  reporting/     result tables and plots
  cli.py         Typer CLI (plan, validate-case, leakage-check, score-example)
  config.py      YAML config loader

configs/         project.yaml + benchmark/ and evaluation/ specs
docs/            technical specs (02–04) and adr/ decision records
examples/        worked data_drift case (evidence bundle + failure summary)
scripts/         vertical-slice entrypoint + repo-hygiene guard
tests/           unit + integration
```

Planning, roadmap, and tracking material live in a separate folder outside this
repository; `scripts/check_repo_hygiene.py` (wired into `make hygiene`,
pre-commit, and CI) fails the build if any of it leaks back in.

---

## Evaluation design

Every case is diagnosed under four variants, held to the same output contract so
the scores stay comparable:

| Variant | Evidence access |
|---|---|
| `plain_llm` | question only, no structured evidence |
| `rag_baseline` | retrieved passages, no grounding constraint |
| `evidence_bound` | must cite the allowed evidence bundle |
| `full_aletheia` | evidence-bound + counterfactual + abstention discipline |

Four metrics are reported per variant — **correctness**, **faithfulness**,
**abstention**, and the **divergence** between groundedness and correctness — over
a target of 200 controlled cases plus 10–20 organic failures for external
validity. A 20% subset is human-audited and agreement is reported as Cohen's
kappa. Config guardrails require a reproducible case manifest, a counterfactual
evidence condition, and error analysis before any publishable claim; stretch
case counts (240–300) are only pursued after the main evaluation and human audit
are done.

No result tables are published yet — the diagnosis runtime is still scaffolding.
This section describes the measurement design, not measured outcomes.

## Benchmark and data

The P1 slice uses the IBM sample **Telco Customer Churn** dataset (~7,043 rows,
target `Churn`), with `Contract` as the drift feature. The injector is
dataset-agnostic; switching datasets changes only the `dataset` block in
`configs/project.yaml`. **UCI Bank Marketing** (CC BY 4.0) is kept as a
clean-license alternative if a formally open dataset is required. Raw and derived
data are not tracked in git.

## Development

```bash
make install      # editable install with dev extras
make lint         # ruff check
make hygiene      # repo-hygiene guard (code vs. tracking separation)
make test         # pytest
make check        # lint + hygiene + test
```

CI runs lint, the hygiene guard, and the test suite on Python 3.11. `pre-commit`
runs ruff (lint + format) and the repo-hygiene guard locally; strict `mypy` is
configured in `pyproject.toml`.

## Related projects

Aletheia reuses infrastructure and methodology from three companion repositories:

- [`projmem`](https://github.com/umynameislove/projmem) — local-first experiment
  memory and run lineage.
- [`crossroute-audit`](https://github.com/umynameislove/crossroute-audit) —
  explanation-faithfulness auditing via causal interventions.
- [`FactoryLens`](https://github.com/umynameislove/FactoryLens) — a bounded
  diagnosis agent used as an applied case study.

## License

MIT. See [LICENSE](LICENSE). If you use Aletheia Lab, please cite it via
[CITATION.cff](CITATION.cff).
