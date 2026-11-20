# Aletheia Lab

**Evidence-grounded diagnosis and audit for machine learning.**

Aletheia Lab uses controlled experiments to test what the available evidence
can establish about ML failures, model state, and recovery. Its project audit
preserves the observations and decision criteria behind each result.

## Research highlights

- **Failure localization and recovery.** In a controlled Wine and Digits study,
  ten pairs with different fault sites produced identical prediction evidence.
  Recorded input and model lineage distinguished the sites within the tested
  catalogue. A separate repair study showed that prediction behavior can recover
  while the intended state is still altered. [Ground-truth study](docs/p2-ground-truth-validity.md) ·
  [Active diagnosis study](docs/active-diagnosis-study.md)
- **Historical model-state verification.** Two synthetic ONNX runs shared a graph,
  current alias, and output `[4, 3]`. Under the same historical contract,
  the state each run actually used led to opposite decisions. The hosted
  workflow preserves the original decision and citation through refresh,
  follow-up, and export. [ONNX fixtures](fixtures/historical-state/v1/README.md) ·
  [Hosted evidence contract](docs/historical-state-evidence-intake.md)
- **Claim verification.** In a paired diagnosis audit, human references covered
  198 of 636 scored claims. Under the audit's assumptions about the remaining
  claims, either variant could lead. A companion study tests whether adding
  replayable evidence changes review decisions under the same requirements.
  [Measurement audit](docs/diagnosis-comparison-robustness.md) ·
  [Claim validation protocol](docs/claim-support-validation-v3.md)

The primary causal-diagnosis benchmark admitted zero mechanisms under its
registered validity gates.
[Public evidence policy](docs/claim-evidence-governance.md) ·
[Human-reference disclosure](docs/claim-support-v3-2-late-reference-freeze-amendment.md)

## Project audit

Import an experiment ZIP, inspect metrics and citations in the dashboard,
then reopen the saved result or export JSON, Markdown, or PDF. Hosted results
are tied to an immutable snapshot. Workspace and source permissions govern
who can read the result, follow its citations, and download it.
[Dashboard quickstart](docs/p6-dashboard-quickstart.md)

## Run the local demo

On macOS or Linux, run this from the repository root with Python 3.11+ and Git.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
demo_dir=$(mktemp -d)
.venv/bin/aletheia product-demo "$demo_dir"
```

The demo creates a synthetic regression, runs a deterministic mock analysis,
and writes three reports with SHA-256 hashes under `$demo_dir/reports`. It runs
without an API key or provider call. The research methods and their verification
steps are documented separately. [Methods and reproduction](docs/diagnosis-foundations.md)

[How to cite](CITATION.cff) · [MIT license](LICENSE) · [Contributing](CONTRIBUTING.md)
