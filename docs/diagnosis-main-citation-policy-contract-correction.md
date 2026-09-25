# Diagnosis main citation-policy contract correction

This is a **post-result, forward code correction**. It does not amend the
registered analysis plan, census, response contract, claim labels, or sealed
1,024-request outcome. The original registered analysis report remains the
scientific result.

The frozen response contract requires citations for `cause_assertion` and
`evidence_statement` claims in A2, A3, CodeGraph, and FULL. It does not require
them for `uncertainty_statement`, `recommended_action`, or `other` claims. The
materializer (`diagnosis_main_materialization.py::_prepare_claim`) already
encoded this variant **and** claim-type rule. The analysis input validator
(`diagnosis_main_analysis.py::_validate_observed_contracts`) instead checked
the variant alone, so its validation contract could reject a correctly
materialized optional-type claim. The correction aligns that validator with
the frozen response contract and materializer; it does not change scoring,
citation validity, missingness, or any metric formula.

The pre-execution V7 freeze remains bound to the original analysis source and
freeze-test bytes. Auditing V7 against this post-result checkout now fails
closed on those two bindings, as it should; the historical manifest is not
resealed or treated as a current execution authorization. The updated
regression test checks that no other frozen binding has drifted.

Verification on the sealed inputs was offline and read-only. The corrected
analysis reproduced **every field** of the existing registered report,
including its self-hash
`163d51b66a0e2e1f23e8d7ccdc2c4c80061c907f080605925498617848ac52c0`.
All 1,024 terminal requests and the existing `benchmark_local_support_not_established`
disposition remain unchanged. The protected census, analysis input, registered
report, and recovery/scoring receipts were byte-identical before and after
verification. No provider call or registered attempt was made for this fix.

Focused synthetic tests cover every controlled variant and claim type, plus
both missing and falsely required citation-policy flags. This correction is
not a new analysis or a basis for stronger scientific claims.
