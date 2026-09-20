# Diagnosis main-study contract

## Decision summary

This contract freezes a finite, outcome-blind benchmark policy comparison. It does not
register a randomized trial, a model-ranking leaderboard, a causal estimand, or
a claim about all software failures. The main protected outcomes remain closed
and no registered attempt has been consumed.

The controlled matrix is exactly:

- 32 case families from a complete census of the bound P2R and P2-v3.3 source
  units: 10 data-drift, 10 preprocessing-mismatch, and 12 label-noise families;
- six dataset×mechanism superfamilies;
- four deterministic sibling contexts per family: `full`, `missing_key`,
  `noisy`, and `counterevidence`;
- eight controlled paths: `A1`, `A2`, `A3`, `B0`, `B1`, `B2`, `CodeGraph`, and
  `FULL`;
- 128 contexts and 1,024 controlled logical requests; and
- 896 provider-backed requests, 128 deterministic B0 requests, and 1,408
  provider turns if every declared route reaches its final turn.

`B3` is not a ninth controlled cell. It is the pinned 35-case LogDx-CI external
transfer stratum and keeps its native corpus, metrics, denominator, and
limitations. The 72-request Qwen sensitivity and RQ6b are also outside the
primary pool.

## Estimand and aggregation

The single registered primary estimand is the equal-family mean of paired
`B1 - A3` evidence-accountability loss across `full`, `missing_key`, and `noisy`.
Positive values favor A3 because lower loss is better. `counterevidence` is an
auxiliary behavior condition and is never inserted into the primary estimand.

Aggregation is fixed before outcomes:

1. Atomic claims receive equal weight within an output.
2. Outputs receive equal weight within family×condition×variant.
3. The three primary conditions receive equal weight within family×variant.
4. `B1 - A3` is paired within family.
5. The 32 family effects receive equal weight in the finite census estimate.

This avoids treating multiple claims, conditions, or output records from one
family as independent evidence. The six superfamilies are retained for
dependence sensitivity, not counted as 32 independent populations.

## Evidence-accountability loss

The primary atomic-claim loss is 1 for `contradicted` or `unsupported` and 0
for `fully_supported` or `partially_supported`. A locked sensitivity analysis
scores `partially_supported` as 0.5.

At output level:

- terminal technical failure = 1;
- successful output with zero claims = 1;
- appropriate abstention in `missing_key` or `counterevidence` = 0;
- false abstention in `full` or `noisy` = 1; and
- otherwise loss is the mean atomic-claim loss.

This combined loss is the accountability endpoint, not a substitute for all
behavioral reporting. Claim harm, output coverage, abstention, citation
validity, terminal failure, latency, tokens, and cost must also be shown
separately so an apparent gain cannot be hidden abstention or reduced output.

## Registered support rule and uncertainty

There is no null-hypothesis test and no p-value. The BCa family-resampling
interval is a descriptive perturbation/stability summary for this finite
census. It is not a causal interval or a guarantee of repeated-sampling
coverage for a population of projects.

The primary result supports the registered benchmark-local direction only if
all four conditions hold:

1. mean `B1 - A3` loss is at least 0.05;
2. the descriptive 95% BCa lower endpoint is above zero;
3. at least 75% of family effects are positive; and
4. the minimum leave-one-superfamily-out estimate is above zero.

Failure of any condition is reported as the registered support rule not met.
It is not repaired by changing a threshold, denominator, family set, condition,
loss, or exclusion after outcomes are visible.

## Fairness and attribution

The clean sequential comparisons are:

- `B1-A1`: plain versus structured rendering of the registered visible facts;
- `A1-A2`: citation requirement; and
- `A2-A3`: abstention/missing-evidence contract.

`B1-A3` is intentionally a bundle contrast. `B1-B2` combines selection and
multi-turn interaction. B0 is deterministic, CodeGraph and FULL receive
different structures, and B3 is external transfer. None of those differences
may be described as an isolated component effect.

Every provider turn is restricted to the request's declared visible corpus.
B2 and CodeGraph have two provider turns; FULL has three. The runtime persists
the request before invocation and writes a create-only result/selection ledger
afterward. An incomplete turn cannot be replayed automatically, repaired, or
silently switched to another model/prompt.

## Failure, missingness, and deviation

- A missing terminal record, duplicate, extra record, or identity mismatch
  invalidates the registered execution.
- A recorded terminal technical failure remains in the denominator with loss
  one; complete-case analysis is not a primary substitute.
- Post-outcome exclusions are forbidden.
- A scientific change requires a new forward-linked manifest before outcome
  opening. A technical correction requires a forward receipt.
- Null, negative, infeasible, and precision-limited outcomes are retained.

## External sensitivity boundaries

Qwen uses 12 deterministically selected families, the three primary evidence
conditions, and only B1/A3: 72 requests. It tests whether the within-model
policy direction is visible in a second model family. It is not pooled with
GPT-4.1 and is not used for a cross-model superiority claim. Its development
calibration evaluates only operational compatibility and repeatability.

LogDx-CI supports the narrow statement that the pinned evaluator scores and
result manifest were reproduced from release-cached diagnoses. It is not a
fresh diagnoser run or an independent truth validation.

RQ6b contributes zero cases and zero requests to this registration. A later
named-project study requires a prospective source/acquisition/holdout seal
before protected data access.

## Main-study freeze criterion

The census, analysis contract, response contract, controlled runtime, runtime
preflight, Qwen 72-request census, LogDx boundary, and RQ6b decision are frozen.
The main-study freeze remains fail-closed until both remaining conditions are
evidenced:

1. the exact Qwen Q8/llama.cpp development-calibration receipt passes (or Q6 is
   activated only under the predeclared operational rule and disclosed); and
2. an outcome-blind independent methods reviewer separately approves the
   engineering preflight and final main-study freeze.

Only a new forward-linked manifest that passes
`audit_diagnosis_main_freeze.py --require-ready` can precede a separate fresh
authorization for the registered main execution.
