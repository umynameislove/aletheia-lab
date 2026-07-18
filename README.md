# Aletheia Lab

[![CI](https://github.com/umynameislove/aletheia-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/umynameislove/aletheia-lab/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Evidence-grounded failure diagnosis for machine learning systems.**

Aletheia Lab is an open-source evaluation framework for testing whether an AI
diagnosis is correct, supported by the available evidence, and appropriately
cautious when decisive information is missing. It turns controlled ML failures
into reproducible benchmark cases with a strict boundary between observable
evidence and the hidden answer key.

> A plausible diagnosis is not necessarily a faithful diagnosis.

## Why Aletheia

When an AI assistant explains a model failure, several properties can diverge:

- **Correctness** — did it identify the true cause?
- **Faithfulness** — do its claims follow from the evidence it was allowed to see?
- **Abstention** — does it avoid overclaiming when the evidence is insufficient?
- **Provenance** — can every claim, input, and result be traced to a reproducible artifact?

Aletheia evaluates these properties separately. This distinguishes a diagnosis
that is correct for the right reasons from a lucky guess, a well-cited mistake,
or an unsupported confident answer.

## How it works

```text
controlled fault
    -> reproducible case + hidden ground truth
    -> observable evidence under full / missing / noisy conditions
    -> diagnosis variant
    -> correctness / faithfulness / abstention evaluation
    -> auditable report
```

The framework is designed around four principles:

1. **Controlled causes.** Fault injection provides an independently known cause.
2. **Hard evidence boundaries.** Ground truth is never included in diagnosis input.
3. **Counterfactual evidence.** The same case can be tested with complete, missing,
   or distracting evidence.
4. **Reproducibility by default.** Seeds, checksums, manifests, metrics, and
   environment metadata are first-class artifacts.

## Current capabilities

Aletheia currently provides:

- deterministic acquisition and preprocessing for a tabular ML dataset;
- seeded train, validation, and test splits with leakage guards;
- a reproducible scikit-learn baseline packaged with preprocessing;
- deterministic categorical data-drift injection and PSI measurement;
- typed contracts for benchmark cases, evidence, diagnoses, and evaluations;
- deterministic collection and immutable persistence of a 15-context
  evidence matrix, with canonical hashes and structural/semantic leakage gates;
- an executable 15-context x 2-variant matched-pilot contract with identical
  observable facts, frozen budgets, raw-before-parse records, bounded retries,
  immutable manifests, and an offline deterministic adapter;
- an explicitly authorized eight-request OpenAI smoke runner, cryptographically
  bound to a recomputed no-network preflight and exact model snapshot;
- a deterministic evaluator that reports cause correctness, citation validity,
  evidence-role support, abstention/overclaim behavior, and paired-condition
  sensitivity as separate results;
- CLI commands for data preparation, baseline training, verification, and
  contract validation;
- automated linting, repository-hygiene checks, and tests in CI.

Aletheia Lab is research software, not a production incident-response system.
The repository includes the complete deterministic benchmark, evidence-store,
matched-diagnosis, and provider-preflight workflows described below. Generated
experiment artifacts remain local and are independently validated before use.

## Installation

Requirements: Python 3.11 or newer.

```bash
git clone https://github.com/umynameislove/aletheia-lab.git
cd aletheia-lab
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Quickstart

Inspect the CLI and active configuration:

```bash
aletheia --help
aletheia info --config configs/project.yaml
```

Download, verify, and preprocess the configured dataset:

```bash
make data
```

Train the deterministic baseline and verify that two independent runs agree:

```bash
make baseline
make baseline-verify
```

Generate the benchmark case matrix, then collect and independently verify its evidence
store:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark generate-p1 \
  --config configs/project.yaml --output-dir experiments/p1/cases
PYTHONPATH=src python -m aletheia_lab benchmark generate-p1-evidence \
  --cases-dir experiments/p1/cases --output-dir experiments/p1/evidence-store
PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-evidence \
  --cases-dir experiments/p1/cases --store-dir experiments/p1/evidence-store
```

The machine gate intentionally reports human review as pending until an
independent reviewer supplies an attested, hash-complete review record.

Exercise and validate the matched diagnosis runtime without making an external
API call:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark run-p1-pilot-mock \
  --store-dir experiments/p1/evidence-store \
  --output-dir experiments/p1/outputs/matched-pilot-mock
PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-pilot \
  --store-dir experiments/p1/evidence-store \
  --output-dir experiments/p1/outputs/matched-pilot-mock
```

The deterministic mock verifies contracts, persistence, retry behavior, and
reproducibility. It is deliberately not an empirical model baseline.

Freeze and audit the OpenAI external request set without sending data to a
provider:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark preflight-p1-openai \
  --store-dir experiments/p1/evidence-store \
  --config configs/evaluation/openai_pilot.yaml \
  --output experiments/p1/outputs/openai-preflight.json
```

The preflight locks `gpt-4.1-2025-04-14`, proves 15 matched pairs / 30 requests,
selects the eight-request smoke subset, scans the exact outbound payloads for
secrets and evaluator metadata, and reports four separate cost projections:
smoke one-attempt, smoke retry-ceiling, full one-attempt, and full retry-ceiling.
These are conservative token estimates under the frozen price/config contract,
not provider billing guarantees. Preflight does not construct an API client or
authorize an external send.

After inspecting the preflight artifact and its printed confirmation digest,
run only the frozen eight-request smoke subset. The API key is read from the
process environment after all local authorization checks and is never written
to an artifact:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark run-p1-openai-smoke \
  --store-dir experiments/p1/evidence-store \
  --config configs/evaluation/openai_pilot.yaml \
  --preflight experiments/p1/outputs/openai-preflight.json \
  --output-dir experiments/p1/outputs/openai-smoke \
  --confirm-preflight-sha256 <digest-printed-by-preflight>
PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-openai-smoke \
  --output-dir experiments/p1/outputs/openai-smoke \
  --store-dir experiments/p1/evidence-store \
  --config configs/evaluation/openai_pilot.yaml \
  --preflight experiments/p1/outputs/openai-preflight.json
```

This command makes externally billed requests. A wrong digest, changed source
store, changed config, changed request set, or changed provider identity fails
closed before an output can be accepted. Interrupted runs retain received raw
artifacts but remain invalid until a new output directory is used.

Only after the smoke output has passed validation may the complete 30-request
matrix be considered. Full execution requires both the exact preflight digest
and the displayed **estimated** full retry-ceiling cost; it rejects legacy
preflights that do not contain the four-budget block. The estimate is an
authorization checkpoint, not a provider billing guarantee:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark run-p1-openai-full \
  --store-dir experiments/p1/evidence-store \
  --config configs/evaluation/openai_pilot.yaml \
  --preflight experiments/p1/outputs/openai-preflight.json \
  --output-dir experiments/p1/outputs/openai-full \
  --confirm-preflight-sha256 <digest-printed-by-preflight> \
  --confirm-estimated-full-retry-ceiling-usd <estimate-printed-by-preflight>
PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-openai-full \
  --output-dir experiments/p1/outputs/openai-full \
  --store-dir experiments/p1/evidence-store \
  --config configs/evaluation/openai_pilot.yaml \
  --preflight experiments/p1/outputs/openai-preflight.json
```

The full runner preserves all attempts, allows no more than the frozen retry
budget, writes an authorization record before the first provider request, and
accepts only the complete 15-context x 2-variant request census.

Evaluate a validated pilot without collapsing correctness and groundedness into
one score:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark evaluate-p1-pilot \
  --pilot-dir experiments/p1/outputs/matched-pilot-mock \
  --store-dir experiments/p1/evidence-store \
  --cases-dir experiments/p1/cases \
  --output experiments/p1/outputs/matched-pilot-mock-evaluation.json
```

For any external output, also pass `--openai-config` and `--preflight` so the
evaluator identifies and revalidates its smoke or full execution authorization.
The deterministic cause
matcher is an auditable P1 baseline and explicitly requires final human semantic
review; it is not presented as a general-purpose semantic judge.

Run the complete local quality check:

```bash
make check
```

Generated datasets, models, predictions, and experiment runs are intentionally
excluded from Git. See [`docs/dataset-card.md`](docs/dataset-card.md) for
the dataset source, checksums, transformations, and usage constraints.

## Architecture

```text
src/aletheia_lab/
  data/          verified download and deterministic preprocessing
  baseline/      seeded splits, preprocessing, model training, metrics, artifacts
  benchmark/     fault injectors, signals, manifests, and validators
  evidence/      evidence contracts, persistence, and leakage detection
  diagnosis/     diagnosis variants, prompts, and structured output contracts
  evaluation/    correctness, faithfulness, abstention, agreement, and statistics
  reporting/     reusable result tables and plots
```

Configuration lives in `configs/`, technical specifications in `docs/`, and
tests in `tests/`. Research tracking and private planning material are kept
outside the repository so the public project remains focused on usable code,
documentation, and reproducible artifacts.

## Documentation

- [Benchmark protocol](docs/benchmark-protocol.md)
- [Evidence contract](docs/evidence-contract.md)
- [Evaluation protocol](docs/evaluation-protocol.md)
- [Dataset card](docs/dataset-card.md)
- [Architecture decisions](docs/adr/)

## Evidence and safety model

A benchmark case separates four concerns:

- the **case manifest**, which identifies the dataset, fault, seed, and artifacts;
- the **diagnosis input**, containing only evidence visible to the diagnoser;
- the **hidden ground truth**, available only to evaluators;
- the **provenance record**, which makes the injection reproducible.

Leakage checks fail when answer-key terms appear in visible evidence. Generated
artifacts are checksummed, and deterministic workflows compare independent runs
rather than assuming that setting a random seed is sufficient.

## Development

```bash
make install        # install the project with development dependencies
make lint           # run Ruff
make hygiene        # reject tracked caches, generated outputs, and private material
make test           # run the test suite
make check          # lint + hygiene + tests
make format         # format source and tests
```

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

## Research context

Aletheia extends a broader research question from explanation faithfulness to
system diagnosis: an explanation that looks convincing may still be disconnected
from the mechanism or evidence that produced the observed behavior. Controlled
fault injection, withheld evidence, and explicit abstention make that question
testable at the system level.

Companion projects provide related methodology and infrastructure:

- [`crossroute-audit`](https://github.com/umynameislove/crossroute-audit) —
  mechanistic auditing of explanation faithfulness;
- [`projmem`](https://github.com/umynameislove/projmem) — reproducible experiment
  memory and lineage;
- [`FactoryLens`](https://github.com/umynameislove/FactoryLens) — an applied,
  bounded diagnosis-agent case study.

## Citation

If Aletheia Lab supports your research, cite the project using
[`CITATION.cff`](CITATION.cff).

## License

The source code is available under the [MIT License](LICENSE). Third-party
datasets and models retain their original licenses and are not redistributed by
this repository.
