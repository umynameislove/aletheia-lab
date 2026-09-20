# Independent methods review for diagnosis main-study readiness

## Timing and independence

Run this review only after the already locked main census, outcome-eligible
runtime, and 72-request subset are joined by a passing Qwen development-
calibration receipt. The reviewer must not inspect
main model outputs, human outcome labels, hidden ground truth beyond design-only
eligibility metadata, or any analysis result. The reviewer must not be the
person who authored the final manifest.

One review may cover both the engineering preflight and final main-study
freeze, but it must give a separate decision for each. AI review may support
consistency checking; it is not
represented as an independent human methods approval.

## Required review packet

- V1 and every forward-linked freeze manifest;
- exact 32-family/128-context/1,024-request census and leakage audit;
- all eight controlled route implementations, attempt-store/reconciliation
  evidence, and runtime information-path audit;
- the separate 35-case B3/LogDx external-transfer boundary;
- main response contract and analysis plan/entrypoint;
- Qwen Q8 artifact/build/template/calibration receipt and exact 72 IDs;
- LogDx cached-replay receipt and claim boundary;
- RQ6b scope decision;
- focused tests, complete evaluation profile, static checks, and dependency
  inventory; and
- a diff proving that no protected outcome was opened to satisfy readiness.

## Reviewer checklist

The reviewer must answer every item with `true` or reject readiness:

1. Every main request has one immutable identity and belongs to the sealed
   census; family/split/context counts reconcile.
2. Required siblings and paired B1/A3 cells are complete before execution.
3. Hidden truth, evaluator metadata, labels, and variant-identifying leakage do
   not cross the provider boundary.
4. Matched contrasts receive the frozen observable facts and budgets; every
   non-matched path is reported in its own stratum.
5. The response contract and parser enforce citation/abstention policy without
   modifying historical artifacts.
6. Retries preserve request identity; terminal failures remain in the frozen
   denominator; no output repair or silent fallback exists.
7. The primary formula and aggregation implemented by the one-shot entrypoint
   match the analysis plan exactly.
8. Missing, duplicate, extra, or hash-mismatched terminal records fail closed.
9. The Qwen model, GGUF, runner, embedded template, build, host, decoding, and
   exact 72 IDs match the final manifest; Q6 activation, if any, is operational
   only and separately reported.
10. LogDx wording is limited to cached evaluator replay unless a separate fresh
    run exists.
11. RQ6b is absent from the current census and denominators.
12. No threshold, family, denominator, endpoint, retry, exclusion, or null
    branch was adapted to protected outcomes.
13. All bound hashes and tests reproduce on the review checkout.
14. Public claims remain within the declared evidence and precision boundary.
15. The primary interval is described as finite-census descriptive stability,
    not a p-value, causal interval, or superpopulation guarantee.
16. EviScope and other closest work are acknowledged; the paper does not claim
    novelty for paired evidence perturbation, and possible model-training
    contamination/familiarity is retained as a limitation.

## Machine commands

From a clean checkout of the reviewed commit:

```bash
source .venv/bin/activate
PYTHONPATH=src python scripts/audit_qwen_local_calibration.py \
  --receipt '/absolute/private/path/to/q8-calibration-v1.json'
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py
python scripts/run_test_profile.py evaluation --repeat 3
python -m pytest -q
ruff check .
mypy --strict --python-version 3.12 src
```

At review time the non-strict audit must have integrity `pass` and may remain
blocked only on the independent-review requirement. The review receipt must
bind the reviewed commit, manifest file SHA-256, canonical manifest self-hash,
Qwen calibration-receipt hash, test-receipt hashes, reviewer code, review date,
the 16 booleans, separate
`engineering_preflight_decision` and `main_freeze_decision`, limitations, and
its own canonical
SHA-256. A rejection or qualification is preserved; it is not edited into a
pass. After approval, a new final forward manifest binds that receipt; only
that final manifest may pass `--require-ready`. This avoids the circular claim
that a manifest requiring independent approval was already ready before the
approval existed.
