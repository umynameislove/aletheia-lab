# Evidence Schema

## Nguyên tắc

Evidence phải là object có ID, không phải text dump mơ hồ.

Mỗi causal claim của diagnosis phải trỏ được về một hoặc nhiều evidence IDs.

## Evidence item

```json
{
  "evidence_id": "metric-001",
  "kind": "metric",
  "title": "Evaluation metric regression",
  "content": "Accuracy dropped from 0.91 to 0.72.",
  "source_path": "data/interim/metrics/example.json",
  "visible_to_diagnoser": true
}
```

## Evidence bundle

```json
{
  "evidence_bundle_id": "ev-case-0001",
  "case_id": "case-0001",
  "allowed_evidence": [],
  "withheld_evidence": [],
  "counterfactual_evidence": [],
  "leakage_check_passed": true
}
```

## Evidence kinds

- metric
- config
- log
- artifact
- dataset_profile
- lineage
- counterfactual
- human_note

## Output contract cho evidence-bound diagnosis

- `root_cause_hypothesis`
- `causal_chain`
- `supporting_evidence_ids`
- `counterevidence_ids`
- `missing_evidence`
- `confidence`
- `abstention_if_needed`

## Lỗi cần tránh

- Evidence quá “sạch” đến mức gợi đáp án.
- Evidence là paragraph dài không cite được.
- Log chứa hidden ground truth.
- Model được xem injection script.
- Model được xem filename tiết lộ cause.
