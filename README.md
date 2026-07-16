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
- CLI commands for data preparation, baseline training, verification, and
  contract validation;
- automated linting, repository-hygiene checks, and tests in CI.

The project is in **active alpha development**. Dataset preparation, baseline
training, and data-drift injection are operational. The complete diagnosis and
meta-faithfulness runtime is being integrated and should not yet be treated as a
production incident-response system.

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
aletheia plan --config configs/project.yaml
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

Run the complete local quality check:

```bash
make check
```

Generated datasets, models, predictions, and experiment runs are intentionally
excluded from Git. See [`docs/05_DATASET_CARD.md`](docs/05_DATASET_CARD.md) for
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
