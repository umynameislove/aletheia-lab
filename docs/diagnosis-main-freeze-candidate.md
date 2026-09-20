# Main diagnosis freeze candidate

## Decision

The current main-study freeze candidate is content-addressed and fail-closed, but the
main study is **not ready for registration or execution**. The candidate locks
the choices that already have prospective authority and records every remaining
requirement without filling missing fields by inference.

The machine-readable authority is
`configs/evaluation/diagnosis_main_freeze_candidate.json`. Its integrity can
pass while readiness remains blocked:

```bash
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py
PYTHONPATH=src python scripts/audit_diagnosis_main_freeze.py --require-ready
```

The first command verifies the candidate's identity and all repository-bound
hashes. The second intentionally returns a blocking exit code until every named
requirement has been removed by a forward-linked freeze revision.

## Bound evidence

- Public-claim governance reconciles every current public statement to its
  evidence state and denominator, while keeping the local RQ0 result outside
  public surfaces until publication review.
- RQ0 claim-support instrument validity passed all four prespecified gates on
  exactly 200 human-adjudicated development claims.
- The onboarding chronology remains `PASS WITH GAP`; the main result does not
  erase the late reference-key freeze.
- The seven-run AI panel remains advisory and outside the human reference,
  adjudication and primary scientific metrics.
- The P2R v1.2 negative result, zero admitted-mechanism denominator and P3 core
  closeout remain unchanged.
- The release-profile and dependency-drift audit passed three distinct
  evaluation-profile seeds, the coverage/runtime gates, static quality checks
  and a live dependency-vulnerability lookup.

Private item-level human labels, rationales, rater mapping and local paths are
not present in this candidate. It binds only approved content identities and
aggregate scientific status.

## Choices already locked

- OpenAI `gpt-4.1-2025-04-14`, temperature `0`, top-p `1`, seed `17`, 600 output
  tokens, 60-second deadline and at most two provider attempts for one immutable
  request;
- the nine-variant census and distinct matched, reference, external and system
  reporting classes;
- every prompt content hash;
- 12,000 context tokens, 32 retrieved items and three turns for matched main
  variants, with hidden truth and evaluator metadata excluded;
- the two registered UCI archive identities already used by the feasibility
  boundary;
- zero admitted mechanisms, separate assumption-limited/rejected tracks and an
  evidence-accountability track that cannot be relabeled as causal diagnosis;
- policy-level metric thresholds already fixed in the canonical gate matrix;
- failure retention, exclusion, non-pooling and no-fallback rules; and
- the no-detectable-benefit wording branch, including invalid-study handling
  and prohibited equivalence, universal-failure and hidden-success claims.

## Fail-closed conditions

The existing preregistration is still a template and contains unresolved `TBD`
fields. The exact main-study case/split/request census, aggregation formulas,
multiplicity policy, precision justification and analysis entrypoint are not
content-addressed. The required LogDx-CI native reproduction, local Qwen
identity, baseline information-path fairness audit and diagnosis execution gate have
not closed. The prospective RQ6b seal must either close or be explicitly left
outside this registration. Independent methods approval is also outstanding.

None of these gaps may be repaired by opening main-study outcomes, changing a threshold
after seeing a result or treating development-fixture success as main-study
evidence.

## Final-manifest requirements

A final manifest must bind the exact main case, split and request census;
replace every analysis-plan `TBD` with fixed formulas, aggregation,
multiplicity, missingness and precision rules; and identify the one-shot
analysis entrypoint with all dependent source hashes. It must also bind the
native LogDx-CI reproduction, the local Qwen sensitivity artifact, the baseline
information-path fairness result, the diagnosis execution contract and the
declared RQ6b seal scope.

Independent methods approval and a forward link from this candidate are
required. Only that final manifest may be considered for a separate execution
authorization.
