# Diagnosis main freeze candidate V2

## Current decision

The forward candidate is internally consistent but **main-study readiness
remains blocked**. Its manifest is
`configs/evaluation/diagnosis_main_freeze_candidate_v2.json`, with canonical
self-hash `e25059164cd5625a0deb6209debd27523f7239494c38c3dafa4a82968de203e4`.
It authorizes no main execution and records zero consumed registered attempts.

The candidate reconciles all nine blockers in V1 rather than rewriting V1.
Four are closed forward:

1. exact analysis formulas, aggregation, multiplicity, missingness, precision,
   bootstrap, and null-result rules;
2. the one-shot main-analysis entrypoint and its dependency/environment hashes;
3. the LogDx-CI v1.2 pin, license/census/mapping, and correctly scoped cached
   evaluator replay; and
4. RQ6b exclusion from this registration, with its later prospective boundary
   preserved.

Five V1 requirements are represented by four consolidated blockers:

1. the sealed 30–36-family main case/split/request census does not exist;
2. the registry and synthetic development executor do not yet constitute an
   outcome-eligible nine-path main runtime;
3. Qwen Q8 download/build/calibration and the exact 12-family/72-request subset
   are not complete; and
4. independent methods approval must occur after technical closure.

## Scientific corrections in V2

- The primary confirmatory scope is one paired family-level `B1 - A3` harm
  estimand. Secondary metrics are descriptive estimates/intervals, not an
  unimplemented family of confirmatory Holm tests.
- Family effects receive equal weight after claim → output → family-condition-
  variant → condition aggregation. Missing terminal requests invalidate the
  registered execution; terminal technical failures and zero-claim successes
  remain in the denominator with harm score one.
- The 30–36-family design is explicitly precision-limited. It makes no formal
  power guarantee for a 0.05 effect and must report the achieved clustered
  interval.
- Qwen uses its documented sampling policy with a fixed seed. The seed is a
  repeatability control, not a determinism claim. Q6 is an operational fallback
  from a different converter repository and is not interchangeable with Q8.
- The completed LogDx result is called a cached evaluator replay. It is not a
  fresh model/API run, end-to-end native reproduction, or independent truth
  validation.
- RQ6b is zero cases and zero requests in the current registration. A later run
  requires a new prospective seal before protected case data are opened.

## Verification

Run from the repository root:

```bash
source .venv/bin/activate
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py
```

Expected now: integrity `pass`, readiness `blocked`, and the four blocker codes
above. The strict command must return exit code 2:

```bash
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py --require-ready
test "$?" -eq 2
```

Exit code 2 is the correct result for V2. It may become zero only on a new
forward-linked manifest after all four blockers close. Do not edit V1 or V2 to
make the lifecycle state pass retroactively.

## Required readiness transitions

1. Build and independently audit the exact main family/context/request census.
2. Implement the outcome-eligible runtime and test actual information-path
   conformance across declared reporting paths.
3. Download and verify Q8 outside Git, build pinned llama.cpp, perform only
   development operational calibration, then freeze the 72 request IDs from
   the sealed census.
4. Issue a V3 final candidate and send it to an outcome-blind independent
   methods reviewer.
5. Run `--require-ready`; only a pass may precede a separate, fresh main-run
   execution authorization.
