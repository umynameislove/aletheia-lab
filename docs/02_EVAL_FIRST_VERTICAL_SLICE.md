# P1 Eval-First Vertical Slice

## Lý do

Rủi ro lớn nhất của Aletheia là xây platform quá rộng nhưng eval yếu. Vì vậy P1 phải chạy end-to-end trước khi mở rộng.

## Scope P1

- Fault type: `data_drift`
- Case kernel: 5 injection families × 3 evidence contexts = 15 contexts
- Dataset: 1 dataset tabular classification nhỏ/dễ reproduce
- Variants:
  - Plain LLM
  - RAG baseline
  - Evidence-bound
  - Full Aletheia placeholder nếu memory chưa xong
- Metrics:
  - correctness
  - faithfulness
  - abstention
  - divergence label

## Pipeline

```text
base dataset
  -> inject data_drift
  -> train/evaluate
  -> classify measured outcome
  -> apply versioned failure-eligibility policy
  -> collect evidence
  -> run 4 diagnosis variants
  -> score diagnosis
  -> write error analysis
```

## Quality gate

Không qua P1 nếu thiếu một trong các mục:

- Có case manifest.
- Mỗi context có injected change và measured outcome.
- Chỉ eligible regression mới có hidden failure cause; stable/improvement là controls.
- Ba evidence contexts cùng family dùng chung `case_family_id`, injection provenance và evaluator truth.
- Evidence dùng contract v2; version/source/role là bắt buộc; `full/noisy` đủ
  decisive evidence; `missing_key` materialize các withheld counterpart evaluator-side.
- Diagnosis projection condition-blind cả field lẫn ID và không leak ground-truth.
- Có ít nhất 2 variants chạy thật.
- Có metric output dạng bảng.
- Có 5-10 lỗi được phân tích thủ công.

## Claim được phép sau P1

Được nói:

> Đã có vertical slice chứng minh pipeline đánh giá chạy được.

Không được đồng nhất “đã inject data drift” với “data drift đã gây failure”. Chưa được nói:

> Aletheia tốt hơn baseline.

Muốn nói tốt hơn baseline phải đợi P4/P5.

P1-G5A chỉ khóa schema/rubric. Collector thật, persistence gate và semantic leakage
audit vẫn phải hoàn thành trước khi coi evidence contract gate đã đóng.
