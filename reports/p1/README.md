# Phase 1 Frozen Closeout

This directory is the self-contained audit package for the completed Aletheia
Lab Phase 1 feasibility pilot.

## Decision

**GO to Phase 2.**

The decision means that the controlled data-drift slice, evidence contract,
matched diagnosis execution, evaluation and audit chain are technically
feasible. It is not a comparative-performance, production-readiness or broad
external-validity claim.

## Frozen results

| Layer | Result |
|---|---|
| Experimental census | 5 independent families, 15 contexts, 30 model outputs |
| Machine correctness | 23 correct, 1 incorrect, 6 not asserted |
| Machine support | 30/30 fully supported |
| Machine behavior | 29/30 compliant |
| Machine missing-key sensitivity | 10/10 |
| Machine noisy robustness | 8/10 |
| Evidence review | 15/15 entries and 5/5 families PASS |
| Diagnosis review | 24 correct, 6 not asserted, 30/30 compliant |
| Human–machine agreement | 29/30 |
| Human semantic noisy robustness | 10/10 |

Machine and human results are intentionally reported side by side. The
entry-level disagreement and the difference between structural and semantic
noisy robustness are preserved in the source reviews.

## Contents

- `p1-result-lock.json`: immutable machine result binding.
- `p1-machine-result.json`: frozen canonical machine table.
- `p1-final-closeout.json`: strict final decision and census.
- `human-review/evidence-*`: pseudonymized evidence review summary and its packets.
- `human-review/diagnosis-*`: pseudonymized diagnosis review summary and its packets.

## Offline validation

```bash
PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-final \
  --record reports/p1/p1-final-closeout.json \
  --machine-result reports/p1/p1-machine-result.json \
  --result-lock reports/p1/p1-result-lock.json \
  --evidence-review reports/p1/human-review/evidence-review.json \
  --evidence-blind-packet reports/p1/human-review/evidence-blind-packet.json \
  --evidence-mapping-packet reports/p1/human-review/evidence-mapping-packet.json \
  --diagnosis-review reports/p1/human-review/diagnosis-review.json \
  --diagnosis-blind-packet reports/p1/human-review/diagnosis-blind-packet.json \
  --diagnosis-mapping-packet reports/p1/human-review/diagnosis-mapping-packet.json
```

The validator does not contact a provider or read an API key.
