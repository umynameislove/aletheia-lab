# Independent methods review for the Qwen3.8 forward freeze

## Timing and independence

Review only after the exact Qwen3.8 Q8 preparation and seven-call synthetic
calibration pass. The reviewer must not inspect main model outputs, protected
outcome labels, hidden ground truth beyond design-only eligibility metadata, or
analysis results. The reviewer must not be the author of the final ready
manifest. AI consistency checks do not count as independent human approval.

## Required packet

- every forward-linked main freeze and the Qwen3.8 amendment;
- Qwen3.8 candidate, exact Q8/build/template identities, private calibration
  receipt hash, and exact 72 request IDs;
- sealed 32-family/128-context/1,024-request census and leakage audit;
- eight controlled runtime paths and information-path fairness audit;
- main response contract, V3 analysis plan, and one-shot entrypoint;
- separate LogDx-CI and RQ6b scope records; and
- focused tests, evaluation profile, dependency inventory, and a diff showing
  protected outcomes remained unopened.

## Required decisions

The reviewer must answer every item `true` or reject readiness:

1. The Qwen3.8 replacement was prospective, outcome-blind, and preserved its
   predecessor rather than rewriting history.
2. Base revision, Q8 bytes/SHA-256, source and embedded template, llama.cpp
   tag/commit, Metal Release build, host, and server flags match the candidate.
3. Text-only, non-thinking mode and official non-thinking sampling are fixed;
   no vision/MTP/DFlash sidecar, tool, shell, web, or fallback model is enabled.
4. Calibration contains exactly six synthetic B1/A3 cells and one same-seed
   replicate, retains no raw output in its receipt, and uses no answer-quality
   selection.
5. The same preselected 12 families and 72 request IDs remain unchanged and
   independent of model identity.
6. Qwen is descriptive, within-model only, separate from GPT-4.1, and excluded
   from cross-model superiority or pooled primary evidence.
7. Every main request belongs to the sealed census and all required sibling
   contrasts reconcile.
8. Hidden truth, evaluator metadata, labels, and variant-identifying leakage do
   not cross a provider boundary.
9. Retry, failure, abstention, missingness, exclusion, denominator, threshold,
   and analysis rules remain prospective and fail closed.
10. LogDx wording remains limited to its frozen evidence and RQ6b remains
    outside the current census.
11. Bound hashes and the required test profile reproduce on the reviewed
    checkout.
12. Public claims retain the benchmark-local, non-causal, contamination-aware
    precision boundary.

The receipt must bind the reviewed commit, amendment, candidate, calibration
receipt, main manifest, test evidence, reviewer code/date, all 12 booleans,
separate `engineering_preflight_decision` and `main_freeze_decision`, stated
limitations, and its own canonical SHA-256. A rejection is preserved.

## Review commands

```bash
source .venv/bin/activate
PYTHONPATH=src python scripts/audit_qwen_local_calibration.py \
  --receipt '/absolute/private/path/to/qwen38-q8-calibration-v1.json'
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py
python scripts/run_test_profile.py evaluation --repeat 3
ruff check .
mypy --strict --python-version 3.12 src
```

The non-strict main-freeze audit must pass integrity while remaining blocked.
Only the subsequent forward manifest that binds both the calibration and review
receipts may pass `--require-ready`; a separate fresh authorization is still
required before any registered execution.
